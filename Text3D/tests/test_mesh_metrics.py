"""Testes de utils.mesh_metrics — meshes sintéticas trimesh, sem GPU."""

from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from text3d.utils.mesh_metrics import (  # noqa: E402
    boundary_edge_count,
    classify_components,
    drop_internal_components,
    mesh_quality_metrics,
    split_components,
)


def _outer_box() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(2.0, 2.0, 2.0))


def _inner_debris() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(0.1, 0.1, 0.1))


def _external_debris() -> trimesh.Trimesh:
    box = trimesh.creation.box(extents=(0.1, 0.1, 0.1))
    box.apply_translation((5.0, 0.0, 0.0))
    return box


def _combined(*parts: trimesh.Trimesh) -> trimesh.Trimesh:
    return trimesh.util.concatenate(list(parts))


class TestSplitAndBoundary:
    def test_split_single(self):
        assert len(split_components(_outer_box())) == 1

    def test_split_multi(self):
        mesh = _combined(_outer_box(), _external_debris())
        assert len(split_components(mesh)) == 2

    def test_split_empty(self):
        assert split_components(trimesh.Trimesh()) == []

    def test_boundary_closed_box_zero(self):
        assert boundary_edge_count(_outer_box()) == 0

    def test_boundary_open_plane_positive(self):
        plane = trimesh.Trimesh(
            vertices=np.array([[0, 0, 0], [1, 0, 0], [1, 1, 0], [0, 1, 0]], dtype=float),
            faces=np.array([[0, 1, 2], [0, 2, 3]]),
        )
        assert boundary_edge_count(plane) == 4

    def test_boundary_empty(self):
        assert boundary_edge_count(trimesh.Trimesh()) == 0


class TestClassifyComponents:
    def test_internal_detected(self):
        mesh = _combined(_outer_box(), _inner_debris())
        main, internal, external = classify_components(mesh)
        assert main is not None and len(main.faces) == 12
        assert len(internal) == 1
        assert external == []

    def test_external_detected(self):
        mesh = _combined(_outer_box(), _external_debris())
        _main, internal, external = classify_components(mesh)
        assert internal == []
        assert len(external) == 1

    def test_empty(self):
        main, internal, external = classify_components(trimesh.Trimesh())
        assert main is None and internal == [] and external == []


class TestDropInternalComponents:
    def test_drops_small_internal(self):
        mesh = _combined(_outer_box(), _inner_debris())
        cleaned, dropped, _stats = drop_internal_components(mesh)
        assert dropped == 1
        assert len(split_components(cleaned)) == 1

    def test_keeps_large_internal(self):
        big_inner = trimesh.creation.box(extents=(1.5, 1.5, 1.5))
        mesh = _combined(_outer_box(), big_inner)
        cleaned, dropped, _stats = drop_internal_components(mesh, max_volume_ratio=0.15)
        assert dropped == 0
        assert len(split_components(cleaned)) == 2

    def test_keeps_external(self):
        mesh = _combined(_outer_box(), _external_debris())
        cleaned, dropped, _stats = drop_internal_components(mesh)
        assert dropped == 0
        assert len(split_components(cleaned)) == 2

    def test_no_components_noop(self):
        box = _outer_box()
        cleaned, dropped, _stats = drop_internal_components(box)
        assert dropped == 0
        assert cleaned is box


class TestMeshQualityMetrics:
    def test_clean_box(self):
        m = mesh_quality_metrics(_outer_box())
        assert m["faces"] == 12
        assert m["is_watertight"] is True
        assert m["boundary_edges"] == 0
        assert m["components"] == 1
        assert m["internal_components"] == 0

    def test_with_internal_junk(self):
        mesh = _combined(_outer_box(), _inner_debris(), _external_debris())
        m = mesh_quality_metrics(mesh)
        assert m["components"] == 3
        assert m["internal_components"] == 1
        assert m["external_components"] == 1
        assert 0.0 < m["internal_volume_ratio"] < 0.01

    def test_empty_mesh(self):
        m = mesh_quality_metrics(trimesh.Trimesh())
        assert m["faces"] == 0
        assert m["components"] == 0


class TestManyComponentsPerf:
    """Regressão do hang: mesh MC ruidosa tem MILHARES de componentes — o
    filtro tem de ser vetorizado por labels, nunca objeto-por-objeto."""

    def _noisy_mesh(self, n: int) -> trimesh.Trimesh:
        rng = np.random.default_rng(7)
        parts = [trimesh.creation.box(extents=(4.0, 4.0, 4.0))]
        for _ in range(n):
            b = trimesh.creation.box(extents=(0.02, 0.02, 0.02))
            b.apply_translation(rng.uniform(-1.5, 1.5, size=3))
            parts.append(b)
        return trimesh.util.concatenate(parts)

    def test_thousands_of_debris_fast(self):
        import time

        # Acima de _FAST_AABB_COMPONENTS (5k) — path AABB-only O(F+C).
        mesh = self._noisy_mesh(6000)
        t0 = time.time()
        cleaned, dropped, _stats = drop_internal_components(mesh)
        elapsed = time.time() - t0
        assert dropped == 6000
        assert len(cleaned.faces) == 12
        assert elapsed < 15.0  # regressão: loops por label = horas

    def test_metrics_on_noisy_mesh(self):
        mesh = self._noisy_mesh(500)
        m = mesh_quality_metrics(mesh)
        assert m["internal_components"] == 500
        assert m["main_faces"] == 12
