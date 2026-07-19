"""Adapter do Paint3D — Hunyuan3D-Paint 2.1 para texturização 3D (PBR).

O Paint3D não tem um generator com ``warmup``/``unload``. O ``PaintBatchProcessor``
é um context manager: ``__enter__`` carrega a pipeline, ``paint_mesh`` pinta,
``__exit__`` liberta. O adapter normaliza isto no contrato canónico.

Input: ``request["mesh_path"]`` + ``request["image_path"]``.
Output: GLB texturado via ``save_glb``.
"""

from __future__ import annotations

from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do paint3d (PaintBatchProcessor — context manager)."""

    name = "paint3d"

    def load(self, **kwargs: Any) -> Any:
        from paint3d import defaults as _defaults
        from paint3d.painter import PaintBatchProcessor

        # UMS-only / peak-planning keys — PaintBatchProcessor não os aceita.
        quant = kwargs.pop("sdnq_preset", None) or kwargs.pop("quant_mode", None)
        kwargs.pop("offload", None)
        mem_eff = kwargs.pop("memory_efficient", None)
        if mem_eff is None and quant is not None:
            mem_eff = str(quant).strip().lower() not in ("none", "null", "")
        mem_eff = bool(mem_eff)

        # Shape do pipeline (afeta activação/VRAM): vistas, resolução, atlas.
        # O runtime budget (vram_budget) pode reduzir vistas depois, nunca subir.
        if mem_eff:
            default_views = _defaults.MEMORY_EFFICIENT_MAX_VIEWS
            default_res = _defaults.MEMORY_EFFICIENT_VIEW_RESOLUTION
        else:
            default_views = _defaults.DEFAULT_PAINT_MAX_VIEWS
            default_res = _defaults.DEFAULT_PAINT_VIEW_RESOLUTION
        max_views = int(kwargs.get("max_num_view") or default_views)
        view_res = int(kwargs.get("view_resolution") or default_res)
        render_size = kwargs.get("render_size")
        texture_size = kwargs.get("texture_size")
        bake_exp = kwargs.get("bake_exp")

        proc = PaintBatchProcessor(
            max_num_view=max_views,
            view_resolution=view_res,
            render_size=int(render_size) if render_size else None,
            texture_size=int(texture_size) if texture_size else None,
            bake_exp=int(bake_exp) if bake_exp else _defaults.DEFAULT_PAINT_BAKE_EXP,
            verbose=bool(kwargs.get("verbose", False)),
            memory_efficient=mem_eff,
            gpu_ids=kwargs.get("gpu_ids"),
            torch_compile=bool(kwargs.get("torch_compile", False)),
            torch_compile_mode=str(kwargs.get("torch_compile_mode", "default")),
            channels_last=bool(kwargs.get("channels_last", False)),
            allow_group_offload=bool(kwargs.get("allow_group_offload", False)),
        )
        return proc.__enter__()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        mesh_path = request.get("mesh_path") or request.get("mesh")
        image_path = request.get("image_path") or request.get("image")
        output = request.get("output")
        if not mesh_path or not image_path or not output:
            return {"status": "error", "error": "mesh_path, image_path e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        self.report_progress(request, 0.0, "started")
        t_start = time.perf_counter()

        from paint3d.utils.mesh_io import load_mesh_trimesh, save_glb

        if self.should_abort(request):
            return self.cancelled_response("cancelled before load mesh")
        self.report_progress(request, 0.15, "loading_mesh")
        mesh_objs = load_mesh_trimesh(mesh_path)

        if self.should_abort(request):
            return self.cancelled_response("cancelled before paint")
        # Runtime VRAM budget (views/tiles/DINO) — canónico Shared, exposto UMS.
        # Overrides por-request: vistas/resolução pedidas (clamped ao pipeline).
        budget_hints: dict[str, Any] = {}
        if request.get("max_num_view"):
            budget_hints["requested_views"] = int(request["max_num_view"])
        if request.get("view_resolution"):
            budget_hints["requested_resolution"] = int(request["view_resolution"])
        try:
            budget = self.apply_runtime_budget(model, request, progress_pct=0.22, **budget_hints)
        except (RuntimeError, MemoryError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_code": "VRAM_INSUFFICIENT",
                "hint": "Runtime budget / MeshRender sem headroom — `ums evict` ou reduz views.",
            }
        self.report_progress(request, 0.25, "painting")
        textured = model.paint_mesh(mesh_objs, image_path)

        if self.should_abort(request):
            return self.cancelled_response("cancelled before save")
        self.report_progress(request, 0.85, "saving")
        saved = save_glb(textured, output)

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        out: dict[str, Any] = {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }
        if budget:
            out["runtime_budget"] = budget
        return out

    def unload(self, model: Any) -> None:
        exit_method = getattr(model, "__exit__", None)
        if callable(exit_method):
            model.__exit__(None, None, None)
