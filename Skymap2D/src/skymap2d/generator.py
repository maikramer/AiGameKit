"""Skymap2D — gerador de skymaps equirectangular 360° com FLUX.1-dev + LoRA local."""

from __future__ import annotations

import gc
import os
import re
from collections.abc import Callable, Generator
from typing import Any

import torch
from PIL import Image

from gamedev_shared.logging import Logger

from .presets import get_preset_params, get_preset_prompt
from .utils import generate_seed, validate_params, validate_prompt

_logger = Logger()

DEFAULT_LORA_MODEL_ID = "MultiTrickFox/Flux-LoRA-Equirectangular-v3"
DEFAULT_BASE_MODEL_ID = "Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32"

BASE_EQUIRECTANGULAR_INSTRUCTIONS = (
    "equirectangular 360 degree panorama, hdri environment map, "
    "full spherical view, no visible seams at edges, "
    "no borders, no frame, no text, no watermark"
)

DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": 3.5,
    "num_inference_steps": 28,
    "seed": None,
    "width": 2048,
    "height": 1024,
    "cfg_scale": 3.5,
    "negative_prompt": "",
    "lora_strength": 1.0,
}


def default_model_id() -> str:
    """ID do LoRA equirectangular (env ``SKYMAP2D_MODEL_ID`` ou default)."""
    return os.environ.get("SKYMAP2D_MODEL_ID", DEFAULT_LORA_MODEL_ID)


def default_base_model_id() -> str:
    """ID do modelo base FLUX.1-dev (env ``SKYMAP2D_BASE_MODEL_ID`` ou default)."""
    return os.environ.get("SKYMAP2D_BASE_MODEL_ID", DEFAULT_BASE_MODEL_ID)


def augment_prompt_for_equirectangular(prompt: str) -> str:
    """Acrescenta instruções equirectangular/panorama automaticamente.

    Se o utilizador já menciona equirectangular/panorama/360/hdri, não duplica.
    """
    p = (prompt or "").strip()
    if not p:
        return p
    if re.search(
        r"\b(equirectangular|panorama|panoramic|360|hdri|spherical)\b",
        p,
        flags=re.IGNORECASE,
    ):
        return p
    return f"{BASE_EQUIRECTANGULAR_INSTRUCTIONS}, {p}"


def merge_negative_prompt(preset_neg: str, user_neg: str) -> str:
    """Combina negative prompt do preset com o do utilizador."""
    preset_neg = (preset_neg or "").strip()
    user_neg = (user_neg or "").strip()
    if not preset_neg:
        return user_neg
    if not user_neg:
        return preset_neg
    if preset_neg.lower() in user_neg.lower():
        return user_neg
    if user_neg.lower() in preset_neg.lower():
        return preset_neg
    return f"{preset_neg}, {user_neg}"


def _register_sdnq() -> tuple[Any, Any]:
    try:
        from sdnq import SDNQConfig  # noqa: F401
        from sdnq.common import use_torch_compile as triton_is_available
        from sdnq.loader import apply_sdnq_options_to_model

        from gamedev_shared.sdnq import patch_lora_shape_calculation

        patch_lora_shape_calculation()

        return triton_is_available, apply_sdnq_options_to_model
    except ImportError as e:
        raise ImportError("O pacote 'sdnq' é necessário. Instale com: pip install sdnq") from e


def _maybe_apply_quantized_matmul(pipe: Any, triton_is_available: Any) -> None:
    from gamedev_shared.sdnq import apply_quantized_matmul

    apply_quantized_matmul(pipe, enabled=bool(triton_is_available))


def _fix_equirect_latitude(image: Image.Image) -> Image.Image:
    """Corrige panoramas Flux-LoRA-Equirectangular que saem com o nadir ao centro vertical.

    Numa equirect standard, a fila central é o horizonte (elevação 0°), o topo é o zénite
    (+90°) e o fundo é o nadir (-90°). O modelo Flux-LoRA-Equirectangular-v3 gera com os
    polos ao centro e o horizonte nas bordas superior/inferior — equivale a um desfasamento
    de 90° em latitude. Corrigimos com um scroll vertical de metade da altura (wrap em V).
    """
    w, h = image.size
    if h < 4:
        return image

    mid = h // 2
    top = image.crop((0, 0, w, mid))
    bottom = image.crop((0, mid, w, h))
    corrected = Image.new("RGB", (w, h))
    corrected.paste(bottom, (0, 0))
    corrected.paste(top, (0, h - mid))
    _logger.info("Equirect latitude shift aplicado (nadir ao centro → nadir no fundo).")
    return corrected


