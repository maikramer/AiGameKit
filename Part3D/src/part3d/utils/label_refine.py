"""Refinamento crease-aware das labels de faces do P3-SAM.

O P3-SAM projeta máscaras de pontos amostrados para faces e faz flood-fill
por maioria — sem noção de arestas vivas. Resultado típico: a fronteira de
uma parte (ex. porta) derrama para a parede em zonas planas e ficam ilhas
soltas de labels erradas (speckle da projeção).

Este módulo corre em CPU/numpy (sem VRAM) depois da segmentação:

1. ``absorb_label_islands`` — componentes pequenos de cada label são
   absorvidos pelo vizinho dominante (peso = perímetro partilhado ponderado
   pela suavidade — ilhas separadas por crease ficam, separadas por zona
   plana são absorvidas).
2. ``icm_boundary_snap`` — ICM local (modelo de Potts): cada face na
   fronteira muda para a label vizinha que minimiza o custo do corte, onde
   atravessar uma aresta plana é caro e uma crease (sobretudo côncava) é
   barata. A fronteira "encaixa" nas arestas vivas (moldura da porta) em vez
   de serrilhar pela parede.
"""

from __future__ import annotations

import contextlib
from typing import Any

import numpy as np

_EPS = 1e-12


def edge_costs(
    mesh: Any,
    *,
    smooth_angle_deg: float = 25.0,
    concave_factor: float = 0.35,
) -> np.ndarray:
    """Custo de fronteira por aresta de adjacência (comprimento x suavidade).

    Aresta plana (ângulo diedro ~0) → custo alto (mau lugar para fronteira).
    Crease acentuada → custo baixo; côncava (típica de porta embutida) ainda
    mais baixo via ``concave_factor``.
    """
    angles = np.nan_to_num(np.asarray(mesh.face_adjacency_angles, dtype=np.float64), nan=0.0)
    convex = np.asarray(mesh.face_adjacency_convex, dtype=bool)
    edges = np.asarray(mesh.face_adjacency_edges)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    lengths = np.linalg.norm(verts[edges[:, 0]] - verts[edges[:, 1]], axis=1)

    sigma = np.radians(max(smooth_angle_deg, 1.0))
    ang = np.minimum(angles, np.pi / 2)
    smooth = np.exp(-0.5 * (ang / sigma) ** 2)
    cost = np.where(convex, smooth, smooth * concave_factor)
    # Nunca gratuito — evita fronteiras a vaguear ao longo de creases longas.
    cost = np.maximum(cost, 0.02)
    return cost * np.maximum(lengths, _EPS)


def _same_label_components(labels: np.ndarray, adj: np.ndarray) -> tuple[int, np.ndarray]:
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = labels.shape[0]
    same = labels[adj[:, 0]] == labels[adj[:, 1]]
    rows = adj[same, 0]
    cols = adj[same, 1]
    graph = coo_matrix((np.ones(rows.shape[0], dtype=np.int8), (rows, cols)), shape=(n, n))
    return connected_components(graph, directed=False)


def absorb_label_islands(
    labels: np.ndarray,
    adj: np.ndarray,
    face_areas: np.ndarray,
    *,
    min_frac: float = 0.15,
    min_faces: int = 12,
    edge_cost: np.ndarray | None = None,
    max_passes: int = 3,
) -> np.ndarray:
    """Absorve componentes pequenos de cada label no vizinho dominante."""
    labels = labels.copy()
    weights = edge_cost if edge_cost is not None else np.ones(adj.shape[0], dtype=np.float64)

    for _ in range(max_passes):
        n_comp, comp = _same_label_components(labels, adj)
        comp_area = np.bincount(comp, weights=face_areas, minlength=n_comp)
        comp_size = np.bincount(comp, minlength=n_comp)
        comp_label = np.empty(n_comp, dtype=labels.dtype)
        comp_label[comp] = labels

        best_area: dict[int, float] = {}
        comps_per_label: dict[int, int] = {}
        for ci in range(n_comp):
            lab = int(comp_label[ci])
            best_area[lab] = max(best_area.get(lab, 0.0), float(comp_area[ci]))
            comps_per_label[lab] = comps_per_label.get(lab, 0) + 1

        small = np.zeros(n_comp, dtype=bool)
        for ci in range(n_comp):
            lab = int(comp_label[ci])
            if comps_per_label[lab] > 1:
                small[ci] = comp_area[ci] < min_frac * best_area[lab] or comp_size[ci] < min_faces
            else:
                # Label com um único componente: só absorver se for ruído mínimo.
                small[ci] = comp_size[ci] < min_faces
        if not small.any():
            break

        ca = comp[adj[:, 0]]
        cb = comp[adj[:, 1]]
        boundary = np.flatnonzero(ca != cb)
        votes: dict[tuple[int, int], float] = {}
        for e in boundary:
            for s, o in ((int(ca[e]), int(cb[e])), (int(cb[e]), int(ca[e]))):
                if small[s] and not small[o]:
                    key = (s, int(comp_label[o]))
                    votes[key] = votes.get(key, 0.0) + float(weights[e])

        target: dict[int, tuple[int, float]] = {}
        for (s, lab), v in votes.items():
            if s not in target or v > target[s][1]:
                target[s] = (lab, v)
        if not target:
            break

        changed = False
        for s, (lab, _v) in target.items():
            if lab != int(comp_label[s]):
                labels[comp == s] = lab
                changed = True
        if not changed:
            break
    return labels


