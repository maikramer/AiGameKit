"""Adapter do Text2Icon — Sana Sprint (NVlabs/Sana) para ícones transparentes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text2icon (SanaIconGenerator)."""

    name = "text2icon"

    def load(self, **kwargs: Any) -> Any:
        from text2icon.generator import SanaIconGenerator

        # UMS: channels_last on (bench 6GB: ~-13% hot). Compile piora hot — off.
        load_kwargs: dict[str, Any] = {
            "verbose": kwargs.get("verbose", False),
            "channels_last": kwargs.get("channels_last", True),
            "torch_compile": kwargs.get("torch_compile", False),
        }
        if self.should_use_low_vram_mode():
            load_kwargs["low_vram"] = kwargs.get("low_vram", True)
        skip = {"verbose", "low_vram", "channels_last", "torch_compile"}
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        gen = SanaIconGenerator(**load_kwargs)
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

        steps = int(request.get("steps", 20))
        should_abort, on_step = self.abort_hooks(request, num_inference_steps=steps)
        self.report_progress(request, 0.0, "started")

        t_start = time.perf_counter()
        try:
            image, metadata = model.generate(
                prompt=prompt,
                negative_prompt=request.get("negative_prompt", ""),
                guidance_scale=float(request.get("guidance", 4.5)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                width=int(request.get("width", 512)),
                height=int(request.get("height", 512)),
                remove_background=bool(request.get("transparent", False)),
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from text2icon.image_processor import save_image

        out_path = Path(output)
        self.report_progress(request, 0.95, "saving")
        saved = save_image(
            image,
            prompt=metadata.get("prompt_final", prompt),
            params=metadata,
            output_dir=out_path.parent,
            filename=out_path.name,
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
