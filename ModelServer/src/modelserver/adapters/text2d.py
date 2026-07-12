"""Adapter do Text2D — FLUX.2 Klein (SDNQ) para text-to-image.

O ``KleinFluxGenerator`` retorna uma ``PIL.Image`` nua (não tuple). Default
guidance=1.0, steps=4 (klein é few-step).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2d (KleinFluxGenerator)."""

    name = "text2d"

    def load(self, **kwargs: Any) -> Any:
        from text2d.generator import KleinFluxGenerator

        gen = KleinFluxGenerator(verbose=kwargs.get("verbose", False), **kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}

        t_start = time.perf_counter()
        image = model.generate(
            prompt=prompt,
            height=int(request.get("height", 1024)),
            width=int(request.get("width", 1024)),
            guidance_scale=float(request.get("guidance", 1.0)),
            num_inference_steps=int(request.get("steps", 4)),
            seed=request.get("seed"),
        )

        from text2d.generator import KleinFluxGenerator

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        img_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
        saved = KleinFluxGenerator.save_image(image, out_path, image_format=img_format)

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
