"""Testes do ``IdleEvictor`` — três níveis de libertação de VRAM.

1. ``unload`` de pesos após ``idle_timeout_sec``.
2. ``shutdown`` do subprocesso worker após ``worker_shutdown_sec`` (o unload
   deixa o contexto CUDA, ~0.3-1 GiB, preso ao processo).
3. Health-check ``ping``: worker wedged segura VRAM sem terminar jobs.

O ciclo é chamado diretamente (``_check_and_evict`` etc.) para não depender de
temporização de threads.
"""

from __future__ import annotations

import time
from typing import Any

from modelserver.backend_manager import BackendManager
from modelserver.idle_evictor import IdleEvictor
from modelserver.registry import BackendDescriptor, Registry

from .conftest_helpers import MockAdapter


class FakePool:
    """Pool de subprocessos mínimo: alive/loaded/ping/shutdown observáveis."""

    def __init__(self, *, ping_ok: bool = True) -> None:
        self.alive: set[str] = set()
        self.loaded: set[str] = set()
        self.shutdown_calls: list[str] = []
        self.ping_calls: list[str] = []
        self.ping_ok = ping_ok

    def load(self, backend: str, tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.alive.add(backend)
        self.loaded.add(backend)
        return {"event": "ready", "vram_mib": 2000}

    def generate(self, backend: str, request: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"status": "ok"}

    def unload(self, backend: str) -> bool:
        self.loaded.discard(backend)
        return True

    def shutdown(self, backend: str) -> bool:
        self.shutdown_calls.append(backend)
        was_alive = backend in self.alive
        self.alive.discard(backend)
        self.loaded.discard(backend)
        return was_alive

    def is_loaded(self, backend: str) -> bool:
        return backend in self.loaded

    def is_alive(self, backend: str) -> bool:
        return backend in self.alive

    def vram_mib(self, backend: str) -> int | None:
        return 2000 if backend in self.loaded else None

    def loaded_backends(self) -> set[str]:
        return set(self.loaded)

    def ping(self, backend: str) -> bool:
        self.ping_calls.append(backend)
        return self.ping_ok


def _make_manager(pool: FakePool) -> BackendManager:
    descriptors = {
        "text3d": BackendDescriptor(name="text3d", adapter="_mock", vram_mib=2000, priority=30, tool="text3d"),
    }
    registry = Registry(descriptors=descriptors)
    registry._adapter_instances["text3d"] = MockAdapter(name="text3d")
    return BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None, subprocess_pool=pool)


def _age(manager: BackendManager, backend: str, seconds: float) -> None:
    """Envelhece artificialmente o ``last_used`` do backend."""
    manager._states[backend].last_used = time.monotonic() - seconds


