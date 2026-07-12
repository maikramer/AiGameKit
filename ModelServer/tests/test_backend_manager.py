"""Testes do BackendManager — carga lazy, evicção, ref-counting (com adapters mock)."""

from __future__ import annotations

from pathlib import Path

import pytest

from modelserver.backend_manager import BackendManager
from modelserver.registry import BackendDescriptor, Registry

from .conftest_helpers import MockAdapter


def _make_registry() -> Registry:
    """Registry com 3 adapters mock controlados."""
    specs = {"alpha": (1000, 10), "beta": (3000, 30), "gamma": (5000, 50)}
    descriptors = {n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()}
    registry = Registry(descriptors=descriptors)
    for n in specs:
        registry._adapter_instances[n] = MockAdapter(name=n)
    return registry


class TestLoadAndGenerate:
    """Carga lazy e geração via adapter."""

    def test_lazy_load_on_first_generate(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        assert not mgr.is_loaded("alpha")
        resp = mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == "ok"
        assert mgr.is_loaded("alpha")

    def test_second_generate_reuses_loaded_model(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("alpha", {"prompt": "a", "output": "/tmp/a.png"})
        adapter = registry.adapter("alpha")
        assert adapter.load_calls == 1  # carregou 1 vez
        mgr.generate("alpha", {"prompt": "b", "output": "/tmp/b.png"})
        assert adapter.load_calls == 1  # não recarregou — reusou

    def test_generate_unknown_backend(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        with pytest.raises(KeyError):
            mgr.generate("nope", {})


class TestEviction:
    """Evicção manual e automática."""

    def test_evict_specific_backend(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert mgr.is_loaded("alpha")

        evicted = mgr.evict("alpha")
        assert evicted is True
        assert not mgr.is_loaded("alpha")
        adapter = registry.adapter("alpha")
        assert adapter.unload_calls == 1

    def test_evict_not_loaded_returns_false(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        assert mgr.evict("alpha") is False

    def test_evict_all(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        mgr.generate("beta", {"prompt": "x", "output": "/tmp/x.png"})

        count = mgr.evict_all()
        assert count == 2
        assert mgr.loaded_names() == []

    def test_auto_eviction_when_vram_low(self) -> None:
        """Ao carregar um backend novo, se VRAM não chega, evicta LRU idle."""
        registry = _make_registry()
        # Simular VRAM baixa para forçar evicção.
        state = {"free": 99999}
        mgr = BackendManager(registry, query_free_mib=lambda: state["free"], clear_vram=lambda: None)

        # Carregar alpha (1000 MiB) — atualiza free simulado.
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        state["free"] = 1500  # só cabe alpha, não gamma (5000)

        # Carregar gamma (5000 MiB) — precisa evictar alpha (idle) + qualquer outro.
        # alpha=1000, beta=3000 (idle), gamma=5000. Para chegar a 5000 livres:
        # free=1500, precisa de 5000 → deficit=3500. Evicta alpha(1000)+beta(3000)=4000.
        # Mas beta não está carregado... só alpha está. Então evicta alpha (1000) → free=2500.
        # Ainda < 5000, mas só há alpha carregado → não chega. ensure_loaded carrega na mesma.
        mgr.generate("gamma", {"prompt": "x", "output": "/tmp/x.png"})
        assert mgr.is_loaded("gamma")
        assert not mgr.is_loaded("alpha")  # alpha foi evicted na tentativa

    def test_ensure_vram_evicts_until_free(self) -> None:
        registry = _make_registry()
        # Mock dinâmico: free = base - soma de vram_mib dos backends carregados.
        def _free_mib() -> int:
            loaded = sum(d.vram_mib for d in registry if mgr.is_loaded(d.name))
            return max(0, 8000 - loaded)

        mgr = BackendManager(registry, query_free_mib=_free_mib, clear_vram=lambda: None)

        # Carregar 2 backends (consomem "VRAM" simulada: 8000 - 1000 - 3000 = 4000 livres).
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        mgr.generate("beta", {"prompt": "x", "output": "/tmp/x.png"})

        # Pedir 6000 MiB — só há 4000 livres. Evicta alpha(1000)+beta(3000) → 8000 livres.
        ok = mgr.ensure_vram(6000)
        assert ok is True
        assert not mgr.is_loaded("alpha")
        assert not mgr.is_loaded("beta")


class TestRefCounting:
    """Ref-counting protege backends em uso de evicção."""

    def test_ref_counting_during_generate(self) -> None:
        """Enquanto um backend tem ref>0, evict recusa."""
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        # Simular carga manual + ref incrementado.
        mgr.ensure_loaded("alpha")
        mgr._states["alpha"].ref_count = 1  # simular "em uso"
        evicted = mgr.evict("alpha")
        assert evicted is False  # recusou evictar por ter ref>0
        assert mgr.is_loaded("alpha")  # ainda carregado

    def test_ref_count_returns_to_zero_after_generate(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert mgr._states["alpha"].ref_count == 0  # volta a 0 após generate


class TestErrorRecovery:
    """Em erro de geração, o backend é descarregado para recovery."""

    def test_generate_error_evicts_model(self) -> None:
        registry = _make_registry()
        # Trocar adapter alpha por um que falha no generate.
        failing = MockAdapter(name="alpha", fail_generate=True)
        registry._adapter_instances["alpha"] = failing

        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        resp = mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == "error"
        assert "generate failed" in resp["error"]
        assert not mgr.is_loaded("alpha")  # descarregado após erro

    def test_status_report(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})

        status = mgr.status()
        assert status["loaded_count"] == 1
        assert status["loaded_vram_mib"] == 1000
        alpha_status = next(b for b in status["backends"] if b["name"] == "alpha")
        assert alpha_status["loaded"] is True
        beta_status = next(b for b in status["backends"] if b["name"] == "beta")
        assert beta_status["loaded"] is False
