"""Adapter do Skymap2D — FLUX.1-dev SDNQ + LoRA equirectangular."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do skymap2d (SkymapGenerator)."""

    name = "skymap2d"

    def load(self, **kwargs: Any) -> Any:
        from skymap2d.generator import SkymapGenerator

        load_kwargs: dict[str, Any] = {"verbose": kwargs.get("verbose", False)}
        if self.should_use_low_vram_mode():
            load_kwargs["memory_efficient"] = kwargs.get("memory_efficient", True)
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in ("verbose", "memory_efficient")})
        gen = SkymapGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        steps = int(request.get("steps", 28))
        should_abort, on_step = self.abort_hooks(request, num_inference_steps=steps)
        self.report_progress(request, 0.0, "started")

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        image_format = "exr" if ext == "exr" else "png"
        exr_scale = float(request.get("exr_scale", 1.0))

        t_start = time.perf_counter()
        try:
            image, metadata = model.generate(
                prompt=prompt,
                negative_prompt=request.get("negative_prompt", ""),
                guidance_scale=float(request.get("guidance", 3.5)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                width=int(request.get("width", 2048)),
                height=int(request.get("height", 1024)),
                cfg_scale=request.get("cfg_scale"),
                lora_strength=float(request.get("lora_strength", 1.0)),
                preset=request.get("preset"),
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from skymap2d.image_processor import save_image

        self.report_progress(request, 0.95, "saving")
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
        self.report_progress(request, 1.0, "done")
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
