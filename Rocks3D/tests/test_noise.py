"""Tests for rocks3d.noise."""

from __future__ import annotations

import numpy as np
import pytest
from numpy.testing import assert_allclose, assert_array_equal
from rocks3d.noise import _simplex3_batch, fbm3, perlin3, simplex3, worley3

# The module docstring documents simplex output as "approximately [-1, 1]".
# Empirical max is ~0.9; we assert the documented theoretical bound of 1.0.
SIMPLEX_ABS_BOUND = 1.0


class TestSimplex3Determinism:
    def test_same_args_bit_identical(self) -> None:
        a = simplex3(1.5, -2.3, 0.7, seed=42)
        b = simplex3(1.5, -2.3, 0.7, seed=42)
        assert_array_equal([a], [b])

    def test_different_seed_usually_different(self) -> None:
        a = simplex3(1.1, 2.2, 3.3, seed=1)
        b = simplex3(1.1, 2.2, 3.3, seed=2)
        assert a != b

    def test_default_seed_is_zero(self) -> None:
        explicit = simplex3(0.4, 0.5, 0.6, seed=0)
        default = simplex3(0.4, 0.5, 0.6)
        assert_array_equal([explicit], [default])


class TestSimplex3Range:
    def test_output_within_documented_range(self) -> None:
        coords = np.linspace(-5.0, 5.0, 20)
        for x in coords:
            for y in coords:
                for z in coords:
                    v = simplex3(x, y, z, seed=0)
                    assert -SIMPLEX_ABS_BOUND <= v <= SIMPLEX_ABS_BOUND, f"out of range at ({x},{y},{z}): {v}"

    def test_output_is_python_float(self) -> None:
        v = simplex3(0.3, 0.4, 0.5, seed=0)
        assert isinstance(v, float)
        assert np.isfinite(v)


class TestSimplex3Smoothness:
    def test_nearby_inputs_nearby_outputs(self) -> None:
        x0, y0, z0 = 1.1, 2.2, 3.3
        eps = 1e-4
        v0 = simplex3(x0, y0, z0, seed=0)
        v1 = simplex3(x0 + eps, y0, z0, seed=0)
        # Simplex gradient magnitude is bounded; a local Lipschitz constant of
        # 10 comfortably upper-bounds the output step for an input step of eps.
        assert abs(v1 - v0) <= 10.0 * eps

    def test_no_discontinuities_along_line(self) -> None:
        t = np.linspace(0.0, 10.0, 500)
        step = t[1] - t[0]
        prev = simplex3(t[0], 0.0, 0.0, seed=0)
        for xi in t[1:]:
            cur = simplex3(float(xi), 0.0, 0.0, seed=0)
            assert abs(cur - prev) <= 10.0 * step
            prev = cur


class TestSimplex3EdgeCases:
    def test_origin_is_finite_and_in_range(self) -> None:
        v = simplex3(0.0, 0.0, 0.0, seed=0)
        assert np.isfinite(v)
        assert abs(v) <= SIMPLEX_ABS_BOUND

    def test_negative_coordinates(self) -> None:
        for coords in [(-1.0, -1.0, -1.0), (-3.7, 2.1, -0.5), (0.0, -5.0, 5.0)]:
            v = simplex3(*coords, seed=0)
            assert np.isfinite(v)
            assert abs(v) <= SIMPLEX_ABS_BOUND

    def test_large_coordinates_no_nan(self) -> None:
        big = 1.0e4
        for coords in [(big, big, big), (-big, -big, -big), (big, -big, big)]:
            v = simplex3(*coords, seed=0)
            assert np.isfinite(v), f"non-finite at {coords}: {v}"
            assert abs(v) <= SIMPLEX_ABS_BOUND

    def test_very_large_coordinates_still_finite(self) -> None:
        v = simplex3(1.0e6, 1.0e6, 1.0e6, seed=0)
        assert np.isfinite(v)


