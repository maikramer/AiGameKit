"""Adapter text3d para o modo subprocesso (``text3d serve --ums-worker``).

Herdam de :class:`gamedev_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.text3d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from typing import Any

from gamedev_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do text3d (HunyuanTextTo3DGenerator / Hunyuan3D-Omni)."""

    name = "text3d"

    def load(self, **kwargs: Any) -> Any:
        from text3d.generator import HunyuanTextTo3DGenerator
        from text3d.hy3dshape_paths import ensure_hy3dshape_on_path
        from text3d.ums_load import map_ums_load_kwargs

        ensure_hy3dshape_on_path(quiet=True)

        # Peak/offload: só do request (CLI hw_auto / with_ums_peak_opts).
        load_kwargs = map_ums_load_kwargs(kwargs)
        gen = HunyuanTextTo3DGenerator(**load_kwargs)
        gen.warmup()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        from text3d.ums_generate import run_generate

        return run_generate(
            model,
            request,
            should_abort=lambda: self.should_abort(request),
            report_progress=lambda pct, msg: self.report_progress(request, pct, msg),
            apply_runtime_budget=lambda: self.apply_runtime_budget(model, request, progress_pct=0.05),
            cancelled_response=self.cancelled_response,
        )

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload_hunyuan", None) or getattr(model, "unload", None)
        if callable(unload):
            unload()
