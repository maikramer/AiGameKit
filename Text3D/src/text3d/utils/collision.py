"""Geração de mesh de colisão (convex hull simplificado) para física em engines de jogo."""

from __future__ import annotations

from pathlib import Path

from .export import _export_glb_with_normals, _load_as_trimesh


def generate_collision_mesh(
    input_path: Path,
    output_path: Path,
    *,
    max_faces: int = 300,
    convex_hull: bool = True,
) -> Path:
    """Generate a simplified collision mesh from any GLB/OBJ/PLY.

    Pipeline:
    1. Load mesh
    2. Optionally compute convex hull
    3. Aggressive quadric decimation to ``max_faces``
    4. Export as GLB

    Args:
        input_path: Source mesh file.
        output_path: Destination GLB.
        max_faces: Target face count (minimum 4).
        convex_hull: Compute convex hull before simplification.

    Returns:
        Path to the written collision GLB.

    Raises:
        ValueError: Mesh has fewer than 4 faces.
    """
    mesh = _load_as_trimesh(input_path)
    n = len(mesh.faces)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); collision não aplicável.")

    if convex_hull:
        mesh = mesh.convex_hull

    from .mesh_lod import simplify_to_face_count

    mesh = simplify_to_face_count(mesh, max(4, max_faces))

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _export_glb_with_normals(mesh, output_path)
    return output_path
