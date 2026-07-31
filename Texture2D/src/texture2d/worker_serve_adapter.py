"""Adapter texture2d para o modo subprocesso (``texture2d serve --ums-worker``).

Herdam de :class:`gamedev_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.texture2d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted
from gamedev_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do texture2d (TextureGenerator — SD1.5 + circular padding)."""

    name = "texture2d"

    def load(self, **kwargs: Any) -> Any:
        from texture2d.generator import TextureGenerator

        load_kwargs: dict[str, Any] = {"verbose": kwargs.get("verbose", False)}
        skip = {"verbose", "group_offload", "sequential_offload", "memory_efficient"}
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        gen = TextureGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        from texture2d.generator import DEFAULT_GUIDANCE, DEFAULT_RESOLUTION, DEFAULT_STEPS

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=DEFAULT_STEPS)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
        t_start = time.perf_counter()
        try:
            image, metadata = model.generate(
                prompt=prompt,
                negative_prompt=request.get("negative_prompt", ""),
                guidance_scale=float(request.get("guidance", DEFAULT_GUIDANCE)),
                num_inference_steps=steps,
                seed=request.get("seed"),
                width=int(request.get("width", DEFAULT_RESOLUTION)),
                height=int(request.get("height", DEFAULT_RESOLUTION)),
                preset=request.get("preset"),
                ground=request.get("ground", "auto"),
                should_abort=should_abort,
                on_step=on_step,
            )
        except GenerationAborted:
            return self.cancelled_response("cancelled during diffusion")

        if self.should_abort(request):
            return self.cancelled_response("cancelled after diffusion")

        from texture2d.image_processor import save_image

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
        return self.finish_response(output=saved, seconds=elapsed, seed=metadata.get("seed"))

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
