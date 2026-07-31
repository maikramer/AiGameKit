"""Carregamento e exportação de meshes 3D (GLB/GLTF via bpy canónico)."""

from __future__ import annotations

from pathlib import Path

from aigamekit_shared.bpy_mesh import load_glb
from aigamekit_shared.bpy_mesh import save_glb as _bpy_save_glb

_MERGE_THRESHOLD = 2e-4


def load_mesh_bpy(path: str | Path) -> list:
    """Carrega GLB/GLTF via bpy e devolve lista de mesh objects."""
    return load_glb(path)


def _merge_duplicates_bmesh(obj, threshold: float = _MERGE_THRESHOLD) -> None:
    """Merge duplicate vertices (delegado em ``aigamekit_shared.mesh_repair``)."""
    import logging

    from aigamekit_shared.mesh_repair import remove_doubles

    before = len(obj.data.vertices)
    removed = remove_doubles(obj, threshold=threshold)
    logging.getLogger("paint3d.save_glb").info("bmesh merge: %d → %d verts", before, before - removed)


def save_glb(objects, output_path: str | Path) -> Path:
    """Exporta mesh objects via ``aigamekit_shared.bpy_mesh.save_glb``.

    Mesmo contrato que Text3D/Rigging/Animator: shade-smooth + NORMAL+TANGENT
    + JPEG. Merge de duplicados só quando há UVs (costuras de atlas).
    """
    if not isinstance(objects, (list, tuple)):
        objects = [objects]

    mesh_objs = [obj for obj in objects if getattr(obj, "type", None) == "MESH"]
    for obj in mesh_objs:
        if obj.data.uv_layers:
            _merge_duplicates_bmesh(obj)

    from aigamekit_shared.bpy_mesh import smooth_shade_scene

    # 180°: painted cartoon — sem creases duros que o exporter parta em seams.
    smooth_shade_scene(mesh_objs, degrees=180.0)

    _bpy_save_glb(
        objects,
        output_path,
        export_normals=True,
        export_tangents=True,
        export_image_format="JPEG",
        verify_stage="painted",
    )
    return Path(output_path)


load_mesh_trimesh = load_mesh_bpy
