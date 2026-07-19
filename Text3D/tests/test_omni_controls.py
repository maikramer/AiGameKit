"""Testes unitários dos helpers de controlo Omni (sem GPU / sem pesos)."""

from __future__ import annotations

import numpy as np
import pytest
import torch

from text3d.utils.omni_controls import (
    DEFAULT_OMNI_BBOX,
    bbox_tensor,
    normalize_mesh_vertices,
    resolve_control_kwargs,
)


def test_default_bbox_tuple() -> None:
    assert DEFAULT_OMNI_BBOX == (1.0, 1.0, 1.0)


def test_bbox_tensor_3() -> None:
    t = bbox_tensor([0.8, 0.64, 1.0], device="cpu", dtype=torch.float32)
    assert t.shape == (1, 1, 3)
    assert torch.allclose(t, torch.tensor([[[0.8, 0.64, 1.0]]]))


def test_bbox_tensor_6_aabb() -> None:
    t = bbox_tensor([0, 0, 0, 2, 1, 1], device="cpu", dtype=torch.float32)
    assert t.shape == (1, 1, 3)
    # size=(2,1,1) → normalizado pelo max → (1, 0.5, 0.5)
    assert torch.allclose(t, torch.tensor([[[1.0, 0.5, 0.5]]]))


def test_bbox_tensor_bad_len() -> None:
    with pytest.raises(ValueError, match="3 ou 6"):
        bbox_tensor([1.0, 2.0], device="cpu", dtype=torch.float32)


def test_normalize_mesh_vertices_centered() -> None:
    verts = np.array([[0.0, 0.0, 0.0], [2.0, 0.0, 0.0], [0.0, 4.0, 0.0]], dtype=np.float64)
    out = normalize_mesh_vertices(verts, scale=1.0)
    assert out.min() >= -1.0 - 1e-6
    assert out.max() <= 1.0 + 1e-6


def test_resolve_none_uses_default_bbox() -> None:
    kw = resolve_control_kwargs("none", device="cpu", dtype=torch.float32)
    assert "bbox" in kw
    assert kw["bbox"].shape == (1, 1, 3)


def test_resolve_bbox_requires_values() -> None:
    with pytest.raises(ValueError, match="bbox"):
        resolve_control_kwargs("bbox", device="cpu", dtype=torch.float32)


def test_resolve_invalid_type() -> None:
    with pytest.raises(ValueError, match="control_type"):
        resolve_control_kwargs("banana", device="cpu", dtype=torch.float32)
