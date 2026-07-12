"""Adapter do Skymap2D — FLUX.1-dev SDNQ + LoRA equirectangular para skymaps 360°.

Como o Texture2D mas sem ``ground``/``seamless_fix``. Default 2048×1024 (panorama
equirect 2:1). Suporta PNG e EXR.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do skymap2d (SkymapGenerator)."""

    name = "skymap2d"

    def load(self, **kwargs: Any) -> Any:
        from skymap2d.generator import SkymapGenerator

        gen = SkymapGenerator(verbose=kwargs.get("verbose", False), **kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        image_format = "exr" if ext == "exr" else "png"
        exr_scale = float(request.get("exr_scale", 1.0))

        t_start = time.perf_counter()
        image, metadata = model.generate(
            prompt=prompt,
            negative_prompt=request.get("negative_prompt", ""),
            guidance_scale=float(request.get("guidance", 3.5)),
            num_inference_steps=int(request.get("steps", 28)),
            seed=request.get("seed"),
            width=int(request.get("width", 2048)),
            height=int(request.get("height", 1024)),
            cfg_scale=request.get("cfg_scale"),
            lora_strength=float(request.get("lora_strength", 1.0)),
            preset=request.get("preset"),
        )

        from skymap2d.image_processor import save_image

        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompt),
            params=metadata,
            output_dir=out_path.parent,
            filename=out_path.name,
            image_format=image_format,
            exr_scale=exr_scale,
        )

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
            "seed": metadata.get("seed"),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
