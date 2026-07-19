"""Testes do refresh_runtime_budget do PaintBatchProcessor (overrides UMS)."""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("torch")

from paint3d import painter as painter_mod
from paint3d.painter import PaintBatchProcessor


@pytest.fixture
def captured_budget(monkeypatch):
    """Substitui apply_runtime_vram_budget e captura os kwargs efetivos."""
    calls: list[dict[str, Any]] = []

    def _fake(config, pipe, *, requested_views, requested_resolution, memory_efficient, verbose=False):
        calls.append(
            {
                "requested_views": requested_views,
                "requested_resolution": requested_resolution,
                "memory_efficient": memory_efficient,
            }
        )
        return {"max_views": requested_views, "view_resolution": requested_resolution}

    monkeypatch.setattr(painter_mod, "apply_runtime_vram_budget", _fake)
    return calls


def _loaded_proc(**kwargs: Any) -> PaintBatchProcessor:
    proc = PaintBatchProcessor(**kwargs)
    proc._pipe = object()  # simular pipeline carregado
    proc._config = object()
    return proc


class TestRefreshRuntimeBudget:
    def test_returns_none_before_load(self) -> None:
        proc = PaintBatchProcessor()
        assert proc.refresh_runtime_budget() is None

    def test_defaults_use_load_shape(self, captured_budget) -> None:
        proc = _loaded_proc(max_num_view=6, view_resolution=512)
        budget = proc.refresh_runtime_budget()
        assert budget == {"max_views": 6, "view_resolution": 512}
        assert captured_budget[0]["requested_views"] == 6
        assert captured_budget[0]["requested_resolution"] == 512

    def test_request_can_lower_views(self, captured_budget) -> None:
        proc = _loaded_proc(max_num_view=6, view_resolution=512)
        proc.refresh_runtime_budget(requested_views=4)
        assert captured_budget[0]["requested_views"] == 4

    def test_request_clamped_to_load_shape(self, captured_budget) -> None:
        """Pedir mais vistas/resolução que o load não sobe — câmaras fixas."""
        proc = _loaded_proc(max_num_view=6, view_resolution=512)
        proc.refresh_runtime_budget(requested_views=10, requested_resolution=768)
        assert captured_budget[0]["requested_views"] == 6
        assert captured_budget[0]["requested_resolution"] == 512

    def test_resolution_floor_256(self, captured_budget) -> None:
        proc = _loaded_proc(max_num_view=6, view_resolution=512)
        proc.refresh_runtime_budget(requested_resolution=64)
        assert captured_budget[0]["requested_resolution"] == 256

    def test_memory_efficient_propagated(self, captured_budget) -> None:
        proc = _loaded_proc(max_num_view=4, view_resolution=512, memory_efficient=True)
        proc.refresh_runtime_budget()
        assert captured_budget[0]["memory_efficient"] is True
