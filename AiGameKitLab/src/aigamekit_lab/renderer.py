"""Native bpy EEVEE/Workbench screenshot rendering for GLB files.

Provides headless multi-angle PNG rendering using bpy directly,
without delegating to Animator3D via subprocess. Includes weight-painting
heatmaps, turntable GIFs, and material inspection — all fully native.
"""

from __future__ import annotations

import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from aigamekit_lab.glb_import import import_glb

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


def parse_views(views: str | None) -> list[str]:
    """Separa e valida nomes de vista separados por vírgula.

    Substitui o padrão antigo que saltava nomes desconhecidos em silêncio e
    devolvia "0 screenshots" com exit 0 (um ``--views 2`` produzia um
    report.json vazio sem qualquer aviso).

    Args:
        views: Nomes separados por vírgula (ex.: ``front,three_quarter``).
            Vazio/None devolve as vistas por omissão.

    Returns:
        Lista de nomes válidos, sem duplicados de whitespace.

    Raises:
        ValueError: Algum nome não existe em ``CAMERA_PRESETS``; a mensagem
            lista os inválidos e as vistas válidas.
    """
    names = [v.strip() for v in (views or "").split(",") if v.strip()]
    if not names:
        return list(DEFAULT_VIEWS)
    unknown = [v for v in names if v not in CAMERA_PRESETS]
    if unknown:
        raise ValueError(f"vista(s) desconhecida(s): {', '.join(unknown)} — válidas: {', '.join(ALL_VIEWS)}")
    return names


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
        # TEXTURE shows the base-color map (falls back to material color
        # when the mesh has no texture); MATERIAL ignores textures entirely.
        scene.display.shading.color_type = "TEXTURE"
    else:
        _ensure_scene_lighting()


def _ensure_scene_lighting() -> None:
    """Give EEVEE renders light: GLB scenes usually ship no lights/world."""
    bpy = _require_bpy()
    scene = bpy.context.scene
    if scene.world is None:
        scene.world = bpy.data.worlds.new("DebugWorld")
    scene.world.use_nodes = True
    bg = scene.world.node_tree.nodes.get("Background")
    if bg is not None:
        bg.inputs[0].default_value = (0.9, 0.9, 0.9, 1.0)
        bg.inputs[1].default_value = 1.0
    if not any(obj.type == "LIGHT" for obj in scene.objects):
        bpy.ops.object.light_add(type="SUN", location=(3, -3, 5))
        sun = bpy.context.object
        sun.data.energy = 3.0
        sun.rotation_euler = (math.radians(45), 0, math.radians(35))


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


def weight_to_color(w: float) -> tuple[float, float, float]:
    """Map a vertex weight [0..1] to a Blue→Green→Red color (2-segment ramp).

    Matches Animator3D's weight heatmap palette (no yellow): w=0→Blue,
    w=0.5→Green, w=1→Red. Extracted as pure function for unit testing.

    Args:
        w: Vertex weight clamped to [0, 1].

    Returns:
        ``(r, g, b)`` tuple, each in [0, 1].
    """
    w = max(0.0, min(1.0, float(w)))
    if w < 0.5:
        return (0.0, w * 2.0, 1.0 - w * 2.0)
    return ((w - 0.5) * 2.0, 1.0 - (w - 0.5) * 2.0, 0.0)


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
    # validar vistas antes de tocar na cena/GLB — falhar cedo em --views errado
    view_names = parse_views(views)

    from aigamekit_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

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

    from aigamekit_lab.debug_tools import _enrich_inspect_data, _inspect_scene

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


