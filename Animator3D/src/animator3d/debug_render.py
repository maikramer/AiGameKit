"""Render headless de modelos 3D para debugging por agentes IA.

Importa GLB via bpy, monta cameras em angulos predefinidos,
renderiza PNGs e exporta metadados JSON.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

CAMERA_PRESETS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0, -4, 1.2), (0, 0, 0.5)),
    "back": ((0, 4, 1.2), (0, 0, 0.5)),
    "left": ((-4, 0, 1.2), (0, 0, 0.5)),
    "right": ((4, 0, 1.2), (0, 0, 0.5)),
    "top": ((0, -0.01, 5), (0, 0, 0.5)),
    "three_quarter": ((3, -3, 2.5), (0, 0, 0.3)),
    # Vista baixa (silhueta / pés) e contra-plongée (dramático)
    "low_front": ((0, -4.5, 0.45), (0, 0, 0.35)),
    "worm": ((0, -2.2, 0.2), (0, 0, 0.65)),
}

ALL_VIEWS = list(CAMERA_PRESETS.keys())
DEFAULT_VIEWS = ["front", "three_quarter", "right", "back"]


def _bpy():
    import bpy

    return bpy


def _look_at(camera, target: tuple[float, float, float]) -> None:
    from mathutils import Vector

    direction = Vector(target) - camera.location
    rot_quat = direction.to_track_quat("-Z", "Y")
    camera.rotation_euler = rot_quat.to_euler()


def _setup_render(
    resolution: int,
    *,
    engine: str = "workbench",
    film_transparent: bool = True,
    workbench_light: str = "STUDIO",
) -> None:
    bpy = _bpy()
    scene = bpy.context.scene
    eng = (engine or "workbench").lower().strip()
    if eng == "eevee":
        # Blender 5.x: EEVEE Next; 3.x/4.x: BLENDER_EEVEE
        for candidate in ("BLENDER_EEVEE_NEXT", "BLENDER_EEVEE"):
            try:
                scene.render.engine = candidate
                break
            except (TypeError, ValueError):
                continue
        else:
            scene.render.engine = "BLENDER_WORKBENCH"
    else:
        scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.film_transparent = film_transparent
    scene.render.resolution_x = resolution
    scene.render.resolution_y = resolution
    scene.render.image_settings.file_format = "PNG"
    scene.render.image_settings.color_mode = "RGBA"
    if scene.render.engine == "BLENDER_WORKBENCH":
        scene.display.shading.background_type = "VIEWPORT"
        scene.display.shading.light = workbench_light if workbench_light in ("FLAT", "STUDIO", "MATCAP") else "STUDIO"
        scene.display.shading.color_type = "MATERIAL"


def _add_camera(
    location: tuple[float, float, float],
    target: tuple[float, float, float],
    *,
    ortho: bool = False,
) -> Any:
    bpy = _bpy()
    bpy.ops.object.camera_add(location=location)
    camera = bpy.context.object
    if ortho:
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = 5.0
    else:
        camera.data.angle = math.radians(40.0)
    camera.data.clip_start = 0.01
    camera.data.clip_end = 100.0
    _look_at(camera, target)
    bpy.context.scene.camera = camera
    return camera


def _auto_frame_camera(camera) -> None:
    """Ajusta a camera para enquadrar todos os objectos da cena."""
    bpy = _bpy()
    from mathutils import Vector

    all_coords = []
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            bbox = [obj.matrix_world @ Vector(c) for c in obj.bound_box]
            all_coords.extend(bbox)

    if not all_coords:
        return

    import numpy as np

    pts = np.array([(v.x, v.y, v.z) for v in all_coords])
    center = pts.mean(axis=0)
    extent = (pts.max(axis=0) - pts.min(axis=0)).max()

    cam_loc = camera.location
    direction = Vector(center) - cam_loc
    dist = direction.length
    if dist < 0.01:
        return

    if camera.data.type == "ORTHO":
        camera.data.ortho_scale = max(float(extent) * 1.4, 0.25)
        return

    needed_dist = extent / (2 * math.tan(camera.data.angle / 2))
    scale = needed_dist / dist if dist > 0 else 1.0
    if scale > 0.3:
        new_loc = Vector(center) - direction.normalized() * needed_dist * 1.3
        camera.location = new_loc
        _look_at(camera, tuple(center))


def _remove_camera(camera) -> None:
    bpy = _bpy()
    bpy.data.objects.remove(camera, do_unlink=True)


def _show_armature_wireframe(visible: bool) -> None:
    bpy = _bpy()
    for obj in bpy.context.scene.objects:
        if obj.type == "ARMATURE":
            obj.show_in_front = visible
            obj.data.display_type = "WIRE" if visible else "OCTAHEDRAL"
            obj.hide_render = not visible
            obj.hide_set(not visible)


def collect_scene_metadata(input_path: Path) -> dict[str, Any]:
    """Recolhe metadados da cena actual (apos import)."""
    from . import bpy_ops

    meta = bpy_ops.inspect_scene()
    meta["input"] = str(input_path)
    meta["file_size_bytes"] = input_path.stat().st_size if input_path.is_file() else 0
    # Alias legível para agentes (totais + lista de meshes)
    meta["mesh"] = {
        "total_vertex_count": meta.get("mesh_totals", {}).get("vertex_count", 0),
        "total_face_count": meta.get("mesh_totals", {}).get("face_count", 0),
        "objects": meta.get("meshes", []),
    }
    meta["animations"] = [
        {
            "name": a["name"],
            "frame_range": [int(a["frame_range"][0]), int(a["frame_range"][1])],
        }
        for a in meta.get("actions", [])
    ]

    return meta


def render_screenshots(
    input_path: Path,
    output_dir: Path,
    *,
    views: list[str] | None = None,
    resolution: int = 512,
    show_bones: bool = False,
    frame: int | None = None,
    frames: list[int] | None = None,
    engine: str = "workbench",
    ortho: bool = False,
    film_transparent: bool = True,
) -> dict[str, Any]:
    """Importa GLB/FBX, renderiza vistas e devolve report JSON.

    * ``frame``: um único frame para todas as vistas (ficheiros ``{view}.png``).
    * ``frames``: vários frames (``{view}_f{NNNN}.png``) para inspeccionar animação.
    """
    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)

    use_frame_list = frames is not None and len(frames) > 0
    if use_frame_list:
        frame_indices: list[int | None] = list(frames)
    elif frame is not None:
        frame_indices = [frame]
    else:
        frame_indices = [None]

    _show_armature_wireframe(show_bones)
    _setup_render(resolution, engine=engine, film_transparent=film_transparent)

    output_dir.mkdir(parents=True, exist_ok=True)
    views = views or DEFAULT_VIEWS
    screenshots = []

    for fi in frame_indices:
        if fi is not None:
            bpy.context.scene.frame_set(int(fi))

        for view_name in views:
            preset = CAMERA_PRESETS.get(view_name)
            if preset is None:
                continue
            loc, target = preset
            camera = _add_camera(loc, target, ortho=ortho)
            _auto_frame_camera(camera)

            if use_frame_list and fi is not None:
                out_path = output_dir / f"{view_name}_f{int(fi):04d}.png"
            else:
                out_path = output_dir / f"{view_name}.png"

            bpy.context.scene.render.filepath = str(out_path)
            bpy.ops.render.render(write_still=True)

            entry: dict[str, Any] = {"view": view_name, "path": str(out_path)}
            if fi is not None:
                entry["frame"] = int(fi)
            screenshots.append(entry)
            _remove_camera(camera)

    meta = collect_scene_metadata(input_path)
    meta["screenshots"] = screenshots
    meta["render_settings"] = {
        "resolution": resolution,
        "show_bones": show_bones,
        "frame": frame,
        "frames": frames,
        "engine": engine,
        "ortho": ortho,
        "film_transparent": film_transparent,
    }

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    meta["report_path"] = str(report_path)
    return meta


def render_weight_heatmap(
    input_path: Path,
    output_dir: Path,
    bone_name: str,
    *,
    views: list[str] | None = None,
    resolution: int = 512,
    engine: str = "workbench",
    ortho: bool = False,
    film_transparent: bool = True,
) -> dict[str, Any]:
    """Renderiza heatmap de pesos de um osso especifico."""
    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.clear_scene()
    bpy_ops.import_asset(input_path)

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH" or not obj.vertex_groups:
            continue
        vg = obj.vertex_groups.get(bone_name)
        if vg is None:
            continue

        if not obj.data.vertex_colors:
            obj.data.vertex_colors.new(name="WeightHeatmap")
        color_layer = obj.data.vertex_colors["WeightHeatmap"]

        for poly in obj.data.polygons:
            for li in poly.loop_indices:
                vi = obj.data.loops[li].vertex_index
                try:
                    w = vg.weight(vi)
                except RuntimeError:
                    w = 0.0
                # Blue (0) → Green (0.5) → Red (1)
                if w < 0.5:
                    r, g, b = 0.0, w * 2, 1.0 - w * 2
                else:
                    r, g, b = (w - 0.5) * 2, 1.0 - (w - 0.5) * 2, 0.0
                color_layer.data[li].color = (r, g, b, 1.0)

        obj.data.update()

    _setup_render(resolution, engine=engine, film_transparent=film_transparent)
    bpy.context.scene.display.shading.color_type = "VERTEX"
    _show_armature_wireframe(True)

    output_dir.mkdir(parents=True, exist_ok=True)
    views = views or DEFAULT_VIEWS
    screenshots = []

    for view_name in views:
        preset = CAMERA_PRESETS.get(view_name)
        if preset is None:
            continue
        loc, target = preset
        camera = _add_camera(loc, target, ortho=ortho)
        _auto_frame_camera(camera)

        out_path = output_dir / f"weights_{bone_name}_{view_name}.png"
        bpy.context.scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        screenshots.append({"view": view_name, "bone": bone_name, "path": str(out_path)})
        _remove_camera(camera)

    meta = collect_scene_metadata(input_path)
    meta["weight_heatmap"] = {"bone": bone_name, "screenshots": screenshots}

    report_path = output_dir / f"weights_{bone_name}_report.json"
    report_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    meta["report_path"] = str(report_path)
    return meta
