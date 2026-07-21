"""Testes do BackendManager — carga lazy, evicção, ref-counting (com adapters mock)."""

from __future__ import annotations

import pytest
from modelserver.backend_manager import BackendManager
from modelserver.registry import BackendDescriptor, Registry

from .conftest_helpers import MockAdapter


def _make_registry() -> Registry:
    """Registry com 3 adapters mock controlados."""
    specs = {"alpha": (1000, 10), "beta": (3000, 30), "gamma": (5000, 50)}
    descriptors = {
        n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
    }
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

    def test_evict_all_scrubs_even_when_empty(self) -> None:
        """Bug fix: loaded=[] ainda pode ter contexto CUDA — scrub sempre."""
        clears: list[int] = []
        registry = _make_registry()
        mgr = BackendManager(
            registry,
            query_free_mib=lambda: 99999,
            clear_vram=lambda: clears.append(1),
            query_process_vram_mib=lambda: 1400,
        )
        assert mgr.evict_all() == 0
        assert clears, "evict_all com loaded=[] deve scrub cache"
        info = mgr.scrub_dead_vram()
        assert info["dead_vram"] is True


class TestAdmitFreeAccounting:
    """``_admit_free_mib`` credita o cache do allocator (reserved-allocated)."""

    def test_credits_reusable_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 4000, clear_vram=lambda: None)
        monkeypatch.setattr(
            BackendManager,
            "_torch_alloc_stats",
            staticmethod(lambda: {"allocated_mib": 100, "reserved_mib": 1500, "reusable_mib": 1400}),
        )
        assert mgr._admit_free_mib() == 4000 + 1400

    def test_no_torch_no_credit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 4000, clear_vram=lambda: None)
        monkeypatch.setattr(
            BackendManager,
            "_torch_alloc_stats",
            staticmethod(lambda: {"allocated_mib": None, "reserved_mib": None, "reusable_mib": None}),
        )
        assert mgr._admit_free_mib() == 4000

    def test_admit_succeeds_with_cache_credit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """free cru insuficiente + cache reutilizável suficiente → backend carrega."""
        registry = _make_registry()
        # gamma: vram_mib=5000 → peak = 5000 + activation + safety (> 4000 cru).
        state = {"free": 4000}
        mgr = BackendManager(registry, query_free_mib=lambda: state["free"], clear_vram=lambda: None)
        monkeypatch.setattr(
            BackendManager,
            "_torch_alloc_stats",
            staticmethod(lambda: {"allocated_mib": 100, "reserved_mib": 4000, "reusable_mib": 3900}),
        )
        resp = mgr.generate("gamma", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == "ok"
        assert mgr.is_loaded("gamma")

    def test_refusal_when_credit_insufficient(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """free + cache ainda abaixo do peak → recusa honesta (sem OOM)."""
        registry = _make_registry()
        state = {"free": 100}
        mgr = BackendManager(registry, query_free_mib=lambda: state["free"], clear_vram=lambda: None)
        monkeypatch.setattr(
            BackendManager,
            "_torch_alloc_stats",
            staticmethod(lambda: {"allocated_mib": 0, "reserved_mib": 50, "reusable_mib": 50}),
        )
        with pytest.raises(Exception) as excinfo:
            mgr.ensure_loaded("gamma")
        assert "peak" in str(excinfo.value).lower() or "VRAM" in str(excinfo.value)

    def test_ensure_vram_scrubs_dead_residual(self) -> None:
        """Sem backends evictáveis mas free baixo → scrub residual e reavalia."""
        state = {"free": 500, "clears": 0}

        def _free() -> int:
            return state["free"]

        def _clear() -> None:
            state["clears"] += 1
            state["free"] = 7000

        registry = _make_registry()
        mgr = BackendManager(
            registry,
            query_free_mib=_free,
            clear_vram=_clear,
            query_process_vram_mib=lambda: 1400,
        )
        ok = mgr.ensure_vram(6000)
        assert ok is True
        assert state["clears"] >= 1

    def test_auto_eviction_when_vram_low(self) -> None:
        """VRAM baixa: evicta idle; se pico ainda não cabe → recusa (sem OOM)."""
        registry = _make_registry()
        state = {"free": 99999}
        mgr = BackendManager(registry, query_free_mib=lambda: state["free"], clear_vram=lambda: None)

        mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        state["free"] = 1500  # após alpha; gamma peak ≫ 1500

        # Evicta alpha mas free mock não sobe → ainda < peak gamma → VRAM_INSUFFICIENT.
        resp = mgr.generate("gamma", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp.get("status") == "error"
        assert resp.get("error_code") == "VRAM_INSUFFICIENT"
        assert not mgr.is_loaded("gamma")
        assert not mgr.is_loaded("alpha")  # alpha evicted na tentativa

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

    def test_ref_count_zero_after_generate_error(self) -> None:
        """Erro no generate: um único decrement (não double → underflow lógico)."""
        registry = _make_registry()
        failing = MockAdapter(name="alpha", fail_generate=True)
        registry._adapter_instances["alpha"] = failing
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        resp = mgr.generate("alpha", {"prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == "error"
        # Estado pode ter sido removido no evict; se existir, ref=0.
        state = mgr._states.get("alpha")
        if state is not None:
            assert state.ref_count == 0


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


class TestEnsureLoadedPin:
    """Regressão: pin de ref_count atómico com ensure_loaded (sem janela de eviction)."""

    def test_pin_blocks_eviction_until_unpinned(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        model = mgr.ensure_loaded("alpha", _pin=True)
        assert model is not None
        assert mgr._states["alpha"].ref_count == 1
        # ref_count=1 logo ao sair do ensure — evict recusado, sem janela ref=0.
        assert mgr.evict("alpha") is False
        assert mgr.is_loaded("alpha")
        with mgr._struct_lock:
            mgr._states["alpha"].ref_count -= 1
        assert mgr.evict("alpha") is True

    def test_unpinned_ensure_stays_evictable(self) -> None:
        registry = _make_registry()
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.ensure_loaded("alpha")
        assert mgr._states["alpha"].ref_count == 0
        assert mgr.evict("alpha") is True
