"""Testes do autotune (sem GPU)."""

from __future__ import annotations

import pytest

pytest.importorskip("trimesh")

import numpy as np
import trimesh

from part3d.utils.autotune import (
    _compute_cond_batch_size,
    autotune_generate,
    autotune_segment,
    get_max_parts_for_vram,
    mesh_geometry_score,
)


def _box_mesh() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=[1, 1, 1])


def _dense_sphere() -> trimesh.Trimesh:
    return trimesh.creation.icosphere(subdivisions=4)


def test_geometry_score_increases_with_complexity() -> None:
    s = mesh_geometry_score(_box_mesh())
    d = mesh_geometry_score(_dense_sphere())
    assert d >= s


def test_low_vram_more_aggressive_than_high_vram() -> None:
    mesh = _box_mesh()
    low = autotune_segment(mesh, vram_gb=5.0)
    high = autotune_segment(mesh, vram_gb=20.0)
    assert low.pressure_index >= high.pressure_index
    assert low.point_num <= high.point_num


def test_many_parts_reduces_quality_vs_few() -> None:
    mesh = _box_mesh()
    few = autotune_generate(mesh, num_parts=4, vram_gb=12.0)
    many = autotune_generate(mesh, num_parts=24, vram_gb=12.0)
    assert many.pressure_index >= few.pressure_index
    assert many.octree_resolution <= few.octree_resolution


def test_numpy_types_for_part_count() -> None:
    mesh = _box_mesh()
    g = autotune_generate(mesh, int(np.int64(8)), vram_gb=10.0)
    assert g.num_parts == 8


# ---- cond_batch_size (chunked conditioner) ----


def test_cond_batch_size_low_vram_is_small() -> None:
    """GPU com 5.6 GB não cabe 7 partes de uma vez."""
    bs = _compute_cond_batch_size(7, vram_gb=5.6)
    assert 1 <= bs < 7


def test_cond_batch_size_high_vram_fits_all() -> None:
    """GPU com 24 GB cabe tudo de uma vez."""
    bs = _compute_cond_batch_size(7, vram_gb=24.0)
    assert bs == 7


def test_cond_batch_size_never_exceeds_num_parts() -> None:
    bs = _compute_cond_batch_size(3, vram_gb=48.0)
    assert bs == 3


def test_cond_batch_size_at_least_1() -> None:
    bs = _compute_cond_batch_size(10, vram_gb=1.0)
    assert bs >= 1


def test_autotune_generate_includes_cond_batch() -> None:
    mesh = _box_mesh()
    g = autotune_generate(mesh, num_parts=7, vram_gb=5.6)
    assert hasattr(g, "cond_batch_size")
    assert 1 <= g.cond_batch_size <= 7
    assert hasattr(g, "max_parts_allowed")
    assert g.max_parts_allowed >= 1


def test_max_parts_for_vram_low_vram_limits_parts() -> None:
    """Com 5.6 GB, deve limitar a ~1 parte (DiT ≈ 3.6 GB + VAE + ativações)."""
    max_p = get_max_parts_for_vram(5.6)
    assert max_p is not None
    assert 1 <= max_p <= 2  # Esperado 1 parte com VRAM muito limitada


def test_max_parts_for_vram_high_vram_allows_more() -> None:
    """Com 24 GB, deve permitir muitas partes (até o cap de 16)."""
    max_p = get_max_parts_for_vram(24.0)
    assert max_p is not None
    assert max_p >= 10


def test_max_parts_quantized_allows_at_least_as_many_as_fp16() -> None:
    fp = get_max_parts_for_vram(5.6, dit_quantized=False)
    q = get_max_parts_for_vram(5.6, dit_quantized=True)
    assert fp is not None and q is not None
    assert q >= fp


def test_autotune_low_vram_keeps_steps_when_dit_quantized() -> None:
    mesh = _box_mesh()
    g_q = autotune_generate(mesh, num_parts=6, vram_gb=5.6, dit_quantized=True)
    g_fp = autotune_generate(mesh, num_parts=6, vram_gb=5.6, dit_quantized=False)
    assert g_q.num_inference_steps >= g_fp.num_inference_steps
