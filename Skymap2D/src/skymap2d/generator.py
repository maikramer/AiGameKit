"""Skymap2D — gerador de skymaps equirectangular 360° via HF Inference API."""

from __future__ import annotations

import io
import logging
import os
import re
from collections.abc import Generator
from typing import Any

from PIL import Image

from gamedev_shared.hf import get_hf_token

from .presets import get_preset_params, get_preset_prompt
from .utils import generate_seed, validate_params, validate_prompt

logger = logging.getLogger(__name__)

DEFAULT_MODEL_ID = "MultiTrickFox/Flux-LoRA-Equirectangular-v3"

BASE_EQUIRECTANGULAR_INSTRUCTIONS = (
    "equirectangular 360 degree panorama, hdri environment map, "
    "full spherical view, no visible seams at edges, "
    "no borders, no frame, no text, no watermark"
)

DEFAULT_PARAMS: dict[str, Any] = {
    "guidance_scale": 6.0,
    "num_inference_steps": 40,
    "seed": None,
    "width": 2048,
    "height": 1024,
    "cfg_scale": 6.0,
    "negative_prompt": "",
    "lora_strength": 1.0,
}


def default_model_id() -> str:
    return os.environ.get("SKYMAP2D_MODEL_ID", DEFAULT_MODEL_ID)


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
    logger.info("Equirect latitude shift aplicado (nadir ao centro → nadir no fundo).")
    return corrected


class SkymapGenerator:
    """Gerador de skymaps equirectangular 360° via HF Inference API."""

    def __init__(self, model_id: str | None = None) -> None:
        self.model_id = self._normalize_model_id(model_id or default_model_id())
        self.client = self._init_client()

    @staticmethod
    def _normalize_model_id(model_id: str) -> str:
        if model_id.startswith("models/"):
            return model_id[len("models/") :]
        return model_id

    def _init_client(self):
        from huggingface_hub import InferenceClient

        token = get_hf_token()
        client = InferenceClient(token=token)
        logger.info(f"InferenceClient inicializado — modelo: {self.model_id}")
        return client

    def generate(
        self,
        prompt: str,
        negative_prompt: str = "",
        guidance_scale: float = 6.0,
        num_inference_steps: int = 40,
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
        if not self.client:
            raise RuntimeError("Inference client não inicializado")

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
            logger.warning(
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

        hf_params: dict[str, Any] = {
            "negative_prompt": negative_prompt,
            "guidance_scale": float(guidance_scale),
            "num_inference_steps": int(num_inference_steps),
            "width": int(width),
            "height": int(height),
            "seed": int(seed),
            "cfg_scale": float(cfg_scale),
            "lora_scale": float(lora_strength),
        }

        image: Image.Image | None = None

        try:
            image = self.client.text_to_image(
                prompt=prompt,
                model=self.model_id,
                **hf_params,
            )
        except TypeError:
            image = self.client.text_to_image(
                prompt=prompt,
                model=self.model_id,
            )
        except Exception as e:
            logger.warning(f"text_to_image falhou ({e}); fallback para POST raw.")
            payload = {"inputs": prompt, "parameters": hf_params}
            raw = self.client.post(model=self.model_id, json=payload)
            image = Image.open(io.BytesIO(raw)).convert("RGB")

        if image is None:
            raise RuntimeError("Nenhuma imagem devolvida pela HF Inference API")

        image = image.convert("RGB")

        iw, ih = image.size
        if (iw, ih) != (width, height):
            logger.warning(
                f"HF API devolveu {iw}x{ih} em vez de {width}x{height}; "
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
        logger.info(f"Batch: {total} skymaps")

        for idx, prompt in enumerate(prompts):
            try:
                merged = {**DEFAULT_PARAMS, **base_params}
                merged.pop("seed", None)
                merged.pop("prompt", None)

                image, metadata = self.generate(prompt=prompt, **merged)
                yield image, metadata, idx
            except Exception as e:
                logger.error(f"Erro no skymap {idx + 1}/{total}: {e}")
                yield None, {"error": str(e), "index": idx}, idx
