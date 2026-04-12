from __future__ import annotations

import base64
import binascii
import io
import math
from pathlib import Path

from PIL import Image

from ..schemas import Text3DParams


def _decode_image_b64(data: str) -> Image.Image:
    s = data.strip()
    if "," in s[:80] and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    try:
        raw = base64.b64decode(s, validate=False)
    except binascii.Error as e:
        raise ValueError("from_image_base64 inválido") from e
    return Image.open(io.BytesIO(raw)).convert("RGB")


def run_text3d(_job_id: str, params: Text3DParams, out_dir: Path) -> list[str]:
    from text3d import defaults as t3_defaults
    from text3d.generator import HunyuanTextTo3DGenerator
    from text3d.utils.export import save_mesh
    from text3d.utils.mesh_repair import repair_mesh

    out_dir.mkdir(parents=True, exist_ok=True)
    device = "cpu" if params.cpu else None

    steps = params.num_inference_steps
    octree = params.octree_resolution
    chunks = params.num_chunks
    if params.preset is not None:
        pv = t3_defaults.PRESET_HUNYUAN[params.preset]
        steps = pv["steps"]
        octree = pv["octree"]
        chunks = pv["chunks"]
    if steps is None:
        steps = t3_defaults.DEFAULT_HY_STEPS
    if octree is None:
        octree = t3_defaults.DEFAULT_OCTREE_RESOLUTION
    if chunks is None:
        chunks = t3_defaults.DEFAULT_NUM_CHUNKS
    guidance = params.guidance_scale if params.guidance_scale is not None else t3_defaults.DEFAULT_HY_GUIDANCE

    rot_done = False
    origin_done = False
    try:
        if params.export_rotation_x_deg is not None:
            t3_defaults.set_export_rotation_x_rad_override(math.radians(float(params.export_rotation_x_deg)))
            rot_done = True
        if params.export_origin is not None:
            t3_defaults.set_export_origin_override(params.export_origin)
            origin_done = True

        gen = HunyuanTextTo3DGenerator(
            device=device,
            low_vram_mode=params.low_vram,
            verbose=False,
            hunyuan_subfolder=params.model_subfolder or HunyuanTextTo3DGenerator.DEFAULT_SUBFOLDER,
            hunyuan_model_id=params.hunyuan_model_id or HunyuanTextTo3DGenerator.DEFAULT_HF_ID,
        )
        try:
            if params.from_image_base64:
                pil_in = _decode_image_b64(params.from_image_base64)
                if params.max_retries > 1:
                    result = gen.generate_from_image_with_quality_check(
                        pil_in,
                        max_retries=params.max_retries,
                        hy_seed=params.seed,
                        num_inference_steps=steps,
                        guidance_scale=guidance,
                        octree_resolution=octree,
                        num_chunks=chunks,
                        mc_level=params.mc_level,
                    )
                else:
                    result = gen.generate_from_image(
                        pil_in,
                        num_inference_steps=steps,
                        guidance_scale=guidance,
                        octree_resolution=octree,
                        num_chunks=chunks,
                        hy_seed=params.seed,
                        mc_level=params.mc_level,
                    )
                artifacts: list[str] = []
            else:
                assert params.prompt is not None
                _gen_kw = dict(
                    t2d_width=params.image_width,
                    t2d_height=params.image_height,
                    t2d_steps=params.t2d_steps,
                    t2d_guidance=params.t2d_guidance,
                    text2d_model_id=params.text2d_model_id,
                    num_inference_steps=steps,
                    guidance_scale=guidance,
                    octree_resolution=octree,
                    num_chunks=chunks,
                    hy_seed=params.seed,
                    mc_level=params.mc_level,
                    t2d_full_gpu=params.t2d_full_gpu,
                    optimize_prompt=params.optimize_prompt,
                )
                if params.max_retries > 1:
                    result, ref_img = gen.generate_with_quality_check(
                        params.prompt,
                        max_retries=params.max_retries,
                        t2d_seed=params.seed,
                        return_reference_image=True,
                        **_gen_kw,
                    )
                else:
                    result, ref_img = gen.generate(
                        params.prompt,
                        t2d_seed=params.seed,
                        return_reference_image=True,
                        **_gen_kw,
                    )
                artifacts = []
                if params.save_reference_image:
                    ref_name = f"{params.output_basename}_text2d.png"
                    ref_img.save(out_dir / ref_name, format="PNG")
                    artifacts.append(ref_name)

            if not params.no_mesh_repair:
                result = repair_mesh(
                    result,
                    keep_largest=True,
                    merge_vertices=True,
                    remove_ground_shadow=not params.no_ground_shadow_removal,
                    ground_artifact_mesh_space="hunyuan",
                    ground_shadow_aggressive=params.ground_shadow_aggressive
                    and not params.ground_shadow_very_aggressive,
                    ground_shadow_very_aggressive=params.ground_shadow_very_aggressive,
                    smooth_iterations=max(0, params.mesh_smooth),
                    remesh=params.remesh,
                    remesh_resolution=params.remesh_resolution,
                    remesh_iterations=params.remesh_iterations,
                    remesh_max_surf_dist_factor=params.remesh_max_surf_dist_factor,
                )

            mesh_name = f"{params.output_basename}.{params.output_format}"
            save_mesh(result, out_dir / mesh_name, format=params.output_format, origin_mode=params.export_origin)
            artifacts.append(mesh_name)
            return artifacts
        finally:
            gen._unload_hunyuan()
    finally:
        if rot_done:
            t3_defaults.set_export_rotation_x_rad_override(None)
        if origin_done:
            t3_defaults.set_export_origin_override(None)