def icm_boundary_snap(
    labels: np.ndarray,
    adj: np.ndarray,
    cost: np.ndarray,
    *,
    iterations: int = 20,
    data_weight: float = 0.35,
    boundary_hops: int = 2,
) -> np.ndarray:
    """ICM ancorado sobre uma faixa estreita da fronteira.

    ``data_weight`` penaliza abandonar a máscara P3-SAM original. Sem este
    termo unary, o Potts puro prefere apagar partes pequenas para eliminar
    fronteiras. ``boundary_hops`` impede deriva para o interior da região.
    """
    labels = labels.copy()
    original = labels.copy()
    n = labels.shape[0]
    neighbors: list[list[tuple[int, float]]] = [[] for _ in range(n)]
    for e in range(adj.shape[0]):
        f0, f1 = int(adj[e, 0]), int(adj[e, 1])
        c = float(cost[e])
        neighbors[f0].append((f1, c))
        neighbors[f1].append((f0, c))

    boundary = labels[adj[:, 0]] != labels[adj[:, 1]]
    active = set(adj[boundary].ravel().tolist())
    frontier = set(active)
    for _ in range(max(0, boundary_hops - 1)):
        expanded = set(frontier)
        for f in frontier:
            expanded.update(nf for nf, _c in neighbors[f])
        frontier = expanded - active
        active.update(expanded)
    allowed = set(active)

    for _ in range(iterations):
        if not active:
            break
        flipped: list[int] = []
        for f in sorted(active):
            nb = neighbors[f]
            if len(nb) < 2:
                continue
            cur = int(labels[f])
            total = 0.0
            gain: dict[int, float] = {}
            for nf, c in nb:
                total += c
                lab = int(labels[nf])
                gain[lab] = gain.get(lab, 0.0) + c
            best_lab = cur
            anchor = data_weight * total
            best_cost = total - gain.get(cur, 0.0) + (anchor if cur != int(original[f]) else 0.0)
            for lab, g in gain.items():
                if lab < 0:
                    continue
                cand_cost = total - g + (anchor if lab != int(original[f]) else 0.0)
                if cand_cost < best_cost - _EPS:
                    best_cost = cand_cost
                    best_lab = lab
            if best_lab != cur:
                labels[f] = best_lab
                flipped.append(f)
        if not flipped:
            break
        nxt: set[int] = set()
        for f in flipped:
            nxt.add(f)
            for nf, _c in neighbors[f]:
                if nf in allowed:
                    nxt.add(nf)
        active = nxt
    return labels


def relabel_connected_components(
    labels: np.ndarray,
    adj: np.ndarray,
    face_areas: np.ndarray,
    *,
    min_faces: int = 32,
    min_area_frac: float = 1e-4,
) -> np.ndarray:
    """Dá ID próprio a cada componente físico e remove detritos mínimos.

    P3-SAM pode reutilizar uma label em ilhas desconectadas. Um AABB agregado
    dessas ilhas atravessa o objeto e faz X-Part fundir regiões sem relação.
    """
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    labels = labels.copy()
    n = labels.shape[0]
    same = labels[adj[:, 0]] == labels[adj[:, 1]]
    edges = adj[same]
    graph = coo_matrix(
        (
            np.ones(edges.shape[0] * 2, dtype=np.int8),
            (
                np.concatenate((edges[:, 0], edges[:, 1])),
                np.concatenate((edges[:, 1], edges[:, 0])),
            ),
        ),
        shape=(n, n),
    )
    n_comp, comp = connected_components(graph, directed=False)
    comp_area = np.bincount(comp, weights=face_areas, minlength=n_comp)
    comp_size = np.bincount(comp, minlength=n_comp)
    total_area = max(float(np.sum(face_areas)), _EPS)
    next_label = int(np.max(labels[labels >= 0], initial=-1)) + 1

    for lab in (int(x) for x in np.unique(labels) if x >= 0):
        comp_ids = np.unique(comp[labels == lab])
        if comp_ids.size <= 1:
            continue
        order = sorted(comp_ids.tolist(), key=lambda ci: float(comp_area[ci]), reverse=True)
        for ci in order[1:]:
            mask = (comp == ci) & (labels == lab)
            if comp_size[ci] < min_faces or comp_area[ci] / total_area < min_area_frac:
                labels[mask] = -1
            else:
                labels[mask] = next_label
                next_label += 1
    return labels


