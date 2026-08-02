"""Testes do ``BackendManager.zero_vram`` e do comando ``zero`` (``ums zero``).

Cobre o caminho «zerar a VRAM sem parar o supervisor»: ``evict`` só larga
pesos, mas os workers subprocesso ficam vivos a segurar o contexto CUDA
(~0.3-1 GiB cada). ``zero`` termina todos os workers vivos (sem reload — o
próximo generate faz spawn fresco), evicta resíduos in-process e scrubba
caches, recusando com fila ocupada (nunca mata um worker mid-job).

Estratégia: reutiliza ``MockRespawnPool``/factories de ``test_respawn.py``
(manager-level) e ``_start_ums``/``_send_request`` de ``test_server.py``
(protocolo sobre socket real com adapters mock). Sem GPU.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from modelserver import protocol as P
from modelserver.backend_manager import ShapeBusyError
from modelserver.registry import BackendDescriptor, Registry

from .test_backend_manager_hybrid import MockSubprocessPool
from .test_respawn import _make_manager
from .test_server import _make_registry, _send_request, _start_ums, _stop_ums

# ---------------------------------------------------------------------------
# Manager-level (MockRespawnPool)
# ---------------------------------------------------------------------------


class TestZeroVramManager:
    def test_zero_kills_all_live_workers(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        assert pool.is_alive("sub_tool")

        summary = mgr.zero_vram()

        assert summary["workers_killed"] == 1
        assert pool.shutdown_calls == ["sub_tool"]
        assert not pool.is_alive("sub_tool")
        assert not mgr.is_loaded("sub_tool")
        result = summary["results"][0]
        assert result["name"] == "sub_tool"
        assert result["killed"] is True
        assert result["was_alive"] is True
        assert result["had_model"] is True

    def test_zero_skips_dead_workers(self) -> None:
        mgr, pool = _make_manager()
        # Sem ensure_loaded — worker nunca nasceu.
        summary = mgr.zero_vram()

        assert summary["workers_killed"] == 0
        assert pool.shutdown_calls == []
        result = summary["results"][0]
        assert result["name"] == "sub_tool"
        assert result["killed"] is False
        assert result["was_alive"] is False
        assert result["had_model"] is False

    def test_zero_refuses_with_ref_count(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4", _pin=True)
        assert mgr._states["sub_tool"].ref_count == 1

        with pytest.raises(ShapeBusyError, match="sub_tool"):
            mgr.zero_vram()

        # Não matou o worker.
        assert pool.shutdown_calls == []
        assert pool.is_alive("sub_tool")

    def test_zero_evicts_inprocess_backends(self) -> None:
        mgr, _ = _make_manager()
        mgr.ensure_loaded("inproc")
        assert mgr.is_loaded("inproc")

        summary = mgr.zero_vram()

        assert summary["evicted_in_process"] == 1
        assert not mgr.is_loaded("inproc")
        # Backend in-process nunca entra na lista de workers a matar.
        assert all(r["name"] != "inproc" for r in summary["results"])

    def test_zero_reports_free_mib(self) -> None:
        mgr, _ = _make_manager(free_mib=99999)
        summary = mgr.zero_vram()
        assert summary["free_mib_before"] == 99999
        assert summary["free_mib_after"] == 99999
        assert summary["scrub"]["scrubbed"] is True

    def test_zero_unloads_loaded_worker_before_kill(self) -> None:
        mgr, pool = _make_manager()
        mgr.ensure_loaded("sub_tool", sdnq_preset="sdnq-int4")
        # Simular unload prévio (evict) — worker vivo, sem modelo.
        mgr.evict("sub_tool")
        assert pool.is_alive("sub_tool")

        summary = mgr.zero_vram()

        # Mesmo descarregado, o worker vivo segura contexto CUDA — tem de morrer.
        assert summary["workers_killed"] == 1
        assert summary["results"][0]["had_model"] is False
        assert summary["results"][0]["killed"] is True


# ---------------------------------------------------------------------------
# Protocolo (socket real, adapters/pool mock)
# ---------------------------------------------------------------------------


class TestZeroVramDispatch:
    def test_zero_ok_with_subprocess_backend(self, tmp_path: Path) -> None:
        descriptors = {
            "sub_be": BackendDescriptor(name="sub_be", adapter="_m", vram_mib=1000, priority=10, tool="text3d"),
        }
        registry = Registry(descriptors=descriptors)
        pool = MockSubprocessPool()
        srv, sock, thread = _start_ums(tmp_path, registry=registry, subprocess_pool=pool)
        try:
            resp = _send_request(sock, {"cmd": P.CMD_ZERO})
            assert resp["status"] == P.STATUS_OK
            assert "supervisor intacto" in resp["message"]
            assert resp["workers_killed"] == 0  # worker nunca nasceu
            assert resp["results"][0]["name"] == "sub_be"
            assert resp["results"][0]["was_alive"] is False
            assert resp["loaded_backends"] == []
            assert "scrub" in resp
        finally:
            _stop_ums(srv, sock, thread)

    def test_zero_evicts_loaded_inprocess_backend(self, tmp_path: Path) -> None:
        registry = _make_registry()
        srv, sock, thread = _start_ums(tmp_path, registry=registry)
        try:
            _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "alpha"})
            assert _send_request(sock, {"cmd": P.CMD_STATUS})["loaded_count"] == 1

            resp = _send_request(sock, {"cmd": P.CMD_ZERO})
            assert resp["status"] == P.STATUS_OK
            assert resp["evicted_in_process"] >= 1
            assert _send_request(sock, {"cmd": P.CMD_STATUS})["loaded_count"] == 0
        finally:
            _stop_ums(srv, sock, thread)

    def test_zero_busy_refused(self, tmp_path: Path) -> None:
        registry = _make_registry(slow=True, delay=0.8)
        srv, sock, thread = _start_ums(tmp_path, registry=registry)
        try:
            # Bloquear o worker com um generate lento → inflight > 0.
            blocker = threading.Thread(
                target=_send_request,
                args=(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "block"}),
                kwargs={"timeout": 15.0},
                daemon=True,
            )
            blocker.start()
            time.sleep(0.1)
            resp = _send_request(sock, {"cmd": P.CMD_ZERO})
            assert resp["status"] == P.STATUS_ERROR
            assert resp["error_code"] == P.ERR_ZERO_BUSY
            assert "hint" in resp
            blocker.join(timeout=15.0)
            # O job bloqueante sobreviveu — zero nunca mata worker mid-job.
            assert _send_request(sock, {"cmd": P.CMD_STATUS})["loaded_count"] == 1
        finally:
            _stop_ums(srv, sock, thread)
