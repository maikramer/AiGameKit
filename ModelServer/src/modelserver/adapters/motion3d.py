"""Adapter do Motion3D — Motius T2M-GPT HumanML3D text-to-motion."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do motion3d (MotionGenerator) — fallback in-process."""

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
        )
        if self.should_abort(request):
            return self.cancelled_response("cancelled after generate")

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return {
            "status": "ok",
            "output": str(Path(saved)),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
