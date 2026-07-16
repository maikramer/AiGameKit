"""Transfer semantic face labels from a simplified proxy to a high-poly mesh."""

from __future__ import annotations

from typing import Any

import numpy as np


def validate_proxy_alignment(source_mesh: Any, target_mesh: Any, *, tolerance: float = 0.05) -> None:
    """Require proxy and target to share object space before nearest transfer."""
    source_bounds = np.asarray(source_mesh.bounds, dtype=np.float64)
    target_bounds = np.asarray(target_mesh.bounds, dtype=np.float64)
    source_extent = source_bounds[1] - source_bounds[0]
    target_extent = target_bounds[1] - target_bounds[0]
    scale = max(float(np.max(target_extent)), 1e-8)
    center_error = np.abs(source_bounds.mean(axis=0) - target_bounds.mean(axis=0)) / scale
    extent_error = np.abs(source_extent - target_extent) / scale
    if float(max(np.max(center_error), np.max(extent_error))) > tolerance:
        raise ValueError("segmentation proxy is not aligned with the target mesh")


def transfer_face_labels(
    source_mesh: Any,
    source_ids: np.ndarray,
    target_mesh: Any,
    *,
    neighbors: int = 8,
    normal_weight: float = 0.05,
    chunk_size: int = 100_000,
) -> np.ndarray:
    """Map proxy labels to target faces by center distance plus normal agreement."""
    from scipy.spatial import cKDTree
    from trimesh.triangles import closest_point

    labels = np.asarray(source_ids, dtype=np.int64)
    source_centers = np.asarray(source_mesh.triangles_center, dtype=np.float64)
    target_centers = np.asarray(target_mesh.triangles_center, dtype=np.float64)
    if labels.shape[0] != source_centers.shape[0]:
        raise ValueError("source_ids length must match source mesh faces")
    if source_centers.shape[0] == 0:
        return np.full(target_centers.shape[0], -1, dtype=np.int64)

    validate_proxy_alignment(source_mesh, target_mesh)
    source_normals = np.asarray(source_mesh.face_normals, dtype=np.float64)
    source_triangles = np.asarray(source_mesh.triangles, dtype=np.float64)
    target_normals = np.asarray(target_mesh.face_normals, dtype=np.float64)
    scale = max(float(np.max(np.ptp(source_centers, axis=0))), 1e-8)
    k = min(max(1, neighbors), source_centers.shape[0])
    tree = cKDTree(source_centers)
    result = np.empty(target_centers.shape[0], dtype=np.int64)

    for start in range(0, target_centers.shape[0], chunk_size):
        stop = min(start + chunk_size, target_centers.shape[0])
        _center_distance, indices = tree.query(target_centers[start:stop], k=k, workers=-1)
        if k == 1:
            result[start:stop] = labels[np.asarray(indices)]
            continue
        points = target_centers[start:stop]
        candidate_triangles = source_triangles[indices]
        repeated_points = np.repeat(points, k, axis=0)
        nearest_points = closest_point(candidate_triangles.reshape(-1, 3, 3), repeated_points)
        distance = np.linalg.norm(nearest_points - repeated_points, axis=1).reshape(-1, k)
        candidate_normals = source_normals[indices]
        normal_similarity = np.abs(
            np.einsum("nkj,nj->nk", candidate_normals, target_normals[start:stop], optimize=True)
        )
        score = np.asarray(distance) / scale + normal_weight * (1.0 - normal_similarity)
        best = np.argmin(score, axis=1)
        result[start:stop] = labels[indices[np.arange(stop - start), best]]
    return result
