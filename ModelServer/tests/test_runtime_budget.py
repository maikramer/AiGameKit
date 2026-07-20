"""Testes do runtime VRAM budget no UMS — helper base + propagação para stats."""

from __future__ import annotations

from typing import Any

import pytest
from modelserver.backend_manager import _LOAD_KWARG_KEYS, BackendManager, InsufficientVramError
from modelserver.registry import BackendDescriptor, Registry
from modelserver.runtime_budget import suggest_paint_budget, suggest_text3d_chunks

from .conftest_helpers import MockAdapter


class _BudgetModel:
    """Model fake com refresh_runtime_budget(**hints)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def refresh_runtime_budget(self, **hints: Any) -> dict[str, Any]:
        self.calls.append(dict(hints))
        return {"max_views": hints.get("requested_views", 6), "dino_device": "cpu"}


class _LegacyBudgetModel:
    """Model fake antigo: refresh_runtime_budget() sem kwargs."""

    def refresh_runtime_budget(self) -> dict[str, Any]:
        return {"max_views": 6}


class _SoftFailBudgetModel:
    def refresh_runtime_budget(self) -> dict[str, Any]:
        raise ValueError("sem sinal VRAM")


class _VramGateBudgetModel:
    def refresh_runtime_budget(self) -> dict[str, Any]:
        raise RuntimeError("VRAM insuficiente para MeshRender: 34 MiB livres")


class TestApplyRuntimeBudgetHelper:
    """BackendAdapter.apply_runtime_budget — contrato genérico."""

    def test_passes_hints_and_returns_budget(self) -> None:
        adapter = MockAdapter()
        model = _BudgetModel()
        budget = adapter.apply_runtime_budget(model, {}, requested_views=4)
        assert budget == {"max_views": 4, "dino_device": "cpu"}
        assert model.calls == [{"requested_views": 4}]

    def test_legacy_model_without_hint_support(self) -> None:
        """TypeError com hints → retry sem overrides (model antigo)."""
        adapter = MockAdapter()
        budget = adapter.apply_runtime_budget(_LegacyBudgetModel(), {}, requested_views=4)
        assert budget == {"max_views": 6}

    def test_model_without_method_returns_none(self) -> None:
        adapter = MockAdapter()
        assert adapter.apply_runtime_budget(object(), {}) is None

    def test_soft_exception_returns_none(self) -> None:
        adapter = MockAdapter()
        assert adapter.apply_runtime_budget(_SoftFailBudgetModel(), {}) is None

    def test_vram_runtime_error_propagates(self) -> None:
        """MeshRender / OOM gate NÃO pode ser engolido."""
        import pytest

        adapter = MockAdapter()
        with pytest.raises(RuntimeError, match="MeshRender"):
            adapter.apply_runtime_budget(_VramGateBudgetModel(), {})

    def test_reports_progress_with_summary(self) -> None:
        adapter = MockAdapter()
        seen: list[tuple[float | None, str | None]] = []
        request = {"_progress": lambda pct, msg: seen.append((pct, msg))}
        adapter.apply_runtime_budget(_BudgetModel(), request, progress_pct=0.22, requested_views=4)
        assert seen and seen[0][0] == 0.22
        assert "max_views=4" in (seen[0][1] or "")


class _BudgetAdapter(MockAdapter):
    """Adapter mock cujo generate devolve runtime_budget na resposta."""

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        out = super().generate(model, request)
        out["runtime_budget"] = {"num_chunks": 131072, "auto_num_chunks": True}
        return out


class TestManagerRecordsRuntimeBudget:
    """BackendManager.generate → stats.last_runtime_budget."""

    def _make_manager(self) -> tuple[BackendManager, Registry]:
        descriptors = {"alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10)}
        registry = Registry(descriptors=descriptors)
        registry._adapter_instances["alpha"] = _BudgetAdapter(name="alpha")
        return BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None), registry

    def test_budget_from_response_recorded_in_stats(self) -> None:
        mgr, _registry = self._make_manager()
        resp = mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == "ok"
        s = mgr.stats.get("alpha")
        assert s is not None
        assert s.last_runtime_budget == {"num_chunks": 131072, "auto_num_chunks": True}

    def test_no_budget_in_response_is_noop(self) -> None:
        descriptors = {"beta": BackendDescriptor(name="beta", adapter="_mock_beta", vram_mib=1000, priority=10)}
        registry = Registry(descriptors=descriptors)
        registry._adapter_instances["beta"] = MockAdapter(name="beta")
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("beta", {"prompt": "x", "output": "/tmp/x.png"})
        s = mgr.stats.get("beta")
        assert s is not None
        assert s.last_runtime_budget is None


class TestLoadKwargKeys:
    """Kwargs que moldam a activação chegam ao adapter.load (paint3d/text3d)."""

    def test_paint3d_shape_keys_forwarded(self) -> None:
        for key in ("max_num_view", "view_resolution", "render_size", "texture_size", "bake_exp"):
            assert key in _LOAD_KWARG_KEYS

    def test_group_offload_key_forwarded(self) -> None:
        assert "allow_group_offload" in _LOAD_KWARG_KEYS


class TestShapeReload:
    """ensure_loaded recarrega quando max_num_view / quant diverge."""

    def test_reuse_same_shape(self) -> None:
        descriptors = {"alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10)}
        registry = Registry(descriptors=descriptors)
        adapter = MockAdapter(name="alpha")
        registry._adapter_instances["alpha"] = adapter
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.ensure_loaded("alpha", max_num_view=6, view_resolution=512)
        mgr.ensure_loaded("alpha", max_num_view=6, view_resolution=512)
        assert adapter.load_calls == 1

    def test_reload_on_shape_mismatch(self) -> None:
        descriptors = {"alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10)}
        registry = Registry(descriptors=descriptors)
        adapter = MockAdapter(name="alpha")
        registry._adapter_instances["alpha"] = adapter
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.ensure_loaded("alpha", max_num_view=6)
        mgr.ensure_loaded("alpha", max_num_view=4)
        assert adapter.load_calls == 2
        assert adapter.unload_calls == 1

    def test_shape_matches_loaded(self) -> None:
        descriptors = {"alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10)}
        registry = Registry(descriptors=descriptors)
        registry._adapter_instances["alpha"] = MockAdapter(name="alpha")
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.ensure_loaded("alpha", max_num_view=6)
        assert mgr.shape_matches_loaded("alpha", {"max_num_view": 6}) is True
        assert mgr.shape_matches_loaded("alpha", {"max_num_view": 4}) is False
        assert mgr.shape_matches_loaded("alpha", {}) is True


class TestGroupOffloadPeak:
    """text3d+sdnq ⇒ group_offload peak ≈ largest + act (não weights+0.65·act)."""

    def test_resolve_peak_params_text3d_sdnq(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        quant, mem, go, streams = mgr.resolve_peak_params("text3d", {"sdnq_preset": "sdnq-int4"})
        assert quant == "sdnq-int4"
        assert mem is True
        assert go is True
        # text3d carrega pesos completos e offloada depois → admit full-weights.
        assert streams is False

    def test_resolve_peak_params_text2d_sdnq(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        quant, mem, go, streams = mgr.resolve_peak_params("text2d", {"quant_preset": "sdnq-uint8"})
        assert quant == "sdnq-uint8"
        assert mem is True
        assert go is False
        # text2d mem ⟺ diffusers model_cpu offload — o LOAD já é streaming.
        assert streams is True

    def test_group_offload_peak_below_full_weights(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        full = mgr.peak_vram_mib("text3d", quant_mode="sdnq-int4", memory_efficient=False, group_offload=False)
        go = mgr.peak_vram_mib("text3d", quant_mode="sdnq-int4", memory_efficient=True, group_offload=True)
        assert go < full
        w_go, a_go = mgr.footprint_parts_mib(
            "text3d", quant_mode="sdnq-int4", memory_efficient=True, group_offload=True
        )
        w_full, _a = mgr.footprint_parts_mib(
            "text3d", quant_mode="sdnq-int4", memory_efficient=False, group_offload=False
        )
        assert w_go < w_full  # só largest onloaded
        assert a_go >= 2048  # activação completa (não x0.65)

    def test_ensure_loaded_admits_full_weights_not_largest(self) -> None:
        """Load frio: admit usa pesos completos mesmo com allow_group_offload."""
        registry = Registry()
        probe = BackendManager(registry)
        full = probe.peak_vram_mib("text3d", quant_mode="sdnq-int4", memory_efficient=True, group_offload=False)
        go = probe.peak_vram_mib("text3d", quant_mode="sdnq-int4", memory_efficient=True, group_offload=True)
        assert go < full
        mid = (go + full) // 2
        assert go < mid < full
        mgr = BackendManager(registry, query_free_mib=lambda: mid, clear_vram=lambda: None)
        with pytest.raises(InsufficientVramError) as ei:
            mgr.ensure_loaded("text3d", sdnq_preset="sdnq-int4", allow_group_offload=True)
        assert ei.value.peak_mib >= full


class TestSuggestHelpers:
    """Reexports canónicos de gamedev_shared.vram_budget (fórmulas puras)."""

    def test_suggest_text3d_chunks_with_free_bytes(self) -> None:
        out = suggest_text3d_chunks(free_bytes=4 * 1024**3)
        assert out["auto"] is True
        assert out["num_chunks"] > 0

    def test_suggest_text3d_chunks_without_signal(self) -> None:
        out = suggest_text3d_chunks(free_bytes=0)
        assert out["auto"] is False
        assert out["num_chunks"] is None

    def test_suggest_paint_budget_caps_views(self) -> None:
        # ~1 GiB livre: orçamento corta as vistas pedidas.
        out = suggest_paint_budget(requested_views=8, requested_resolution=512, free_bytes=1 * 1024**3)
        assert out["max_views"] < 8
        assert out["cfg_batch_chunking"] is True


class TestStreamsOnLoadPeak:
    """text2d com offload: peak de warmup = largest-module + act (não pesos completos)."""

    def test_text2d_offload_peak_fits_6gb(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 4281, clear_vram=lambda: None)
        quant, mem, _go, streams = mgr.resolve_peak_params(
            "text2d", {"memory_efficient": True, "quant_preset": "sdnq-int4"}
        )
        assert streams is True
        peak = mgr.peak_vram_mib("text2d", quant_mode=quant, memory_efficient=mem, group_offload=streams)
        full = mgr.peak_vram_mib("text2d", quant_mode=quant, memory_efficient=mem, group_offload=False)
        assert peak < full
        # largest(int4) + act 1.5 GiB + safety ≪ full weights 8.5 GiB — cabe na 6 GB.
        assert peak < 5000

    def test_footprint_key_override_4b_vs_9b(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        peak_4b = mgr.peak_vram_mib(
            "text2d",
            quant_mode="sdnq-int4",
            memory_efficient=True,
            group_offload=True,
            footprint_key="flux-klein-4b",
        )
        peak_9b = mgr.peak_vram_mib(
            "text2d",
            quant_mode="sdnq-int4",
            memory_efficient=True,
            group_offload=True,
            footprint_key="flux-klein-9b",
        )
        assert peak_4b < peak_9b

    def test_ensure_vram_target_uses_streams(self) -> None:
        """ensure_vram(backend=text2d) com offload: alvo ≈ streamed, não full-weights."""
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        ok = mgr.ensure_vram(100, backend="text2d", quant_mode="sdnq-int4", memory_efficient=True)
        assert ok is True

    def test_streams_explicit_override(self) -> None:
        registry = Registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        _q, _m, _g, streams = mgr.resolve_peak_params("text3d", {"sdnq_preset": "sdnq-int4", "streams_on_load": True})
        assert streams is True
