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

_logger = Logger("text2d")

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
        gpu_ids: list[int] | None = None,
    ):
        self.verbose = verbose
        self.low_vram = low_vram
        self.model_id = model_id or _model_id(low_vram=self.low_vram)
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self.torch_dtype = _torch_dtype_for(self.device)
        self._pipe: Any = None
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
            for gid in self.gpu_ids or range(torch.cuda.device_count()):
                if gid < torch.cuda.device_count():
                    torch.cuda.reset_peak_memory_stats(gid)

        if self.device == "cpu":
            pipe.to("cpu")
        elif self.low_vram:
            pipe.enable_model_cpu_offload()
        elif self._try_multi_gpu(pipe):
            self._status("Modelo carregado — split multi-GPU (accelerate)")
        else:
            pipe.to(self.device)

        if torch.cuda.is_available() and self.device == "cuda" and not self.low_vram:
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
