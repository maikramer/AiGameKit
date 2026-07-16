"""Admit/refuse por pico pesos+activação — sem OOM silencioso."""

from __future__ import annotations

import pytest
from modelserver.backend_manager import BackendManager, InsufficientVramError
from modelserver.registry import Registry


class TestEnsureLoadedAdmitsPeak:
    def test_refuses_when_free_below_peak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_VRAM_SAFETY_MIB", "384")
        registry = Registry()
        # 6GB livre — text3d fp16 peak ~8GB+
        mgr = BackendManager(registry, query_free_mib=lambda: 5657, clear_vram=lambda: None)
        with pytest.raises(InsufficientVramError) as ei:
            mgr.ensure_loaded("text3d", sdnq_preset="none")
        err = ei.value
        assert err.peak_mib > 5657
        assert err.activation_mib > 0
        assert err.quant_mode == "none"

    def test_int4_admitted_on_6gb(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_VRAM_SAFETY_MIB", "384")
        registry = Registry()
        loaded: dict[str, object] = {}

        class _FakeAdapter:
            def load(self, **kwargs):
                loaded["ok"] = True
                return object()

            def unload(self, model):
                pass

        mgr = BackendManager(registry, query_free_mib=lambda: 5657, clear_vram=lambda: None)
        # Patch só o adapter text3d — evita torch.
        monkeypatch.setattr(mgr._registry, "adapter", lambda name: _FakeAdapter())
        model = mgr.ensure_loaded("text3d", sdnq_preset="sdnq-int4")
        assert model is not None
        assert loaded.get("ok") is True

    def test_ensure_vram_uses_backend_peak(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_VRAM_SAFETY_MIB", "384")
        registry = Registry()
        free = {"v": 5657}
        mgr = BackendManager(registry, query_free_mib=lambda: free["v"], clear_vram=lambda: None)
        # Pedido cliente 5000 < peak text3d none — sem backends loaded, não consegue libertar.
        ok = mgr.ensure_vram(5000, backend="text3d", quant_mode="none")
        assert ok is False
