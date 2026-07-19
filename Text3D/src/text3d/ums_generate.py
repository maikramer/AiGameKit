"""Orquestração canónica de generate Text3D (UMS adapter + futuros CLIs).

Move a lógica que vivia em ``ModelServer.adapters.text3d`` para o package —
adapter fica thin; CLI in-process pode reutilizar o mesmo caminho.
"""

from __future__ import annotations

import contextlib
import time
from collections.abc import Callable
from typing import Any


def run_generate(
    model: Any,
    request: dict[str, Any],
    *,
    should_abort: Callable[[], bool] | None = None,
    report_progress: Callable[[float, str], None] | None = None,
    apply_runtime_budget: Callable[[], dict[str, Any] | None] | None = None,
    cancelled_response: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Corre text/image-to-3D + topology + save.

    Hooks opcionais vêm do ``BackendAdapter`` (abort / progress / budget).
    Sem hooks = caminho in-process simples.
    """
    from text3d.omni_presets import merge_omni_controls, write_omni_fingerprint

    def _abort() -> bool:
        return bool(should_abort()) if should_abort else False

    def _progress(pct: float, msg: str) -> None:
        if report_progress:
            report_progress(pct, msg)

    def _cancelled(reason: str) -> dict[str, Any]:
        if cancelled_response:
            return cancelled_response(reason)
        return {"status": "cancelled", "error": reason}

    output = request.get("output")
    if not output:
        return {"status": "error", "error": "output é obrigatório"}
    if _abort():
        return _cancelled("cancelled before generate")

    _progress(0.0, "started")
    t_start = time.perf_counter()
    from_image = request.get("from_image")

    try:
        _omni = merge_omni_controls(
            control_type=request.get("control_type"),
            bbox=request.get("bbox"),
            bbox_preset=request.get("bbox_preset"),
            size=request.get("size"),
            size_m=request.get("size_m"),
            pose_file=request.get("pose_file"),
            pose_preset=request.get("pose_preset"),
            point_cloud=request.get("point_cloud"),
            voxel_mesh=request.get("voxel_mesh"),
            category=request.get("category"),
        )
    except (KeyError, FileNotFoundError, ValueError) as exc:
        return {"status": "error", "error": str(exc)}

    control_kwargs = {
        "control_type": _omni["control_type"],
        "bbox": _omni["bbox"],
        "pose_file": _omni["pose_file"],
        "point_cloud": _omni["point_cloud"],
        "voxel_mesh": _omni["voxel_mesh"],
    }
    control_kwargs = {k: v for k, v in control_kwargs.items() if v is not None}

    steps = int(request.get("steps", request.get("num_inference_steps", 50)))
    guidance = float(request.get("guidance", 4.5))
    octree = int(request.get("octree_resolution", 384))
    chunks = int(request.get("num_chunks", 20000))
    seed = request.get("seed")
    mc_level_raw = request.get("mc_level", "auto")
    mc_level: float | str = "auto" if str(mc_level_raw).strip().lower() == "auto" else float(mc_level_raw)
    bounds_mode = str(request.get("bounds_mode", "auto"))
    auto_chunks = bool(request.get("auto_num_chunks", True))
    remove_bg = bool(request.get("remove_bg", True))

    from text3d.bbox_tune import apply_bbox_tune, size_m_from_mapping

    try:
        size_m_vals = size_m_from_mapping(request.get("size_m"))
    except ValueError:
        size_m_vals = None
    if request.get("bbox_tune", True) is not False:
        user_steps = "steps" in request or "num_inference_steps" in request
        user_octree = "octree_resolution" in request
        user_chunks = "num_chunks" in request
        vram_raw = request.get("total_vram_gib")
        try:
            total_vram_gib = float(vram_raw) if vram_raw is not None else None
        except (TypeError, ValueError):
            total_vram_gib = None
        steps, octree, chunks, _bt = apply_bbox_tune(
            steps=steps,
            octree=octree,
            chunks=chunks,
            size_m=size_m_vals,
            category=request.get("category"),
            bbox_preset=request.get("bbox_preset"),
            total_vram_gib=total_vram_gib,
            volume_decoder=request.get("volume_decoder"),
            tune_steps=not user_steps,
            tune_octree=not user_octree,
            tune_chunks=not user_chunks,
            quality=request.get("quality"),
            group_offload=bool(request.get("allow_group_offload", True)),
        )

    if apply_runtime_budget is not None:
        try:
            pre_budget = apply_runtime_budget()
        except (RuntimeError, MemoryError) as exc:
            return {
                "status": "error",
                "error": str(exc),
                "error_code": "VRAM_INSUFFICIENT",
            }
        if pre_budget and pre_budget.get("num_chunks") and auto_chunks and "num_chunks" not in request:
            chunks = int(pre_budget["num_chunks"])

    if from_image:
        if _abort():
            return _cancelled("cancelled before image_to_3d")
        _progress(0.1, "image_to_3d")
        mesh = model.generate_from_image(
            image=from_image,
            num_inference_steps=steps,
            guidance_scale=guidance,
            octree_resolution=octree,
            num_chunks=chunks,
            hy_seed=seed,
            mc_level=mc_level,
            bounds_mode=bounds_mode,
            auto_num_chunks=auto_chunks,
            remove_bg=remove_bg,
            keep_loaded=True,
            **control_kwargs,
        )
    else:
        prompt = request.get("prompt", "")
        if not prompt:
            return {"status": "error", "error": "prompt ou from_image é obrigatório"}
        if _abort():
            return _cancelled("cancelled before text_to_3d")
        _progress(0.1, "text_to_3d")
        if _abort():
            return _cancelled("cancelled before text2d stage")
        result = model.generate(
            prompt=prompt,
            t2d_seed=seed,
            return_reference_image=False,
            t2d_width=int(request.get("t2d_width", 1024)),
            t2d_height=int(request.get("t2d_height", 1024)),
            t2d_steps=int(request.get("t2d_steps", 4)),
            t2d_guidance=float(request.get("t2d_guidance", 1.0)),
            text2d_model_id=request.get("text2d_model_id"),
            num_inference_steps=steps,
            guidance_scale=guidance,
            octree_resolution=octree,
            num_chunks=chunks,
            hy_seed=seed,
            mc_level=mc_level,
            bounds_mode=bounds_mode,
            auto_num_chunks=auto_chunks,
            t2d_full_gpu=bool(request.get("t2d_full_gpu", False)),
            optimize_prompt=bool(request.get("optimize_prompt", True)),
            remove_bg=remove_bg,
            **control_kwargs,
        )
        mesh = result if not isinstance(result, tuple) else result[0]

    if _abort():
        return _cancelled("cancelled after mesh generate")
    _progress(0.65, "mesh_generated")

    from text3d.utils.export import save_mesh

    if _abort():
        return _cancelled("cancelled before save")
    _progress(0.85, "saving")

    if size_m_vals is not None and hasattr(mesh, "apply_scale"):
        from text3d.bbox_tune import scale_factor_to_meters

        extents = getattr(mesh, "extents", None)
        if extents is not None:
            _sf = scale_factor_to_meters(float(max(extents)), size_m_vals)
            if _sf is not None:
                mesh.apply_scale(_sf)

    topology_fix = bool(request.get("topology_fix", True))
    if topology_fix:
        from text3d.utils.mesh_lod import prepare_mesh_topology

        mesh = prepare_mesh_topology(mesh, size_m=size_m_vals)

    origin_mode = request.get("origin_mode")
    saved = save_mesh(mesh, output, origin_mode=origin_mode)
    with contextlib.suppress(OSError):
        write_omni_fingerprint(
            saved,
            {
                **_omni,
                "bounds_mode": bounds_mode,
                "mc_level": mc_level,
                "size_m": size_m_vals,
            },
        )

    elapsed = time.perf_counter() - t_start
    _progress(1.0, "done")
    out: dict[str, Any] = {
        "status": "ok",
        "output": str(saved),
        "seconds": round(elapsed, 2),
        "control_type": _omni.get("control_type") or "none",
        "pose_preset": _omni.get("pose_preset"),
        "bbox_preset": _omni.get("bbox_preset"),
        "topology_fix": topology_fix,
        "octree_resolution": octree,
        "steps": steps,
    }
    decode_stats = getattr(model, "last_decode_stats", None) or {}
    if "num_chunks" in decode_stats:
        out["runtime_budget"] = {
            "num_chunks": decode_stats.get("num_chunks"),
            "num_chunks_static": decode_stats.get("num_chunks_static"),
            "auto_num_chunks": decode_stats.get("auto_num_chunks"),
            "free_vram_bytes": decode_stats.get("free_vram_bytes"),
        }
    return out
