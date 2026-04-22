"""Carregamento e exportação de meshes 3D (GLB/OBJ/PLY)."""

from __future__ import annotations

from pathlib import Path

import trimesh


def load_mesh_trimesh(path: str | Path) -> trimesh.Trimesh:
    """Carrega GLB/OBJ/PLY e devolve um único Trimesh (fundir cenas)."""
    path = Path(path)
    loaded = trimesh.load(str(path), force=None)
    if isinstance(loaded, trimesh.Scene):
        return loaded.dump(concatenate=True)
    if isinstance(loaded, trimesh.Trimesh):
        return loaded
    raise TypeError(f"Formato não suportado: {type(loaded)}")


def save_glb(mesh: trimesh.Trimesh, output_path: str | Path) -> Path:
    """Exporta mesh como GLB com vertex normals e doubleSided."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    _ = mesh.vertex_normals
    if hasattr(mesh, "visual") and hasattr(mesh.visual, "material"):
        mesh.visual.material.doubleSided = True
    scene = trimesh.Scene(geometry={"mesh": mesh})
    glb_bytes = scene.export(file_type="glb", include_normals=True)
    with open(str(output_path), "wb") as f:
        f.write(glb_bytes)
    return output_path
