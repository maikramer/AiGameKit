"""
Text2D — Geração de imagens com FLUX.2 Klein (SDNQ / Disty0).

Default: 9B SDNQ (high-VRAM), 4B SDNQ (--low-vram).
Requer `sdnq` instalado para registar quantização no diffusers/transformers.
"""

from __future__ import annotations

import gc
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

import torch
from PIL import Image

from gamedev_shared.logging import Logger

_logger = Logger()

# Modelos BASE (fp16, não pré-quantizados). A quantização (uint8/int8/int4/fp8) é
# escolhida por VRAM e aplicada em **runtime** via SDNQ — assim seguimos as melhorias
# do SDNQ upstream em vez de depender de checkpoints pré-quantizados congelados.
HIGH_VRAM_MODEL_ID = "black-forest-labs/FLUX.2-klein-9B"
LOW_VRAM_MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"


def model_footprint(model_id: str) -> Any:
    """Pegada fp16 (GiB) do modelo BASE — alimenta ``plan_offload`` para escolher o
    preset de quantização + offload por VRAM.
    """
    from gamedev_shared.lowvram import ModelFootprint

    # Pegadas calibradas com medições reais no RTX 4050 6GB: o "4B"/"9B" é só o
    # transformer; o pipeline inclui um text-encoder grande (Mistral-class), por isso o
    # fp16 total é bem maior. Com int4, o residente do 4B ~4.5GB → em 6GB tem de ir a
    # offload (validado: pico ~4.1GiB em model_cpu offload).
    if model_id == LOW_VRAM_MODEL_ID:
        return ModelFootprint(fp16_weights_gib=14.0, activation_gib=1.5, largest_module_gib=5.0)
    # 9B base (default/high ou override desconhecido).
    return ModelFootprint(fp16_weights_gib=26.0, activation_gib=1.5, largest_module_gib=9.0)


def _model_id(low_vram: bool = False) -> str:
    if os.environ.get("TEXT2D_MODEL_ID"):
        return os.environ["TEXT2D_MODEL_ID"]
    return LOW_VRAM_MODEL_ID if low_vram else HIGH_VRAM_MODEL_ID


def default_model_id() -> str:
    """Modelo HF por defeito (ou `TEXT2D_MODEL_ID`)."""
    return _model_id()


def _torch_dtype_for(device: str) -> torch.dtype:
    d = device.lower()
    if d.startswith("cpu"):
        return torch.float32
    if d.startswith("cuda") and torch.cuda.is_available():
        if getattr(torch.cuda, "is_bf16_supported", lambda: False)():
            return torch.bfloat16
        return torch.float16
    return torch.float32


def _register_sdnq() -> tuple[Any, Any]:
    """Importa SDNQ e devolve (triton_is_available, apply_sdnq_options_to_model)."""
    try:
        from sdnq import SDNQConfig  # noqa: F401 — registo diffusers/transformers
        from sdnq.common import use_torch_compile as triton_is_available
        from sdnq.loader import apply_sdnq_options_to_model

        return triton_is_available, apply_sdnq_options_to_model
    except ImportError as e:
        raise ImportError("O pacote 'sdnq' é necessário para o modelo SDNQ. Instale com: pip install sdnq") from e


def _maybe_apply_quantized_matmul(pipe: Any, triton_is_available: Any) -> None:
    """Apply SDNQ quantized matmul to pipeline sub-modules (via shared helper)."""
    from gamedev_shared.sdnq import apply_quantized_matmul

    apply_quantized_matmul(pipe, enabled=bool(triton_is_available))


