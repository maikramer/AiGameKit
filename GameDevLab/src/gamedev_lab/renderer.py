"""Native bpy EEVEE/Workbench screenshot rendering for GLB files.

Provides headless multi-angle PNG rendering using bpy directly,
without delegating to Animator3D via subprocess.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

# Camera presets: (location, look_at_target) — Y-up glTF convention.
CAMERA_PRESETS: dict[str, tuple[tuple[float, float, float], tuple[float, float, float]]] = {
    "front": ((0, -4, 1.2), (0, 0, 0.5)),
    "back": ((0, 4, 1.2), (0, 0, 0.5)),
    "left": ((-4, 0, 1.2), (0, 0, 0.5)),
    "right": ((4, 0, 1.2), (0, 0, 0.5)),
    "top": ((0, -0.01, 5), (0, 0, 0.5)),
    "three_quarter": ((3, -3, 2.5), (0, 0, 0.3)),
    "low_front": ((0, -4.5, 0.45), (0, 0, 0.35)),
    "worm": ((0, -2.2, 0.2), (0, 0, 0.65)),
}

ALL_VIEWS = list(CAMERA_PRESETS.keys())
DEFAULT_VIEWS = ["front", "three_quarter", "right", "back"]


def _require_bpy():
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required for rendering. Install with: pip install bpy") from None


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
) -> None:
    bpy = _require_bpy()
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
        scene.display.shading.light = "STUDIO"
        scene.display.shading.color_type = "MATERIAL"


def _add_camera(
    location: tuple[float, float, float],
    target: tuple[float, float, float],
    *,
    ortho: bool = False,
) -> Any:
    bpy = _require_bpy()
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
    """Adjust camera distance to frame all mesh objects in the scene."""
    bpy = _require_bpy()
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
    bpy = _require_bpy()
    bpy.data.objects.remove(camera, do_unlink=True)


def _show_armature_wireframe(visible: bool) -> None:
    bpy = _require_bpy()
    for obj in bpy.context.scene.objects:
        if obj.type == "ARMATURE":
            obj.show_in_front = visible
            obj.data.display_type = "WIRE" if visible else "OCTAHEDRAL"
            obj.hide_render = not visible
            obj.hide_set(not visible)


def render_screenshots(
    glb_path: str | Path,
    output_dir: str | Path,
    *,
    views: str = "front,three_quarter,right,back",
    resolution: int = 512,
    engine: str = "workbench",
    ortho: bool = False,
    transparent_film: bool = True,
    show_bones: bool = False,
    frame: int | None = None,
    frame_list: str | None = None,
) -> dict[str, Any]:
    """Render multi-angle screenshots of a GLB file using native bpy.

    Opens the GLB in bpy (headless), positions camera for each view,
    renders via EEVEE or Workbench, saves PNGs, exports metadata.
    Returns dict with screenshot paths and metadata (matching Animator3D
    screenshot report format for CLI compatibility).

    Args:
        glb_path: Path to GLB/GLTF file.
        output_dir: Directory to write PNG screenshots into.
        views: Comma-separated view names (e.g. ``"front,back,right"``).
        resolution: Render resolution in pixels (square).
        engine: ``"workbench"`` or ``"eevee"``.
        ortho: Use orthographic camera.
        transparent_film: Render with transparent background.
        show_bones: Show armature wireframe overlay.
        frame: Single frame number for all views.
        frame_list: Comma-separated frame numbers (e.g. ``"1,36,72"``).

    Returns:
        Report dict with ``screenshots``, ``world_bounds``, ``mesh``,
        ``animations``, and render settings.
    """
    from gamedev_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = _require_bpy()
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(glb_path))

    view_names = [v.strip() for v in views.split(",") if v.strip()]
    if not view_names:
        view_names = DEFAULT_VIEWS

    frames: list[int | None] = []
    use_frame_list = frame_list is not None
    if use_frame_list:
        frames = [int(f.strip()) for f in frame_list.split(",") if f.strip()]
    elif frame is not None:
        frames = [frame]
    else:
        frames = [None]

    _show_armature_wireframe(show_bones)
    _setup_render(resolution, engine=engine, film_transparent=transparent_film)

    screenshots: list[dict[str, Any]] = []
    for fi in frames:
        if fi is not None:
            bpy.context.scene.frame_set(int(fi))

        for view_name in view_names:
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

    from gamedev_lab.debug_tools import _enrich_inspect_data, _inspect_scene

    meta = _inspect_scene()
    _enrich_inspect_data(meta, glb_path)
    meta["screenshots"] = screenshots
    meta["render_settings"] = {
        "resolution": resolution,
        "show_bones": show_bones,
        "frame": frame,
        "frames": [int(f) for f in frames if f is not None] if use_frame_list else None,
        "engine": engine,
        "ortho": ortho,
        "film_transparent": transparent_film,
    }

    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    meta["report_path"] = str(report_path)

    return meta
