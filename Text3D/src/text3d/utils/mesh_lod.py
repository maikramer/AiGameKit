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


def _mesh_repair_pymeshlab(mesh: trimesh.Trimesh, *, skip_remesh: bool = False) -> trimesh.Trimesh:
    """Weld por distância, close holes, Taubin smoothing e isotropic remesh via pymeshlab.

    Pipeline de reparo 4-fases para micro-cracks do marching cubes:
    1. Merge close vertices (0.1% diagonal — fecha cracks do octree/MC)
    2. Close small holes (boundary loops ≤ 30 edges — cracks residuais)
    3. Taubin smoothing (preserva volume, sem shrinkage)
    4. Isotropic remeshing adaptativo (saltar com ``skip_remesh=True``)

    Tudo numa única ronda pymeshlab para minimizar export/import.
    """
    try:
        import logging

        log = logging.getLogger(__name__)

        bbox_diag = float(np.linalg.norm(mesh.bounds[1] - mesh.bounds[0]))
        if bbox_diag < 1e-10:
            log.warning("Mesh com bounding box degenerada — skip weld/smooth/remesh")
            return mesh

        def _apply(ms):
            import pymeshlab

            # FASE 1 — Topologia: merge vértices próximos (0.1% diagonal)
            ms.meshing_merge_close_vertices(threshold=pymeshlab.PureValue(bbox_diag * 0.001))

            # Non-manifold repair pós-merge
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_unreferenced_vertices()

            # FASE 2 — Fechar buracos pequenos (rachaduras MC = 3-15 arestas de boundary)
            ms.meshing_close_holes(maxholesize=30)

            # Remover debris/flotadores (componentes < 1% diagonal)
            ms.meshing_remove_connected_component_by_diameter(mincomponentdiag=pymeshlab.PercentageValue(1))

            # FASE 3 — Taubin smoothing (λ/μ low-pass, preserva volume)
            ms.apply_coord_taubin_smoothing(stepsmoothnum=3)

            # FASE 4 — Isotropic remeshing (regulariza triângulos)
            if not skip_remesh:
                ms.meshing_isotropic_explicit_remeshing(
                    iterations=3,
                    targetlen=pymeshlab.PercentageValue(1),
                    adaptive=True,
                )

            # Cleanup final
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_unreferenced_vertices()

        result = _pymeshlab_roundtrip(mesh, _apply)
        log.info(
            "Reparo pymeshlab: %d→%d faces, %d→%d vértices",
            len(mesh.faces),
            len(result.faces),
            len(mesh.vertices),
            len(result.vertices),
        )
        return result
    except Exception as exc:
        import logging

        logging.getLogger(__name__).warning("Reparo pymeshlab falhou (%s) — mesh inalterada", exc)
        return mesh


def prepare_mesh_topology(mesh: trimesh.Trimesh, *, skip_remesh: bool = False) -> trimesh.Trimesh:
    """Prepara topologia antes de export, LOD ou :func:`repair_mesh`.

    Pipeline completo de reparo pós-marching-cubes:

    1. Merge vertices exactos + quase coincidentes (``digits_vertex=4``)
    2. Remove faces duplicadas e vértices órfãos
    3. Repara non-manifold edges/vertices (pymeshlab)
    4. Weld por distância adaptativa (0.1% diagonal), close holes ≤ 30 edges,
       Taubin smoothing, isotropic remesh — fecha micro-cracks e regulariza
    5. Cleanup final

    Usada por defeito em ``text3d generate``, ``generate-batch`` e ``text3d lod``.
    """
    import logging

    log = logging.getLogger(__name__)

    m = mesh.copy()
    n_faces_before = len(m.faces)

    # 1. Merge vertices exactos
    try:
        m.merge_vertices(merge_tex=True)
    except Exception as exc:
        log.warning("merge_vertices exacto falhou: %s", exc)

    # 2. Remove duplicatas e órfãos
    try:
        m.process(validate=True, merge_tex=True)
    except Exception as exc:
        log.warning("process(validate=True) falhou: %s", exc)
    try:
        m.remove_unreferenced_vertices()
    except Exception as exc:
        log.warning("remove_unreferenced_vertices falhou: %s", exc)

    # 3. Merge por casas decimais (digits_vertex=4 — alinhado com Rigging3D)
    try:
        m.merge_vertices(merge_tex=True, digits_vertex=4)
    except Exception as exc:
        log.warning("merge_vertices(digits_vertex=4) falhou: %s", exc)

    # 4. Non-manifold repair
    m = _manifold_repair(m)

    # 5. Weld + close holes + smooth + remesh (pipeline pymeshlab completa)
    m = _mesh_repair_pymeshlab(m, skip_remesh=skip_remesh)

    # 6. Cleanup final
    try:
        m.merge_vertices(merge_tex=True)
    except Exception as exc:
        log.warning("merge_vertices final falhou: %s", exc)
    try:
        m.process(validate=True, merge_tex=True)
    except Exception as exc:
        log.warning("process(validate=True) final falhou: %s", exc)
    try:
        m.remove_unreferenced_vertices()
    except Exception as exc:
        log.warning("remove_unreferenced_vertices final falhou: %s", exc)
    try:
        trimesh_repair.fix_normals(m, multibody=True)
    except Exception as exc:
        log.warning("fix_normals falhou: %s", exc)

    log.info(
        "prepare_mesh_topology: %d→%d faces, %d→%d vértices",
        n_faces_before,
        len(m.faces),
        len(mesh.vertices),
        len(m.vertices),
    )
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
