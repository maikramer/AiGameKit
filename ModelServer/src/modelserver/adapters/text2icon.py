"""Adapter do Text2Icon — Sana Sprint (NVlabs/Sana) para ícones transparentes.

Normaliza a API do ``SanaIconGenerator`` (warmup/generate/unload) no contrato
canónico do UMS. O model object retornado por ``load`` é o ``SanaIconGenerator``
já aquecido.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2icon (SanaIconGenerator)."""

    name = "text2icon"

    def load(self, **kwargs: Any) -> Any:
        from text2icon.generator import SanaIconGenerator

        load_kwargs: dict[str, Any] = {"verbose": kwargs.get("verbose", False)}
        if self.should_use_low_vram_mode():
            load_kwargs["low_vram"] = kwargs.get("low_vram", True)
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in ("verbose", "low_vram")})
        gen = SanaIconGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}

        t_start = time.perf_counter()
        image, metadata = model.generate(
            prompt=prompt,
            negative_prompt=request.get("negative_prompt", ""),
            guidance_scale=float(request.get("guidance", 4.5)),
            num_inference_steps=int(request.get("steps", 20)),
            seed=request.get("seed"),
            width=int(request.get("width", 512)),
            height=int(request.get("height", 512)),
            remove_background=bool(request.get("transparent", False)),
        )

        from text2icon.image_processor import save_image

        out_path = Path(output)
        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompt),
            params=metadata,
            output_dir=out_path.parent,
            filename=out_path.name,
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
