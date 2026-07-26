"""Testes do ``BackendManager.respawn`` / ``respawn_all``.

Cobre o mecanismo que falta para tornar o UMS independente do código das
tools: depois de editar código de uma tool, o worker persistente no venv dela
ainda tem o módulo antigo em memória. ``evict`` só descarrega pesos; ``respawn``
mata o subprocesso do worker e (opcionalmente) arranca um novo com o mesmo
``load_shape``, pelo que o próximo ``generate`` já corre o código atualizado
sem reiniciar o supervisor UMS.

Estratégia: mock do ``SubprocessWorkerPool`` (``MockRespawnPool``) que regista
``shutdown``/``load``/``unload``/``is_alive`` sem spawnar nada, igual ao
padrão de ``test_backend_manager_hybrid.py``.
"""

from __future__ import annotations

from typing import Any

import pytest
from modelserver.backend_manager import BackendManager, ShapeBusyError
from modelserver.registry import BackendDescriptor, Registry

from .conftest_helpers import MockAdapter

# ---------------------------------------------------------------------------
# Mock pool (regista chamadas; simula estado vivo/carregado)
# ---------------------------------------------------------------------------


class MockRespawnPool:
    """Mock do SubprocessWorkerPool focado em respawn (shutdown/load/is_alive)."""

    def __init__(self) -> None:
        self.load_calls: list[tuple[str, str, dict[str, Any]]] = []
        self.shutdown_calls: list[str] = []
        self.unload_calls: list[str] = []
        self._alive: set[str] = set()
        self._loaded: set[str] = set()

    def load(self, backend: str, tool: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        self.load_calls.append((backend, tool, dict(kwargs)))
        self._alive.add(backend)
        self._loaded.add(backend)
        return {"event": "ready", "vram_mib": 2000}

    def generate(self, backend: str, request: dict[str, Any], **_: Any) -> dict[str, Any]:
        return {"status": "ok"}

    def unload(self, backend: str) -> bool:
        self.unload_calls.append(backend)
        self._loaded.discard(backend)
        return True

    def shutdown(self, backend: str) -> bool:
        self.shutdown_calls.append(backend)
        was_alive = backend in self._alive
        self._alive.discard(backend)
        self._loaded.discard(backend)
        # Espelha o pool real: devolve False se o worker nunca existiu.
        return was_alive

    def is_loaded(self, backend: str) -> bool:
        return backend in self._loaded

    def is_alive(self, backend: str) -> bool:
        return backend in self._alive

    def vram_mib(self, backend: str) -> int | None:
        return 2000 if backend in self._loaded else None

    def loaded_backends(self) -> set[str]:
        return set(self._loaded)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_registry() -> Registry:
    """Registry com 2 backends: 'sub_tool' (subprocesso) e 'inproc' (in-process)."""
    descriptors = {
        "sub_tool": BackendDescriptor(name="sub_tool", adapter="_mock_sub", vram_mib=2000, priority=30, tool="text3d"),
        "inproc": BackendDescriptor(name="inproc", adapter="_mock_inproc", vram_mib=3000, priority=20, tool=None),
    }
    registry = Registry(descriptors=descriptors)
    registry._adapter_instances["inproc"] = MockAdapter(name="inproc")
    return registry


def _make_manager(
    *, pool: MockRespawnPool | None = None, free_mib: int = 99999
) -> tuple[BackendManager, MockRespawnPool]:
    p = pool or MockRespawnPool()
    mgr = BackendManager(
        _make_registry(),
        query_free_mib=lambda: free_mib,
        clear_vram=lambda: None,
        subprocess_pool=p,
    )
    return mgr, p


# ---------------------------------------------------------------------------
# Tests — respawn lazy (default): mata worker, não recarrega
# ---------------------------------------------------------------------------


class TestRespawnLazy:
    def test_lazy_kills_worker_without_reload(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        assert pool.is_alive("sub_tool")
        assert len(pool.load_calls) == 1

        result = mgr.respawn("sub_tool", lazy=True)

        assert result["respawned"] is True
        assert result["mode"] == "lazy"
        assert result["had_model"] is True
        assert result["was_alive"] is True
        # shutdown chamado, sem novo load.
        assert pool.shutdown_calls == ["sub_tool"]
        assert len(pool.load_calls) == 1  # só o load inicial
        # Estado do manager: descarregado.
        assert not mgr.is_loaded("sub_tool")

    def test_lazy_preserves_load_shape_for_next_load(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4", max_num_view=6)

        result = mgr.respawn("sub_tool", lazy=True)

        assert result["load_shape"] == {"sdnq_preset": "sdnq-int4", "max_num_view": 6}
        # Próximo ensure_loaded arranca worker novo e preserva a shape.
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4", max_num_view=6)
        assert len(pool.load_calls) == 2
        _, _, kwargs = pool.load_calls[1]
        assert kwargs == {"sdnq_preset": "sdnq-int4", "max_num_view": 6}

    def test_lazy_on_dead_worker_returns_respawned_false(self) -> None:
        mgr, pool = _make_manager()
        # Sem ensure_loaded — worker nunca nasceu.
        result = mgr.respawn("sub_tool", lazy=True)
        assert result["respawned"] is False
        assert result["was_alive"] is False
        assert result["had_model"] is False
        # shutdown chamado mesmo assim (idempotente), mas devolve False.
        assert pool.shutdown_calls == ["sub_tool"]


# ---------------------------------------------------------------------------
# Tests — respawn hot: mata e recarrega com mesmo load_shape
# ---------------------------------------------------------------------------


class TestRespawnHot:
    def test_hot_kills_and_reloads_with_same_shape(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        assert len(pool.load_calls) == 1

        result = mgr.respawn("sub_tool", lazy=False)

        assert result["respawned"] is True
        assert result["mode"] == "hot"
        # shutdown + novo load com a shape guardada.
        assert pool.shutdown_calls == ["sub_tool"]
        assert len(pool.load_calls) == 2
        _, _, kwargs = pool.load_calls[1]
        assert kwargs == {"sdnq_preset": "sdnq-int4"}
        # Fica quente: is_loaded True de novo.
        assert mgr.is_loaded("sub_tool")

    def test_hot_on_dead_worker_loads_with_saved_shape(self) -> None:
        mgr, pool = _make_manager()
        # Carrega, depois simula morte do worker (sem passar pelo manager).
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        pool._alive.discard("sub_tool")
        pool._loaded.discard("sub_tool")

        result = mgr.respawn("sub_tool", lazy=False)

        # was_alive False (morreu), mas reload acontece com a shape guardada.
        assert result["was_alive"] is False
        assert len(pool.load_calls) == 2
        assert mgr.is_loaded("sub_tool")


# ---------------------------------------------------------------------------
# Tests — guards (recusa quando ocupado; no-op em in-process)
# ---------------------------------------------------------------------------


class TestRespawnGuards:
    def test_refuses_when_ref_count_positive(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4", _pin=True)
        assert mgr._states["sub_tool"].ref_count == 1

        with pytest.raises(ShapeBusyError, match="sub_tool"):
            mgr.respawn("sub_tool", lazy=True)

        # Não matou o worker.
        assert pool.shutdown_calls == []

    def test_inprocess_backend_is_noop(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("inproc")

        result = mgr.respawn("inproc", lazy=True)

        assert result["respawned"] is False
        assert result["mode"] == "in-process"
        assert "sem worker subprocesso" in result["reason"]
        assert pool.shutdown_calls == []
        # Backend in-process continua carregado.
        assert mgr.is_loaded("inproc")

    def test_unknown_backend_raises_keyerror(self) -> None:
        mgr, _ = _make_manager()
        with pytest.raises(KeyError):
            mgr.respawn("nonexistent", lazy=True)


# ---------------------------------------------------------------------------
# Tests — respawn_all
# ---------------------------------------------------------------------------


class TestRespawnAll:
    def test_respawn_all_only_targets_subprocess_backends(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        mgr.ensure_loaded("inproc")

        results = mgr.respawn_all(lazy=True)

        # Apenas 1 resultado: sub_tool (inproc é filtrado por _use_subprocess).
        assert len(results) == 1
        assert results[0]["name"] == "sub_tool"
        assert results[0]["respawned"] is True
        assert pool.shutdown_calls == ["sub_tool"]

    def test_respawn_all_empty_when_nothing_loaded(self) -> None:
        mgr, _ = _make_manager()
        results = mgr.respawn_all(lazy=True)
        # sub_tool tem tool: → entra na lista mesmo sem estar loaded (shutdown no-op).
        assert len(results) == 1
        assert results[0]["respawned"] is False
