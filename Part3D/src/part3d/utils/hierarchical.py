"""Hierarchical P3-SAM refinement for under-segmented large regions."""

from __future__ import annotations

from collections import deque
from typing import Any

import numpy as np


def large_region_candidates(
    mesh: Any,
    face_ids: np.ndarray,
    *,
    min_area_frac: float = 0.18,
    max_regions: int = 2,
) -> list[tuple[int, np.ndarray, float]]:
    """Return largest labels worth a local detail pass."""
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total = max(float(np.sum(areas)), 1e-12)
    candidates: list[tuple[int, np.ndarray, float]] = []
    for label in (int(x) for x in np.unique(face_ids) if x >= 0):
        indices = np.flatnonzero(face_ids == label)
        frac = float(np.sum(areas[indices]) / total)
        if frac >= min_area_frac:
            candidates.append((label, indices, frac))
    candidates.sort(key=lambda item: item[2], reverse=True)
    return candidates[: max(0, max_regions)]


def detail_partition_is_useful(
    face_areas: np.ndarray,
    child_ids: np.ndarray,
    *,
    min_child_frac: float = 0.005,
    max_dominant_frac: float = 0.95,
    max_children: int = 24,
) -> bool:
    """Reject no-op, debris-only, and explosive local partitions."""
    valid = child_ids >= 0
    if not valid.any():
        return False
    total = max(float(np.sum(face_areas)), 1e-12)
    fractions = [float(np.sum(face_areas[child_ids == label]) / total) for label in np.unique(child_ids[valid])]
    significant = [frac for frac in fractions if frac >= min_child_frac]
    return 2 <= len(significant) <= max_children and max(significant) <= max_dominant_frac


def prune_detail_partition(
    mesh: Any,
    child_ids: np.ndarray,
    *,
    min_child_frac: float = 0.005,
) -> np.ndarray:
    """Remove local child masks too small to become standalone X-Part inputs."""
    labels = np.asarray(child_ids, dtype=np.int64).copy()
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total = max(float(np.sum(areas)), 1e-12)
    keep = {
        int(label)
        for label in np.unique(labels)
        if label >= 0 and float(np.sum(areas[labels == label]) / total) >= min_child_frac
    }
    labels[~np.isin(labels, list(keep))] = -1
    adjacency = np.asarray(mesh.face_adjacency, dtype=np.int64)
    if adjacency.size == 0 or not keep:
        return labels

    from scipy.sparse import coo_matrix

    edges = np.concatenate((adjacency, adjacency[:, ::-1]), axis=0)
    graph = coo_matrix(
        (np.ones(edges.shape[0], dtype=np.int8), (edges[:, 0], edges[:, 1])),
        shape=(labels.shape[0], labels.shape[0]),
    ).tocsr()
    queue = deque(int(x) for x in np.flatnonzero(labels >= 0))
    while queue:
        face = queue.popleft()
        for neighbor in graph.indices[graph.indptr[face] : graph.indptr[face + 1]]:
            if labels[neighbor] < 0:
                labels[neighbor] = labels[face]
                queue.append(int(neighbor))
    return labels


def merge_detail_partition(
    parent_ids: np.ndarray,
    parent_face_indices: np.ndarray,
    child_ids: np.ndarray,
) -> np.ndarray:
    """Replace one parent label with collision-free local child labels."""
    if parent_face_indices.shape[0] != child_ids.shape[0]:
        raise ValueError("child_ids must match parent_face_indices")
    result = np.asarray(parent_ids, dtype=np.int64).copy()
    valid_children = sorted(int(x) for x in np.unique(child_ids) if x >= 0)
    if len(valid_children) < 2:
        return result

    parent_label = int(result[parent_face_indices[0]])
    next_label = int(np.max(result[result >= 0], initial=-1)) + 1
    mapping = {valid_children[0]: parent_label}
    for child in valid_children[1:]:
        mapping[child] = next_label
        next_label += 1

    local = np.full(child_ids.shape, parent_label, dtype=np.int64)
    for child, global_label in mapping.items():
        local[child_ids == child] = global_label
    result[parent_face_indices] = local
    return result
