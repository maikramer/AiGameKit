"""Adapter do Texture2D — Stable Diffusion v1.5 + circular padding para texturas 2D.

Normaliza a API do ``TextureGenerator`` (warmup/generate/unload) no contrato
canónico. Retorna tuple (Image, metadata); o adapter trata o save.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do texture2d (TextureGenerator — SD1.5 + circular padding)."""

    name = "texture2d"

    def load(self, **kwargs: Any) -> Any:
        from texture2d.generator import TextureGenerator

        # SD1.5 fp16 (~2.5 GB) cabe em qualquer GPU CUDA — sem offload/clamp.
        load_kwargs: dict[str, Any] = {"verbose": kwargs.get("verbose", False)}
        skip = {"verbose", "group_offload", "sequential_offload", "memory_efficient"}
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        gen = TextureGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        from texture2d.generator import DEFAULT_GUIDANCE, DEFAULT_RESOLUTION, DEFAULT_STEPS

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}

        t_start = time.perf_counter()
        image, metadata = model.generate(
            prompt=prompt,
            negative_prompt=request.get("negative_prompt", ""),
            guidance_scale=float(request.get("guidance", DEFAULT_GUIDANCE)),
            num_inference_steps=int(request.get("steps", DEFAULT_STEPS)),
            seed=request.get("seed"),
            width=int(request.get("width", DEFAULT_RESOLUTION)),
            height=int(request.get("height", DEFAULT_RESOLUTION)),
            preset=request.get("preset"),
            ground=request.get("ground", "auto"),
        )

        from texture2d.image_processor import save_image

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
