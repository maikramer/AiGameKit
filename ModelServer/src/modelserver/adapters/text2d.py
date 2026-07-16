"""Adapter do Text2D — FLUX.2 Klein (SDNQ) para text-to-image."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2d (KleinFluxGenerator)."""

    name = "text2d"

    def load(self, **kwargs: Any) -> Any:
        from text2d.generator import KleinFluxGenerator

        # UMS: modelo fica quente - amortiza cold do torch.compile + channels_last
        # (bench 6GB: ~-10% hot). Explicit kwargs / preload request ganham.
        load_kwargs: dict[str, Any] = {
            "verbose": kwargs.get("verbose", False),
            "torch_compile": kwargs.get("torch_compile", True),
            "torch_compile_mode": kwargs.get("torch_compile_mode", "default"),
            "channels_last": kwargs.get("channels_last", True),
        }
        if self.should_use_low_vram_mode():
            load_kwargs["memory_efficient"] = kwargs.get("memory_efficient", True)
        skip = {"verbose", "memory_efficient", "torch_compile", "torch_compile_mode", "channels_last"}
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        gen = KleinFluxGenerator(**load_kwargs)
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

        steps = int(request.get("steps", 4))
        should_abort, on_step = self.abort_hooks(request, num_inference_steps=steps)
        self.report_progress(request, 0.0, "started")

        t_start = time.perf_counter()
        try:
            image, _metadata = model.generate(
                prompt=prompt,
                height=int(request.get("height", 1024)),
                width=int(request.get("width", 1024)),
                guidance_scale=float(request.get("guidance", 1.0)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2d.generator import KleinFluxGenerator

        out_path = Path(output)
        ext = out_path.suffix.lower().lstrip(".")
        img_format = "JPEG" if ext in ("jpg", "jpeg") else "PNG"
        self.report_progress(request, 0.95, "saving")
        saved = KleinFluxGenerator.save_image(image, out_path, image_format=img_format)

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
