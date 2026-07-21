"""Reparação de mesh em arrays (numpy/scipy) — fase vetorizada do topology-fix.

Contraforte de :mod:`gamedev_shared.mesh_repair` (bmesh/bpy): os passos de
**filtro/selecção** do perfil ``topology_clean`` (sanitize, welds, long edges,
slivers, debris, cascas internas) são aqui executados sobre arrays
``(verts, tris)`` sem objectos bpy — 10-100x mais rápido em meshes grandes
(medido: weld 19 s → ~3 s exacto em 7M verts; slivers ~14 s → 0.3 s em
2.35M faces; shells ~21 s → ~2 s). Os passos genuinamente topológicos
(``fill_holes`` / ``holes_fill`` / pinch / erode / triangulate / normais)
permanecem em bmesh — ver ``repair_mesh_object_with_profile``.

Semântica alinhada com o caminho bmesh (paridade é o objectivo; desvios
documentados por função):

* ``weld_vertices`` — cKDTree ``query_pairs`` (distância euclidiana exacta) +
  componentes conexos ⇒ fecho transitivo como ``remove_doubles``. O vértice
  representante é o de menor índice (o bmesh escolhe um alvo interno — com
  limiares µm/mm a diferença de posição é desprezável).
* ``drop_internal_shell_faces`` — o raio BVH por face é substituído por
  k-NN de centros (cKDTree) + intersecção raio-triângulo **exacta**
  (Möller-Trumbore vectorizado) sobre os candidatos: mesmo teste de
  primeiro-impacte, ``wall_gap`` e regra de normal (``|n·hn| >= |opp|``).
  ``k_neighbors`` limita os candidatos (paredes gémeas finas estão sempre
  entre os centros mais próximos); aumentar em meshes muito irregulares.

scipy é importado lazily (não é dependência obrigatória do Shared); sem ele,
``weld_vertices`` cai para a grelha quantizada (aproximada, ~10x mais rápida)
e as funções que precisam de componentes conexos usam um union-find numpy.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import numpy as np

log = logging.getLogger(__name__)

WeldMethod = Literal["exact", "grid"]


# ---------------------------------------------------------------------------
# Helpers de grafo / arestas (numpy puro; scipy lazy)
# ---------------------------------------------------------------------------


def _require_scipy_spatial() -> Any:
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        raise ImportError("scipy é necessário para o engine de arrays (pip install scipy)") from None
    return cKDTree


def _connected_components(n_nodes: int, src: np.ndarray, dst: np.ndarray) -> tuple[np.ndarray, int]:
    """Labels de componente conexo (undirected) sobre ``(src, dst)``.

    scipy CSR quando disponível (C, ~0.2 s em 7M arestas); senão label
    propagation + pointer jumping em numpy (converge em poucas iterações para
    grupos pequenos — típico de welds/debris).
    """
    if len(src) == 0:
        return np.arange(n_nodes, dtype=np.int64), n_nodes
    try:
        from scipy import sparse
        from scipy.sparse import csgraph

        graph = sparse.csr_matrix(
            (np.ones(len(src), dtype=np.int8), (src, dst)),
            shape=(n_nodes, n_nodes),
        )
        n_comp, labels = csgraph.connected_components(graph, directed=False)
        return labels.astype(np.int64, copy=False), int(n_comp)
    except ImportError:
        pass

    labels = np.arange(n_nodes, dtype=np.int64)
    a = src.astype(np.int64, copy=False)
    b = dst.astype(np.int64, copy=False)
    for _ in range(256):
        prev = labels
        lo = np.minimum(labels[a], labels[b])
        np.minimum.at(labels, a, lo)
        np.minimum.at(labels, b, lo)
        labels = labels[labels]  # pointer jumping
        if (labels == prev).all():
            break
    uniq, labels = np.unique(labels, return_inverse=True)
    return labels.astype(np.int64, copy=False), len(uniq)


def _pack_edges(tris: np.ndarray) -> np.ndarray:
    """Arestas (3M, 2) com endpoints ordenados (sem dedup)."""
    e = np.empty((len(tris) * 3, 2), dtype=np.int64)
    e[0::3, 0] = tris[:, 0]
    e[0::3, 1] = tris[:, 1]
    e[1::3, 0] = tris[:, 1]
    e[1::3, 1] = tris[:, 2]
    e[2::3, 0] = tris[:, 2]
    e[2::3, 1] = tris[:, 0]
    e.sort(axis=1)
    return e


def _unique_edges(tris: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Arestas únicas + contagem de faces incidentes + inverse por aresta (3M,).

    Returns:
        ``(edges (E,2), face_counts (E,), inverse (3M,))`` — ``inverse`` mapeia
        cada aresta de :func:`_pack_edges` para o índice da aresta única.
    """
    if len(tris) == 0:
        return np.zeros((0, 2), dtype=np.int64), np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    n_verts_max = int(tris.max()) + 1
    if n_verts_max >= (1 << 32):
        raise ValueError("mesh demasiado grande para packing de arestas em int64")
    e = _pack_edges(tris)
    keys = (e[:, 0] << 32) | e[:, 1]
    _uk, first, inverse, counts = np.unique(keys, return_index=True, return_inverse=True, return_counts=True)
    return e[first], counts.astype(np.int64), inverse.astype(np.int64)


