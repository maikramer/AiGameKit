"""Geração de variantes LOD (níveis de detalhe) de meshes GLB para uso offline e em runtime."""

from __future__ import annotations

import contextlib
import tempfile
from pathlib import Path

import numpy as np
import trimesh
import trimesh.repair as trimesh_repair
from trimesh.grouping import group_rows

from .export import _export_glb_with_normals, _load_as_trimesh

# ---------------------------------------------------------------------------
# Helpers inlined to break the import dependency on mesh_repair
# ---------------------------------------------------------------------------


def _pymeshlab_roundtrip(mesh: trimesh.Trimesh, apply_fn) -> trimesh.Trimesh:
    """Exporta → aplica filtros pymeshlab via callback → reimporta."""
    with tempfile.TemporaryDirectory(prefix="pml_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        mesh.export(in_ply)
        import pymeshlab

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)
        apply_fn(ms)
        ms.save_current_mesh(out_ply)
        m_new = trimesh.load(out_ply, force="mesh")
        if isinstance(m_new, trimesh.Trimesh) and len(m_new.faces) > 0:
            return m_new
    return mesh


def _manifold_repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Repara topologia non-manifold, duplicatas e vértices órfãos via pymeshlab."""
    try:

        def _apply(ms):
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_unreferenced_vertices()

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        m = mesh.copy()
        with contextlib.suppress(Exception):
            m.merge_vertices()
            m.remove_unreferenced_vertices()
            trimesh_repair.fix_normals(m, multibody=True)
        return m


def _boundary_edge_count(mesh: trimesh.Trimesh) -> int:
    """Conta arestas de fronteira (buracos abertos)."""
    try:
        return len(group_rows(mesh.edges_sorted, require_count=1))
    except Exception:
        return -1


def prepare_mesh_topology(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Prepara topologia antes de export, LOD ou :func:`repair_mesh`.

    Funde vértices duplicados e quase coincidentes (export GLB), remove duplicatas e aplica
    :func:`_manifold_repair` (sem remoção de bases, sem pymeshfix ``clean()``). Reduz
    tamanho em disco e evita faces soltas após decimação ou pipelines seguintes.

    Usada por defeito antes de gerar LODs.
    """
    m = mesh.copy()
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True)
    with contextlib.suppress(Exception):
        m.remove_duplicate_faces()
    with contextlib.suppress(Exception):
        m.remove_unreferenced_vertices()
    # Aproximar vértices quase coincidentes (tolerância por casas decimais)
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True, digits_vertex=5)
    m = _manifold_repair(m)
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True)
    with contextlib.suppress(Exception):
        m.remove_duplicate_faces()
    with contextlib.suppress(Exception):
        m.remove_unreferenced_vertices()
    with contextlib.suppress(Exception):
        trimesh_repair.fix_normals(m, multibody=True)
    return m


def pymeshfix_mesh_repair_only(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Só PyTMesh ``fill_small_boundaries`` (sem ``clean``).

    ``clean()`` remove triângulos com auto-intersecções; em meshes decimadas (LOD) isso
    costuma eliminar troncos ou quase toda a geometria. Não usa pymeshlab nem
    ``repair_mesh``.

    Se ``pymeshfix`` não estiver instalado ou não houver fronteira aberta, devolve a mesh
    inalterada.
    """
    m = mesh.copy()
    try:
        from pymeshfix import PyTMesh
    except ImportError:
        return m

    if _boundary_edge_count(m) <= 0:
        return m

    try:
        verts = np.asarray(m.vertices, dtype=np.float64)
        faces = np.asarray(m.faces, dtype=np.int64)
        mfix = PyTMesh()
        mfix.load_array(verts, faces)
        mfix.fill_small_boundaries(nbe=0, refine=True)
        v, f = mfix.return_arrays()
        return trimesh.Trimesh(
            vertices=np.asarray(v, dtype=np.float64),
            faces=np.asarray(f, dtype=np.int64),
            process=True,
        )
    except Exception:
        return m


def apply_lod_meshfix(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Opcional: só ``fill_small_boundaries`` via ``pymeshfix_mesh_repair_only``."""
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
