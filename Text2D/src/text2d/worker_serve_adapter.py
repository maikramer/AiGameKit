"""Adapter text2d para o modo subprocesso (``text2d serve --ums-worker``).

Herdam de :class:`gamedev_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.text2d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted
from gamedev_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do text2d (KleinFluxGenerator)."""

    name = "text2d"

    def load(self, **kwargs: Any) -> Any:
        from text2d.generator import KleinFluxGenerator
        from text2d.ums_load import map_ums_load_kwargs

        # Peak/offload: só do request (CLI hw_auto / with_ums_peak_opts).
        load_kwargs = map_ums_load_kwargs(kwargs)
        gen = KleinFluxGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        error, steps, should_abort, on_step = self.begin_generate(request, default_steps=4)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")

        # Observabilidade: shape da geração (admit já usou quant; aqui diagnóstico).
        runtime_budget = {
            "width": int(request.get("width", 1024)),
            "height": int(request.get("height", 1024)),
            "steps": steps,
            "memory_efficient": bool(getattr(model, "memory_efficient", False)),
            "quant_preset": getattr(model, "quant_preset", None),
            "model_id": getattr(model, "model_id", None),
        }

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
        return self.finish_response(output=saved, seconds=elapsed, runtime_budget=runtime_budget)

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
