"""Testes do BackendManager híbrido: subprocesso vs in-process.

Cenário: um registry com 2 backends — um "subprocesso" (desc.tool definido,
sem adapter real) e um "in-process" (sem desc.tool, adapter mock). O manager
deve despachar corretamente: o subprocesso vai ao SubprocessWorkerPool mock;
o in-process vai ao adapter real.

Cobre também:
- ``_use_subprocess`` respeita ``AIGAMEKIT_UMS_SUBPROCESS=0`` (rollback).
- ``is_loaded`` / ``loaded_names`` / ``shape_matches_loaded`` em modo subprocesso.
- ``_evict_unlocked`` em modo subprocesso (chama pool.unload, não adapter.unload).
- ``idle_candidates`` retorna backends em qualquer modo.
- ``generate`` despacha para o pool com progress/abort hooks.
"""

from __future__ import annotations

from typing import Any

import pytest
from modelserver.backend_manager import BackendManager
from modelserver.registry import BackendDescriptor, Registry

from .conftest_helpers import MockAdapter

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockSubprocessPool:
    """Mock do SubprocessWorkerPool: regista chamadas sem spawnar nada."""

    def __init__(self) -> None:
        self.load_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.generate_calls: list[tuple[str, dict[str, Any]]] = []
        self.unload_calls: list[str] = []
        self.shutdown_calls: list[str] = []
        self._loaded: set[str] = set()
        self._alive: set[str] = set()
        self._vram: dict[str, int] = {}

    def load(self, backend: str, tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.load_calls.append((backend, tool, dict(kwargs)))
        self._loaded.add(backend)
        self._alive.add(backend)
        self._vram[backend] = 2000
        return {"event": "ready", "vram_mib": 2000}

    def generate(
        self,
        backend: str,
        request: dict[str, Any],
        *,
        on_progress: Any = None,
        should_abort: Any = None,
    ) -> dict[str, Any]:
        self.generate_calls.append((backend, dict(request)))
        if on_progress:
            on_progress(0.5, "mid")
        return {"status": "ok", "output": "/tmp/sub_out.glb"}

    def unload(self, backend: str) -> bool:
        self.unload_calls.append(backend)
        self._loaded.discard(backend)
        return True

    def shutdown(self, backend: str) -> bool:
        self.shutdown_calls.append(backend)
        self._loaded.discard(backend)
        self._alive.discard(backend)
        return True

    def shutdown_all(self) -> None:
        for b in list(self._alive):
            self.shutdown(b)

    def is_loaded(self, backend: str) -> bool:
        return backend in self._loaded

    def is_alive(self, backend: str) -> bool:
        return backend in self._alive

    def vram_mib(self, backend: str) -> int | None:
        return self._vram.get(backend)

    def loaded_backends(self) -> set[str]:
        return set(self._loaded)


def _make_hybrid_registry() -> Registry:
    """Registry com 2 backends: 'sub_mock' (subprocesso) e 'inproc_mock'."""
    descriptors = {
        "sub_mock": BackendDescriptor(
            name="sub_mock",
            adapter="_mock_sub",
            vram_mib=2000,
            priority=30,
            tool="text3d",  # modo subprocesso
        ),
        "inproc_mock": BackendDescriptor(
            name="inproc_mock",
            adapter="_mock_inproc",
            vram_mib=3000,
            priority=20,
            tool=None,  # modo in-process
        ),
    }
    registry = Registry(descriptors=descriptors)
    # Adapter real apenas para o in-process; o subprocesso nunca é instanciado.
    registry._adapter_instances["inproc_mock"] = MockAdapter(name="inproc_mock")
    return registry


def _make_hybrid_manager(
    *,
    subprocess_pool: MockSubprocessPool | None = None,
    free_mib: int = 99999,
) -> tuple[BackendManager, MockSubprocessPool]:
    pool = subprocess_pool or MockSubprocessPool()
    mgr = BackendManager(
        _make_hybrid_registry(),
        query_free_mib=lambda: free_mib,
        clear_vram=lambda: None,
        subprocess_pool=pool,
    )
    return mgr, pool


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestUseSubprocess:
    def test_subprocess_when_tool_set_and_pool_present(self) -> None:
        mgr, _ = _make_hybrid_manager()
        assert mgr._use_subprocess("sub_mock") is True

    def test_inprocess_when_tool_none(self) -> None:
        mgr, _ = _make_hybrid_manager()
        assert mgr._use_subprocess("inproc_mock") is False

    def test_env_override_disables_globally(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AIGAMEKIT_UMS_SUBPROCESS", "0")
        mgr, _ = _make_hybrid_manager()
        assert mgr._use_subprocess("sub_mock") is False

    def test_no_pool_means_inprocess(self) -> None:
        mgr = BackendManager(_make_hybrid_registry(), query_free_mib=lambda: 99999, clear_vram=lambda: None)
        assert mgr._use_subprocess("sub_mock") is False


class TestSubprocessLoad:
    def test_ensure_loaded_routes_to_pool(self) -> None:
        mgr, pool = _make_hybrid_manager()
        _handle = mgr.ensure_loaded("sub_mock", _pin=True, sdnq_preset="sdnq-int4")
        assert len(pool.load_calls) == 1
        backend, tool, kwargs = pool.load_calls[0]
        assert backend == "sub_mock"
        assert tool == "text3d"
        assert kwargs["sdnq_preset"] == "sdnq-int4"
        assert mgr.is_loaded("sub_mock")
        # Pin contador incrementado.
        state = mgr._states["sub_mock"]
        assert state.ref_count == 1
        assert state.subprocess_loaded is True
        assert state.model is None  # sem objecto torch no processo UMS

    def test_ensure_loaded_reuses_worker_when_shape_matches(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-int4")
        mgr._states["sub_mock"].ref_count = 0  # simular release
        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-int4")
        # Mesma shape → apenas 1 load.
        assert len(pool.load_calls) == 1

    def test_ensure_loaded_reloads_on_shape_mismatch(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-int4")
        mgr._states["sub_mock"].ref_count = 0  # allow reload
        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-uint8")
        assert len(pool.load_calls) == 2

    def test_ensure_loaded_reloads_when_worker_dead_but_marked_loaded(self) -> None:
        """Regressão nest: ``subprocess_loaded`` True + worker morto → reload."""
        mgr, pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-int4")
        mgr._states["sub_mock"].ref_count = 0
        # Simular morte do worker sem o manager saber (crash / idle kill).
        pool._alive.discard("sub_mock")
        pool._loaded.discard("sub_mock")
        assert mgr._states["sub_mock"].subprocess_loaded is True

        mgr.ensure_loaded("sub_mock", sdnq_preset="sdnq-int4")

        assert len(pool.load_calls) == 2
        assert mgr.is_loaded("sub_mock")
        assert pool.is_alive("sub_mock")


class TestSubprocessGenerate:
    def test_generate_routes_to_pool_with_progress_abort(self) -> None:
        mgr, pool = _make_hybrid_manager()
        progresses: list[tuple[float | None, str | None]] = []
        result = mgr.generate(
            "sub_mock",
            {
                "prompt": "x",
                "output": "/tmp/x.glb",
                "_progress": lambda pct, msg: progresses.append((pct, msg)),
                "_abort": lambda: False,
            },
        )
        assert result["status"] == "ok"
        assert result["output"] == "/tmp/sub_out.glb"
        assert len(pool.generate_calls) == 1
        # Progress foi repassado pelo pool.
        assert (0.5, "mid") in progresses

    def test_generate_loads_if_not_loaded(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.generate("sub_mock", {"prompt": "x", "output": "/tmp/x.glb"})
        # Pool.load foi chamado automaticamente.
        assert len(pool.load_calls) == 1
        assert len(pool.generate_calls) == 1


class TestSubprocessEvict:
    def test_evict_routes_to_pool_unload(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="x")
        evicted = mgr.evict("sub_mock")
        assert evicted is True
        assert "sub_mock" in pool.unload_calls
        assert not mgr.is_loaded("sub_mock")
        # subprocess_loaded limpo; model mantém-se None.
        state = mgr._states["sub_mock"]
        assert state.subprocess_loaded is False

    def test_evict_all_covers_subprocess_backends(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="x")
        count = mgr.evict_all()
        assert count == 1
        assert "sub_mock" in pool.unload_calls


class TestSubprocessIdle:
    def test_idle_candidates_includes_subprocess_backends(self) -> None:
        import time

        mgr, _pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="x")
        mgr._states["sub_mock"].ref_count = 0  # idle
        # Forçar last_used antigo (threshold 0 = qualquer idade conta).
        mgr._states["sub_mock"].last_used = time.monotonic() - 100
        candidates = mgr.idle_candidates(0.0)
        names = [n for n, _ in candidates]
        assert "sub_mock" in names


class TestSubprocessSnapshot:
    def test_snapshot_uses_pool_vram(self) -> None:
        mgr, _pool = _make_hybrid_manager()
        mgr.ensure_loaded("sub_mock", sdnq_preset="x")
        snap = mgr._snapshot("sub_mock")
        assert snap is not None
        # Pool reporta 2000 MiB — preferido sobre a estimativa footprint.
        assert snap.vram_mib == 2000


class TestInProcessStillWorks:
    """Com pool presente, backends sem 'tool:' continuam in-process."""

    def test_inproc_backend_loads_via_adapter(self) -> None:
        mgr, pool = _make_hybrid_manager()
        mgr.generate("inproc_mock", {"prompt": "x", "output": "/tmp/x.png"})
        # Pool não foi usado.
        assert len(pool.load_calls) == 0
        assert len(pool.generate_calls) == 0
        # Estado in-process: model não-None.
        state = mgr._states["inproc_mock"]
        assert state.model is not None
        assert state.subprocess_loaded is False

    def test_inproc_evict_calls_adapter_unload(self) -> None:
        mgr, _pool = _make_hybrid_manager()
        mgr.generate("inproc_mock", {"prompt": "x", "output": "/tmp/x.png"})
        adapter = mgr._registry.adapter("inproc_mock")
        before = adapter.unload_calls
        mgr.evict("inproc_mock")
        assert adapter.unload_calls == before + 1


class TestRollbackViaEnv:
    def test_env_zero_forces_inprocess_for_subprocess_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """AIGAMEKIT_UMS_SUBPROCESS=0: sub_mock corre in-process em vez de via pool."""
        monkeypatch.setenv("AIGAMEKIT_UMS_SUBPROCESS", "0")
        # Para o fallback funcionar, sub_mock precisa de um adapter real.
        registry = _make_hybrid_registry()
        registry._adapter_instances["sub_mock"] = MockAdapter(name="sub_mock")
        pool = MockSubprocessPool()
        mgr = BackendManager(
            registry,
            query_free_mib=lambda: 99999,
            clear_vram=lambda: None,
            subprocess_pool=pool,
        )
        mgr.generate("sub_mock", {"prompt": "x", "output": "/tmp/x.png"})
        # Nenhuma chamada ao pool — correu in-process.
        assert len(pool.load_calls) == 0
        assert len(pool.generate_calls) == 0