def render_weight_heatmap(
    glb_path: str | Path,
    output_dir: str | Path,
    bone_name: str,
    *,
    views: str = "front,three_quarter,right,back",
    resolution: int = 512,
    engine: str = "workbench",
    ortho: bool = False,
    transparent_film: bool = True,
) -> dict[str, Any]:
    """Render weight-painting heatmaps for a bone using native bpy.

    For each mesh with a vertex group matching ``bone_name``, paints a
    vertex color layer (Blue→Green→Red ramp via :func:`weight_to_color`)
    and renders views with workbench ``color_type="VERTEX"``. Bones are
    shown as wireframe overlay on every view.

    Args:
        glb_path: Path to GLB/GLTF file.
        output_dir: Directory to write PNGs and report into.
        bone_name: Vertex group / bone name for weight lookup.
        views: Comma-separated view names.
        resolution: Render resolution in pixels (square).
        engine: ``"workbench"`` or ``"eevee"``.
        ortho: Use orthographic camera.
        transparent_film: Render with transparent background.

    Returns:
        Report dict with ``weight_heatmap`` ({bone, screenshots, render_settings}).
    """
    # validar vistas antes de tocar na cena/GLB — falhar cedo em --views errado
    view_names = parse_views(views)

    from aigamekit_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

    painted_meshes: list[str] = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        vg = obj.vertex_groups.get(bone_name)
        if vg is None:
            continue
        mesh = obj.data
        layer = mesh.vertex_colors.get("WeightHeatmap")
        if layer is None:
            layer = mesh.vertex_colors.new(name="WeightHeatmap")
        colors = layer.data
        loops = mesh.loops
        for li, loop in enumerate(loops):
            vi = loop.vertex_index
            try:
                w = float(vg.weight(vi))
            except (RuntimeError, TypeError):
                w = 0.0
            r, g, b = weight_to_color(w)
            colors[li].color = (r, g, b, 1.0)
        mesh.update()
        painted_meshes.append(obj.name)

    _show_armature_wireframe(True)
    _setup_render(resolution, engine=engine, film_transparent=transparent_film)
    bpy.context.scene.display.shading.color_type = "VERTEX"

    safe_bone = bone_name.replace("/", "_").replace(" ", "_")

    screenshots: list[dict[str, Any]] = []
    for view_name in view_names:
        preset = CAMERA_PRESETS.get(view_name)
        if preset is None:
            continue
        loc, target = preset
        camera = _add_camera(loc, target, ortho=ortho)
        _auto_frame_camera(camera)

        out_path = output_dir / f"weights_{safe_bone}_{view_name}.png"
        bpy.context.scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        screenshots.append({"view": view_name, "bone": bone_name, "path": str(out_path)})
        _remove_camera(camera)

    from aigamekit_lab.debug_tools import _enrich_inspect_data, _inspect_scene

    meta = _inspect_scene()
    _enrich_inspect_data(meta, glb_path)
    meta["weight_heatmap"] = {
        "bone": bone_name,
        "painted_meshes": painted_meshes,
        "screenshots": screenshots,
        "render_settings": {
            "resolution": resolution,
            "engine": engine,
            "ortho": ortho,
            "film_transparent": transparent_film,
        },
    }
    report_path = output_dir / f"weights_{safe_bone}_report.json"
    report_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    meta["report_path"] = str(report_path)
    return meta


