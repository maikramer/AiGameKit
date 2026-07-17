from __future__ import annotations

from types import SimpleNamespace

import numpy as np
from part3d.utils.label_fuse import fuse_protrusion_labels, thin_labels


def _mesh(n: int) -> SimpleNamespace:
    # Fake tall ladder (label 0) + body (1) + high flag in donor (2).
    centers = np.zeros((n, 3), dtype=np.float64)
    areas = np.ones(n, dtype=np.float64)
    # 0..9 ladder along Y
    centers[:10, 1] = np.linspace(0, 1, 10)
    centers[:10, 0] = 0.01
    # 10..39 body
    centers[10:40, 0] = np.linspace(-0.3, 0.3, 30)
    centers[10:40, 1] = 0.5
    centers[10:40, 2] = np.linspace(-0.3, 0.3, 30)
    # 40..49 flag high
    centers[40:, 1] = 1.2
    centers[40:, 0] = 0.02
    return SimpleNamespace(
        triangles_center=centers,
        area_faces=areas,
        faces=np.zeros((n, 3), dtype=np.int64),
        vertices=np.array([[0, 0, 0], [0, 1.5, 0], [1, 0, 0]], dtype=np.float64),
    )


def test_thin_labels_detects_ladder() -> None:
    mesh = _mesh(50)
    ids = np.array([0] * 10 + [1] * 40)
    assert 0 in thin_labels(mesh, ids, max_area_frac=0.5)


def test_fuse_adds_donor_flag_keeps_base_ladder() -> None:
    mesh = _mesh(50)
    base = np.array([0] * 10 + [1] * 40)  # ladder + body (flag stuck in body)
    donor = np.array([0] * 8 + [1] * 32 + [2] * 10)  # fractured ladder + flag
    # Make donor ladder overlap base ladder so it is skipped; flag faces fresh.
    donor[:10] = 0
    donor[40:] = 2
    fused = fuse_protrusion_labels(mesh, base, donor, max_area_frac=0.5, min_donor_new_frac=0.3)
    assert set(fused[:10]) == {0}  # ladder preserved
    assert len(set(fused[40:])) == 1
    assert fused[40] != 1  # flag peeled from body