def compact_mesh(
    verts: np.ndarray,
    tris: np.ndarray,
    keep_faces: np.ndarray | None = None,
    *,
    drop_degenerate: bool = True,
) -> tuple[np.ndarray, np.ndarray]:
    """Remove faces (máscara), vértices órfãos e faces degeneradas; remapeia.

    Equivalente em arrays ao delete FACES + delete VERTS órfãos do caminho
    bmesh. Faces com índice repetido (pós-weld) são removidas quando
    ``drop_degenerate``.
    """
    tris = np.asarray(tris, dtype=np.int64)
    verts = np.asarray(verts, dtype=np.float64)
    if keep_faces is not None:
        tris = tris[keep_faces]
    if drop_degenerate and len(tris):
        deg = (tris[:, 0] == tris[:, 1]) | (tris[:, 1] == tris[:, 2]) | (tris[:, 2] == tris[:, 0])
        if deg.any():
            tris = tris[~deg]
    if len(tris) == 0:
        return np.zeros((0, 3), dtype=np.float64), np.zeros((0, 3), dtype=np.int64)
    used = np.unique(tris.reshape(-1))
    remap = np.empty(len(verts), dtype=np.int64)
    remap[used] = np.arange(len(used), dtype=np.int64)
    return verts[used], remap[tris]


# ---------------------------------------------------------------------------
# Sanitize + weld
# ---------------------------------------------------------------------------


