"""Adapter motion3d para o modo subprocesso (``motion3d serve --ums-worker``)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do motion3d (MotionGenerator)."""

    name = "motion3d"

    def load(self, **kwargs: Any) -> Any:
        from motion3d.generator import MotionGenerator

        device = kwargs.get("device")
        if device is None and kwargs.get("gpu_ids"):
            device = "cuda"
        gen = MotionGenerator.get_instance(device=device)
        if not gen.loaded:
            gen.load()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        error, _steps, _should_abort, _on_step = self.begin_generate(request, default_steps=1)
        if error:
            return error

        prompt = request.get("prompt", "")
        output = request.get("output")
        if not prompt or not output:
            return {"status": "error", "error": "prompt e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        self.report_progress(request, 0.0, "started")
        t_start = time.perf_counter()
        saved = model.generate(
            prompt=prompt,
            output=output,
            max_frames=request.get("max_frames"),
            seed=request.get("seed"),
            temperature=request.get("temperature"),
            also_npz=bool(request.get("also_npz", False)),
            metadata={
                "quality": request.get("quality") or "",
                "category": request.get("category") or "",
            },
        )
        if self.should_abort(request):
            return self.cancelled_response("cancelled after generate")

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return self.finish_response(output=str(Path(saved)), seconds=elapsed)

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
