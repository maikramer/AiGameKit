"""Adapter do Part3D — Hunyuan3D-Part (P3-SAM + X-Part).

``Part3DPipeline`` é context-managed: ``__enter__`` chama ``load()``,
``__call__`` / ``segment`` decompõe, ``__exit__`` liberta. O adapter
normaliza isto no contrato canónico load/generate/unload.

Input: ``request["mesh_path"]`` (+ opcional ``output``, seed, steps, …).
Output: GLB de partes via ``save_scene_geometries``.
"""

from __future__ import annotations

from typing import Any

from .base import BackendAdapter


class Adapter(BackendAdapter):
    """Adapter do part3d (Part3DPipeline — context manager)."""

    name = "part3d"

    def load(self, **kwargs: Any) -> Any:
        from part3d.pipeline import Part3DPipeline

        mem_eff = kwargs.pop("memory_efficient", None)
        if mem_eff is None:
            mem_eff = self.should_use_low_vram_mode(threshold_mib=8000)

        pipe = Part3DPipeline(
            verbose=kwargs.get("verbose", False),
            cpu_offload=kwargs.get("cpu_offload", mem_eff),
            memory_efficient=mem_eff,
            quantization_mode=kwargs.get("quantization_mode", "auto"),
            quantize_dit=kwargs.get("quantize_dit", mem_eff),
            sdnq_preset=kwargs.get("sdnq_preset", "sdnq-uint8" if mem_eff else None),
            gpu_ids=kwargs.get("gpu_ids"),
            volume_decoder=kwargs.get("volume_decoder", "auto"),
            mc_algo=kwargs.get("mc_algo", "mc"),
            channels_last=kwargs.get("channels_last", True),
            enable_torch_compile=kwargs.get("enable_torch_compile", False),
            quality=kwargs.get("quality"),
        )
        return pipe.__enter__()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import time
        from pathlib import Path

        import numpy as np

        mesh_path = request.get("mesh_path") or request.get("mesh")
        output = request.get("output")
        if not mesh_path or not output:
            return {"status": "error", "error": "mesh_path e output são obrigatórios"}

        t_start = time.perf_counter()
        segment_only = bool(request.get("segment_only", False))
        seed = request.get("seed")
        output_segmented = request.get("output_segmented")
        if output_segmented is None:
            output_segmented = str(Path(output).with_name(Path(output).stem.replace("_parts", "") + "_segmented.glb"))
            if output_segmented == str(Path(output)):
                output_segmented = str(Path(output).with_name(Path(output).stem + "_segmented.glb"))

        from gamedev_shared.bpy_mesh import (
            load_mesh_as_trimesh,
            save_colored_mesh,
            save_empty_glb,
            save_scene_geometries,
        )

        gen_kwargs: dict[str, Any] = {}
        if request.get("octree_resolution") is not None:
            gen_kwargs["octree_resolution"] = int(request["octree_resolution"])
        steps = request.get("num_inference_steps", request.get("steps"))
        if steps is not None:
            gen_kwargs["num_inference_steps"] = int(steps)
        if request.get("num_chunks") is not None:
            gen_kwargs["num_chunks"] = int(request["num_chunks"])
        if request.get("mc_algo") is not None:
            gen_kwargs["mc_algo"] = str(request["mc_algo"])
        if request.get("point_num") is not None:
            gen_kwargs["point_num"] = int(request["point_num"])
        if request.get("prompt_num") is not None:
            gen_kwargs["prompt_num"] = int(request["prompt_num"])

        if segment_only:
            mesh = load_mesh_as_trimesh(mesh_path)
            _aabb, face_ids, clean_mesh = model.segment(mesh, seed=seed)
            color_map = {int(uid): np.random.randint(0, 255, size=3) for uid in np.unique(face_ids) if uid >= 0}
            face_colors = np.array([color_map.get(int(fid), [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            save_colored_mesh(clean_mesh, face_colors, output_segmented)
            elapsed = time.perf_counter() - t_start
            return {
                "status": "ok",
                "output": str(output_segmented),
                "seconds": round(elapsed, 2),
            }

        parts_scene, face_ids, clean_mesh = model(mesh_path, seed=seed, **gen_kwargs)

        if not parts_scene.geometry:
            color_map = {int(uid): np.random.randint(0, 255, size=3) for uid in np.unique(face_ids) if uid >= 0}
            face_colors = np.array([color_map.get(int(fid), [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            save_colored_mesh(clean_mesh, face_colors, output_segmented)
            save_empty_glb(output)
        else:
            save_scene_geometries(parts_scene, output)
            color_map = {int(uid): np.random.randint(0, 255, size=3) for uid in np.unique(face_ids) if uid >= 0}
            face_colors = np.array([color_map.get(int(fid), [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            save_colored_mesh(clean_mesh, face_colors, output_segmented)

        elapsed = time.perf_counter() - t_start
        return {
            "status": "ok",
            "output": str(output),
            "output_segmented": str(output_segmented),
            "seconds": round(elapsed, 2),
        }

    def unload(self, model: Any) -> None:
        exit_method = getattr(model, "__exit__", None)
        if callable(exit_method):
            model.__exit__(None, None, None)
