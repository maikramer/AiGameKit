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
        from paint3d.painter import PaintBatchProcessor

        proc = PaintBatchProcessor(verbose=kwargs.get("verbose", False), **kwargs)
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
        self.report_progress(request, 0.25, "painting")
        textured = model.paint_mesh(mesh_objs, image_path)

        if self.should_abort(request):
            return self.cancelled_response("cancelled before save")
        self.report_progress(request, 0.85, "saving")
        saved = save_glb(textured, output)

        elapsed = time.perf_counter() - t_start
        self.report_progress(request, 1.0, "done")
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        exit_method = getattr(model, "__exit__", None)
        if callable(exit_method):
            model.__exit__(None, None, None)
