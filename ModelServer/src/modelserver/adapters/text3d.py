"""Adapter do Text3D — Hunyuan3D-2.1 para text/image-to-3D.

O ``HunyuanTextTo3DGenerator`` não tem ``warmup()`` — a pipeline Hunyuan carrega
lazy no primeiro ``generate`` via ``_load_hunyuan()``. O adapter força a carga
no ``load`` para que o modelo fique quente. Unload é ``unload_hunyuan()``.

Suporta dois modos:
  - text-to-3D: ``request["prompt"]`` → gera imagem de referência (Text2D) + mesh
  - image-to-3D: ``request["from_image"]`` → só stage Hunyuan (sem Text2D)

Output: GLB via ``save_mesh`` (com ``prepare_mesh_topology`` opcional).
"""

from __future__ import annotations

from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do text3d (HunyuanTextTo3DGenerator)."""

    name = "text3d"

    def load(self, **kwargs: Any) -> Any:
        from text3d.generator import HunyuanTextTo3DGenerator

        gen = HunyuanTextTo3DGenerator(verbose=kwargs.get("verbose", False), **kwargs)
        # Forçar carga da pipeline Hunyuan (não há warmup() neste generator).
        gen._load_hunyuan()
        return gen

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time

        output = request.get("output")
        if not output:
            return {"status": "error", "error": "output é obrigatório"}

        t_start = time.perf_counter()
        from_image = request.get("from_image")

        if from_image:
            mesh = model.generate_from_image(
                image=from_image,
                num_inference_steps=int(request.get("steps", request.get("num_inference_steps", 30))),
                guidance_scale=float(request.get("guidance", 5.5)),
                octree_resolution=int(request.get("octree_resolution", 512)),
                num_chunks=int(request.get("num_chunks", 8000)),
                hy_seed=request.get("seed"),
                mc_level=float(request.get("mc_level", 0.0)),
                remove_bg=bool(request.get("remove_bg", True)),
                keep_loaded=True,
            )
        else:
            prompt = request.get("prompt", "")
            if not prompt:
                return {"status": "error", "error": "prompt ou from_image é obrigatório"}
            result = model.generate(
                prompt=prompt,
                t2d_seed=request.get("seed"),
                return_reference_image=False,
                t2d_width=int(request.get("t2d_width", 1024)),
                t2d_height=int(request.get("t2d_height", 1024)),
                t2d_steps=int(request.get("t2d_steps", 4)),
                t2d_guidance=float(request.get("t2d_guidance", 1.0)),
                text2d_model_id=request.get("text2d_model_id"),
                num_inference_steps=int(request.get("steps", 30)),
                guidance_scale=float(request.get("guidance", 5.5)),
                octree_resolution=int(request.get("octree_resolution", 512)),
                num_chunks=int(request.get("num_chunks", 8000)),
                hy_seed=request.get("seed"),
                mc_level=float(request.get("mc_level", 0.0)),
                t2d_full_gpu=bool(request.get("t2d_full_gpu", False)),
                optimize_prompt=bool(request.get("optimize_prompt", True)),
                remove_bg=bool(request.get("remove_bg", True)),
            )
            mesh = result if not isinstance(result, tuple) else result[0]

        # Topology repair + save (alinha com o CLI generate).
        from text3d.utils.export import save_mesh
        from text3d.utils.mesh_lod import prepare_mesh_topology

        mesh = prepare_mesh_topology(mesh)
        origin_mode = request.get("origin_mode")
        saved = save_mesh(mesh, output, origin_mode=origin_mode)

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(saved),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        unload = getattr(model, "unload_hunyuan", None) or getattr(model, "unload", None)
        if callable(unload):
            unload()
