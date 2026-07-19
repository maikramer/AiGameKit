"""Adapter do Text3D — Hunyuan3D-Omni para text/image-to-3D.

Contrato UMS canónico: ``load`` / ``generate`` / ``unload``.

Orquestração de generate vive em ``text3d.ums_generate`` (package) — adapter
só traduz hooks UMS (abort / progress / runtime budget).
"""

from __future__ import annotations

from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text3d (HunyuanTextTo3DGenerator / Hunyuan3D-Omni)."""

    name = "text3d"

    def load(self, **kwargs: Any) -> Any:
        from text3d.generator import HunyuanTextTo3DGenerator
        from text3d.hy3dshape_paths import ensure_hy3dshape_on_path
        from text3d.ums_load import map_ums_load_kwargs

        ensure_hy3dshape_on_path(quiet=True)

        low_vram = self.should_use_low_vram_mode(threshold_mib=7000)
        load_kwargs = map_ums_load_kwargs(kwargs, low_vram=low_vram)
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
