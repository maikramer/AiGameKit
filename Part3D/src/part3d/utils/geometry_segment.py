"""General-purpose geometry-first mesh part segmentation.

The geometry pass deliberately has no semantic knowledge of buildings,
characters, vehicles, or any other asset category.  It partitions the welded
surface graph into connected regions using local dihedral creases and a
region-normal constraint.  P3-SAM remains responsible for semantics.

Returns face label ids (>=0). Negative ids are unused.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class GeometrySegmentParams:
    weld_digits: int = 5
    # Adjacent faces may join only across a locally smooth edge.
    crease_angle_deg: float = 32.0
    # Prevents single-link chaining through bevels from joining surfaces whose
    # aggregate orientations are very different.
    region_normal_angle_deg: float = 48.0
    # Concave edges are stronger part-boundary evidence than convex bevels.
    concave_angle_factor: float = 1.25
    # Tiny regions caused by tessellation/noise are merged into the neighbor
    # with the strongest smooth shared boundary.
    min_part_area_frac: float = 0.0015
    min_part_faces: int = 24
    merge_angle_deg: float = 35.0
    max_parts: int = 64


def weld_mesh_topology(mesh: Any, *, digits: int = 5) -> Any:
    """Return a copy with welded vertices so adjacency is usable."""
    out = mesh.copy()
    out.merge_vertices(merge_tex=True, merge_norm=True, digits_vertex=int(digits))
    out.remove_unreferenced_vertices()
    return out


def _drop_tiny_detached_components(mesh: Any, params: GeometrySegmentParams) -> Any:
    """Remove microscopic disconnected triangle debris before partitioning."""
    n_faces = len(mesh.faces)
    if n_faces == 0:
        return mesh
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    all_faces = np.ones(n_faces, dtype=bool)
    components, n_components = _connected_components_masked(adjacency, all_faces)
    if n_components <= 1:
        return mesh

    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    component_area = np.bincount(components, weights=areas, minlength=n_components)
    component_faces = np.bincount(components, minlength=n_components)
    min_area = float(params.min_part_area_frac) * max(float(areas.sum()), 1e-12)
    keep_component = (component_faces >= int(params.min_part_faces)) | (component_area >= min_area)
    if not np.any(keep_component):
        keep_component[int(np.argmax(component_area))] = True
    keep_faces = keep_component[components]
    if np.all(keep_faces):
        return mesh

    cleaned = mesh.copy()
    cleaned.update_faces(keep_faces)
    cleaned.remove_unreferenced_vertices()
    return cleaned


def _connected_components_masked(
    face_adjacency: np.ndarray,
    mask: np.ndarray,
) -> tuple[np.ndarray, int]:
    """Label connected components inside ``mask``. Outside → -1."""
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components

    n = int(mask.shape[0])
    labels = np.full(n, -1, dtype=np.int64)
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return labels, 0
    remap = np.full(n, -1, dtype=np.int64)
    remap[idx] = np.arange(idx.size, dtype=np.int64)
    keep = mask[face_adjacency[:, 0]] & mask[face_adjacency[:, 1]]
    if not np.any(keep):
        labels[idx] = np.arange(idx.size, dtype=np.int64)
        return labels, int(idx.size)
    rows = remap[face_adjacency[keep, 0]]
    cols = remap[face_adjacency[keep, 1]]
    graph = coo_matrix(
        (np.ones(rows.shape[0], dtype=np.int8), (rows, cols)),
        shape=(idx.size, idx.size),
    )
    n_comp, comp = connected_components(graph, directed=False)
    labels[idx] = comp.astype(np.int64)
    return labels, int(n_comp)


def _dihedral_angles_deg(mesh: Any) -> np.ndarray:
    fa = mesh.face_adjacency
    fn = np.asarray(mesh.face_normals, dtype=np.float64)
    dots = np.einsum("ij,ij->i", fn[fa[:, 0]], fn[fa[:, 1]], optimize=True)
    dots = np.clip(dots, -1.0, 1.0)
    return np.degrees(np.arccos(dots))


def _initial_regions(mesh: Any, params: GeometrySegmentParams) -> np.ndarray:
    """Build connected smooth regions without single-link normal leakage."""
    n_faces = len(mesh.faces)
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if n_faces == 0:
        return np.zeros(0, dtype=np.int64)
    if adjacency.size == 0:
        return np.arange(n_faces, dtype=np.int64)

    normals = np.asarray(mesh.face_normals, dtype=np.float64)
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    angles = np.degrees(np.nan_to_num(np.asarray(mesh.face_adjacency_angles), nan=np.pi))
    convex = np.asarray(mesh.face_adjacency_convex, dtype=bool)
    effective_angles = angles * np.where(convex, 1.0, float(params.concave_angle_factor))

    parent = np.arange(n_faces, dtype=np.int64)
    region_area = np.maximum(areas.copy(), 1e-12)
    normal_sum = normals * region_area[:, None]

    def find(face: int) -> int:
        root = face
        while parent[root] != root:
            root = int(parent[root])
        while parent[face] != face:
            nxt = int(parent[face])
            parent[face] = root
            face = nxt
        return root

    max_edge_angle = float(params.crease_angle_deg)
    max_region_angle = float(params.region_normal_angle_deg)
    for edge_idx in np.argsort(effective_angles, kind="mergesort"):
        if effective_angles[edge_idx] > max_edge_angle:
            break
        r0 = find(int(adjacency[edge_idx, 0]))
        r1 = find(int(adjacency[edge_idx, 1]))
        if r0 == r1:
            continue
        n0 = normal_sum[r0]
        n1 = normal_sum[r1]
        denom = float(np.linalg.norm(n0) * np.linalg.norm(n1))
        if denom > 1e-12:
            dot = float(np.clip(np.dot(n0, n1) / denom, -1.0, 1.0))
            if float(np.degrees(np.arccos(dot))) > max_region_angle:
                continue
        if region_area[r0] < region_area[r1]:
            r0, r1 = r1, r0
        parent[r1] = r0
        region_area[r0] += region_area[r1]
        normal_sum[r0] += normal_sum[r1]

    roots = np.fromiter((find(i) for i in range(n_faces)), dtype=np.int64, count=n_faces)
    _unique, labels = np.unique(roots, return_inverse=True)
    return labels.astype(np.int64)


def _region_neighbor_scores(
    mesh: Any,
    labels: np.ndarray,
    *,
    merge_angle_deg: float,
) -> dict[int, dict[int, float]]:
    """Accumulate smooth shared-boundary affinity between adjacent regions."""
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if adjacency.size == 0:
        return {}
    different = labels[adjacency[:, 0]] != labels[adjacency[:, 1]]
    edge_ids = np.flatnonzero(different)
    if edge_ids.size == 0:
        return {}

    angles = np.nan_to_num(np.asarray(mesh.face_adjacency_angles, dtype=np.float64), nan=np.pi)
    edges = np.asarray(mesh.face_adjacency_edges, dtype=np.int64)
    vertices = np.asarray(mesh.vertices, dtype=np.float64)
    lengths = np.linalg.norm(vertices[edges[:, 0]] - vertices[edges[:, 1]], axis=1)
    sigma = np.radians(max(float(merge_angle_deg), 1.0))
    affinity = np.maximum(0.02, np.exp(-0.5 * (angles / sigma) ** 2)) * np.maximum(lengths, 1e-12)

    scores: dict[int, dict[int, float]] = {}
    for edge_idx in edge_ids:
        a = int(labels[adjacency[edge_idx, 0]])
        b = int(labels[adjacency[edge_idx, 1]])
        score = float(affinity[edge_idx])
        scores.setdefault(a, {})[b] = scores.setdefault(a, {}).get(b, 0.0) + score
        scores.setdefault(b, {})[a] = scores.setdefault(b, {}).get(a, 0.0) + score
    return scores


def _merge_small_regions(mesh: Any, labels: np.ndarray, params: GeometrySegmentParams) -> np.ndarray:
    """Merge tessellation fragments while keeping every resulting label connected."""
    out = _compact_labels(labels)
    face_areas = np.asarray(mesh.area_faces, dtype=np.float64)
    min_area = float(params.min_part_area_frac) * max(float(face_areas.sum()), 1e-12)

    for _ in range(12):
        n_regions = int(out.max(initial=-1)) + 1
        if n_regions <= 1:
            break
        region_area = np.bincount(out, weights=face_areas, minlength=n_regions)
        region_faces = np.bincount(out, minlength=n_regions)
        too_small = (region_area < min_area) | (region_faces < int(params.min_part_faces))
        if n_regions > int(params.max_parts):
            keep_count = max(1, int(params.max_parts))
            order = np.argsort(region_area, kind="mergesort")
            too_small[order[: n_regions - keep_count]] = True
        if not np.any(too_small):
            break

        scores = _region_neighbor_scores(mesh, out, merge_angle_deg=params.merge_angle_deg)
        remap = np.arange(n_regions, dtype=np.int64)
        changed = False
        for region in np.flatnonzero(too_small):
            neighbors = scores.get(int(region), {})
            if not neighbors:
                continue
            candidates = [
                (score, region_area[neighbor], -neighbor, neighbor)
                for neighbor, score in neighbors.items()
                if not too_small[neighbor] or region_area[neighbor] >= region_area[region]
            ]
            if not candidates:
                candidates = [
                    (score, region_area[neighbor], -neighbor, neighbor) for neighbor, score in neighbors.items()
                ]
            target = int(max(candidates)[3])
            if target != int(region):
                remap[region] = target
                changed = True
        if not changed:
            break

        # Resolve simultaneous chains and cycles deterministically.
        for region in range(n_regions):
            seen: set[int] = set()
            cur = region
            while int(remap[cur]) != cur and cur not in seen:
                seen.add(cur)
                cur = int(remap[cur])
            root = min(seen) if cur in seen else cur
            for item in seen:
                remap[item] = root
        out = _compact_labels(remap[out])
    return out


def segment_mesh_geometry(
    mesh: Any,
    params: GeometrySegmentParams | None = None,
) -> tuple[Any, np.ndarray]:
    """Segment ``mesh`` into connected, crease-bounded geometric regions.

    Returns:
        (welded_mesh, face_ids) with contiguous labels starting at 0.
    """
    p = params or GeometrySegmentParams()
    welded = weld_mesh_topology(mesh, digits=p.weld_digits)
    welded = _drop_tiny_detached_components(welded, p)
    if len(welded.faces) == 0:
        return welded, np.zeros(0, dtype=np.int64)
    labels = _initial_regions(welded, p)
    labels = _merge_small_regions(welded, labels, p)
    return welded, labels


def snap_semantic_labels_to_geometry(
    mesh: Any,
    semantic_labels: np.ndarray,
    params: GeometrySegmentParams | None = None,
    *,
    dominance_threshold: float = 0.80,
    semantic_core_min_area_frac: float = 0.0015,
) -> tuple[np.ndarray, np.ndarray]:
    """Snap confident P3-SAM regions to general geometric super-regions.

    Geometry is only a boundary prior: it never invents semantic labels.
    A meaningful label's strongest geometric region is protected so a small
    semantic part embedded in a broad smooth surface cannot be erased merely
    because the neighboring label has more area.

    Returns:
        ``(snapped_semantic_labels, geometry_region_ids)``.
    """
    labels = np.asarray(semantic_labels, dtype=np.int64).copy()
    if labels.shape[0] != len(mesh.faces):
        raise ValueError("semantic_labels length must match mesh faces")
    if labels.size == 0:
        return labels, labels.copy()

    p = params or GeometrySegmentParams()
    welded = weld_mesh_topology(mesh, digits=p.weld_digits)
    if len(welded.faces) != labels.shape[0]:
        raise ValueError("topology welding changed face count")
    regions = _merge_small_regions(welded, _initial_regions(welded, p), p)
    areas = np.asarray(welded.area_faces, dtype=np.float64)
    total_area = max(float(areas.sum()), 1e-12)
    valid_labels = labels >= 0
    if not np.any(valid_labels):
        return labels, regions

    semantic_area = np.bincount(labels[valid_labels], weights=areas[valid_labels])
    meaningful = semantic_area >= float(semantic_core_min_area_frac) * total_area

    region_support: dict[int, dict[int, float]] = {}
    core_region: dict[int, int] = {}
    core_support: dict[int, float] = {}
    for region in np.unique(regions):
        region_mask = (regions == region) & valid_labels
        if not np.any(region_mask):
            continue
        support = np.bincount(labels[region_mask], weights=areas[region_mask])
        support_map = {int(label): float(value) for label, value in enumerate(support) if value > 0.0}
        region_support[int(region)] = support_map
        for label, value in support_map.items():
            if value > core_support.get(label, -1.0):
                core_support[label] = value
                core_region[label] = int(region)

    threshold = float(np.clip(dominance_threshold, 0.5, 1.0))
    for region, support in region_support.items():
        region_mask = regions == region
        region_area = max(float(areas[region_mask].sum()), 1e-12)
        dominant_label, dominant_area = max(support.items(), key=lambda item: (item[1], -item[0]))
        if dominant_area / region_area < threshold:
            continue
        protects_minority_core = any(
            label != dominant_label
            and label < meaningful.shape[0]
            and meaningful[label]
            and core_region.get(label) == region
            for label in support
        )
        if protects_minority_core:
            continue
        labels[region_mask] = dominant_label
    return labels, regions


def _flood_unlabeled(labels: np.ndarray, face_adjacency: np.ndarray, *, max_iters: int) -> np.ndarray:
    """Propagate labels into unlabeled faces via adjacency majority."""
    out = labels.copy()
    if face_adjacency.size == 0:
        if np.any(out < 0):
            out[out < 0] = 0
        return out

    a = face_adjacency[:, 0]
    b = face_adjacency[:, 1]
    for _ in range(max_iters):
        unlabeled = out < 0
        if not np.any(unlabeled):
            break
        # Directed votes: labeled neighbor → unlabeled face
        votes_src = []
        votes_dst = []
        ab_lab = (~unlabeled[a]) & unlabeled[b]
        if np.any(ab_lab):
            votes_src.append(out[a[ab_lab]])
            votes_dst.append(b[ab_lab])
        ba_lab = (~unlabeled[b]) & unlabeled[a]
        if np.any(ba_lab):
            votes_src.append(out[b[ba_lab]])
            votes_dst.append(a[ba_lab])
        if not votes_dst:
            break
        src = np.concatenate(votes_src)
        dst = np.concatenate(votes_dst)
        order = np.argsort(dst, kind="mergesort")
        src = src[order]
        dst = dst[order]
        # For each dst face, pick mode of src labels
        uniq_dst, start_idx = np.unique(dst, return_index=True)
        start_idx = np.append(start_idx, src.shape[0])
        changed = False
        for i, face in enumerate(uniq_dst):
            chunk = src[start_idx[i] : start_idx[i + 1]]
            out[face] = int(np.bincount(chunk.astype(np.int64)).argmax())
            changed = True
        if not changed:
            break
    if np.any(out < 0):
        known = out[out >= 0]
        fill_id = int(np.bincount(known).argmax()) if known.size else 0
        out[out < 0] = fill_id
    return out


def _compact_labels(labels: np.ndarray) -> np.ndarray:
    out = labels.copy()
    valid = out >= 0
    if not np.any(valid):
        return np.zeros_like(out)
    _uniq, inv = np.unique(out[valid], return_inverse=True)
    out[valid] = inv.astype(np.int64)
    return out


def aabbs_from_face_ids(mesh: Any, face_ids: np.ndarray) -> np.ndarray:
    """Vertex AABBs per part — same format as Space ``get_aabb_from_face_ids``.

    Must use vertices (not triangle centers): undersized boxes starve X-Part
    surface sampling and drop/warp parts.
    """
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int64)
    ids = np.asarray(face_ids, dtype=np.int64)
    boxes: list[np.ndarray] = []
    for uid in np.unique(ids):
        if uid < 0:
            continue
        pts = verts[faces[ids == uid].reshape(-1)]
        if pts.size == 0:
            continue
        boxes.append(np.stack([pts.min(axis=0), pts.max(axis=0)], axis=0))
    if not boxes:
        return np.zeros((0, 2, 3), dtype=np.float64)
    return np.stack(boxes, axis=0)