class TestUnloadLevel:
    def test_unloads_weights_after_idle_timeout(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        _age(mgr, "text3d", 200)

        IdleEvictor(mgr, idle_timeout_sec=120.0)._check_and_evict()

        assert not mgr.is_loaded("text3d")
        # Worker continua vivo — recarregar é mais barato que respawn.
        assert pool.is_alive("text3d")
        assert pool.shutdown_calls == []

    def test_keeps_warm_model_inside_timeout(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        _age(mgr, "text3d", 30)

        IdleEvictor(mgr, idle_timeout_sec=120.0)._check_and_evict()

        assert mgr.is_loaded("text3d")


class TestWorkerShutdownLevel:
    def test_shuts_down_worker_after_longer_idle(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        _age(mgr, "text3d", 600)
        evictor = IdleEvictor(mgr, idle_timeout_sec=120.0, worker_shutdown_sec=300.0)

        evictor._check_and_evict()  # unload primeiro
        evictor._shutdown_idle_workers()

        assert pool.shutdown_calls == ["text3d"]
        assert not pool.is_alive("text3d")

    def test_loaded_worker_is_not_shut_down(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        _age(mgr, "text3d", 600)

        # Sem passar pelo unload, o worker tem modelo carregado: só o nível 1 age.
        IdleEvictor(mgr, worker_shutdown_sec=300.0)._shutdown_idle_workers()

        assert pool.shutdown_calls == []

    def test_zero_disables_worker_shutdown(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        _age(mgr, "text3d", 9999)
        evictor = IdleEvictor(mgr, idle_timeout_sec=120.0, worker_shutdown_sec=0.0)

        evictor._check_and_evict()
        evictor._shutdown_idle_workers()

        assert pool.shutdown_calls == []

    def test_refuses_while_refs_active(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        mgr._states["text3d"].ref_count = 1
        _age(mgr, "text3d", 9999)

        assert mgr.shutdown_worker("text3d") is False
        assert pool.shutdown_calls == []

    def test_refuses_while_gen_lock_held_during_load(self) -> None:
        """Race real: IdleEvictor via ``last_activity`` antiga mata worker mid-load.

        ``ensure_loaded`` segura ``gen_lock`` durante spawn/load (pode >60s) com
        ``ref_count==0`` e ``is_loaded()==False``. Sem o guard, shutdown corre e
        o generate a seguir falha com ``worker não está vivo``.
        """
        pool = FakePool()
        mgr = _make_manager(pool)
        # Simular worker vivo pós-unload (modelo fora, processo ainda up).
        mgr.ensure_loaded("text3d")
        mgr.evict("text3d")
        assert pool.is_alive("text3d")
        assert not mgr.is_loaded("text3d")
        # Timer antigo — IdleEvictor acharia idle há 829s.
        mgr._states["text3d"].last_activity = time.monotonic() - 829.0

        held = mgr._states["text3d"].gen_lock
        assert held.acquire(blocking=False)
        try:
            candidates = mgr.idle_worker_candidates(300.0)
            assert candidates == []
            assert mgr.shutdown_worker("text3d") is False
            IdleEvictor(mgr, worker_shutdown_sec=300.0)._shutdown_idle_workers()
            assert pool.shutdown_calls == []
            assert pool.is_alive("text3d")
        finally:
            held.release()


class TestHealthCheck:
    def test_wedged_worker_is_killed(self) -> None:
        pool = FakePool(ping_ok=False)
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")

        results = mgr.health_check_workers()

        assert results == [{"backend": "text3d", "ok": False}]
        assert pool.shutdown_calls == ["text3d"]

    def test_healthy_worker_survives(self) -> None:
        pool = FakePool(ping_ok=True)
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")

        results = mgr.health_check_workers()

        assert results == [{"backend": "text3d", "ok": True}]
        assert pool.shutdown_calls == []

    def test_evictor_respects_health_interval(self) -> None:
        pool = FakePool()
        mgr = _make_manager(pool)
        mgr.ensure_loaded("text3d")
        evictor = IdleEvictor(mgr, health_check_sec=60.0)

        evictor._health_check()
        first = len(pool.ping_calls)
        evictor._health_check()  # dentro do intervalo — não repete

        assert first == 1
        assert len(pool.ping_calls) == 1


class TestVramRecovery:
    def test_ensure_vram_reaps_strays_before_refusing(self) -> None:
        pool = FakePool()
        descriptors = {
            "text3d": BackendDescriptor(name="text3d", adapter="_mock", vram_mib=2000, priority=30, tool="text3d"),
        }
        registry = Registry(descriptors=descriptors)
        registry._adapter_instances["text3d"] = MockAdapter(name="text3d")
        free = {"mib": 1000}
        reaped: list[bool] = []

        def fake_reap() -> dict[str, Any]:
            reaped.append(True)
            free["mib"] = 6000  # órfão morreu, VRAM voltou
            return {"count": 1, "vram_mib_freed": 5000, "reaped": [{"pid": 900}]}

        mgr = BackendManager(
            registry,
            query_free_mib=lambda: free["mib"],
            clear_vram=lambda: None,
            subprocess_pool=pool,
            reap_strays=fake_reap,
        )

        assert mgr.ensure_vram(5000) is True
        assert reaped == [True]

    def test_reap_strays_without_hook_is_noop(self) -> None:
        mgr = _make_manager(FakePool())
        assert mgr.reap_strays() is False
