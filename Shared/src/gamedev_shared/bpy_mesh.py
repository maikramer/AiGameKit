"""Shared bpy mesh utilities — load, save, query, clear scene.

Provides I/O helpers that use bpy as the backend instead of trimesh,
plus conversion functions for trimesh compatibility at package boundaries.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _require_bpy():
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required but not installed. Install with: pip install bpy") from None


def load_glb(path: str | Path) -> list:
    """Import GLB/GLTF via bpy, return all imported mesh objects.

    Clears scene before import to avoid object pollution.
    Preserves transforms, armatures, shape keys, materials.
    """
    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()
    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(path))
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def apply_smooth_by_angle(obj: Any, degrees: float = 60.0) -> None:
    """Smooth-shade *obj*, keeping hard edges only above *degrees*.

    Blender 4.1 removed ``mesh.use_auto_smooth`` / ``auto_smooth_angle`` in
    favour of the ``object.shade_smooth_by_angle`` operator. Older code that
    still set the removed attributes (wrapped in ``suppress``) silently did
    nothing on bpy 5.x, leaving meshes fully smooth. This wrapper applies the
    angle correctly on both APIs.

    A higher angle (default 60°) is friendlier to *organic* assets and their
    decimated LODs — only genuinely sharp creases stay hard, instead of the
    old 30° which faceted gently-curved surfaces.
    """
    import contextlib
    import math

    bpy = _require_bpy()
    mesh = obj.data
    for poly in mesh.polygons:
        poly.use_smooth = True

    angle = math.radians(degrees)
    if hasattr(bpy.ops.object, "shade_smooth_by_angle"):
        with contextlib.suppress(RuntimeError, TypeError):
            bpy.ops.object.select_all(action="DESELECT")
            obj.select_set(True)
            bpy.context.view_layer.objects.active = obj
            bpy.ops.object.shade_smooth_by_angle(angle=angle)
            return
    # Legacy bpy (< 4.1)
    with contextlib.suppress(AttributeError):
        mesh.use_auto_smooth = True
        mesh.auto_smooth_angle = angle


def _needs_tangents(objects: Any) -> bool:
    """Whether any mesh in *objects* has both UVs and a normal-map material.

    Tangents only matter for tangent-space normal maps; computing them when no
    normal map is present just splits vertices at UV seams for nothing.
    """
    for obj in objects:
        if getattr(obj, "type", None) != "MESH":
            continue
        if not obj.data.uv_layers:
            continue
        for mat in obj.data.materials:
            if mat is None or not getattr(mat, "use_nodes", False):
                continue
            if any(n.type == "NORMAL_MAP" for n in mat.node_tree.nodes):
                return True
    return False


def save_glb(objects, path: str | Path, **kwargs: Any) -> None:
    """Export scene/objects to GLB via bpy native exporter.

    Preserves armature, skinning, animations, materials, UVs.

    Defaults are conservative for game assets:

    - ``export_image_format="JPEG"``: textures saved as JPEG (3-5 MB total for a
      typical 2048² PBR set vs 30-40 MB as PNG).
    - ``export_normals=True``: normals are kept (turn off for shape-only stages
      where they will be recomputed downstream).
    - ``export_tangents=True``: MikkTSpace tangents are written so tangent-space
      normal maps render without seams across UV islands (and stay correct when
      a skinned mesh deforms). Automatically disabled when ``export_normals`` is
      off, since tangents without normals are meaningless.
    - ``export_all_influences=False``: skin weights limited to the 4 most
      influential joints per vertex (GLTF standard); avoids extra
      ``JOINTS_n/WEIGHTS_n`` attribute sets.

    Any keyword passed via ``**kwargs`` overrides the corresponding default and
    is forwarded to ``bpy.ops.export_scene.gltf``. This is what callers like
    Paint3D rely on to enforce JPEG / no-normals on their stage outputs.
    """
    import contextlib
    import io

    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    if objects is not None:
        if not isinstance(objects, (list, tuple)):
            objects = [objects]
        bpy.ops.object.select_all(action="DESELECT")
        for o in objects:
            o.select_set(True)
        use_selection = True
    else:
        use_selection = False

    export_kwargs: dict[str, Any] = {
        "filepath": str(path),
        "export_apply": True,
        "export_animations": True,
        "export_skins": True,
        "export_morph": True,
        "export_normals": True,
        "export_tangents": True,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "JPEG",
        "export_keep_originals": False,
        "export_all_influences": False,
        "use_selection": use_selection,
    }
    export_kwargs.update(kwargs)

    # Tangents are only needed to render a tangent-space *normal map*, and
    # exporting them splits vertices at UV seams. So enable them only when a
    # mesh actually has both UVs and a normal-map material — otherwise plain
    # geometry would be needlessly inflated (e.g. a cube 8→24 verts).
    if export_kwargs.get("export_tangents") and export_kwargs.get("export_normals", True):
        candidates = objects if objects else bpy.context.scene.objects
        export_kwargs["export_tangents"] = _needs_tangents(candidates)
    else:
        export_kwargs["export_tangents"] = False

    # Suppress bpy stdout spam
    stdout = io.StringIO()
    with contextlib.redirect_stdout(stdout):
        bpy.ops.export_scene.gltf(**export_kwargs)


def get_mesh_objects() -> list:
    """Return all mesh objects in current scene."""
    bpy = _require_bpy()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def get_bounds(obj) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """World-space AABB of *obj* as (min_corner, max_corner)."""
    _require_bpy()
    verts_world = [obj.matrix_world @ v.co for v in obj.data.vertices]
    if not verts_world:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xs = [v.x for v in verts_world]
    ys = [v.y for v in verts_world]
    zs = [v.z for v in verts_world]
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def face_count(obj) -> int:
    """Total polygon count of *obj*."""
    return len(obj.data.polygons)


def vertex_count(obj) -> int:
    """Total vertex count of *obj*."""
    return len(obj.data.vertices)


def clear_scene() -> None:
    """Delete ALL objects in current scene (canonical Blender reset).

    Uses ``bpy.ops.wm.read_factory_settings(use_empty=True)`` to reset
    to a clean state — removes objects, meshes, armatures, cameras, lights,
    materials, images, textures, shape keys.
    """
    bpy = _require_bpy()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def load_any(path: str | Path) -> list:
    """Dispatch to GLB/GLTF or PLY importer based on file extension."""
    bpy = _require_bpy()
    path = Path(path).expanduser().resolve()
    clear_scene()
    ext = path.suffix.lower()
    if ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif ext == ".ply":
        bpy.ops.import_mesh.ply(filepath=str(path))
    else:
        raise ValueError(f"Unsupported format: {ext}")
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


# ---------------------------------------------------------------------------
# Conversion helpers — numpy arrays ↔ bpy meshes (no trimesh dependency)
# ---------------------------------------------------------------------------


def create_mesh_from_arrays(
    vertices: Any,
    faces: Any,
    name: str = "Mesh",
) -> Any:
    """Create a bpy mesh object from numpy-compatible vertex/face arrays.

    Args:
        vertices: (N, 3) array-like of vertex positions.
        faces: (M, K) array-like of face indices (triangles or quads).
        name: Object/mesh name in Blender.

    Returns:
        The created bpy object.
    """
    import numpy as np

    bpy = _require_bpy()

    verts_np = np.asarray(vertices, dtype=np.float64)
    faces_np = np.asarray(faces, dtype=np.int64)

    mesh_data = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh_data)
    bpy.context.collection.objects.link(obj)

    mesh_data.from_pydata(verts_np.tolist(), [], faces_np.tolist())
    mesh_data.update()
    return obj


def apply_face_colors(obj: Any, face_colors: Any) -> None:
    """Apply per-face RGB colors as a vertex color (color attribute) layer.

    Args:
        obj: bpy mesh object (must have polygons).
        face_colors: (F, 3) uint8 array of RGB colours, one per face.
    """
    import numpy as np

    _require_bpy()
    mesh = obj.data
    colors = np.asarray(face_colors, dtype=np.float64) / 255.0

    # Use modern color_attributes API (Blender 4.x+ / bpy 4.x+)
    if hasattr(mesh, "color_attributes") and hasattr(mesh.color_attributes, "new"):
        color_attr = mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="CORNER")
    elif hasattr(mesh, "vertex_colors") and hasattr(mesh.vertex_colors, "new"):
        color_attr = mesh.vertex_colors.new(name="Col")
    else:
        raise RuntimeError("bpy mesh has no color_attributes or vertex_colors API")

    for i, poly in enumerate(mesh.polygons):
        r, g, b = float(colors[i, 0]), float(colors[i, 1]), float(colors[i, 2])
        for loop_idx in poly.loop_indices:
            color_attr.data[loop_idx].color = (r, g, b, 1.0)


def save_empty_glb(path: str | Path) -> None:
    """Export an empty GLB (no geometry). Useful as placeholder."""
    clear_scene()
    save_glb(None, path)


def save_colored_mesh(mesh: Any, face_colors: Any, path: str | Path) -> None:
    """Save a mesh-like object with per-face colours as GLB via bpy.

    *mesh* only needs ``.vertices`` (Nx3) and ``.faces`` (MxK) attributes.
    """
    import numpy as np

    clear_scene()
    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    obj = create_mesh_from_arrays(verts, faces)
    apply_face_colors(obj, np.asarray(face_colors))
    save_glb([obj], path)


def save_scene_geometries(scene: Any, path: str | Path) -> None:
    """Save a trimesh.Scene-like object as GLB via bpy.

    Iterates *scene.geometry* (dict of name → mesh-like with .vertices/.faces)
    and exports all meshes.
    """
    import numpy as np

    clear_scene()
    for name, geom in scene.geometry.items():
        verts = np.asarray(geom.vertices)
        faces = np.asarray(geom.faces)
        create_mesh_from_arrays(verts, faces, name=str(name))
    save_glb(None, path)


def load_mesh_as_trimesh(path: str | Path):
    """Load mesh via bpy, return trimesh.Trimesh for pipeline compatibility.

    Lazy-imports trimesh internally so the calling module stays trimesh-free.
    Used at package boundaries where the pipeline still expects trimesh input.
    """
    import numpy as np
    import trimesh

    bpy = _require_bpy()
    objs = load_glb(path)
    if not objs:
        raise ValueError(f"No mesh objects found in {path}")
    obj = objs[0]

    depsgraph = bpy.context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    mesh_eval = obj_eval.to_mesh()

    verts = np.array([tuple(v.co) for v in mesh_eval.vertices], dtype=np.float64)
    faces = np.array([tuple(p.vertices) for p in mesh_eval.polygons], dtype=np.int64)

    obj_eval.to_mesh_clear()
    return trimesh.Trimesh(vertices=verts, faces=faces, process=False)