class KleinFluxGenerator:
    """Carrega Flux2KleinPipeline com pesos SDNQ (Disty0)."""

    def __init__(
        self,
        device: str | None = None,
        low_vram: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        quant_preset: str | None = None,
    ):
        self.verbose = verbose
        self.low_vram = low_vram
        self.model_id = model_id or _model_id(low_vram=self.low_vram)
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids
        # Preset SDNQ explícito (override); None = o planner decide por VRAM em runtime.
        self.quant_preset = quant_preset

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
        self._plan: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu: bool = False

        if self.verbose:
            _logger.info(f"device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    def set_status_callback(self, fn: Callable[[str], None] | None) -> None:
        """Callback opcional (ex. Rich) para mensagens de fase durante o load."""
        self._on_status = fn

    def _log(self, msg: str) -> None:
        if self.verbose:
            _logger.info(msg)

    def _clear_cache(self) -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
        else:
            _logger.step(msg)

    def warmup(self) -> None:
        """Carrega o pipeline (download HF + pesos). Idempotente."""
        self._load_pipeline()

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        # Allocator com expandable_segments ANTES do 1º alloc CUDA — reduz fragmentação
        # (decisiva no limite de 6GB) e evita OOM por memória reservada-mas-não-alocada.
        from gamedev_shared.quantization import set_memory_optimization_env

        set_memory_optimization_env()

        triton_is_available, _ = _register_sdnq()

        from diffusers import Flux2KleinPipeline

        plan = self._resolve_plan()
        self._plan = plan

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        # Preset SDNQ a aplicar em runtime (post-load), decidido por VRAM. O pipeline
        # diffusers não aceita SDNQConfig no from_pretrained (precisaria de um backend
        # registado); a quantização do modelo base faz-se depois do load, layer-by-layer.
        preset = self._resolve_preset(plan)
        qlabel = preset or "fp16 (sem quant)"

        self._preflight_download()

        self._status(f"Passo 1/4 — from_pretrained base em CPU (quant={qlabel}; 1ª vez baixa vários GB)")
        self._log(f"Carregando base {self.model_id} (quant={qlabel})...")
        pipe = Flux2KleinPipeline.from_pretrained(self.model_id, **kwargs)

        if preset:
            self._status(f"Passo 2/4 — quantização SDNQ {preset} em runtime (layer-by-layer)")
            self._runtime_quantize(pipe, preset)

        self._status("Passo 3/4 — SDNQ (matmul quantizado opcional via Triton)")
        _maybe_apply_quantized_matmul(pipe, triton_is_available)

        self._status(f"Passo 4/4 — colocação: {plan.summary()}")
        self._clear_cache()

        if torch.cuda.is_available() and self.device == "cuda":
            for gid in self.gpu_ids or range(torch.cuda.device_count()):
                if gid < torch.cuda.device_count():
                    torch.cuda.reset_peak_memory_stats(gid)

        did_offload = False
        if self.device == "cpu":
            pipe.to("cpu")
        elif plan.multi_gpu_ids and self._try_multi_gpu(pipe):
            self._status("Modelo carregado — split multi-GPU (accelerate)")
        elif plan.low_vram or self.low_vram:
            self._apply_offload(pipe, plan, forced=self.low_vram)
            did_offload = True
        else:
            # Full-GPU é a estimativa do planner; se a colocação real estourar a VRAM
            # (footprint otimista), cai para CPU offload — rede de segurança always-fit.
            try:
                pipe.to(self.device)
            except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
                self._log(f"pipe.to({self.device}) OOM ({exc}); fallback para CPU offload")
                self._clear_cache()
                self._apply_offload(pipe, plan, forced=True)
                did_offload = True

        if torch.cuda.is_available() and self.device == "cuda" and not did_offload:
            if self._multi_gpu:
                gpu_ids = self.gpu_ids or list(range(torch.cuda.device_count()))
                parts = []
                for gid in gpu_ids:
                    torch.cuda.synchronize(gid)
                    alloc = torch.cuda.memory_allocated(gid) / (1024**3)
                    peak = torch.cuda.max_memory_allocated(gid) / (1024**3)
                    parts.append(f"cuda:{gid} ~{alloc:.2f} GB (pico ~{peak:.2f} GB)")
                self._status("Modelo carregado — " + ", ".join(parts))
            else:
                torch.cuda.synchronize()
                alloc = torch.cuda.memory_allocated(0) / (1024**3)
                peak = torch.cuda.max_memory_allocated(0) / (1024**3)
                self._status(f"Modelo carregado — VRAM ~{alloc:.2f} GB (pico após load ~{peak:.2f} GB)")

        self._pipe = pipe
        return pipe

    def _preflight_download(self) -> None:
        """Garante o checkpoint em disco antes do load (download com resume/progresso).

        Não-bloqueante: se o preflight falhar (offline mas em cache, ou hub indisponível),
        deixa o ``from_pretrained`` tentar/cair como antes.
        """
        try:
            from gamedev_shared.model_download import ensure_model

            ensure_model(self.model_id, cache_dir=self.cache_dir, on_status=self._status)
        except Exception as exc:
            self._log(f"preflight download falhou ({exc}); a deixar from_pretrained tratar")

    def _resolve_plan(self) -> Any:
        """Resolve o :class:`OffloadPlan` por VRAM (quant + offload) para o modelo base.

        Respeita ``gpu_ids`` (restringe às GPUs escolhidas) e o device. Puro quanto à
        decisão; só lê specs de GPU.
        """
        from gamedev_shared.hardware import cuda_gpu_specs
        from gamedev_shared.lowvram import plan_offload

        specs = [] if self.device == "cpu" else cuda_gpu_specs()
        if self.gpu_ids:
            keep = set(self.gpu_ids)
            specs = [(i, m) for i, m in specs if i in keep]
        allow_multi = self.gpu_ids is None or len(self.gpu_ids) >= 2
        return plan_offload(specs, model_footprint(self.model_id), allow_multi_gpu=allow_multi)

    def _resolve_preset(self, plan: Any) -> str | None:
        """Preset SDNQ a aplicar (``quant_preset`` explícito ganha; senão o do plano).

        Devolve ``None`` quando não há quant (``none``) ou o SDNQ não está disponível.
        """
        preset = self.quant_preset or (plan.quant_mode if plan.quant_mode != "none" else None)
        if not preset or preset == "none":
            return None
        from gamedev_shared.sdnq import is_available

        if not is_available():
            self._log("SDNQ indisponível — base em fp16 (sem quantização)")
            return None
        return preset

    def _runtime_quantize(self, pipe: Any, preset: str) -> None:
        """Quantiza os componentes pesados do pipeline em runtime (SDNQ post-load).

        Carregado em CPU, cada componente é quantizado layer-by-layer (peso baixo na GPU)
        e devolvido já quantizado — evita o pico fp16 do modelo inteiro na VRAM.
        """
        from gamedev_shared.sdnq import quantize_model

        # Quantizar na GPU (rápido) mas devolver os pesos int4 à CPU: a colocação
        # final (pipe.to(cuda) ou offload) move-os depois, evitando o pico fp16+int4
        # simultâneo na VRAM que estoura os 6GB.
        quant_device = "cuda" if self.device == "cuda" and torch.cuda.is_available() else "cpu"
        for attr in ("transformer", "text_encoder", "text_encoder_2"):
            mod = getattr(pipe, attr, None)
            if mod is None:
                continue
            try:
                setattr(
                    pipe,
                    attr,
                    quantize_model(mod, preset, quantization_device=quant_device, return_device="cpu"),
                )
                self._log(f"SDNQ {preset} aplicado a {attr}")
            except Exception as exc:
                self._log(f"quantização de {attr} falhou ({exc}); componente fica em fp16")
        self._clear_cache()

    def _apply_offload(self, pipe: Any, plan: Any, *, forced: bool) -> None:
        """Aplica offload + VAE/attn slicing via planner partilhado.

        Se ``forced`` (utilizador pediu ``--low-vram``) e o plano não traz offload,
        promove a ``model_cpu`` para honrar a intenção. O planner pode já trazer
        ``sequential`` quando nem ``model_cpu`` cabe.
        """
        from dataclasses import replace

        from gamedev_shared.lowvram import OFFLOAD_MODEL, OFFLOAD_NONE, apply_offload_plan

        if forced and plan.offload == OFFLOAD_NONE:
            plan = replace(plan, offload=OFFLOAD_MODEL, vae_slicing=True, vae_tiling=True, attention_slicing=True)
        apply_offload_plan(pipe, plan, device=self.device if self.device != "cuda" else None)
        self._log(f"offload: {plan.summary()}")

    def _try_multi_gpu(self, pipe: Any) -> bool:
        if not torch.cuda.is_available() or torch.cuda.device_count() < 2:
            return False

        gpu_ids = self.gpu_ids
        if not gpu_ids or len(gpu_ids) < 2:
            gpu_ids = list(range(torch.cuda.device_count()))

        primary, secondary = gpu_ids[0], gpu_ids[1]

        try:
            pipe.transformer.to(f"cuda:{primary}")
            pipe.vae.to(f"cuda:{primary}")
            pipe.text_encoder.to(f"cuda:{secondary}")
        except (torch.cuda.OutOfMemoryError, RuntimeError) as exc:
            self._log(f"Multi-GPU placement falhou ({exc})")
            return False

        self._patch_cross_device(pipe, primary, secondary)

        for gid in gpu_ids:
            alloc = torch.cuda.memory_allocated(gid) / (1024**3)
            self._log(f"  cuda:{gid} — {alloc:.2f} GB alocados")

        self._multi_gpu = True
        return True

    def _patch_cross_device(self, pipe: Any, primary: int, secondary: int) -> None:
        primary_dev = f"cuda:{primary}"
        secondary_dev = f"cuda:{secondary}"

        pipe._text2d_primary_device = torch.device(primary_dev)

        if not hasattr(pipe, "_orig_encode_prompt"):
            pipe._orig_encode_prompt = pipe.encode_prompt

        def _patched_encode_prompt(*args: Any, **kwargs: Any) -> Any:
            kwargs["device"] = secondary_dev
            result = pipe._orig_encode_prompt(*args, **kwargs)
            if isinstance(result, torch.Tensor):
                return result.to(primary_dev)
            if isinstance(result, (tuple, list)):
                return type(result)(r.to(primary_dev) if isinstance(r, torch.Tensor) else r for r in result)
            return result

        pipe.encode_prompt = _patched_encode_prompt

        @property  # type: ignore[misc]
        def _patched_execution_device(self: Any) -> torch.device:
            return self._text2d_primary_device

        pipe.__class__._execution_device = _patched_execution_device

    def unload(self) -> None:
        if self._pipe is None:
            return
        del self._pipe
        self._pipe = None
        self._clear_cache()

    def generate(
        self,
        prompt: str,
        height: int = 1024,
        width: int = 1024,
        guidance_scale: float = 1.0,
        num_inference_steps: int = 4,
        seed: int | None = None,
    ) -> Image.Image:
        pipe = self._load_pipeline()

        if self._multi_gpu and self.gpu_ids:
            gen_device = f"cuda:{self.gpu_ids[0]}"
        elif self.device != "cpu":
            gen_device = "cuda"
        else:
            gen_device = "cpu"
        generator = torch.Generator(device=gen_device)
        if seed is not None:
            generator.manual_seed(seed)

        self._clear_cache()

        self._log("Inferência...")
        out = pipe(
            prompt=prompt,
            height=height,
            width=width,
            guidance_scale=guidance_scale,
            num_inference_steps=num_inference_steps,
            generator=generator,
        )
        return out.images[0]

    @staticmethod
    def save_image(image: Image.Image, path: Path, image_format: str | None = None) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format=image_format)
        return path
