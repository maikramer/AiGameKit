"""Skymap2D — gerador de skymaps equirectangular 360° com FLUX.1-dev + LoRA local.

Herda infraestrutura de ``DiffusionGeneratorBase`` (lifecycle, logging, cache,
multi-GPU, save_image, batch generation). Mantém apenas a lógica única:
``_load_pipeline`` (FLUX base + equirect LoRA), ``generate`` (cfg_scale,
lora_strength, preset, correção de latitude equirectangular), e o preflight
download.
"""

from __future__ import annotations

import os
import re
from typing import Any

from PIL import Image

from gamedev_shared.base_generator import DiffusionGeneratorBase

# Re-export para backward compat (testes antigos podem importar de skymap2d.generator).
from gamedev_shared.base_generator import torch_dtype_for as _torch_dtype_for  # noqa: F401
from gamedev_shared.logging import Logger

from .presets import get_preset_params, get_preset_prompt
from .utils import validate_params, validate_prompt

_logger = Logger()

DEFAULT_LORA_MODEL_ID = "MultiTrickFox/Flux-LoRA-Equirectangular-v3"
DEFAULT_BASE_MODEL_ID = "Disty0/FLUX.1-dev-SDNQ-uint4-svd-r32"
DEFAULT_MODEL_ID = DEFAULT_LORA_MODEL_ID


def _flux_dev_uint4_footprint() -> Any:
    """Pegada do FLUX.1-dev pré-quantizado uint4 — consulta o registry centralizado.

    Como o checkpoint já vem uint4 do hub, usamos ``allow_quant=("none",)`` na
    chamada ao planner para evitar quantização extra.
    """
    from gamedev_shared.lowvram import get_footprint

    return get_footprint("flux-dev-uint4")


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


class SkymapGenerator(DiffusionGeneratorBase):
    """Gerador de skymaps equirectangular 360° com FLUX.1-dev + LoRA local.

    Herda de ``DiffusionGeneratorBase``: warmup, unload, _log, _clear_cache,
    _status, _place_with_planner, save_image, generate_batch.
    """

    def __init__(
        self,
        device: str | None = None,
        memory_efficient: bool = False,
        verbose: bool = False,
        model_id: str | None = None,
        cache_dir: str | None = None,
        gpu_ids: list[int] | None = None,
    ) -> None:
        super().__init__(
            device=device,
            verbose=verbose,
            model_id=model_id or default_model_id(),
            cache_dir=cache_dir,
            gpu_ids=gpu_ids,
            memory_efficient=memory_efficient,
        )
        # Modelo base FLUX.1-dev (distinto do LoRA equirectangular em ``self.model_id``).
        self.base_model_id = default_base_model_id()

        if self.verbose:
            _logger.info(f"device={self.device} base_model={self.base_model_id} lora={self.model_id}")

    def _preflight_download(self) -> None:
        """Garante o base SDNQ + a LoRA equirectangular em disco antes do load.

        Download com resume/progresso, não-bloqueante: se falhar (offline mas em cache,
        ou hub indisponível), deixa o ``from_pretrained``/``load_lora_weights`` tratar.
        """
        try:
            from gamedev_shared.model_download import ensure_model

            ensure_model(self.base_model_id, cache_dir=self.cache_dir, on_status=self._status)
            ensure_model(self.model_id, cache_dir=self.cache_dir, on_status=self._status)
        except Exception as exc:
            self._log(f"preflight download falhou ({exc}); a deixar from_pretrained tratar")

    def _load_pipeline(self) -> Any:
        if self._pipe is not None:
            return self._pipe

        os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "0")

        from gamedev_shared.sdnq import apply_quantized_matmul, register_sdnq

        triton_is_available = register_sdnq(patch_lora=True)

        from diffusers import FluxPipeline

        kwargs: dict[str, Any] = {
            "torch_dtype": self.torch_dtype,
        }
        if self.cache_dir:
            kwargs["cache_dir"] = self.cache_dir

        self._preflight_download()

        self._status("Passo 1/4 — from_pretrained (SDNQ uint4, ~7 GB)")
        self._log(f"Carregando base {self.base_model_id}...")
        pipe = FluxPipeline.from_pretrained(self.base_model_id, **kwargs)

        self._status("Passo 2/4 — SDNQ quantized matmul")
        apply_quantized_matmul(pipe, enabled=bool(triton_is_available))

        self._status("Passo 3/4 — Carregando LoRA equirectangular...")
        self._log(f"Carregando LoRA {self.model_id}...")
        pipe.load_lora_weights(self.model_id)

        self._status("Passo 4/4 — Configurando dispositivo...")
        self._clear_cache()
        self._reset_peak_mem_stats()

        # Colocação unificada via planner: full-GPU / group+stream / multi-GPU / CPU.
        # O base é FLUX.1-dev pré-quantizado uint4 (~7 GiB pesos); allow_quant=("none",)
        # porque o checkpoint já vem quantizado do hub (não há SDNQ runtime aqui).
        # model_attr="transformer" para multi-GPU accelerate (FLUX split transformer).
        self._place_with_planner(pipe, _flux_dev_uint4_footprint(), allow_quant=("none",), model_attr="transformer")

        self._status("Modelo carregado — pronto")

        self._pipe = pipe
        return pipe

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

        resolved_seed = self._resolve_seed(seed)

        ratio = width / height if height > 0 else 0
        if abs(ratio - 2.0) > 0.1:
            _logger.warn(
                f"Aspect ratio {width}x{height} ({ratio:.2f}:1) não é 2:1. "
                "O modelo Flux-LoRA-Equirectangular funciona melhor com ratio 2:1 "
                "(ex: 2048x1024, 1408x704)."
            )

        params = {
            "prompt": prompt,
            "negative_prompt": negative_prompt,
            "guidance_scale": guidance_scale,
            "num_inference_steps": num_inference_steps,
            "seed": resolved_seed,
            "width": width,
            "height": height,
            "cfg_scale": cfg_scale,
            "lora_strength": lora_strength,
        }

        is_valid, error = validate_params(params)
        if not is_valid:
            raise ValueError(f"Parâmetros inválidos: {error}")

        generator = self._build_generator(resolved_seed)

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
            _logger.warn(
                f"Pipeline devolveu {iw}x{ih} em vez de {width}x{height}; "
                "a redimensionar para o tamanho pedido (equirect 2:1)."
            )
            image = image.resize((width, height), Image.Resampling.LANCZOS)

        image = _fix_equirect_latitude(image)

        metadata = {
            "seed": resolved_seed,
            "prompt_final": prompt,
            **params,
        }

        return image, metadata