class TestSimplex3Batch:
    def test_batch_matches_scalar_elementwise(self) -> None:
        grid = np.linspace(-2.0, 2.0, 5)
        pts = np.array([[x, y, z] for x in grid for y in grid for z in grid])
        xs, ys, zs = pts[:, 0], pts[:, 1], pts[:, 2]
        seed = 7
        batch = _simplex3_batch(xs, ys, zs, seed=seed)
        scalar = np.array([simplex3(float(x), float(y), float(z), seed=seed) for x, y, z in pts])
        assert_allclose(batch, scalar, rtol=0.0, atol=0.0)

    def test_batch_shape_and_dtype(self) -> None:
        xs = np.array([0.0, 1.0, 2.0])
        ys = np.array([0.0, 1.0, 2.0])
        zs = np.array([0.0, 1.0, 2.0])
        out = _simplex3_batch(xs, ys, zs, seed=0)
        assert out.shape == (3,)
        assert out.dtype == np.float64

    def test_batch_deterministic(self) -> None:
        xs = np.linspace(0.0, 3.0, 10)
        ys = np.linspace(-1.0, 1.0, 10)
        zs = np.linspace(2.0, 4.0, 10)
        a = _simplex3_batch(xs, ys, zs, seed=11)
        b = _simplex3_batch(xs, ys, zs, seed=11)
        assert_array_equal(a, b)

    def test_batch_within_range(self) -> None:
        xs = np.linspace(-4.0, 4.0, 25)
        ys = np.linspace(-4.0, 4.0, 25)
        zs = np.linspace(-4.0, 4.0, 25)
        out = _simplex3_batch(xs, ys, zs, seed=3)
        assert np.all(np.abs(out) <= SIMPLEX_ABS_BOUND)


class TestPerlin3NotImplemented:
    def test_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="perlin3"):
            perlin3(0.0, 0.0, 0.0)

    def test_raises_regardless_of_seed(self) -> None:
        for seed in [0, 1, 99]:
            with pytest.raises(NotImplementedError):
                perlin3(1.0, 2.0, 3.0, seed=seed)


class TestWorley3NotImplemented:
    def test_raises_not_implemented(self) -> None:
        with pytest.raises(NotImplementedError, match="worley3"):
            worley3(0.0, 0.0, 0.0)

    def test_raises_regardless_of_seed(self) -> None:
        for seed in [0, 5, 42]:
            with pytest.raises(NotImplementedError):
                worley3(1.0, 2.0, 3.0, seed=seed)


class TestFbm3Determinism:
    def test_same_seed_same_result(self) -> None:
        pts = np.array([[0.5, 0.5, 0.5], [1.0, 2.0, 3.0], [-1.1, 0.2, 2.3]])
        a = fbm3(pts, octaves=4, seed=7)
        b = fbm3(pts, octaves=4, seed=7)
        assert_array_equal(a, b)

    def test_different_seed_different_result(self) -> None:
        pts = np.array([[0.5, 0.5, 0.5], [1.0, 2.0, 3.0]])
        a = fbm3(pts, seed=1)
        b = fbm3(pts, seed=2)
        assert not np.allclose(a, b)


class TestFbm3Range:
    def test_output_within_unit_bound(self) -> None:
        grid = np.linspace(-3.0, 3.0, 12)
        pts = np.array([[x, y, z] for x in grid for y in grid for z in grid])
        out = fbm3(pts, octaves=4, seed=0)
        assert np.all(np.abs(out) <= 1.0)

    def test_output_shape_matches_n(self) -> None:
        pts = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0], [2.0, 2.0, 2.0]])
        out = fbm3(pts, seed=0)
        assert out.shape == (3,)
        assert out.dtype == np.float64

    def test_output_all_finite(self) -> None:
        grid = np.linspace(-5.0, 5.0, 10)
        pts = np.array([[x, y, z] for x in grid for y in grid for z in grid])
        out = fbm3(pts, octaves=5, seed=0)
        assert np.all(np.isfinite(out))


