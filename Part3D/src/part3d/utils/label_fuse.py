"""Fuse complementary face-label maps (ex. escada duma corrida + bandeira doutra)."""

from __future__ import annotations

from typing import Any

import numpy as np


def label_bbox_stats(
    mesh: Any,
    face_ids: np.ndarray,
    label: int,
) -> dict[str, float]:
    """AABB stats for one label (triangle centers)."""
    mask = np.asarray(face_ids) == int(label)
    if not mask.any():
        return {"thin": 1.0, "aspect": 1.0, "area_frac": 0.0, "up_mean": 0.0}
    centers = np.asarray(mesh.triangles_center, dtype=np.float64)[mask]
    areas = np.asarray(mesh.area_faces, dtype=np.float64)
    total = max(float(areas.sum()), 1e-12)
    ext = centers.max(axis=0) - centers.min(axis=0)
    lo, hi = float(ext.min()), float(ext.max())
    up = int(np.argmax(np.ptp(np.asarray(mesh.vertices, dtype=np.float64), axis=0)))
    return {
        "thin": lo / max(hi, 1e-12),
        "aspect": hi / max(lo, 1e-12),
        "area_frac": float(areas[mask].sum() / total),
        "up_mean": float(centers[:, up].mean()),
    }


def thin_labels(
    mesh: Any,
    face_ids: np.ndarray,
    *,
    max_thin: float = 0.28,
    min_aspect: float = 3.0,
    max_area_frac: float = 0.35,
) -> list[int]:
    """Labels that look like protrusions (ladder / flag / rail)."""
    out: list[int] = []
    for label in sorted(int(x) for x in np.unique(face_ids) if x >= 0):
        st = label_bbox_stats(mesh, face_ids, label)
        if st["area_frac"] > max_area_frac:
            continue
        if st["thin"] <= max_thin or st["aspect"] >= min_aspect:
            out.append(label)
    return out


def fuse_protrusion_labels(
    mesh: Any,
    base_ids: np.ndarray,
    donor_ids: np.ndarray,
    *,
    max_thin: float = 0.28,
    min_aspect: float = 3.0,
    max_area_frac: float = 0.35,
    min_donor_new_frac: float = 0.4,
) -> np.ndarray:
    """Keep base peels; add donor peels that cover new faces (flag vs ladder).

    Typical use: ``base`` = P3-SAM faces (boa escada), ``donor`` = hybrid/fine
    (boa bandeira). Faces already in a base thin label stay; donor thin faces
    mostly outside base thins get a new label.
    """
    base = np.asarray(base_ids, dtype=np.int64).copy()
    donor = np.asarray(donor_ids, dtype=np.int64)
    if base.shape != donor.shape:
        raise ValueError(f"label maps must match: {base.shape} vs {donor.shape}")
    if base.shape[0] != len(mesh.faces):
        raise ValueError("face_ids length must match mesh faces")

    base_thin = set(thin_labels(mesh, base, max_thin=max_thin, min_aspect=min_aspect, max_area_frac=max_area_frac))
    donor_thin = thin_labels(mesh, donor, max_thin=max_thin, min_aspect=min_aspect, max_area_frac=max_area_frac)
    if not donor_thin:
        return base

    in_base_thin = np.isin(base, list(base_thin)) if base_thin else np.zeros(base.shape[0], dtype=bool)
    next_id = int(np.max(base[base >= 0], initial=-1)) + 1

    for dlab in donor_thin:
        dmask = donor == dlab
        if not dmask.any():
            continue
        # Só faces do donor thin que ainda não são peel base.
        fresh = dmask & ~in_base_thin
        fresh_frac = float(fresh.sum()) / float(dmask.sum())
        if fresh_frac < min_donor_new_frac:
            continue  # já coberto pela escada/base peel
        base[fresh] = next_id
        next_id += 1

    return base