def sanitize_nonfinite(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove vértices NaN/Inf (e faces incidentes). Devolve ``(v, f, n_verts_removidos)``."""
    from gamedev_shared.mesh_repair import drop_nonfinite_faces

    finite = np.isfinite(verts).all(axis=1)
    n_bad = int((~finite).sum())
    if n_bad == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    v, f, _ = drop_nonfinite_faces(np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64))
    return v, f, n_bad


def _grid_weld_pairs(verts: np.ndarray, threshold: float) -> tuple[np.ndarray, np.ndarray]:
    """Pares candidatos por grelha quantizada (fallback sem scipy — aproximado).

    Só garante pares dentro da mesma célula (arestas de célula podem escapar);
    usado apenas quando scipy está indisponível.
    """
    ijk = np.floor(verts / max(threshold, 1e-12)).astype(np.int64)
    order = np.lexsort((ijk[:, 2], ijk[:, 1], ijk[:, 0]))
    ijk_s = ijk[order]
    same = (ijk_s[1:] == ijk_s[:-1]).all(axis=1)
    # grupos de células iguais: pares (pos, pos+1) em cadeia — cobre células
    # com qualquer cardinalidade para efeito de CC transitivo.
    a = order[:-1][same]
    b = order[1:][same]
    return a.astype(np.int64), b.astype(np.int64)


def weld_vertices(
    verts: np.ndarray,
    tris: np.ndarray,
    threshold: float,
    *,
    method: WeldMethod = "exact",
) -> tuple[np.ndarray, np.ndarray, int]:
    """Funde vértices a distância euclidiana <= ``threshold`` (fecho transitivo).

    Equivalente a ``bmesh.ops.remove_doubles``: pares exactos via cKDTree +
    componentes conexos. Faces que degeneram (índice repetido) são removidas,
    como no bmesh. Vértices órfãos NÃO são removidos aqui (o bmesh também os
    mantém) — usar :func:`compact_mesh` quando se quiser buffer compacto.

    Args:
        verts: (N, 3) posições.
        tris: (M, 3) triângulos.
        threshold: Distância de fusão (unidades do mesh).
        method: ``exact`` (cKDTree; requer scipy) ou ``grid`` (grelha
            quantizada aproximada, ~10x mais rápida, sem scipy).

    Returns:
        ``(verts, tris, n_verts_fundidos)`` — buffers podem manter órfãos.
    """
    verts = np.asarray(verts, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    n = len(verts)
    if n == 0 or threshold <= 0:
        return verts, tris, 0

    if method == "exact":
        cKDTree = _require_scipy_spatial()
        pairs = cKDTree(verts).query_pairs(float(threshold), output_type="ndarray")
        if len(pairs) == 0:
            return verts, tris, 0
        a = pairs[:, 0].astype(np.int64)
        b = pairs[:, 1].astype(np.int64)
    else:
        a, b = _grid_weld_pairs(verts, float(threshold))
        if len(a) == 0:
            return verts, tris, 0

    labels, n_groups = _connected_components(n, a, b)
    merged = n - n_groups
    if merged <= 0:
        return verts, tris, 0

    # Representante = menor índice do grupo; novos buffers por representante.
    first = np.full(n_groups, n, dtype=np.int64)
    np.minimum.at(first, labels, np.arange(n, dtype=np.int64))
    rep_of_vert = first[labels]
    reps, inverse = np.unique(rep_of_vert, return_inverse=True)
    new_tris = inverse[tris]
    if len(new_tris):
        deg = (
            (new_tris[:, 0] == new_tris[:, 1]) | (new_tris[:, 1] == new_tris[:, 2]) | (new_tris[:, 2] == new_tris[:, 0])
        )
        if deg.any():
            new_tris = new_tris[~deg]
    return verts[reps], new_tris.astype(np.int64, copy=False), int(merged)


def weld_vertices_multi(
    verts: np.ndarray,
    tris: np.ndarray,
    thresholds: list[float],
    *,
    method: WeldMethod = "exact",
) -> tuple[np.ndarray, np.ndarray, list[int]]:
    """Weld multi-limiar numa só árvore (stats por limiar, como o perfil bmesh).

    O perfil ``topology_clean`` corre 3 welds sequenciais (reweld 1e-6 → exact
    1e-5 → densidade ~3e-3); em arrays o mesmo efeito é um único weld no limiar
    máximo — os pares dos limiares menores são subconjunto. Devolve os
    removidos **acumulados** por limiar para logs idênticos ao caminho bmesh.

    Returns:
        ``(verts, tris, removed_por_limiar)`` — mesh final no limiar máximo.
    """
    ts = sorted({float(t) for t in thresholds if t and t > 0})
    if not ts:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), [0] * len(thresholds)
    verts = np.asarray(verts, dtype=np.float64)
    n = len(verts)

    if method == "exact":
        cKDTree = _require_scipy_spatial()
        pairs = cKDTree(verts).query_pairs(ts[-1], output_type="ndarray")
        if len(pairs) == 0:
            return verts, np.asarray(tris, dtype=np.int64), [0] * len(thresholds)
        d2 = ((verts[pairs[:, 0]] - verts[pairs[:, 1]]) ** 2).sum(axis=1)
        removed_per_t: list[int] = []
        labels_final: np.ndarray | None = None
        for t in ts:
            sel = d2 <= t * t
            if sel.any():
                labels, n_groups = _connected_components(
                    n, pairs[sel, 0].astype(np.int64), pairs[sel, 1].astype(np.int64)
                )
            else:
                labels, n_groups = np.arange(n, dtype=np.int64), n
            removed_per_t.append(n - n_groups)
            labels_final = labels
    else:
        removed_per_t = []
        labels_final = None
        for t in ts:
            a, b = _grid_weld_pairs(verts, t)
            if len(a):
                labels, n_groups = _connected_components(n, a, b)
            else:
                labels, n_groups = np.arange(n, dtype=np.int64), n
            removed_per_t.append(n - n_groups)
            labels_final = labels

    assert labels_final is not None
    merged = removed_per_t[-1]
    if merged <= 0:
        return verts, np.asarray(tris, dtype=np.int64), removed_per_t

    n_groups = n - merged
    first = np.full(n_groups, n, dtype=np.int64)
    np.minimum.at(first, labels_final, np.arange(n, dtype=np.int64))
    reps, inverse = np.unique(first[labels_final], return_inverse=True)
    tris64 = np.asarray(tris, dtype=np.int64)
    new_tris = inverse[tris64]
    if len(new_tris):
        deg = (
            (new_tris[:, 0] == new_tris[:, 1]) | (new_tris[:, 1] == new_tris[:, 2]) | (new_tris[:, 2] == new_tris[:, 0])
        )
        if deg.any():
            new_tris = new_tris[~deg]
    return verts[reps], new_tris.astype(np.int64, copy=False), removed_per_t


# ---------------------------------------------------------------------------
# Long edges / slivers / debris / boundary
# ---------------------------------------------------------------------------


def _face_metrics(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Comprimentos das 3 arestas (M, 3) e área (M,) por face — vetorizado."""
    v = verts[tris]
    e = np.stack(
        [
            np.linalg.norm(v[:, 1] - v[:, 0], axis=1),
            np.linalg.norm(v[:, 2] - v[:, 1], axis=1),
            np.linalg.norm(v[:, 0] - v[:, 2], axis=1),
        ],
        axis=1,
    )
    area = 0.5 * np.linalg.norm(np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0]), axis=1)
    return e, area


