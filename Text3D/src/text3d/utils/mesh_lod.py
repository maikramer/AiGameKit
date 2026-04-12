"""Geração de variantes LOD (níveis de detalhe) de meshes GLB para uso offline e em runtime."""

from __future__ import annotations

from pathlib import Path

import trimesh

from .export import _export_glb_with_normals, _load_as_trimesh
from .mesh_repair import prepare_mesh_topology


def apply_lod_meshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Opcional: só ``fill_small_boundaries`` via ``pymeshfix_mesh_repair_only``."""
    from .mesh_repair import pymeshfix_mesh_repair_only

    return pymeshfix_mesh_repair_only(mesh)


def _require_fast_simplification() -> None:
    try:
        import fast_simplification  # noqa: F401
    except ImportError as e:
        msg = (
            "LOD requer o pacote 'fast-simplification' (usado pelo trimesh). "
            "Instala: pip install 'fast-simplification>=0.1.7'"
        )
        raise RuntimeError(msg) from e


def simplify_to_face_count(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    """Quadric edge collapse até ~target_faces (mínimo 4 faces)."""
    _require_fast_simplification()
    n = len(mesh.faces)
    if n <= 4:
        return mesh.copy()
    t = max(4, min(int(target_faces), n - 1))
    if t >= n:
        return mesh.copy()
    return mesh.simplify_quadric_decimation(face_count=t)


def generate_lod_glb_triplet(
    input_path: Path,
    output_dir: Path,
    basename: str,
    *,
    lod1_ratio: float = 0.42,
    lod2_ratio: float = 0.14,
    min_faces_lod1: int = 500,
    min_faces_lod2: int = 150,
    meshfix: bool = False,
) -> list[Path]:
    """Gera três GLB: ``{basename}_lod0.glb`` … ``{basename}_lod2.glb``.

    * LOD0 — mesma resolução que a fonte após :func:`prepare_mesh_topology`
      (vértices fundidos / manifold); não é a mesh crua do disco.
    * LOD1 — ~``lod1_ratio`` das faces (mínimo ``min_faces_lod1``).
    * LOD2 — ~``lod2_ratio`` das faces (mínimo ``min_faces_lod2``).

    Por defeito **não** aplica pymeshfix: decimação deixa malhas abertas mas o ``clean()`` do
    PyTMesh costuma destruir troncos/LOD2. Com ``meshfix=True`` aplica-se só
    ``fill_small_boundaries`` (buracos pequenos), nunca ``clean()``.

    Args:
        input_path: GLB/OBJ/PLY de entrada.
        output_dir: Pasta de saída (criada se não existir).
        basename: Prefixo dos ficheiros (ex.: ``tree_lowpoly``).
        lod1_ratio: Rácio alvo de faces para LOD1 (0-1).
        lod2_ratio: Rácio alvo de faces para LOD2 (0-1).
        min_faces_lod1: Piso de faces para LOD1.
        min_faces_lod2: Piso de faces para LOD2.
        meshfix: Se verdadeiro, aplica ``fill_small_boundaries`` (pymeshfix) antes de exportar.

    Returns:
        Lista ordenada com os três ``Path`` escritos.

    Raises:
        RuntimeError: Se ``fast-simplification`` não estiver instalado.
        ValueError: Mesh vazia ou rácios inválidos.
    """
    if not 0 < lod2_ratio < lod1_ratio <= 1.0:
        raise ValueError("Esperado 0 < lod2_ratio < lod1_ratio <= 1.0")

    input_path = Path(input_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    base = prepare_mesh_topology(_load_as_trimesh(input_path))
    n = len(base.faces)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); LOD não aplicável.")

    out_paths: list[Path] = []
    for level, mesh in enumerate(
        (
            base,
            simplify_to_face_count(base, max(min_faces_lod1, int(n * lod1_ratio))),
            simplify_to_face_count(base, max(min_faces_lod2, int(n * lod2_ratio))),
        )
    ):
        if meshfix:
            mesh = apply_lod_meshfix(mesh)
        path = output_dir / f"{basename}_lod{level}.glb"
        _export_glb_with_normals(mesh, path)
        out_paths.append(path)

    return out_paths
