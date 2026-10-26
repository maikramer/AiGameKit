"""
Text2D — Geração de imagens com FLUX.3 Image Klein (SDNQ runtime).

Modelo único: ``black-forest-labs/flux-3-image-klein`` (transformer 5.6B
guidance-distilled + text encoder Qwen3-VL-4B), servido pelo **ModularPipeline**
do diffusers (lazy load: ``from_pretrained`` só resolve configs; os pesos entram
com ``load_components``). Requer diffusers >= 0.42.0.

Features do modelo expostas: text-to-image, edição/composição com até
``MAX_REFS`` imagens de referência (workflow ``image_conditioned``), layout por
bounding boxes e prompt upsampler 9B opt-in (bloco ``prompt_upsampler``, popado
do pipeline por defeito para não carregar ~19 GB de pesos extra).

Requer ``sdnq`` instalado para registar quantização no diffusers/transformers.
O checkpoint é **gated** (FLUX Kommunity License v1.0): aceitar a licença no
Hub + ``HF_TOKEN``.

Herda infraestrutura de ``DiffusionGeneratorBase`` (lifecycle, logging, cache,
multi-GPU, save_image). Mantém apenas a lógica única: load modular, pop do
upsampler, quantização SDNQ runtime, planner de offload e a chamada de geração.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image

from aigamekit_shared.base_generator import DiffusionGeneratorBase

# Re-export para backward compat (testes antigos importam de text2d.generator).
from aigamekit_shared.base_generator import torch_dtype_for as _torch_dtype_for  # noqa: F401

# Modelo BASE (bf16, não pré-quantizado). A quantização (uint8/int8/int4/int3/int2/fp8)
# é escolhida por VRAM e aplicada em **runtime** via SDNQ — assim seguimos as melhorias
# do SDNQ upstream em vez de depender de checkpoints pré-quantizados congelados.
#
# Alias LOW_VRAM_MODEL_ID mantém-se porque o GameAssets (vramd_batch) importa este
# símbolo; desde a migração FLUX.3 há um só modelo (o antigo ladder 4B/9B acabou).
DEFAULT_MODEL_ID = "black-forest-labs/flux-3-image-klein"
LOW_VRAM_MODEL_ID = DEFAULT_MODEL_ID

# Modelo step-distilled: treinado para 4-16 steps, sem classifier-free guidance
# (o pipeline distilled não tem guider nem negative_prompt). ``guidance_scale``
# continua aceite na API/CLI por compatibilidade com GameAssets/perfis, mas é
# ignorado (aviso único).
DEFAULT_STEPS = 16
TRAINED_STEPS_RANGE = (4, 16)

# Máximo de imagens de referência do workflow image_conditioned (limite do modelo).
MAX_REFS = 10

# Repo do prompt upsampler (Qwen3.5-9B) — só carrega com ``upsample=True``.
PROMPT_UPSAMPLER_REPO = "black-forest-labs/flux-3-image-klein-pup"

# Fração do orçamento de VRAM que um plano full-GPU pode usar; acima disso o
# planner prefere **group offload + CUDA streams** (pico ≈ ativação). Motivo:
# "caber por pouco" rebenta em OOM no VAE decode/runtime. Com GO+streams o pico
# cai para ~ativação e sobra folga real. Kill-switch: ``--no-group-offload``
# ou ``TEXT2D_GROUP_OFFLOAD=0`` (além do global ``AIGAMEKIT_GROUP_OFFLOAD=0``).
FULL_GPU_BUDGET_FRACTION = 0.70

# Env var do kill-switch de group offload (precedência: tool > global).
GROUP_OFFLOAD_ENV = "TEXT2D_GROUP_OFFLOAD"


def model_footprint(model_id: str) -> Any:
    """Pegada fp16 (GiB) do modelo BASE — consulta o registry centralizado.

    FLUX.3 Klein: transformer 5.6B + text encoder Qwen3-VL 4B + VAE Flux2 ≈
    9.7B parâmetros (~18.5 GiB fp16); o maior módulo é o transformer (~11 GiB).
    """
    from aigamekit_shared.lowvram import get_footprint

    return get_footprint("flux3-klein")


def model_footprint_key(model_id: str) -> str:
    """Chave de footprint (registry lowvram) do modelo BASE — para peak vramd."""
    return "flux3-klein"


def _model_id(memory_efficient: bool = False) -> str:
    del memory_efficient  # mantido na assinatura por compat; um só modelo.
    return os.environ.get("TEXT2D_MODEL_ID") or DEFAULT_MODEL_ID


def default_model_id() -> str:
    """Modelo HF por defeito (ou `TEXT2D_MODEL_ID`)."""
    return _model_id()


class KleinFluxGenerator(DiffusionGeneratorBase):
    """Carrega o ModularPipeline FLUX.3 Image Klein com pesos SDNQ runtime.

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _status, _place_with_planner, save_image, generate_batch.

    Group offload + CUDA streams default ON (gate de folga
    ``FULL_GPU_BUDGET_FRACTION`` no planner; kill-switch ``TEXT2D_GROUP_OFFLOAD``).

    Args:
        upsample: Carrega o prompt upsampler 9B (``PROMPT_UPSAMPLER_REPO``) e
            deixa o pipeline expandir o prompt curto na caption completa
            recomendada pela BFL. Default OFF: pop do bloco antes de
            ``load_components`` (os ~19 GB do pup nem tocam em RAM/VRAM).
    """

    GROUP_OFFLOAD_ENV = "TEXT2D_GROUP_OFFLOAD"

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        quant_preset: str | None = None,
        torch_compile: bool | None = None,
        torch_compile_mode: str = "default",
        step_cache: str | None = None,
        channels_last: bool = False,
        group_offload: bool = True,
        upsample: bool = False,
    ) -> None:
        super().__init__(
            device=device,
            verbose=verbose,
            model_id=model_id or _model_id(memory_efficient=memory_efficient),
            cache_dir=cache_dir,
            gpu_ids=gpu_ids,
            memory_efficient=memory_efficient,
            group_offload=group_offload,
            torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            step_cache=step_cache,
            channels_last=channels_last,
        )
        # Preset SDNQ explícito (override); None = o planner decide por VRAM em runtime.
        self.quant_preset = quant_preset
        self.upsample = upsample
        self._plan: Any = None
        self._guidance_warned = False

        if self.verbose:
            from aigamekit_shared.logging import Logger

            Logger().info(f"device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    def _group_offload_allowed(self) -> bool:
        """Group offload habilitado: flag do ctor AND env kill-switch."""
        return bool(self._go_planner_kwargs()["allow_group_offload"])

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        from aigamekit_shared.quantization import set_memory_optimization_env

        set_memory_optimization_env()

        from aigamekit_shared.sdnq import apply_quantized_matmul, register_sdnq

        register_sdnq()

        plan = self._resolve_plan()
        self._plan = plan

        preset = self._resolve_preset(plan)
        qlabel = preset or "bf16 (sem quant)"

        self._preflight_download()

        import torch
        from diffusers import ModularPipeline

        self._status(f"Passo 1/5 — ModularPipeline blocks (quant={qlabel})")
        self._log(f"Carregando base {self.model_id} (quant={qlabel}, upsample={self.upsample})...")
        # Lazy load: from_pretrained só resolve o índice modular; o pop do bloco
        # do upsampler tem de acontecer ANTES do init/load para os ~19 GB do pup
        # nunca tocarem em disco→RAM quando upsample=False (default).
        blocks = ModularPipeline.from_pretrained(self.model_id, cache_dir=self.cache_dir).blocks
        if not self.upsample:
            blocks.sub_blocks.pop("prompt_upsampler", None)
        pipe = blocks.init_pipeline(self.model_id)

        self._status(f"Passo 2/5 — load_components (pesos; 1ª vez baixa vários GB; quant={qlabel})")
        pipe.load_components(
            dtype={"vae": torch.float32, "default": torch.bfloat16},
            cache_dir=self.cache_dir or None,
        )

        if preset:
            self._status(f"Passo 3/5 — quantização SDNQ {preset} em runtime (layer-by-layer)")
            self._runtime_quantize(pipe, preset)

        self._status("Passo 4/5 — SDNQ (matmul quantizado desligado no FLUX.3)")
        # FLUX.3 calcula o embedding de timestep em float32 e o kernel int8 do
        # SDNQ não faz autocast (RuntimeError float != bf16 no Flux3 time
        # embedding). A via dequant+linear (default do preset) funciona; a
        # aceleração de matmul fica desligada para este modelo.
        apply_quantized_matmul(pipe, enabled=False)

        self._status(f"Passo 5/5 — colocação: {plan.summary()}")
        self._clear_cache()
        self._reset_peak_mem_stats()

        # Colocação unificada: _place_with_planner resolve specs (VRAM livre), aplica
        # multi-GPU (accelerate) / group offload + streams / full-GPU conforme planner.
        # A quantização já foi aplicada acima — o placement planeia só com esse quant
        # (allow_quant fixo) e com o gate de folga FULL_GPU_BUDGET_FRACTION: sem
        # folga, prefere group offload + CUDA streams (pico ≈ ativação).
        # target_resolution=1024 (tier medium): VAE tiling + attention slicing
        # ligam como chunks menores mesmo em full-GPU.
        placement_plan = self._place_with_planner(
            pipe,
            model_footprint(self.model_id),
            quant_mode=preset or "none",
            allow_quant=("none", preset) if preset else ("none",),
            model_attr="transformer",
            # Com --upsample o pup 9B também tem de ser streamado pelo GO (sem
            # isto ficaria na CPU e cada caption levaria minutos).
            offload_modules=("transformer", "text_encoder", "prompt_upsampler") if self.upsample else None,
            target_resolution=1024,
            **self._go_planner_kwargs(full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION),
        )

        # Otimizações de speed: compile + step cache + channels_last + attention.
        self._maybe_compile_transformer(pipe, placement_plan)
        self._maybe_apply_step_cache(pipe, placement_plan)
        self._maybe_apply_channels_last(pipe, placement_plan)
        self._maybe_select_attention_backend(pipe, placement_plan)

        self._pipe = pipe
        return pipe

    def _preflight_download(self) -> None:
        """Garante o checkpoint em disco antes do load (download com resume/progresso).

        O repo FLUX.3 traz ficheiros que o ModularPipeline não usa (~23 GB):
        ``variants/*`` (fp8r/nvfp4) e o ``model.safetensors`` original da BFL
        (single-file). O preflight ignora-os; o ``load_components`` só pede os
        componentes do layout diffusers (transformer/text_encoder/vae).
        """
        from aigamekit_shared.model_download import ensure_model

        try:
            ensure_model(
                self.model_id,
                cache_dir=self.cache_dir,
                on_status=self._status,
                ignore_patterns=["variants/*", "model.safetensors", "*.md"],
            )
        except Exception as exc:
            self._log(f"preflight download falhou ({exc}); a deixar load_components tratar")

    def _resolve_plan(self) -> Any:
        """Resolve o OffloadPlan por VRAM (quant + offload) para o modelo base."""
        from aigamekit_shared.hardware import cuda_gpu_specs
        from aigamekit_shared.lowvram import plan_offload

        specs = [] if self.device == "cpu" else cuda_gpu_specs()
        if self.gpu_ids:
            keep = set(self.gpu_ids)
            specs = [(i, m) for i, m in specs if i in keep]
        allow_multi = self.gpu_ids is None or len(self.gpu_ids) >= 2
        return plan_offload(
            specs,
            model_footprint(self.model_id),
            allow_multi_gpu=allow_multi,
            **self._go_planner_kwargs(full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION),
        )

    def _resolve_preset(self, plan: Any) -> str | None:
        """Preset SDNQ a aplicar (``quant_preset`` explícito ganha; senão o do plano)."""
        preset = self.quant_preset or (plan.quant_mode if plan.quant_mode != "none" else None)
        if not preset or preset == "none":
            return None
        from aigamekit_shared.sdnq import is_available

        if not is_available():
            self._log("SDNQ indisponível — base em bf16 (sem quantização)")
            return None
        return preset

    def _runtime_quantize(self, pipe: Any, preset: str) -> None:
        """Quantiza os componentes pesados do pipeline em runtime (SDNQ post-load).

        Componentes FLUX.3 Klein: ``transformer`` (5.6B), ``text_encoder``
        (Qwen3-VL 4B) e, quando ``upsample=True``, ``prompt_upsampler``
        (Qwen3.5 9B — sem quant não passa de GPUs pequenas). O VAE fica em
        fp32 (qualidade do decode).
        """
        from aigamekit_shared.sdnq import quantize_model

        quant_device = "cuda" if self.device == "cuda" else "cpu"
        attrs = ["transformer", "text_encoder"]
        if self.upsample:
            attrs.append("prompt_upsampler")
        for attr in attrs:
            mod = getattr(pipe, attr, None)
            if mod is None:
                continue
            try:
                setattr(
                    pipe,
                    attr,
                    quantize_model(
                        mod,
                        preset,
                        quantization_device=quant_device,
                        return_device="cpu",
                        # O ModularPipeline deriva o dtype de compute de
                        # ``transformer.dtype`` (= dtype do 1.º param flutuante).
                        # Com scales fp32 (default do preset) o pipeline passaria
                        # latents fp32 e os lineares não-quantizados (bf16)
                        # rebentavam com "float != bf16". Scales bf16 mantêm o
                        # compute bf16 e o dequant consistente.
                        dequantize_fp32=False,
                    ),
                )
                self._log(f"SDNQ {preset} aplicado a {attr}")
            except Exception as exc:
                self._log(f"quantização de {attr} falhou ({exc}); componente fica em bf16")
        self._clear_cache()

    def _resolve_ref_images(self, refs: list[Any] | None) -> list[Any] | None:
        """Normaliza referências para PIL (aceita paths/str/PIL). Valida limite."""
        if not refs:
            return None
        images: list[Any] = []
        for ref in refs:
            if isinstance(ref, Image.Image):
                images.append(ref)
                continue
            path = Path(ref)
            if not path.is_file():
                raise FileNotFoundError(f"Imagem de referência não encontrada: {path}")
            images.append(Image.open(path).convert("RGB"))
        if len(images) > MAX_REFS:
            raise ValueError(f"Máximo de {MAX_REFS} imagens de referência (recebidas {len(images)})")
        return images

    def generate(
        self,
        prompt: str,
        height: int | None = None,
        width: int | None = None,
        guidance_scale: float | None = None,
        num_inference_steps: int = DEFAULT_STEPS,
        seed: int | None = None,
        refs: list[Any] | None = None,
        boxes: list[dict[str, Any]] | None = None,
        num_images: int = 1,
        should_abort: Any = None,
        on_step: Any = None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera imagem(ns) a partir de um prompt (e referências/boxes opcionais).

        Args:
            prompt: Pedido curto (o upsampler expande quando ``upsample=True``) ou
                caption completa já escrita à mão.
            height/width: None deixa o pipeline decidir (1024² em t2i; tamanho da
                1.ª referência em image_conditioned).
            guidance_scale: Aceito por compatibilidade (GameAssets/perfis) mas
                **ignorado** — o checkpoint distilled não tem classifier-free
                guidance. Um aviso único é logado.
            num_inference_steps: 4-16 (faixa treinada do modelo distilled).
            refs: Até ``MAX_REFS`` imagens (paths, str ou PIL) — ativa o workflow
                image_conditioned (edição/composição; "a primeira imagem",
                "a segunda imagem"... no prompt).
            boxes: Layout por boxes — dicts ``{id, desc, bbox: [y0,x0,y1,x1] em
                0-1}`` (opicionais ``from``/``src_bbox``/``tgt_bbox`` para edits).
            num_images: Imagens por pedido (``num_images_per_prompt``).

        Returns:
            Tuple ``(image, metadata)`` — a primeira imagem gerada (a lista
            completa vem em ``metadata["images"]`` quando ``num_images > 1``).
        """
        from aigamekit_shared.diffusion_control import attach_step_hooks
        from aigamekit_shared.validation import validate_params

        if guidance_scale is not None and not self._guidance_warned:
            self._log(
                "guidance_scale ignorado: FLUX.3 Image Klein é guidance-distilled "
                "(sem classifier-free guidance); o parâmetro mantém-se na API por compat."
            )
            self._guidance_warned = True

        # Paridade com os siblings (Skymap2D/Texture2D): erro limpo por dimensões
        # não múltiplas de 8 / steps fora do intervalo em vez de falha críptica.
        # W/H podem ser None (o pipeline deriva do tamanho da 1.ª referência).
        params: dict[str, Any] = {
            "num_inference_steps": num_inference_steps,
        }
        if width is not None:
            params["width"] = width
        if height is not None:
            params["height"] = height
        is_valid, error = validate_params(params, min_steps=1, default_guidance=1.0, default_steps=DEFAULT_STEPS)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")
        if not TRAINED_STEPS_RANGE[0] <= num_inference_steps <= TRAINED_STEPS_RANGE[1]:
            self._log(
                f"num_inference_steps={num_inference_steps} fora da faixa treinada "
                f"{TRAINED_STEPS_RANGE} — o resultado pode degradar."
            )

        ref_images = self._resolve_ref_images(refs)

        pipe = self._load_pipeline()
        resolved_seed = self._resolve_seed(seed)
        generator = self._build_generator(resolved_seed)

        self._clear_cache()
        self._log("Inferência...")
        pipe_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "num_inference_steps": num_inference_steps,
            "generator": generator,
            "num_images_per_prompt": max(1, int(num_images)),
        }
        if width is not None:
            pipe_kwargs["width"] = width
        if height is not None:
            pipe_kwargs["height"] = height
        if ref_images:
            pipe_kwargs["image"] = ref_images
        if boxes:
            pipe_kwargs["boxes"] = boxes
        attach_step_hooks(
            pipe_kwargs,
            num_inference_steps=num_inference_steps,
            should_abort=should_abort,
            on_step=on_step,
        )
        # Com upsampler, pedir também a caption que o pup escreveu (observabilidade
        # do que o modelo de imagem recebeu).
        output_keys: str | list[str] = ["images", "prompt"] if self.upsample else "images"
        out = pipe(**pipe_kwargs, output=output_keys)
        caption = None
        if isinstance(out, dict):
            images = out.get("images") or []
            caption = out.get("prompt")
        else:
            images = out
        if not images:
            raise RuntimeError("o pipeline não devolveu imagens")
        metadata: dict[str, Any] = {
            "prompt": prompt,
            "seed": resolved_seed,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
            "refs": len(ref_images) if ref_images else 0,
            "boxes": len(boxes) if boxes else 0,
            "num_images": len(images),
            # Lista completa (só difere de [image] com num_images > 1).
            "images": list(images),
        }
        if caption:
            # Caption expandida pelo prompt upsampler (None sem --upsample).
            metadata["caption"] = caption
        return images[0], metadata

    @staticmethod
    def save_image(image: Image.Image, path: Path | str, image_format: str | None = None) -> Path:
        """Guarda a imagem num path (herda de DiffusionGeneratorBase)."""
        return DiffusionGeneratorBase.save_image(image, path, image_format)
