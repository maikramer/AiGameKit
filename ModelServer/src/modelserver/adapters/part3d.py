"""Adapter do Part3D — decomposição semântica de meshes 3D (X-Part).

O ``Part3DPipeline`` tem ``load()``/``unload()`` explícitos. ``__call__`` faz a
decomposição completa (segment + generate) e retorna ``(parts_scene, face_ids,
clean_mesh)``.

Input: ``request["mesh_path"]``.
Output: GLB das partes (``save_scene_geometries``) + GLB segmented (opcional).
"""

from __future__ import annotations

from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do part3d (Part3DPipeline)."""

    name = "part3d"

    def load(self, **kwargs: Any) -> Any:
        from part3d.pipeline import Part3DPipeline

        pipe = Part3DPipeline(verbose=kwargs.get("verbose", False), **kwargs)
        pipe.load()
        return pipe

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        mesh_path = request.get("mesh_path") or request.get("mesh")
        output = request.get("output")
        if not mesh_path or not output:
            return {"status": "error", "error": "mesh_path e output são obrigatórios"}

        t_start = time.perf_counter()

        gen_kwargs: dict[str, Any] = {}
        for key in ("octree_resolution", "num_inference_steps", "num_chunks"):
            if key in request:
                gen_kwargs[key] = request[key]

        parts_scene, face_ids, clean_mesh = model(
            mesh_path,
            seed=int(request.get("seed", 42)),
            **gen_kwargs,
        )

        from gamedev_shared.bpy_mesh import save_scene_geometries

        saved = save_scene_geometries(parts_scene, output)

        # Segmented mesh opcional (color-coded).
        segmented_path = request.get("output_segmented")
        if segmented_path:
            from gamedev_shared.bpy_mesh import save_colored_mesh

            save_colored_mesh(clean_mesh, face_ids, segmented_path)

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload", None)
        if callable(unload):
            unload()
