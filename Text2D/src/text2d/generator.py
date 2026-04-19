"""
Text2D — Geração de imagens com FLUX.2 Klein (SDNQ / Disty0).

Default: 9B SDNQ (high-VRAM), 4B SDNQ (--low-vram).
Requer `sdnq` instalado para registar quantização no diffusers/transformers.
"""

from __future__ import annotations

import gc
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import torch
from PIL import Image

HIGH_VRAM_MODEL_ID = "Disty0/FLUX.2-klein-9B-SDNQ-4bit-dynamic-svd-r32"
LOW_VRAM_MODEL_ID = "Disty0/FLUX.2-klein-4B-SDNQ-4bit-dynamic"


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
    ):
        self.verbose = verbose
        self.low_vram = low_vram
        self.model_id = model_id or _model_id(low_vram=self.low_vram)
        self.cache_dir = cache_dir

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None

        if self.verbose:
            print(f"[Text2D] device={self.device} dtype={self.torch_dtype} model={self.model_id}")

    def set_status_callback(self, fn: Callable[[str], None] | None) -> None:
        """Callback opcional (ex. Rich) para mensagens de fase durante o load."""
        self._on_status = fn

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[Text2D] {msg}")

    def _clear_cache(self) -> None:
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    def _status(self, msg: str) -> None:
        if self._on_status:
            self._on_status(msg)
        else:
            print(f"[Text2D] {msg}", file=sys.stderr, flush=True)

    def warmup(self) -> None:
        """Carrega o pipeline (download HF + pesos). Idempotente."""
        self._load_pipeline()

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        triton_is_available, _ = _register_sdnq()

        from diffusers import Flux2KleinPipeline

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._status("Passo 1/3 — from_pretrained (rede/disco na 1ª vez: vários GB; GPU pode ficar em ~0% — normal)")
        self._log(f"Carregando {self.model_id}...")
        pipe = Flux2KleinPipeline.from_pretrained(self.model_id, **kwargs)

        self._status("Passo 2/3 — SDNQ (matmul quantizado opcional via Triton)")
        _maybe_apply_quantized_matmul(pipe, triton_is_available)

        if self.device == "cpu":
            mode_label = "cpu"
        elif self.low_vram:
            mode_label = f"{self.device} (cpu_offload — módulos migram 1 a 1)"
        else:
            mode_label = self.device
        self._status(f"Passo 3/3 — a mover o pipeline para {mode_label}")

        self._clear_cache()

        if torch.cuda.is_available() and self.device == "cuda":
            torch.cuda.reset_peak_memory_stats(0)

        if self.device == "cpu":
            pipe.to("cpu")
        elif self.low_vram:
            pipe.enable_model_cpu_offload()
        else:
            pipe.to(self.device)

        if torch.cuda.is_available() and self.device == "cuda" and not self.low_vram:
            torch.cuda.synchronize()
            alloc = torch.cuda.memory_allocated(0) / (1024**3)
            peak = torch.cuda.max_memory_allocated(0) / (1024**3)
            self._status(f"Modelo carregado — VRAM ~{alloc:.2f} GB (pico após load ~{peak:.2f} GB)")

        self._pipe = pipe
        return pipe

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

        gen_device = self.device if self.device != "cpu" else "cpu"
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