def render_turntable(
    glb_path: str | Path,
    output_path: str | Path,
    *,
    frames: int = 24,
    resolution: int = 384,
    engine: str = "workbench",
    ortho: bool = False,
    transparent_film: bool = True,
    show_bones: bool = False,
    frame_duration_ms: int = 120,
) -> dict[str, Any]:
    """Render a 360° turntable GIF of a GLB using native bpy.

    Orbits the camera around the model in ``frames`` steps, renders each
    PNG, then combines them into a single looping GIF via Pillow.

    Args:
        glb_path: Path to GLB/GLTF file.
        output_path: Output ``.gif`` path.
        frames: Number of rotation steps (≥4).
        resolution: Render resolution in pixels (square).
        engine: ``"workbench"`` or ``"eevee"``.
        ortho: Use orthographic camera.
        transparent_film: Render with transparent background.
        show_bones: Show armature wireframe overlay.
        frame_duration_ms: Per-frame duration in the GIF.

    Returns:
        Dict with ``path``, ``frames``, ``resolution``.
    """
    from mathutils import Vector

    from aigamekit_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path).expanduser().resolve()
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(4, int(frames))

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

    _show_armature_wireframe(show_bones)
    _setup_render(resolution, engine=engine, film_transparent=transparent_film)

    # Compute orbit center + radius from mesh bounds (numpy).
    import numpy as np

    all_coords = []
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            all_coords.extend([obj.matrix_world @ Vector(c) for c in obj.bound_box])
    pts = np.array([(v.x, v.y, v.z) for v in all_coords]) if all_coords else np.array([[0.0, 0.0, 0.5]])
    center = pts.mean(axis=0)
    extent = float((pts.max(axis=0) - pts.min(axis=0)).max())
    radius = max(extent * 1.3, 2.0)

    tmp_dir = Path(tempfile.mkdtemp(prefix="aigamekitlab_turntable_"))
    png_paths: list[str] = []
    try:
        for i in range(frames):
            angle = 2.0 * math.pi * i / frames
            loc_x = float(center[0] + radius * math.sin(angle))
            loc_y = float(center[1] - radius * math.cos(angle))
            loc_z = float(center[2] + extent * 0.35)
            target = (float(center[0]), float(center[1]), float(center[2]))
            camera = _add_camera((loc_x, loc_y, loc_z), target, ortho=ortho)
            _auto_frame_camera(camera)

            out_png = tmp_dir / f"frame_{i:04d}.png"
            bpy.context.scene.render.filepath = str(out_png)
            bpy.ops.render.render(write_still=True)
            png_paths.append(str(out_png))
            _remove_camera(camera)

        from PIL import Image

        images = [Image.open(p).convert("RGBA") for p in png_paths]
        images[0].save(
            str(output_path),
            save_all=True,
            append_images=images[1:],
            loop=0,
            duration=frame_duration_ms,
            disposal=2,
        )
        for im in images:
            im.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    return {
        "path": str(output_path),
        "frames": frames,
        "resolution": resolution,
        "engine": engine,
        "ortho": ortho,
        "show_bones": show_bones,
    }


def render_inspect_material(
    glb_path: str | Path,
    output_dir: str | Path,
    *,
    views: str = "front,three_quarter,right",
    resolution: int = 512,
    engine: str = "eevee",
    ortho: bool = False,
    transparent_film: bool = True,
) -> dict[str, Any]:
    """Render views + dump material/texture metadata using native bpy.

    Uses EEVEE by default to capture PBR materials faithfully. Returns
    material info (Principled BSDF inputs, image textures, colorspace,
    wrap modes) alongside screenshots.

    Args:
        glb_path: Path to GLB/GLTF file.
        output_dir: Directory to write PNGs and report into.
        views: Comma-separated view names.
        resolution: Render resolution in pixels (square).
        engine: ``"workbench"`` or ``"eevee"`` (default eevee for PBR).
        ortho: Use orthographic camera.
        transparent_film: Render with transparent background.

    Returns:
        Report dict with ``materials``, ``screenshots``, and metadata.
    """
    # validar vistas antes de tocar na cena/GLB — falhar cedo em --views errado
    view_names = parse_views(views)

    from aigamekit_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

    from aigamekit_lab.debug_tools import _enrich_inspect_data, _inspect_scene, inspect_materials

    meta = _inspect_scene()
    _enrich_inspect_data(meta, glb_path)
    meta["materials"] = inspect_materials()

    _setup_render(resolution, engine=engine, film_transparent=transparent_film)

    screenshots: list[dict[str, Any]] = []
    for view_name in view_names:
        preset = CAMERA_PRESETS.get(view_name)
        if preset is None:
            continue
        loc, target = preset
        camera = _add_camera(loc, target, ortho=ortho)
        _auto_frame_camera(camera)
        out_path = output_dir / f"material_{view_name}.png"
        bpy.context.scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        screenshots.append({"view": view_name, "path": str(out_path)})
        _remove_camera(camera)

    meta["screenshots"] = screenshots
    meta["render_settings"] = {
        "resolution": resolution,
        "engine": engine,
        "ortho": ortho,
        "film_transparent": transparent_film,
    }
    report_path = output_dir / "material_report.json"
    report_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n")
    meta["report_path"] = str(report_path)
    return meta