class SkymapGenerator:
    """Gerador de skymaps equirectangular 360° com FLUX.1-dev + LoRA local."""

    def __init__(
        self,
        device: str | None = None,
        low_vram: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
    ) -> None:
        self.verbose = verbose
        self.low_vram = low_vram
        self.model_id = model_id or default_model_id()
        self.base_model_id = default_base_model_id()
        self.cache_dir = cache_dir
        self.gpu_ids = gpu_ids

        if device is None:
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
        else:
            self.device = device

        self._pipe: Any = None
        self._on_status: Callable[[str], None] | None = None
        self._multi_gpu: bool = False

        if self.verbose:
            _logger.info(f"device={self.device} base_model={self.base_model_id} lora={self.model_id}")

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

        from diffusers import FluxPipeline

        kwargs: dict[str, Any] = {
            "torch_dtype": torch.bfloat16,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._status("Passo 1/4 — from_pretrained (SDNQ uint4, ~7 GB)")
        self._log(f"Carregando base {self.base_model_id}...")
        pipe = FluxPipeline.from_pretrained(self.base_model_id, **kwargs)

        self._status("Passo 2/4 — SDNQ quantized matmul")
        _maybe_apply_quantized_matmul(pipe, triton_is_available)

        self._status("Passo 3/4 — Carregando LoRA equirectangular...")
        self._log(f"Carregando LoRA {self.model_id}...")
        pipe.load_lora_weights(self.model_id)

        self._status("Passo 4/4 — Configurando dispositivo...")
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
            self._status("Modelo carregado — split multi-GPU")
        else:
            pipe.to(self.device)

        self._status("Modelo carregado — pronto")
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

        pipe._skymap2d_primary_device = torch.device(primary_dev)

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
            return self._skymap2d_primary_device

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
        negative_prompt: str = "",
        guidance_scale: float = 3.5,
        num_inference_steps: int = 28,
        seed: int | None = None,
        width: int = 2048,
        height: int = 1024,
        cfg_scale: float | None = None,
        lora_strength: float = 1.0,
        preset: str | None = None,
    ) -> tuple[Image.Image, dict[str, Any]]:
        """Gera um skymap equirectangular 360°.

        Returns:
            Tuple (imagem PIL, metadata dict).
        """
        pipe = self._load_pipeline()

        if preset and preset != "None":
            preset_prompt = get_preset_prompt(preset)
            preset_params = get_preset_params(preset)
            if preset_prompt:
                prompt = f"{preset_prompt}, {prompt}" if prompt else preset_prompt
            if preset_params:
                guidance_scale = float(preset_params.get("guidance_scale", guidance_scale))
                num_inference_steps = int(preset_params.get("num_inference_steps", num_inference_steps))
                width = int(preset_params.get("width", width))
                height = int(preset_params.get("height", height))
                if "negative_prompt" in preset_params:
                    negative_prompt = merge_negative_prompt(
                        str(preset_params.get("negative_prompt") or ""),
                        negative_prompt,
                    )

        prompt = augment_prompt_for_equirectangular(prompt)

        is_valid, error = validate_prompt(prompt, max_length=1200)
        if not is_valid:
            prompt = prompt[:1200]

        if cfg_scale is None:
            cfg_scale = guidance_scale

        if seed is None or seed < 0:
            seed = generate_seed()

        ratio = width / height if height > 0 else 0
        if abs(ratio - 2.0) > 0.1:
            _logger.warning(
                f"Aspect ratio {width}x{height} ({ratio:.2f}:1) não é 2:1. "
                "O modelo Flux-LoRA-Equirectangular funciona melhor com ratio 2:1 "
                "(ex: 2048x1024, 1408x704)."
            )

        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": seed,
            "width": width,
            "height": height,
            "cfg_scale": cfg_scale,
            "lora_strength": lora_strength,
        }

        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

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
        call_kwargs: dict[str, Any] = {
            "prompt": prompt,
            "guidance_scale": float(guidance_scale),
            "num_inference_steps": int(num_inference_steps),
            "width": int(width),
            "height": int(height),
            "generator": generator,
        }
        if lora_strength != 1.0:
            call_kwargs["cross_attention_kwargs"] = {"scale": lora_strength}

        out = pipe(**call_kwargs)
        image = out.images[0]

        image = image.convert("RGB")

        iw, ih = image.size
        if (iw, ih) != (width, height):
            _logger.warning(
                f"Pipeline devolveu {iw}x{ih} em vez de {width}x{height}; "
                "a redimensionar para o tamanho pedido (equirect 2:1)."
            )
            image = image.resize((width, height), Image.Resampling.LANCZOS)

        image = _fix_equirect_latitude(image)

        metadata = {
            "seed": seed,
            "prompt_final": prompt,
            **params,
        }

        return image, metadata

    def generate_batch(
        self,
        prompts: list[str],
        base_params: dict[str, Any] | None = None,
    ) -> Generator[tuple[Image.Image | None, dict[str, Any], int], None, None]:
        """Gera múltiplos skymaps em batch.

        Yields:
            Tuple (imagem | None, metadata, index).
        """
        if base_params is None:
            base_params = {}

        total = len(prompts)
        _logger.info(f"Batch: {total} skymaps")

        for idx, prompt in enumerate(prompts):
            try:
                merged = {**DEFAULT_PARAMS, **base_params}
                merged.pop("seed", None)
                merged.pop("prompt", None)

                image, metadata = self.generate(prompt=prompt, **merged)
                yield image, metadata, idx
            except Exception as e:
                _logger.error(f"Erro no skymap {idx + 1}/{total}: {e}")
                yield None, {"error": str(e), "index": idx}, idx
