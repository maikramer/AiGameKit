"""Adapter part3d para o modo subprocesso (``part3d serve --ums-worker``).

Herdam de :class:`aigamekit_shared.worker_serve_adapter_base.WorkerAdapter`
(standalone, sem depender do package modelserver). Mesma lógica do
``modelserver.adapters.part3d.Adapter`` mas vive no venv da tool.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter


class Adapter(WorkerAdapter):
    """Adapter do part3d (Part3DPipeline — context manager)."""

    name = "part3d"

    def load(self, **kwargs: Any) -> Any:
        from part3d.pipeline import Part3DPipeline

        # Peak: só request (CLI hw_auto / peak opts). Sem fallback GPU-size local.
        mem_eff = bool(kwargs.pop("memory_efficient", False))
        sdnq = kwargs.get("sdnq_preset")
        if sdnq is None and mem_eff:
            sdnq = "sdnq-uint8"

        pipe = Part3DPipeline(
            verbose=kwargs.get("verbose", False),
            cpu_offload=kwargs.get("cpu_offload", mem_eff),
            memory_efficient=mem_eff,
            quantization_mode=kwargs.get("quantization_mode", "auto"),
            quantize_dit=kwargs.get("quantize_dit", mem_eff),
            sdnq_preset=sdnq,
            gpu_ids=kwargs.get("gpu_ids"),
            volume_decoder=kwargs.get("volume_decoder", "auto"),
            mc_algo=kwargs.get("mc_algo", "mc"),
            channels_last=kwargs.get("channels_last", True),
            enable_torch_compile=kwargs.get("enable_torch_compile", False),
            quality=kwargs.get("quality"),
        )
        return pipe.__enter__()

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        import sys
        import time
        from pathlib import Path

        import numpy as np

        mesh_path = request.get("mesh_path") or request.get("mesh")
        output = request.get("output")
        if not mesh_path or not output:
            return {"status": "error", "error": "mesh_path e output são obrigatórios"}
        if self.should_abort(request):
            return self.cancelled_response("cancelled before generate")

        self.report_progress(request, 0.0, "started")
        t_start = time.perf_counter()
        segment_only = bool(request.get("segment_only", False))
        seed = request.get("seed")
        output_segmented = request.get("output_segmented")
        if output_segmented is None:
            output_segmented = str(Path(output).with_name(Path(output).stem.replace("_parts", "") + "_segmented.glb"))
            if output_segmented == str(Path(output)):
                output_segmented = str(Path(output).with_name(Path(output).stem + "_segmented.glb"))

        model.refine_labels = bool(request.get("refine_labels", model.refine_labels))
        model.detail_levels = max(0, int(request.get("detail_levels", model.detail_levels)))
        for name in (
            "bbox_merge_iou",
            "mask_nms_iou",
            "secondary_mask_iou",
            "min_cluster_support",
            "min_predicted_iou",
            "prompt_batch_size",
            "multi_head",
            "head_min_score",
            "head_score_ratio",
            "consensus",
            "consensus_vote",
            "segment_mode",
            "parts_mode",
            "xpart_max_area_frac",
            "cap_part_holes",
        ):
            if request.get(name) is not None:
                setattr(model, name, request[name])
        if getattr(model, "segment_mode", "p3sam") != "geometry":
            auto_mask_module = sys.modules.get(type(model._bbox_predictor).__module__)
            configure_mask_quality = getattr(auto_mask_module, "configure_aigamekit_mask_quality", None)
            if callable(configure_mask_quality):
                configure_mask_quality(
                    mask_nms_iou=model.mask_nms_iou,
                    secondary_mask_iou=model.secondary_mask_iou,
                    min_cluster_support=model.min_cluster_support,
                    min_predicted_iou=model.min_predicted_iou,
                    prompt_batch_size=model.prompt_batch_size,
                    bbox_merge_iou=model.bbox_merge_iou,
                    multi_head=getattr(model, "multi_head", True),
                    head_min_score=getattr(model, "head_min_score", 0.5),
                    head_score_ratio=getattr(model, "head_score_ratio", 0.85),
                    consensus=getattr(model, "consensus", True),
                    consensus_vote=getattr(model, "consensus_vote", 0.5),
                )

        from aigamekit_shared.bpy_mesh import (
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
        if request.get("postprocess") is not None:
            gen_kwargs["postprocess"] = bool(request["postprocess"])
        if request.get("threshold") is not None:
            gen_kwargs["threshold"] = float(request["threshold"])
        if request.get("segmentation_proxy") is not None:
            gen_kwargs["segmentation_proxy_path"] = str(request["segmentation_proxy"])

        if self.should_abort(request):
            return self.cancelled_response("cancelled before segment")
        self.report_progress(request, 0.15, "segment" if segment_only else "segment_and_parts")

        if segment_only:
            _aabb, face_ids, clean_mesh = model.segment_file(
                mesh_path,
                segmentation_proxy_path=gen_kwargs.get("segmentation_proxy_path"),
                seed=seed,
                point_num=gen_kwargs.get("point_num"),
                prompt_num=gen_kwargs.get("prompt_num"),
                postprocess=gen_kwargs.get("postprocess", True),
                threshold=gen_kwargs.get("threshold", 0.99),
            )
            if self.should_abort(request):
                return self.cancelled_response("cancelled after segment")
            self.report_progress(request, 0.9, "saving")
            color_map = {int(uid): np.random.randint(0, 255, size=3) for uid in np.unique(face_ids) if uid >= 0}
            face_colors = np.array([color_map.get(int(fid), [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            save_colored_mesh(clean_mesh, face_colors, output_segmented)
            elapsed = time.perf_counter() - t_start
            self.report_progress(request, 1.0, "done")
            return {
                "status": "ok",
                "output": str(output_segmented),
                "seconds": round(elapsed, 2),
            }

        parts_scene, face_ids, clean_mesh = model(mesh_path, seed=seed, **gen_kwargs)

        if self.should_abort(request):
            return self.cancelled_response("cancelled after parts")
        self.report_progress(request, 0.85, "saving")

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
        self.report_progress(request, 1.0, "done")
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
