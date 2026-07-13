"""
Text2D — Geração de imagens com FLUX.2 Klein (SDNQ / Disty0).

Default: 9B SDNQ (high-VRAM), 4B SDNQ (memory-efficient mode).
Requer `sdnq` instalado para registar quantização no diffusers/transformers.

Herda infraestrutura de ``DiffusionGeneratorBase`` (lifecycle, logging, cache,
multi-GPU, save_image). Mantém apenas a lógica única: planner de offload,
quantização SDNQ runtime, e o pipeline Flux2Klein.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from PIL import Image

from gamedev_shared.base_generator import DiffusionGeneratorBase

# Re-export para backward compat (testes antigos importam de text2d.generator).
from gamedev_shared.base_generator import torch_dtype_for as _torch_dtype_for  # noqa: F401

# Modelos BASE (fp16, não pré-quantizados). A quantização (uint8/int8/int4/fp8) é
# escolhida por VRAM e aplicada em **runtime** via SDNQ — assim seguimos as melhorias
# do SDNQ upstream em vez de depender de checkpoints pré-quantizados congelados.
HIGH_VRAM_MODEL_ID = "black-forest-labs/FLUX.2-klein-9B"
LOW_VRAM_MODEL_ID = "black-forest-labs/FLUX.2-klein-4B"


def model_footprint(model_id: str) -> Any:
    """Pegada fp16 (GiB) do modelo BASE — consulta o registry centralizado.

    Pegadas calibradas com medições reais no RTX 4050 6GB: o "4B"/"9B" é só o
    transformer; o pipeline inclui um text-encoder grande (Mistral-class), por isso o
    fp16 total é bem maior. Com int4, o residente do 4B ~4.5GB → em 6GB tem de ir a
    offload (validado: pico ~4.1GiB em model_cpu offload).
    """
    from gamedev_shared.lowvram import get_footprint

    if model_id == LOW_VRAM_MODEL_ID:
        return get_footprint("flux-klein-4b")
    return get_footprint("flux-klein-9b")


def _model_id(memory_efficient: bool = False) -> str:
    if os.environ.get("TEXT2D_MODEL_ID"):
        return os.environ["TEXT2D_MODEL_ID"]
    return LOW_VRAM_MODEL_ID if memory_efficient else HIGH_VRAM_MODEL_ID


def default_model_id() -> str:
    """Modelo HF por defeito (ou `TEXT2D_MODEL_ID`)."""
    return _model_id()


class KleinFluxGenerator(DiffusionGeneratorBase):
    """Carrega Flux2KleinPipeline com pesos SDNQ (Disty0).

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _status, _try_multi_gpu, _patch_cross_device, save_image, generate_batch.
    """

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
        quant_preset: str | None = None,
    ) -> None:
        super().__init__(
            device=device,
            verbose=verbose,
            model_id=model_id or _model_id(memory_efficient=memory_efficient),
            cache_dir=cache_dir,
            gpu_ids=gpu_ids,
            memory_efficient=memory_efficient,
        )
        # Preset SDNQ explícito (override); None = o planner decide por VRAM em runtime.
        self.quant_preset = quant_preset
        self._plan: Any = None

        if self.verbose:
            from gamedev_shared.logging import Logger

            Logger().info(f"device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        from gamedev_shared.quantization import set_memory_optimization_env

        set_memory_optimization_env()

        from gamedev_shared.sdnq import apply_quantized_matmul, register_sdnq

        triton_is_available = register_sdnq()

        from diffusers import Flux2KleinPipeline

        plan = self._resolve_plan()
        self._plan = plan

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

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
        apply_quantized_matmul(pipe, enabled=bool(triton_is_available))

        self._status(f"Passo 4/4 — colocação: {plan.summary()}")
        self._clear_cache()
        self._reset_peak_mem_stats()

        # Colocação unificada: _place_with_planner resolve specs (VRAM livre), aplica
        # multi-GPU (accelerate) / group offload + streams / full-GPU conforme planner.
        self._place_with_planner(
            pipe, model_footprint(self.model_id), quant_mode=plan.quant_mode, model_attr="transformer"
        )

        self._pipe = pipe
        return pipe

    def _preflight_download(self) -> None:
        """Garante o checkpoint em disco antes do load (download com resume/progresso)."""
        from gamedev_shared.model_download import ensure_model

        try:
            ensure_model(self.model_id, cache_dir=self.cache_dir, on_status=self._status)
        except Exception as exc:
            self._log(f"preflight download falhou ({exc}); a deixar from_pretrained tratar")

    def _resolve_plan(self) -> Any:
        """Resolve o OffloadPlan por VRAM (quant + offload) para o modelo base."""
        from gamedev_shared.hardware import cuda_gpu_specs
        from gamedev_shared.lowvram import plan_offload

        specs = [] if self.device == "cpu" else cuda_gpu_specs()
        if self.gpu_ids:
            keep = set(self.gpu_ids)
            specs = [(i, m) for i, m in specs if i in keep]
        allow_multi = self.gpu_ids is None or len(self.gpu_ids) >= 2
        return plan_offload(specs, model_footprint(self.model_id), allow_multi_gpu=allow_multi)

    def _resolve_preset(self, plan: Any) -> str | None:
        """Preset SDNQ a aplicar (``quant_preset`` explícito ganha; senão o do plano)."""
        preset = self.quant_preset or (plan.quant_mode if plan.quant_mode != "none" else None)
        if not preset or preset == "none":
            return None
        from gamedev_shared.sdnq import is_available

        if not is_available():
            self._log("SDNQ indisponível — base em fp16 (sem quantização)")
            return None
        return preset

    def _runtime_quantize(self, pipe: Any, preset: str) -> None:
        """Quantiza os componentes pesados do pipeline em runtime (SDNQ post-load)."""
        from gamedev_shared.sdnq import quantize_model

        quant_device = "cuda" if self.device == "cuda" else "cpu"
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

    def generate(
        self,
        prompt: str,
        height: int = 1024,
        width: int = 1024,
        guidance_scale: float = 1.0,
        num_inference_steps: int = 4,
        seed: int | None = None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera uma imagem a partir de um prompt.

        Returns:
            Tuple ``(image, metadata)`` — alinhado com as outras tools 2D.
            Metadata inclui prompt, seed, guidance, steps, dimensions.
        """
        pipe = self._load_pipeline()
        resolved_seed = self._resolve_seed(seed)
        generator = self._build_generator(resolved_seed)

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
        image = out.images[0]
        metadata: dict[str, Any] = {
            "prompt": prompt,
            "seed": resolved_seed,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "width": width,
            "height": height,
        }
        return image, metadata

    @staticmethod
    def save_image(image: Image.Image, path: Path | str, image_format: str | None = None) -> Path:
        """Guarda a imagem num path (herda de DiffusionGeneratorBase)."""
        return DiffusionGeneratorBase.save_image(image, path, image_format)