def drop_long_edge_faces(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    max_length: float,
    median_factor: float = 8.0,
    max_removal_ratio: float = 0.5,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove faces com aresta outlier — ver ``remove_long_edges`` (bmesh).

    Limiar = ``max(max_length, median_factor x mediana das arestas únicas)``;
    aborta (0) acima de ``max_removal_ratio`` das faces. Vértices órfãos são
    compactados (como o bmesh).
    """
    if len(tris) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    edges, _, _ = _unique_edges(tris)
    if len(edges) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    elen = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)
    median = float(np.median(elen))
    threshold = max(float(max_length), float(median_factor) * median)

    e_faces, _ = _face_metrics(verts, tris)
    doomed = (e_faces > threshold).any(axis=1)
    n = int(doomed.sum())
    if n == 0 or n > max_removal_ratio * len(tris):
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    v, f = compact_mesh(verts, tris, ~doomed)
    return v, f, n


def drop_sliver_faces(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    max_aspect: float = 80.0,
    max_removal_ratio: float = 0.25,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove triângulos-agulha (``longest²/area > max_aspect``) — paridade bmesh."""
    if len(tris) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    e_faces, area = _face_metrics(verts, tris)
    longest = e_faces.max(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        aspect = (longest * longest) / np.maximum(area, 1e-300)
    doomed = (area < 1e-18) | (aspect > float(max_aspect))
    n = int(doomed.sum())
    if n == 0 or n > max_removal_ratio * len(tris):
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    v, f = compact_mesh(verts, tris, ~doomed)
    return v, f, n


def drop_loose_debris(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    face_ratio: float,
    min_faces: int,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Apaga ilhas minúsculas (union-find/CC por aresta) — paridade bmesh.

    Componentes com ``faces < max(min_faces, face_ratio x total)`` são
    removidos; a maior ilha nunca é apagada. ``face_ratio <= 0`` desativa.
    """
    if face_ratio <= 0 or len(tris) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    edges, _, _ = _unique_edges(tris)
    if len(edges) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    labels, _ = _connected_components(int(tris.max()) + 1, edges[:, 0], edges[:, 1])
    face_comp = labels[tris[:, 0]]
    counts = np.bincount(face_comp)
    total = len(tris)
    threshold = max(int(min_faces), int(float(face_ratio) * total))
    largest = int(counts.max())
    doomed_comps = np.flatnonzero((counts < threshold) & (counts < largest))
    if len(doomed_comps) == 0:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64), 0
    doomed_mask = np.isin(face_comp, doomed_comps)
    n = int(doomed_mask.sum())
    v, f = compact_mesh(verts, tris, ~doomed_mask)
    return v, f, n


def boundary_edges(tris: np.ndarray) -> np.ndarray:
    """Arestas de fronteira (exactamente 1 face incidente) — (K, 2)."""
    edges, counts, _ = _unique_edges(tris)
    return edges[counts == 1]


def boundary_edge_count(tris: np.ndarray) -> int:
    """Número de arestas de fronteira — 0 em meshes fechadas."""
    if len(tris) == 0:
        return 0
    _edges, counts, _ = _unique_edges(tris)
    return int((counts == 1).sum())


# ---------------------------------------------------------------------------
# Cascas internas (double shell MC) — k-NN + raio-triângulo exacto
# ---------------------------------------------------------------------------


def _face_centers_normals(verts: np.ndarray, tris: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    v = verts[tris]
    centers = v.mean(axis=1)
    n = np.cross(v[:, 1] - v[:, 0], v[:, 2] - v[:, 0])
    norm = np.linalg.norm(n, axis=1)
    ok = norm > 1e-18
    normals = np.zeros_like(n)
    normals[ok] = n[ok] / norm[ok, None]
    return centers, normals


def _ray_tri_first_hit_t(
    origins: np.ndarray,
    dirs: np.ndarray,
    v0: np.ndarray,
    v1: np.ndarray,
    v2: np.ndarray,
) -> np.ndarray:
    """Möller-Trumbore vetorizado — ``t`` do impacte ou ``inf`` (sem culling).

    Shapes (..., 3) broadcasting; devolve (...,). Raios paralelos ao triângulo
    (|det| ~ 0) falham — mesmo comportamento do BVH.
    """
    e1 = v1 - v0
    e2 = v2 - v0
    p = np.cross(dirs, e2)
    det = np.einsum("...i,...i->...", e1, p)
    det_ok = np.abs(det) > 1e-12
    inv = np.where(det_ok, 1.0 / np.where(det_ok, det, 1.0), np.nan)
    tvec = origins - v0
    u = np.einsum("...i,...i->...", tvec, p) * inv
    q = np.cross(tvec, e1)
    vv = np.einsum("...i,...i->...", dirs, q) * inv
    t = np.einsum("...i,...i->...", e2, q) * inv
    tol = 1e-6
    hit = det_ok & (u >= -tol) & (vv >= -tol) & ((u + vv) <= 1.0 + tol) & (t > 0.0)
    return np.where(hit, t, np.inf)


def _shell_doomed_mask(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    wall_gap: float,
    eps: float,
    opposite_dot: float,
    k_neighbors: int,
    chunk: int = 200_000,
) -> np.ndarray:
    """Máscara de faces a remover: raio +N bate noutra face dentro de wall_gap.

    Candidatos = ``k_neighbors`` centros mais próximos (cKDTree); impacte
    exacto via Möller-Trumbore; regra de normal idêntica à versão bmesh
    (antiparalela ``d <= opp`` ou paralela/backface ``d >= -opp``).
    """
    cKDTree = _require_scipy_spatial()
    m = len(tris)
    doomed = np.zeros(m, dtype=bool)
    centers, normals = _face_centers_normals(verts, tris)
    zero_n = (normals == 0).all(axis=1)
    tree = cKDTree(centers)
    k = min(int(k_neighbors) + 1, m)
    opp = float(opposite_dot)

    for lo in range(0, m, chunk):
        hi = min(lo + chunk, m)
        c = centers[lo:hi]
        n = normals[lo:hi]
        _dist, idx = tree.query(c, k=k, workers=-1)
        if k == 1:
            idx = idx[:, None]
        idx = idx[:, 1:]  # drop self
        if idx.shape[1] == 0:
            continue
        v = verts[tris[idx]]  # (b, k-1, 3, 3)
        o = (c + n * eps)[:, None, :]
        d = n[:, None, :]
        t = _ray_tri_first_hit_t(
            np.broadcast_to(o, (idx.shape[0], idx.shape[1], 3)),
            np.broadcast_to(d, (idx.shape[0], idx.shape[1], 3)),
            v[..., 0, :],
            v[..., 1, :],
            v[..., 2, :],
        )
        t = np.where(t <= wall_gap, t, np.inf)
        first = np.argmin(t, axis=1)
        rows = np.arange(idx.shape[0])
        t_min = t[rows, first]
        j_first = idx[rows, first]
        valid = np.isfinite(t_min) & ~zero_n[lo:hi]
        if not valid.any():
            continue
        hn = normals[j_first[valid]]
        dot = np.einsum("ij,ij->i", n[valid], hn)
        hit_ok = (dot <= opp) | (dot >= -opp)
        doomed_chunk = np.zeros(hi - lo, dtype=bool)
        doomed_chunk[np.flatnonzero(valid)[hit_ok]] = True
        doomed[lo:hi] = doomed_chunk
    return doomed


def drop_internal_shell_faces(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    wall_gap_ratio: float = 0.08,
    max_removal_ratio: float = 0.55,
    opposite_dot: float = -0.2,
    passes: int = 2,
    k_neighbors: int = 16,
) -> tuple[np.ndarray, np.ndarray, int]:
    """Remove faces do sanduíche fino de paredes duplas (Hunyuan/MC).

    Paridade com :func:`gamedev_shared.mesh_repair.remove_internal_shell_faces`:
    só remove faces cujo raio ``+N`` bate noutra face dentro de ``wall_gap``
    com normal ~oposta/paralela (fachadas exteriores e vãos grandes ficam).
    Guard ``max_removal_ratio`` por passada (abort → 0 nessa passada).

    Args:
        k_neighbors: Candidatos por face (centros mais próximos) para o teste
            de raio exacto. 16 cobre paredes gémeas finas em meshes MC densas;
            aumentar se as paredes forem muito curvas/irregulares.

    Returns:
        ``(verts, tris, n_faces_removidas_total)``.
    """
    verts = np.asarray(verts, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    total_removed = 0
    for _pass in range(max(1, int(passes))):
        if len(tris) == 0:
            break
        diag = float(np.linalg.norm(verts.max(axis=0) - verts.min(axis=0))) or 1.0
        wall_gap = max(float(wall_gap_ratio) * diag, 1e-5)
        eps = max(1e-6, 1e-5 * diag)
        doomed = _shell_doomed_mask(
            verts,
            tris,
            wall_gap=wall_gap,
            eps=eps,
            opposite_dot=opposite_dot,
            k_neighbors=k_neighbors,
        )
        n = int(doomed.sum())
        if n == 0:
            break
        if n > float(max_removal_ratio) * len(tris):
            log.warning(
                "drop_internal_shell_faces: abort pass %d — %d/%d faces (max_ratio=%.2f)",
                _pass,
                n,
                len(tris),
                max_removal_ratio,
            )
            break
        verts, tris = compact_mesh(verts, tris, ~doomed)
        total_removed += n
        log.info("drop_internal_shell_faces: pass %d removeu %d faces (wall_gap=%.4f)", _pass, n, wall_gap)
    return verts, tris, total_removed


# ---------------------------------------------------------------------------
# Decimação rápida (fast_simplification — quadric, C++)
# ---------------------------------------------------------------------------


def simplify_faces_arrays(
    verts: np.ndarray,
    tris: np.ndarray,
    target_faces: int,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Decima para ``target_faces`` via fast_simplification (quadric error).

    ~10-15x mais rápido que o DECIMATE COLLAPSE do Blender em meshes >1M
    faces (medido: 4.4 s vs 63.7 s em 2M -> 198k) e usado no decimate-back do
    ``morphological_close``. ``fast-simplification`` é dependência do Text3D;
    sem ela devolve ``None`` — o caller faz fallback para o modifier.

    Args:
        verts: (N, 3) posições.
        tris: (M, 3) triângulos.
        target_faces: Orçamento de faces de saída.

    Returns:
        ``(verts, tris)`` dizimados, ou ``None`` se a lib/target indisponível.
    """
    if len(tris) <= target_faces or target_faces < 4:
        return np.asarray(verts, dtype=np.float64), np.asarray(tris, dtype=np.int64)
    try:
        import fast_simplification
    except ImportError:
        log.info("simplify_faces_arrays: fast_simplification ausente — fallback para DECIMATE modifier")
        return None
    reduction = 1.0 - float(target_faces) / float(len(tris))
    pts, faces = fast_simplification.simplify(
        np.asarray(verts, dtype=np.float32),
        np.asarray(tris, dtype=np.int32),
        target_reduction=reduction,
    )
    return pts.astype(np.float64), faces.astype(np.int64)


# ---------------------------------------------------------------------------
# Taubin (laplaciano vetorizado; perfil topology_clean tem do_taubin=False)
# ---------------------------------------------------------------------------


def taubin_smooth_arrays(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    iterations: int = 3,
    lam: float = 0.5,
    mu: float = -0.53,
) -> tuple[np.ndarray, int]:
    """Suavização Taubin volume-preserving — mesma fórmula do bmesh, vetorizada.

    Δ = avg(vizinhos por aresta) - v, aplicado com ``lam``/``mu`` alternados.
    """
    verts = np.asarray(verts, dtype=np.float64)
    if len(verts) < 8 or iterations <= 0 or len(tris) == 0:
        return verts, 0
    edges, _, _ = _unique_edges(tris)
    if len(edges) == 0:
        return verts, 0
    src = np.concatenate([edges[:, 0], edges[:, 1]])
    dst = np.concatenate([edges[:, 1], edges[:, 0]])
    deg = np.bincount(src, minlength=len(verts)).astype(np.float64)
    deg[deg < 1.0] = 1.0

    pos = verts.copy()
    out = np.empty_like(pos)
    applied = 0
    for _ in range(int(iterations)):
        for factor in (float(lam), float(mu)):
            np.multiply(pos, -deg[:, None], out=out)  # out = -deg·v
            np.add.at(out, src, pos[dst])  # out[src] += pos[dst]
            pos = pos + (factor / deg)[:, None] * out
        applied += 1
    if applied:
        log.info("taubin_smooth_arrays: %d iterações (λ=%.2f μ=%.2f)", applied, lam, mu)
    return pos, applied


# ---------------------------------------------------------------------------
# Orquestrador do perfil topology_clean (fase de arrays)
# ---------------------------------------------------------------------------


def repair_arrays_topology_clean(
    verts: np.ndarray,
    tris: np.ndarray,
    *,
    reweld_threshold: float = 1e-6,
    weld_threshold: float = 1e-5,
    weld_density: bool = True,
    long_edge_length: float = 8.0 / 512.0,
    long_edge_median_factor: float = 8.0,
    sliver_max_aspect: float | None = 80.0,
    debris_face_ratio: float = 0.0005,
    debris_min_faces: int = 64,
    do_remove_internal_shells: bool = False,
    internal_shell_wall_gap_ratio: float = 0.08,
    internal_shell_max_removal_ratio: float = 0.55,
    internal_shell_passes: int = 2,
    shell_k_neighbors: int = 16,
    weld_method: WeldMethod = "exact",
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    """Fase de filtro do perfil ``topology_clean`` sobre arrays.

    Ordem (paridade com ``repair_mesh_object``): sanitize → welds (reweld +
    exact + densidade, fundidos numa passagem multi-limiar) → long edges →
    slivers → debris → cascas internas (opt). ``dissolve_degenerate`` /
    ``delete_loose`` do caminho bmesh ficam cobertos pelo weld+compact (faces
    degeneradas e vértices órfãos são removidos em cada passo).

    Os passos topológicos (fill_holes / caps / watertight / normais) NÃO fazem
    parte — correr depois em bmesh sobre o mesh já reduzido.

    Returns:
        ``(verts, tris, stats)`` com as mesmas chaves de log do caminho bmesh
        (``nonfinite_verts``, ``rewelded_coincident``, ``welded_exact``,
        ``welded_relative``, ``weld_distance``, ``long_edge_faces``,
        ``sliver_faces``, ``debris_faces``, ``internal_shell_faces``).
    """
    from gamedev_shared.mesh_repair import dynamic_weld_distance

    stats: dict[str, int] = {}
    verts, tris, n_bad = sanitize_nonfinite(verts, tris)
    stats["nonfinite_verts"] = int(n_bad)
    if len(tris) == 0:
        return verts, tris, stats

    dist = dynamic_weld_distance(len(verts)) if weld_density else 0.0
    thresholds = [reweld_threshold, weld_threshold] + ([dist] if dist > 0 else [])
    verts, tris, removed = weld_vertices_multi(verts, tris, thresholds, method=weld_method)
    stats["rewelded_coincident"] = int(removed[0]) if len(removed) > 0 else 0
    stats["welded_exact"] = int(removed[1] - removed[0]) if len(removed) > 1 else 0
    if len(removed) > 2:
        stats["welded_relative"] = int(removed[2] - removed[1])
        stats["weld_distance"] = int(dist * 1_000_000)
    # weld_vertices_multi mantém órfãos só se não houve fusões; compacta sempre.
    verts, tris = compact_mesh(verts, tris)

    verts, tris, stats["long_edge_faces"] = drop_long_edge_faces(
        verts,
        tris,
        max_length=long_edge_length,
        median_factor=long_edge_median_factor,
    )
    if sliver_max_aspect is not None and sliver_max_aspect > 0:
        verts, tris, stats["sliver_faces"] = drop_sliver_faces(verts, tris, max_aspect=sliver_max_aspect)
    verts, tris, stats["debris_faces"] = drop_loose_debris(
        verts,
        tris,
        face_ratio=debris_face_ratio,
        min_faces=debris_min_faces,
    )
    if do_remove_internal_shells and len(tris):
        verts, tris, stats["internal_shell_faces"] = drop_internal_shell_faces(
            verts,
            tris,
            wall_gap_ratio=internal_shell_wall_gap_ratio,
            max_removal_ratio=internal_shell_max_removal_ratio,
            passes=internal_shell_passes,
            k_neighbors=shell_k_neighbors,
        )
    return verts, tris, stats


# ---------------------------------------------------------------------------
# Inter-op com bpy objects (lazy bpy) — fronteira do engine de arrays
# ---------------------------------------------------------------------------


def extract_arrays(obj: Any) -> tuple[np.ndarray, np.ndarray]:
    """``(verts float64, tris int64)`` de um bpy mesh object (loop_triangles)."""
    me = obj.data
    n = len(me.vertices)
    co = np.empty(n * 3, dtype=np.float64)
    me.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    me.calc_loop_triangles()
    m = len(me.loop_triangles)
    tris = np.empty(m * 3, dtype=np.int64)
    me.loop_triangles.foreach_get("vertices", tris)
    return co, tris.reshape(-1, 3)


def arrays_engine_ok(obj: Any, *, has_armature: bool = False) -> bool:
    """True se o engine de arrays é seguro para este mesh object.

    O engine reconstrói o mesh a partir de ``(verts, tris)`` — destrói UVs,
    materiais extra, vertex groups (skin) e shape keys. Só é usado quando nada
    disso existe (shape cru pré-paint do topology-fix); caso contrário o
    chamador deve seguir o caminho bmesh (preservativo).
    """
    if has_armature:
        return False
    me = obj.data
    if len(me.uv_layers) > 0:
        return False
    if getattr(me, "shape_keys", None):
        return False
    if len(obj.vertex_groups) > 0:
        return False
    return len(me.materials) <= 1


def replace_mesh_arrays(obj: Any, verts: np.ndarray, tris: np.ndarray) -> None:
    """Substitui a geometria de ``obj`` pelos arrays (preserva nome + 1 material).

    Usa ``vertices/loops/polygons.add`` + ``foreach_set`` (memcpy) — muito mais
    rápido que ``from_pydata`` em meshes >1M elementos. Fallback para
    ``from_pydata`` se a RNA recusar os arrays directos.
    """
    import bpy

    verts = np.asarray(verts, dtype=np.float64)
    tris = np.asarray(tris, dtype=np.int64)
    old = obj.data
    name = old.name
    mats = list(old.materials)
    me = bpy.data.meshes.new(name)
    n = len(verts)
    m = len(tris)
    try:
        me.vertices.add(n)
        me.vertices.foreach_set("co", verts.reshape(-1))
        me.loops.add(m * 3)
        me.loops.foreach_set("vertex_index", tris.reshape(-1).astype(np.int32))
        me.polygons.add(m)
        me.polygons.foreach_set("loop_start", (np.arange(m, dtype=np.int32) * 3))
        me.polygons.foreach_set("loop_total", np.full(m, 3, dtype=np.int32))
        for mat in mats:
            me.materials.append(mat)
        me.update(calc_edges=True)
        me.validate(verbose=False, clean_customdata=False)
    except Exception:
        log.warning("replace_mesh_arrays: foreach_set falhou — fallback from_pydata", exc_info=True)
        me = bpy.data.meshes.new(name)
        me.from_pydata(verts, [], tris)
        for mat in mats:
            me.materials.append(mat)
        me.update()
    obj.data = me
    bpy.data.meshes.remove(old)
