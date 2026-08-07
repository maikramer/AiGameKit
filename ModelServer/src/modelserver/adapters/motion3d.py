"""Adapter do Motion3D — Tencent HY-Motion-1.0 text-to-motion.

Peak/offload: só do request (CLI hw_auto / ``with_ums_peak_opts``).
Defaults: Lite + ``disable_prompt_engineering``; SDNQ/offload em VRAM baixa.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from .base import BackendAdapter

ModelVariant = Literal["lite", "full"]


def _variant(raw: Any) -> ModelVariant:
    return "full" if str(raw or "lite").lower() == "full" else "lite"


class Adapter(BackendAdapter):
    """Adapter do motion3d (MotionGenerator) — fallback in-process."""

    name = "motion3d"

    def load(self, **kwargs: Any) -> Any:
        from motion3d.generator import MotionGenerator

        device = kwargs.get("device")
        if device is None and kwargs.get("gpu_ids"):
            device = "cuda"
        sdnq = kwargs.get("sdnq_preset") or kwargs.get("quant_mode")
        if isinstance(sdnq, str) and sdnq.lower() in ("none", "null", ""):
            sdnq = None
        mem_eff = bool(kwargs.get("memory_efficient", False))
        offload = kwargs.get("offload_text_encoder")
        if offload is None:
            offload = bool(kwargs.get("offload", mem_eff))
        gen = MotionGenerator.get_instance(
            device=device,
            model=_variant(kwargs.get("model")),
            sdnq_preset=sdnq,
            memory_efficient=mem_eff,
            offload_text_encoder=bool(offload),
            validation_steps=kwargs.get("validation_steps"),
        )
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
            duration=request.get("duration"),
            max_frames=request.get("max_frames"),
            seed=request.get("seed"),
            cfg_scale=request.get("cfg_scale"),
            temperature=request.get("temperature"),
            also_npz=bool(request.get("also_npz", False)),
            metadata={
                "quality": request.get("quality") or "",
                "category": request.get("category") or "",
                "model": request.get("model") or "lite",
            },
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
