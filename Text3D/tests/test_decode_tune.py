"""Testes das fórmulas puras de decode_tune (bounds/mc_level/chunks) — sem GPU."""

from __future__ import annotations

import pytest

from text3d.decode_tune import (
    DEFAULT_BOX_V,
    MAX_NUM_CHUNKS,
    MIN_NUM_CHUNKS,
    SURFACE_DECODER_MIN_OCTREE,
    auto_mc_level,
    auto_num_chunks,
    bounds_for_bbox,
    bytes_per_query_default,
    prefer_surface_decoder,
    resolve_mc_level,
)


class TestBoundsForBbox:
    def test_none_input(self):
        assert bounds_for_bbox(None) is None

    def test_cubic_aspect_keeps_cube(self):
        # Aspecto ~cúbico → sem ganho → None (cubo clássico).
        assert bounds_for_bbox([1.0, 1.0, 1.0]) is None
        assert bounds_for_bbox([1.0, 0.95, 0.98]) is None

    def test_flat_asset_shrinks_thin_axes(self):
        # Porta: 0.55 x 1.0 x 0.12 — eixo fino ganha voxels ~5x mais finos.
        b = bounds_for_bbox([0.55, 1.0, 0.12])
        assert b is not None and len(b) == 6
        ex, ey, ez = b[3], b[4], b[5]
        assert b[0] == -ex and b[1] == -ey and b[2] == -ez
        assert ey == DEFAULT_BOX_V  # eixo maior mantém margem clássica
        assert ex < ey
        assert ez < ex
        assert ez >= 0.20  # piso anti-leakage

    def test_sword_floor_applies(self):
        # Espada 0.12,1.0,0.06 — eixos finos ficam no piso, nunca abaixo.
        b = bounds_for_bbox([0.12, 1.0, 0.06])
        assert b is not None
        assert b[3] >= 0.20 and b[5] >= 0.20

    def test_aabb_6float_input(self):
        b = bounds_for_bbox([-0.275, -0.5, -0.06, 0.275, 0.5, 0.06])
        assert b is not None
        assert b[4] == DEFAULT_BOX_V

    def test_invalid_inputs(self):
        assert bounds_for_bbox([0.0, 0.0, 0.0]) is None
        assert bounds_for_bbox([1.0, -0.5, 0.3]) is None
        assert bounds_for_bbox([1.0, 2.0]) is None

    def test_extents_never_exceed_box_v(self):
        b = bounds_for_bbox([1.0, 1.0, 0.1])
        assert b is not None
        assert all(abs(v) <= DEFAULT_BOX_V + 1e-9 for v in b)


class TestAutoMcLevel:
    def test_matches_upstream_at_512(self):
        assert auto_mc_level(512) == pytest.approx(-1.0 / 512)

    def test_scales_with_octree(self):
        assert auto_mc_level(256) == pytest.approx(-1.0 / 256)
        assert abs(auto_mc_level(256)) > abs(auto_mc_level(512))

    def test_clamped(self):
        assert auto_mc_level(1) >= -0.01

    def test_resolve_auto_and_none(self):
        assert resolve_mc_level("auto", 512) == pytest.approx(-1.0 / 512)
        assert resolve_mc_level(None, 512) == pytest.approx(-1.0 / 512)

    def test_resolve_explicit(self):
        assert resolve_mc_level(0.0, 384) == 0.0
        assert resolve_mc_level(-0.005, 384) == -0.005
        assert resolve_mc_level("-0.002", 384) == -0.002


class TestPreferSurfaceDecoder:
    def test_vanilla_high_octree(self):
        assert prefer_surface_decoder("vanilla", SURFACE_DECODER_MIN_OCTREE) is True
        assert prefer_surface_decoder("hierarchical", 512) is True

    def test_vanilla_low_octree(self):
        assert prefer_surface_decoder("vanilla", 384) is False

    def test_flashvdm_untouched(self):
        assert prefer_surface_decoder("flashvdm", 512) is False


class TestAutoNumChunks:
    def test_no_signal(self):
        assert auto_num_chunks(None) is None
        assert auto_num_chunks(0) is None

    def test_scales_with_free_vram(self):
        gib = 1024**3
        small = auto_num_chunks(1 * gib)
        big = auto_num_chunks(8 * gib)
        assert small is not None and big is not None
        assert big > small

    def test_clamps(self):
        # Soft floor: 1 byte livre → nada cabe → None (não força MIN e OOM).
        assert auto_num_chunks(1) is None
        assert auto_num_chunks(10_000 * 1024**3) == MAX_NUM_CHUNKS
        # Com margem para o soft floor, devolve ≥ MIN.
        free_for_min = int(MIN_NUM_CHUNKS * 96 * 1024 / 0.7) + 1024
        assert auto_num_chunks(free_for_min) == MIN_NUM_CHUNKS

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("TEXT3D_DECODE_BYTES_PER_QUERY", "1024")
        assert bytes_per_query_default() == 1024
        gib = 1024**3
        assert auto_num_chunks(1 * gib) == MAX_NUM_CHUNKS  # 1 KiB/query → clamp topo

    def test_env_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("TEXT3D_DECODE_BYTES_PER_QUERY", "abc")
        assert bytes_per_query_default() == 96 * 1024

    def test_explicit_bytes_per_query(self):
        gib = 1024**3
        n = auto_num_chunks(4 * gib, bytes_per_query=96 * 1024, fraction=0.7)
        assert n == int(4 * gib * 0.7 / (96 * 1024))
