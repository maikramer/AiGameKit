"""Carregamento e exportação de meshes 3D (GLB/GLTF via bpy)."""

from __future__ import annotations

from pathlib import Path

from gamedev_shared.bpy_mesh import load_glb
from gamedev_shared.bpy_mesh import save_glb as _bpy_save_glb

_MERGE_THRESHOLD = 2e-4


def load_mesh_bpy(path: str | Path) -> list:
    """Carrega GLB/GLTF via bpy e devolve lista de mesh objects."""
    return load_glb(path)


def _merge_duplicates_bmesh(obj, threshold: float = _MERGE_THRESHOLD) -> None:
    """Merge duplicate vertices (delegado em ``gamedev_shared.mesh_repair``)."""
    import logging

    from gamedev_shared.mesh_repair import remove_doubles

    before = len(obj.data.vertices)
    removed = remove_doubles(obj, threshold=threshold)
    logging.getLogger("paint3d.save_glb").info("bmesh merge: %d → %d verts", before, before - removed)


def save_glb(objects, output_path: str | Path) -> Path:
    """Exporta mesh objects como GLB com merge de vértices, sem normals, JPEG."""
    if not isinstance(objects, (list, tuple)):
        objects = [objects]

    for obj in objects:
        if obj.type == "MESH" and obj.data.uv_layers:
            _merge_duplicates_bmesh(obj)

    _bpy_save_glb(
        objects,
        output_path,
        export_normals=False,
        export_image_format="JPEG",
    )
    return output_path


load_mesh_trimesh = load_mesh_bpy
