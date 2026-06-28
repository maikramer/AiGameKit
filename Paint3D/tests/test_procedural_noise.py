"""Testes do ruído procedural 3D (valor/trilinear/FBM) — sem deps pesadas."""

from __future__ import annotations

import numpy as np
import pytest

from paint3d.procedural_noise import (
    _noise3_trilinear,
    _v01,
    fbm3,
    normalize_to_unit_cube,
)


class TestV01:
    def test_output_in_unit_range(self) -> None:
        ix = np.array([0, 1, 2, -3, 10], dtype=np.int64)
        iy = np.array([5, -2, 7, 0, 3], dtype=np.int64)
        iz = np.array([1, 2, -1, 4, 9], dtype=np.int64)
        out = _v01(ix, iy, iz, seed=42)
        assert out.shape == (5,)
        assert np.all(out >= -1.0)
        # fractional part x - floor(x) is in [0, 1) so the value is strictly below 1.0
        assert np.all(out < 1.0)

    def test_deterministic_same_inputs(self) -> None:
        args = (np.array([1, 2]), np.array([3, 4]), np.array([5, 6]))
        a = _v01(*args, seed=7)
        b = _v01(*args, seed=7)
        np.testing.assert_array_equal(a, b)

    def test_different_seed_changes_output(self) -> None:
        coords = (np.array([1]), np.array([2]), np.array([3]))
        a = _v01(*coords, seed=1)
        b = _v01(*coords, seed=999)
        assert not np.array_equal(a, b)


class TestNoise3Trilinear:
    def test_empty_input_returns_empty(self) -> None:
        out = _noise3_trilinear(np.zeros((0, 3)), seed=0)
        assert out.shape == (0,)
        assert out.dtype == np.float64

    def test_output_in_unit_range(self) -> None:
        pts = np.random.default_rng(0).uniform(-5.0, 5.0, size=(64, 3))
        out = _noise3_trilinear(pts, seed=3)
        assert out.shape == (64,)
        assert np.all(out >= -1.0)
        assert np.all(out <= 1.0)

    def test_integer_lattice_equals_corner_value(self) -> None:
        """At integer lattice points f=0, so trilinear collapses to corner 0."""
        ipts = np.array([[2.0, 3.0, 5.0], [-1.0, 4.0, 0.0]])
        out = _noise3_trilinear(ipts, seed=11)
        expected = _v01(
            np.array([2, -1], dtype=np.int64),
            np.array([3, 4], dtype=np.int64),
            np.array([5, 0], dtype=np.int64),
            seed=11,
        )
        np.testing.assert_allclose(out, expected, atol=1e-9)


class TestFbm3:
    def test_empty_input_returns_empty(self) -> None:
        out = fbm3(np.zeros((0, 3)), seed=0)
        assert out.shape == (0,)
        assert out.dtype == np.float64

    def test_output_clipped_to_unit_range(self) -> None:
        pts = np.random.default_rng(1).uniform(-2.0, 2.0, size=(128, 3))
        out = fbm3(pts, frequency=4.0, octaves=4, seed=5)
        assert out.shape == (128,)
        assert np.all(out >= -1.0)
        assert np.all(out <= 1.0)

    def test_deterministic_for_fixed_seed(self) -> None:
        pts = np.random.default_rng(2).uniform(-1.0, 1.0, size=(32, 3))
        a = fbm3(pts, octaves=3, seed=12)
        b = fbm3(pts, octaves=3, seed=12)
        np.testing.assert_array_equal(a, b)

    def test_octaves_clamped_to_minimum_one(self) -> None:
        pts = np.random.default_rng(3).uniform(-1.0, 1.0, size=(16, 3))
        # max(1, min(0, 8)) == 1 → identical to an explicit single octave
        np.testing.assert_array_equal(fbm3(pts, octaves=0, seed=4), fbm3(pts, octaves=1, seed=4))

    def test_octaves_clamped_to_maximum_eight(self) -> None:
        pts = np.random.default_rng(3).uniform(-1.0, 1.0, size=(16, 3))
        # min(100, 8) == 8 → identical to eight octaves
        np.testing.assert_array_equal(fbm3(pts, octaves=100, seed=4), fbm3(pts, octaves=8, seed=4))

    def test_increasing_octaves_changes_output(self) -> None:
        pts = np.random.default_rng(4).uniform(-1.0, 1.0, size=(32, 3))
        low = fbm3(pts, octaves=1, seed=8)
        high = fbm3(pts, octaves=5, seed=8)
        assert not np.allclose(low, high)


class TestNormalizeToUnitCube:
    def test_empty_passthrough(self) -> None:
        out = normalize_to_unit_cube(np.zeros((0, 3)))
        assert out.size == 0

    def test_centered_near_origin(self) -> None:
        pts = np.array([[10.0, 10.0, 10.0], [12.0, 12.0, 12.0], [11.0, 11.0, 11.0]])
        out = normalize_to_unit_cube(pts)
        np.testing.assert_allclose(out.mean(axis=0), 0.0, atol=1e-9)

    def test_max_extent_maps_to_unit(self) -> None:
        pts = np.array([[-2.0, -2.0, -2.0], [2.0, 2.0, 2.0]])
        out = normalize_to_unit_cube(pts)
        assert np.max(np.abs(out)) == pytest.approx(1.0, abs=1e-9)

    def test_degenerate_no_nan(self) -> None:
        """Coincident points → zero extent → centered only, no division."""
        pts = np.array([[5.0, 5.0, 5.0], [5.0, 5.0, 5.0]])
        out = normalize_to_unit_cube(pts)
        assert out.shape == (2, 3)
        assert np.all(np.isfinite(out))
        np.testing.assert_allclose(out, 0.0, atol=1e-9)
