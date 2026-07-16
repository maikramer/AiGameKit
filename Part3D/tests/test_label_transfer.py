from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from part3d.utils.label_transfer import transfer_face_labels, validate_proxy_alignment  # noqa: E402


def _split_plane(subdivisions: int) -> trimesh.Trimesh:
    vertices = []
    faces = []
    for side in range(2):
        x0 = float(side)
        for i in range(subdivisions):
            xa = x0 + i / subdivisions
            xb = x0 + (i + 1) / subdivisions
            base = len(vertices)
            vertices.extend(((xa, 0, 0), (xb, 0, 0), (xb, 1, 0), (xa, 1, 0)))
            faces.extend(((base, base + 1, base + 2), (base, base + 2, base + 3)))
    return trimesh.Trimesh(vertices=np.asarray(vertices), faces=np.asarray(faces), process=False)


def test_transfer_face_labels_from_proxy_to_dense_mesh() -> None:
    proxy = _split_plane(1)
    target = _split_plane(8)
    proxy_ids = np.array([3, 3, 7, 7])

    transferred = transfer_face_labels(proxy, proxy_ids, target, neighbors=2)

    centers = target.triangles_center
    assert np.all(transferred[centers[:, 0] < 1.0] == 3)
    assert np.all(transferred[centers[:, 0] > 1.0] == 7)


def test_proxy_alignment_rejects_different_object_space() -> None:
    proxy = _split_plane(1)
    target = _split_plane(2)
    target.apply_translation((10, 0, 0))

    with pytest.raises(ValueError, match="not aligned"):
        validate_proxy_alignment(proxy, target)
