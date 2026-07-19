"""Métricas de qualidade de mesh pós-decode (trimesh + numpy, sem GPU/bpy).

Usadas pelo bench de calibração (``text3d bench-decode``) e pelo filtro de
componentes internos aplicado logo após o decode Omni. Octree alto demais não
adiciona detalhe — só resolve ruído do field *dentro* da mesh (componentes
internos soltos). Estas métricas tornam esse lixo mensurável:

- ``boundary_edge_count``: arestas com 1 só face (rachas/buracos MC).
- ``classify_components`` / ``drop_internal_components``: componentes
  totalmente contidos no componente principal — geometria nunca vista.
- ``mesh_quality_metrics``: snapshot completo para o relatório do bench.

Implementação **O(F + C)** por labels (``trimesh.graph``): volumes por
tetraedros assinados, um ponto (centroid da 1ª face) por componente, AABB /
ray-cast em batch. Loops ``for lid: labels == lid`` são proibidos — com
centenas de milhares de componentes MC isso virava horas de CPU.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Acima disto: só AABB no centroid (ruído MC; ray-cast não vale a pena).
_MAX_COMPONENTS_FOR_RAYCAST = 2_000
# Acima disto: path rápido — todos os componentes cujo centroid cai na AABB
# do principal contam como internos (debris do field).
_FAST_AABB_COMPONENTS = 5_000


def _max_contains_points() -> int:
    """Tecto de pontos p/ o batch de ray-contains.

    Sem embree o intersector puro-numpy é O(F) por raio — em meshes MC de
    centenas de milhar de faces só compensa para poucos raios; acima do tecto
    decide-se só por AABB (debris pequeno dentro da AABB é lixo na mesma).
    """
    try:
        from trimesh import ray as _ray

        has_embree = bool(getattr(_ray, "has_embree", False))
    except Exception:
        has_embree = False
    return 20_000 if has_embree else 512


def _component_labels(mesh: Any) -> np.ndarray:
    """Label de componente conexo por face (vetorizado, sem objetos Trimesh)."""
    import trimesh

    return trimesh.graph.connected_component_labels(mesh.face_adjacency, node_count=len(mesh.faces))


def _signed_volumes_by_label(mesh: Any, labels: np.ndarray, n_labels: int, triangles: np.ndarray) -> np.ndarray:
    """|volume| por componente via soma de tetraedros assinados (numpy puro)."""
    signed = np.einsum("ij,ij->i", triangles[:, 0], np.cross(triangles[:, 1], triangles[:, 2])) / 6.0
    vols = np.zeros(n_labels, dtype=np.float64)
    np.add.at(vols, labels, signed)
    return np.abs(vols)


def _first_face_index(labels: np.ndarray, n_labels: int) -> np.ndarray:
    """Índice da primeira face de cada label — O(F)."""
    first = np.full(n_labels, len(labels), dtype=np.int64)
    np.minimum.at(first, labels, np.arange(len(labels), dtype=np.int64))
    return first


def _main_aabb(triangles: np.ndarray, labels: np.ndarray, main: int) -> tuple[np.ndarray, np.ndarray]:
    """AABB do componente principal a partir das suas faces — O(F_main)."""
    main_tris = triangles[labels == main]
    flat = main_tris.reshape(-1, 3)
    return flat.min(axis=0), flat.max(axis=0)


def split_components(mesh: Any) -> list[Any]:
    """Componentes conexos (inclui não-watertight); mesh vazia → lista vazia.

    Nota: constrói objetos Trimesh — usar só em meshes pequenas/testes. Para o
    path quente usar :func:`classify_component_labels`.
    """
    if mesh is None or len(mesh.faces) == 0:
        return []
    parts = mesh.split(only_watertight=False)
    return list(parts) if len(parts) else [mesh]


def boundary_edge_count(mesh: Any) -> int:
    """Número de arestas referenciadas por exactamente 1 face (fronteira aberta)."""
    if mesh is None or len(mesh.faces) == 0:
        return 0
    from trimesh import grouping

    return len(grouping.group_rows(mesh.edges_sorted, require_count=1))


def classify_component_labels(
    mesh: Any,
    *,
    containment: float = 0.9,
) -> tuple[np.ndarray, int, np.ndarray, np.ndarray, np.ndarray] | None:
    """Classifica componentes por label: (labels, main, internos, externos, volumes).

    Interno = componente cujo centroid (1ª face) está ≥ ``containment`` dentro
    do principal. Path O(F+C): sem loops por label sobre faces. Com
    ``n_labels > _FAST_AABB_COMPONENTS`` decide só por AABB (ruído MC).

    Devolve ``None`` para mesh vazia.
    """
    import trimesh

    if mesh is None or len(mesh.faces) == 0:
        return None
    labels = _component_labels(mesh)
    n_labels = int(labels.max()) + 1 if len(labels) else 0
    triangles = np.asarray(mesh.triangles, dtype=np.float64)
    if n_labels <= 1:
        return (
            labels,
            0,
            np.zeros(0, dtype=np.int64),
            np.zeros(0, dtype=np.int64),
            _signed_volumes_by_label(mesh, labels, max(n_labels, 1), triangles),
        )

    face_counts = np.bincount(labels, minlength=n_labels)
    main = int(np.argmax(face_counts))
    volumes = _signed_volumes_by_label(mesh, labels, n_labels, triangles)
    first = _first_face_index(labels, n_labels)
    centroids = triangles[first].mean(axis=1)  # (C, 3)
    lo, hi = _main_aabb(triangles, labels, main)
    in_aabb = np.all((centroids >= lo) & (centroids <= hi), axis=1)
    in_aabb[main] = False

    others_mask = np.ones(n_labels, dtype=bool)
    others_mask[main] = False

    # Storm de debris MC: AABB chega; ray-cast + agregação por lid era O(C*F).
    if n_labels > _FAST_AABB_COMPONENTS:
        internal = np.flatnonzero(in_aabb)
        external = np.flatnonzero(others_mask & ~in_aabb)
        return labels, main, internal.astype(np.int64), external.astype(np.int64), volumes

    inside = in_aabb.copy()
    use_raycast = n_labels <= _MAX_COMPONENTS_FOR_RAYCAST and n_labels - 1 <= _max_contains_points()
    if use_raycast and np.any(in_aabb):
        try:
            main_mesh = trimesh.Trimesh(
                vertices=np.asarray(mesh.vertices),
                faces=np.asarray(mesh.faces)[labels == main],
                process=False,
            )
            pts = centroids[in_aabb]
            inside[in_aabb] = main_mesh.contains(pts)
        except Exception:
            pass

    # 1 ponto/componente → frac é 0 ou 1; containment ≥ 0.9 ≡ inside.
    internal = np.flatnonzero(others_mask & inside)
    external = np.flatnonzero(others_mask & ~inside)
    # containment < 1.0 com 1 amostra: já coberto (inside bool).
    _ = containment
    return labels, main, internal.astype(np.int64), external.astype(np.int64), volumes


def classify_components(
    mesh: Any,
    *,
    containment: float = 0.9,
) -> tuple[Any | None, list[Any], list[Any]]:
    """Versão por objetos (API de conveniência p/ testes e meshes pequenas)."""
    import trimesh

    info = classify_component_labels(mesh, containment=containment)
    if info is None:
        return None, [], []
    labels, main, internal_ids, external_ids, _vols = info

    def _build(lid: int) -> Any:
        m = trimesh.Trimesh(
            vertices=np.asarray(mesh.vertices),
            faces=np.asarray(mesh.faces)[labels == lid],
            process=False,
        )
        m.remove_unreferenced_vertices()
        return m

    return _build(main), [_build(i) for i in internal_ids], [_build(i) for i in external_ids]


def drop_internal_components(
    mesh: Any,
    *,
    max_volume_ratio: float = 0.15,
    containment: float = 0.9,
) -> tuple[Any, int, dict[str, Any]]:
    """Remove componentes internos pequenos (lixo do field dentro da shell).

    Conservador: só remove componentes contidos no principal E com volume
    < ``max_volume_ratio`` do principal — bolsas legítimas grandes (interior
    modelado) sobrevivem. Path O(F+C). Devolve
    ``(mesh, n_removidos, pre_stats)`` — ``pre_stats`` alimenta o bench
    (ceiling do latent) com lixo PRE-drop; sem remoções, mesh intacta.
    """
    import trimesh

    empty_stats: dict[str, Any] = {
        "pre_faces": len(mesh.faces) if mesh is not None else 0,
        "n_components": 0,
        "n_internal": 0,
        "internal_volume_ratio": 0.0,
        "n_dropped": 0,
    }
    info = classify_component_labels(mesh, containment=containment)
    if info is None:
        return mesh, 0, empty_stats
    labels, main, internal_ids, _external_ids, volumes = info
    n_labels = int(labels.max()) + 1 if len(labels) else 0
    main_vol = max(float(volumes[main]), 1e-12)
    internal_vol = float(volumes[internal_ids].sum()) if len(internal_ids) else 0.0
    pre_stats: dict[str, Any] = {
        "pre_faces": len(mesh.faces),
        "n_components": n_labels,
        "n_internal": len(internal_ids),
        "internal_volume_ratio": internal_vol / main_vol,
        "n_dropped": 0,
    }
    if len(internal_ids) == 0:
        return mesh, 0, pre_stats
    drop_ids = internal_ids[volumes[internal_ids] / main_vol < max_volume_ratio]
    if len(drop_ids) == 0:
        return mesh, 0, pre_stats
    drop_mask = np.zeros(n_labels, dtype=bool)
    drop_mask[drop_ids] = True
    keep_faces = ~drop_mask[labels]
    cleaned = trimesh.Trimesh(
        vertices=np.asarray(mesh.vertices),
        faces=np.asarray(mesh.faces)[keep_faces],
        process=False,
    )
    cleaned.remove_unreferenced_vertices()
    n_dropped = len(drop_ids)
    pre_stats["n_dropped"] = n_dropped
    return cleaned, n_dropped, pre_stats


def mesh_quality_metrics(mesh: Any, *, containment: float = 0.9) -> dict[str, Any]:
    """Snapshot de qualidade para o relatório do bench (JSON-serializável)."""
    if mesh is None or len(mesh.faces) == 0:
        return {
            "vertices": 0,
            "faces": 0,
            "volume": 0.0,
            "extents": [0.0, 0.0, 0.0],
            "is_watertight": False,
            "euler_number": 0,
            "boundary_edges": 0,
            "components": 0,
            "main_faces": 0,
            "internal_components": 0,
            "internal_volume_ratio": 0.0,
            "external_components": 0,
        }
    info = classify_component_labels(mesh, containment=containment)
    assert info is not None
    labels, main, internal_ids, external_ids, volumes = info
    face_counts = np.bincount(labels, minlength=int(labels.max()) + 1 if len(labels) else 1)
    main_vol = max(float(volumes[main]), 1e-12)
    internal_vol = float(volumes[internal_ids].sum()) if len(internal_ids) else 0.0
    try:
        total_volume = abs(float(mesh.volume))
    except Exception:
        total_volume = 0.0
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "volume": total_volume,
        "extents": [float(v) for v in mesh.extents],
        "is_watertight": bool(mesh.is_watertight),
        "euler_number": int(mesh.euler_number),
        "boundary_edges": boundary_edge_count(mesh),
        "components": 1 + len(internal_ids) + len(external_ids),
        "main_faces": int(face_counts[main]),
        "internal_components": len(internal_ids),
        "internal_volume_ratio": internal_vol / main_vol,
        "external_components": len(external_ids),
    }