def refine_face_labels(
    mesh: Any,
    face_ids: np.ndarray,
    *,
    iterations: int = 20,
    smooth_angle_deg: float = 25.0,
    concave_factor: float = 0.35,
    island_min_frac: float = 0.15,
    island_min_faces: int = 12,
    data_weight: float = 0.35,
    boundary_hops: int = 2,
    split_components: bool = True,
) -> np.ndarray:
    """Refina labels P3-SAM: ilhas absorvidas + fronteiras nas arestas vivas.

    Args:
        mesh: ``trimesh.Trimesh`` limpo do P3-SAM (mesmo nº de faces).
        face_ids: Labels por face (negativo = sem parte).

    Returns:
        Novo array de labels (mesma forma; labels podem desaparecer se uma
        parte era só ruído).
    """
    labels = np.asarray(face_ids, dtype=np.int64).copy()
    if labels.shape[0] != len(mesh.faces) or labels.shape[0] == 0:
        return labels
    adj = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if adj.size == 0:
        return labels

    cost = edge_costs(mesh, smooth_angle_deg=smooth_angle_deg, concave_factor=concave_factor)
    face_areas = np.asarray(mesh.area_faces, dtype=np.float64)

    # Sem scipy os passes de ilhas são saltados; o ICM ainda limpa a fronteira.
    with contextlib.suppress(ImportError):
        labels = absorb_label_islands(
            labels,
            adj,
            face_areas,
            min_frac=island_min_frac,
            min_faces=island_min_faces,
            edge_cost=cost,
        )

    labels = icm_boundary_snap(
        labels,
        adj,
        cost,
        iterations=iterations,
        data_weight=data_weight,
        boundary_hops=boundary_hops,
    )

    with contextlib.suppress(ImportError):
        # ICM pode deixar ilhas mínimas novas — passe final barato.
        labels = absorb_label_islands(
            labels,
            adj,
            face_areas,
            min_frac=island_min_frac,
            min_faces=island_min_faces,
            edge_cost=cost,
        )
        if split_components:
            labels = relabel_connected_components(labels, adj, face_areas)
    return labels


def aabbs_from_face_ids(mesh: Any, face_ids: np.ndarray) -> np.ndarray:
    """Recalcula AABBs por label (mesmo formato do ``get_aabb_from_face_ids`` do Space)."""
    verts = np.asarray(mesh.vertices)
    faces = np.asarray(mesh.faces)
    ids = np.asarray(face_ids)
    aabb = []
    for uid in np.unique(ids):
        if uid < 0:
            continue
        pts = verts[faces[ids == uid].reshape(-1)]
        if pts.size == 0:
            continue
        aabb.append([pts.min(axis=0), pts.max(axis=0)])
    return np.asarray(aabb, dtype=np.float64)


def expand_aabbs(
    aabb: np.ndarray,
    *,
    margin_frac: float = 0.04,
    min_pad: float = 1e-4,
) -> np.ndarray:
    """Expande cada AABB por ``margin_frac`` das meias-extensões (geração X-Part).

    Caixas justas às faces cortam o marching cubes nas bordas; um pad relativo
    dá folga sem mudar as labels de superfície. ``margin_frac=0`` é no-op.
    """
    out = np.asarray(aabb, dtype=np.float64)
    if out.size == 0 or float(margin_frac) <= 0:
        return out
    if out.ndim != 3 or out.shape[1:] != (2, 3):
        raise ValueError(f"aabb esperado (K,2,3), got {out.shape}")
    out = out.copy()
    half = 0.5 * (out[:, 1] - out[:, 0])
    pad = np.maximum(half * float(margin_frac), float(min_pad))
    out[:, 0] -= pad
    out[:, 1] += pad
    return out
