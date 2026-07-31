"""Adapter skymap2d para o modo subprocesso (``skymap2d serve --ums-worker``).

Herdam de :class:`gamedev_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.skymap2d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted
from gamedev_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do skymap2d (SkymapGenerator)."""

    name = "skymap2d"

    def load(self, **kwargs: Any) -> Any:
        from skymap2d.generator import SkymapGenerator

        # UMS: compile on (bench 6GB: ~-19% hot; cold ~6 min amortizado).
        # channels_last ~0 no skymap - nao forcar.
        # memory_efficient (cpu-offload): só do request (CLI hw_auto) — sem re-decidir.
        load_kwargs: dict[str, Any] = {
            "verbose": kwargs.get("verbose", False),
            "torch_compile": kwargs.get("torch_compile", True),
            "torch_compile_mode": kwargs.get("torch_compile_mode", "default"),
        }
        skip = {"verbose", "memory_efficient", "torch_compile", "torch_compile_mode", "sdnq_preset"}
        load_kwargs.update({k: v for k, v in kwargs.items() if k not in skip})
        if "memory_efficient" in kwargs:
            load_kwargs["memory_efficient"] = bool(kwargs["memory_efficient"])
        gen = SkymapGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=28)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
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
        return self.finish_response(output=saved, seconds=elapsed, seed=metadata.get("seed"))

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