class TestFbm3Octaves:
    def test_detail_grows_with_octaves(self) -> None:
        """Total variation along a line increases as octaves are added.

        ``fbm3`` normalises by ``max_amplitude``, so overall variance does not
        grow with octaves — but the high-frequency detail (total variation) does.
        """
        t = np.linspace(0.0, 5.0, 200)
        pts = np.column_stack([t, np.zeros_like(t), np.zeros_like(t)])
        tv_low = np.sum(np.abs(np.diff(fbm3(pts, octaves=1, seed=0))))
        tv_high = np.sum(np.abs(np.diff(fbm3(pts, octaves=6, seed=0))))
        assert tv_high > tv_low

    def test_single_octave_matches_persistence_zero(self) -> None:
        """``octaves=N, persistence=0`` is equivalent to ``octaves=1``.

        Zero persistence nullifies every octave after the first, and the
        normalisation factor collapses to the first amplitude.
        """
        pts = np.array([[0.3, 0.7, 1.1], [-0.5, 2.0, -1.3]])
        one = fbm3(pts, octaves=1, seed=4)
        collapsed = fbm3(pts, octaves=4, persistence=0.0, seed=4)
        assert_allclose(collapsed, one, rtol=0.0, atol=0.0)

    def test_zero_octaves_returns_zeros(self) -> None:
        # No octaves summed → max_amplitude stays 0 → the guard returns the zero array.
        pts = np.array([[0.5, 0.5, 0.5], [1.0, 2.0, 3.0]])
        out = fbm3(pts, octaves=0, seed=0)
        assert_array_equal(out, np.zeros(2))


class TestFbm3Parameters:
    def test_lacunarity_changes_output(self) -> None:
        pts = np.array([[0.4, 0.9, 1.7], [2.1, -0.3, 0.5], [-1.0, 1.0, -2.0]])
        base = fbm3(pts, octaves=3, lacunarity=2.0, seed=0)
        stretched = fbm3(pts, octaves=3, lacunarity=3.0, seed=0)
        assert not np.allclose(base, stretched)

    def test_persistence_changes_output(self) -> None:
        pts = np.array([[0.4, 0.9, 1.7], [2.1, -0.3, 0.5], [-1.0, 1.0, -2.0]])
        low = fbm3(pts, octaves=3, persistence=0.5, seed=0)
        high = fbm3(pts, octaves=3, persistence=0.9, seed=0)
        assert not np.allclose(low, high)

    def test_frequency_scales_coordinates(self) -> None:
        pts = np.array([[0.5, 1.0, 1.5], [-0.7, 0.3, 2.1]])
        k = 2.5
        via_param = fbm3(pts, octaves=2, frequency=k, seed=0)
        via_scaled = fbm3(pts * k, octaves=2, frequency=1.0, seed=0)
        assert_allclose(via_param, via_scaled, rtol=0.0, atol=0.0)


class TestFbm3Validation:
    def test_unsupported_noise_type_raises(self) -> None:
        pts = np.array([[0.0, 0.0, 0.0]])
        with pytest.raises(ValueError, match="simplex"):
            fbm3(pts, noise_type="perlin")

    def test_wrong_inner_dimension_raises(self) -> None:
        bad = np.zeros((4, 2))
        with pytest.raises(ValueError, match=r"\(N, 3\)"):
            fbm3(bad, seed=0)

    def test_1d_input_is_reshaped(self) -> None:
        row = np.array([0.5, 1.0, 1.5])
        out = fbm3(row, seed=0)
        assert out.shape == (1,)


class TestFbm3EdgeCases:
    def test_origin_input(self) -> None:
        out = fbm3(np.array([[0.0, 0.0, 0.0]]), octaves=4, seed=0)
        assert np.isfinite(out[0])
        assert abs(out[0]) <= 1.0

    def test_negative_coordinates(self) -> None:
        pts = np.array([[-1.0, -2.0, -3.0], [-0.3, 0.4, -0.5]])
        out = fbm3(pts, octaves=3, seed=0)
        assert np.all(np.isfinite(out))
        assert np.all(np.abs(out) <= 1.0)

    def test_large_coordinates_finite(self) -> None:
        big = 1.0e4
        pts = np.array([[big, big, big], [-big, big, -big]])
        out = fbm3(pts, octaves=3, seed=0)
        assert np.all(np.isfinite(out))
