"""Testes para os helpers de numpy puro em ``text3d.utils.mesh_base_plane``."""

from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np

from text3d.utils.mesh_base_plane import (
    _rotmat_unit_a_to_b,
    _translation_matrix_np,
    _weighted_median_1d,
    _weighted_plane_normal,
)


class TestRotmatUnitAToB:
    def test_identity_when_vectors_equal(self) -> None:
        a = np.array([0.0, 0.0, 1.0])
        r = _rotmat_unit_a_to_b(a, a.copy())
        np.testing.assert_allclose(r, np.eye(3), atol=1e-10)

    def test_rotates_a_to_b(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([0.0, 1.0, 0.0])
        r = _rotmat_unit_a_to_b(a, b)
        np.testing.assert_allclose(r @ a, b, atol=1e-10)

    def test_arbitrary_pair(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([-2.0, 0.5, 4.0])
        r = _rotmat_unit_a_to_b(a, b)
        result = r @ (a / np.linalg.norm(a))
        np.testing.assert_allclose(result, b / np.linalg.norm(b), atol=1e-9)

    def test_antiparallel_180(self) -> None:
        a = np.array([1.0, 0.0, 0.0])
        b = np.array([-1.0, 0.0, 0.0])
        r = _rotmat_unit_a_to_b(a, b)
        np.testing.assert_allclose(r @ a, b, atol=1e-9)

    def test_antiparallel_arbitrary_axis(self) -> None:
        a = np.array([0.3, -0.8, 0.5])
        b = -a
        r = _rotmat_unit_a_to_b(a, b)
        a_hat = a / np.linalg.norm(a)
        np.testing.assert_allclose(r @ a_hat, -a_hat, atol=1e-9)

    def test_zero_vector_a_returns_identity(self) -> None:
        r = _rotmat_unit_a_to_b(np.zeros(3), np.array([1.0, 0.0, 0.0]))
        np.testing.assert_allclose(r, np.eye(3), atol=1e-12)

    def test_zero_vector_b_returns_identity(self) -> None:
        r = _rotmat_unit_a_to_b(np.array([1.0, 0.0, 0.0]), np.zeros(3))
        np.testing.assert_allclose(r, np.eye(3), atol=1e-12)

    def test_returns_3x3_float64(self) -> None:
        r = _rotmat_unit_a_to_b(np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
        assert r.shape == (3, 3)
        assert r.dtype == np.float64

    def test_is_orthonormal(self) -> None:
        a = np.array([1.0, 2.0, 3.0])
        b = np.array([-2.0, 0.5, 4.0])
        r = _rotmat_unit_a_to_b(a, b)
        np.testing.assert_allclose(r @ r.T, np.eye(3), atol=1e-9)
        np.testing.assert_allclose(np.linalg.det(r), 1.0, atol=1e-9)


class TestWeightedMedian1d:
    def test_empty_returns_zero(self) -> None:
        assert _weighted_median_1d(np.array([]), np.array([])) == 0.0

    def test_single_element_returns_itself(self) -> None:
        assert _weighted_median_1d(np.array([7.5]), np.array([3.0])) == 7.5

    def test_uniform_weights_is_median(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([1.0, 1.0, 1.0])
        assert _weighted_median_1d(values, weights) == 2.0

    def test_skewed_weight_towards_low(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([10.0, 1.0, 1.0])
        assert _weighted_median_1d(values, weights) == 1.0

    def test_skewed_weight_towards_high(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([1.0, 1.0, 10.0])
        assert _weighted_median_1d(values, weights) == 3.0

    def test_zero_weight_clipped(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([0.0, 1.0, 1.0])
        result = _weighted_median_1d(values, weights)
        assert 1.0 <= result <= 3.0

    def test_negative_weight_clipped(self) -> None:
        values = np.array([1.0, 2.0, 3.0])
        weights = np.array([-5.0, 1.0, 1.0])
        result = _weighted_median_1d(values, weights)
        assert 1.0 <= result <= 3.0

    def test_unsorted_input(self) -> None:
        values = np.array([3.0, 1.0, 2.0])
        weights = np.array([1.0, 1.0, 1.0])
        assert _weighted_median_1d(values, weights) == 2.0

    def test_returns_float(self) -> None:
        result = _weighted_median_1d(np.array([1, 2, 3]), np.array([1, 1, 1]))
        assert isinstance(result, float)


class TestWeightedPlaneNormal:
    def test_fewer_than_three_points_returns_default_normal(self) -> None:
        points = np.array([[0.0, 0.0, 0.0], [2.0, 4.0, 6.0]])
        weights = np.array([1.0, 1.0])
        centroid, normal = _weighted_plane_normal(points, weights)
        np.testing.assert_allclose(centroid, np.array([1.0, 2.0, 3.0]), atol=1e-12)
        np.testing.assert_allclose(normal, np.array([0.0, -1.0, 0.0]), atol=1e-12)

    def test_normal_is_unit_length(self) -> None:
        points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0]])
        weights = np.ones(4)
        _, normal = _weighted_plane_normal(points, weights)
        np.testing.assert_allclose(np.linalg.norm(normal), 1.0, atol=1e-12)

    def test_planar_cloud_in_xy_normal_along_z(self) -> None:
        points = np.array([[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1.0, 0.0], [2.0, 3.0, 0.0]])
        weights = np.ones(5)
        _, normal = _weighted_plane_normal(points, weights)
        np.testing.assert_allclose(np.abs(normal[2]), 1.0, atol=1e-9)
        np.testing.assert_allclose(normal[0], 0.0, atol=1e-9)
        np.testing.assert_allclose(normal[1], 0.0, atol=1e-9)

    def test_planar_cloud_offset_in_z(self) -> None:
        points = np.array([[0.0, 0.0, 5.0], [1.0, 0.0, 5.0], [0.0, 1.0, 5.0], [1.0, 1.0, 5.0]])
        weights = np.ones(4)
        centroid, normal = _weighted_plane_normal(points, weights)
        np.testing.assert_allclose(centroid[2], 5.0, atol=1e-12)
        np.testing.assert_allclose(np.abs(normal[2]), 1.0, atol=1e-9)

    def test_weighted_centroid(self) -> None:
        points = np.array([[0.0, 0.0, 0.0], [10.0, 0.0, 0.0], [0.0, 10.0, 0.0]])
        weights = np.array([100.0, 1.0, 1.0])
        centroid, _ = _weighted_plane_normal(points, weights)
        # Peso concentrado na origem desloca o centroide ponderado para la.
        assert centroid[0] < 1.0
        assert centroid[1] < 1.0


class TestTranslationMatrixNp:
    def test_shape_and_dtype(self) -> None:
        m = _translation_matrix_np(np.array([1.0, 2.0, 3.0]))
        assert m.shape == (4, 4)
        assert m.dtype == np.float64

    def test_identity_rotation_block(self) -> None:
        m = _translation_matrix_np(np.array([5.0, 6.0, 7.0]))
        np.testing.assert_allclose(m[:3, :3], np.eye(3), atol=1e-12)

    def test_translation_in_last_column(self) -> None:
        m = _translation_matrix_np(np.array([1.0, 2.0, 3.0]))
        np.testing.assert_allclose(m[:3, 3], np.array([1.0, 2.0, 3.0]), atol=1e-12)

    def test_homogeneous_row(self) -> None:
        m = _translation_matrix_np(np.array([1.0, 2.0, 3.0]))
        np.testing.assert_allclose(m[3, :], np.array([0.0, 0.0, 0.0, 1.0]), atol=1e-12)

    def test_applies_to_point(self) -> None:
        m = _translation_matrix_np(np.array([1.0, 2.0, 3.0]))
        p = np.array([0.0, 0.0, 0.0, 1.0])
        np.testing.assert_allclose(m @ p, np.array([1.0, 2.0, 3.0, 1.0]), atol=1e-12)
