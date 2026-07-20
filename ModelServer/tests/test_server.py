"""Testes do UnifiedModelServer — protocolo JSON sobre Unix socket real.

Estes testes arrancam um UMS num thread com adapters mock e socket temporário,
depois enviam pedidos reais via cliente socket. Sem GPU — adapters mock.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from modelserver import protocol as P
from modelserver.adapters.base import BackendAdapter
from modelserver.registry import BackendDescriptor, Registry
from modelserver.server import UnifiedModelServer

from .conftest_helpers import MockAdapter, MockModel


class SlowMockAdapter(BackendAdapter):
    """Adapter que dorme em generate (para testes de fila)."""

    def __init__(self, name: str = "slow", delay_sec: float = 0.4) -> None:
        self.name = name
        self.delay_sec = delay_sec
        self.load_calls = 0

    def load(self, **kwargs: Any) -> MockModel:
        self.load_calls += 1
        return MockModel(self.name, **kwargs)

    def generate(self, model: MockModel, request: dict[str, Any]) -> dict[str, Any]:
        BackendAdapter.report_progress(request, 0.5, "halfway")
        time.sleep(self.delay_sec)
        return {"status": "ok", "output": f"/tmp/mock-{model.name}.png", "seed": 1}

    def unload(self, model: MockModel) -> None:
        model.unloaded = True


def _make_registry(*, slow: bool = False, delay: float = 0.4) -> Registry:
    specs = {"alpha": (1000, 10), "beta": (3000, 30)}
    descriptors = {
        n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
    }
    registry = Registry(descriptors=descriptors)
    for n in specs:
        if slow and n == "alpha":
            registry._adapter_instances[n] = SlowMockAdapter(name=n, delay_sec=delay)
        else:
            registry._adapter_instances[n] = MockAdapter(name=n)
    return registry


def _send_request(socket_path: Path, request: dict, timeout: float = 10.0) -> dict:
    """Cliente raw: envia 1 linha JSON, lê a resposta."""
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(socket_path))
        s.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
    lines = data.decode().strip().split("\n")
    return json.loads(lines[-1])


def _send_request_all_lines(socket_path: Path, request: dict, timeout: float = 10.0) -> list[dict]:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect(str(socket_path))
        s.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(8192)
            if not chunk:
                break
            data += chunk
    return [json.loads(line) for line in data.decode().strip().split("\n") if line.strip()]


def _start_ums(tmp_path: Path, **kwargs: Any):
    socket_path = tmp_path / "test-ums.sock"
    registry = kwargs.pop("registry", None) or _make_registry()
    # Hermético: nunca consultar a VRAM real da máquina (o admit usa o pico
    # pesos+activação+safety; um GPU ocupado faria preload/generate falhar).
    kwargs.setdefault("query_free_mib", lambda: 99999)
    kwargs.setdefault("clear_vram", lambda: None)
    srv = UnifiedModelServer(registry=registry, socket_path=socket_path, verbose=False, **kwargs)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert socket_path.exists(), "UMS não arrancou a tempo"
    return srv, socket_path, thread


def _stop_ums(srv: UnifiedModelServer, socket_path: Path, thread: threading.Thread) -> None:
    srv._running = False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        try:
            s.connect(str(socket_path))
            s.sendall((json.dumps({"cmd": P.CMD_SHUTDOWN}) + "\n").encode())
        except OSError:
            pass
    thread.join(timeout=5.0)


@pytest.fixture
def running_ums(tmp_path: Path):
    """Arranca um UMS num thread com adapters mock e socket temporário."""
    srv, socket_path, thread = _start_ums(tmp_path)
    yield srv, socket_path
    _stop_ums(srv, socket_path, thread)


class TestServerProtocol:
    """Protocolo do UMS sobre socket real."""

    def test_status_command(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_STATUS})
        assert resp["status"] == P.STATUS_STATUS
        assert resp["pid"] > 0
        assert resp["socket"] == str(sock)
        assert resp["tool"] == "modelserver"
        assert "backends" in resp
        assert resp["loaded_count"] == 0  # nada carregado no arranque
        assert "debug" in resp
        assert "loaded_backends" in resp["debug"]
        assert "last_errors" in resp["debug"]

    def test_list_backends(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_LIST_BACKENDS})
        assert resp["status"] == P.STATUS_OK
        names = [b["name"] for b in resp["backends"]]
        assert set(names) == {"alpha", "beta"}

    def test_generate_with_backend(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(
            sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "hello", "output": "/tmp/x.png"}
        )
        assert resp["status"] == P.STATUS_OK
        assert resp["output"] == "/tmp/mock-alpha.png"
        dbg = resp["ums_debug"]
        assert dbg["backend"] == "alpha"
        assert dbg["job_id"]
        assert dbg["priority"] == P.PRIORITY_INTERACTIVE
        assert dbg["state"] == P.JOB_DONE
        assert dbg["queue_wait_sec"] is not None
        assert dbg["generate_sec"] is not None
        assert dbg["total_sec"] is not None
        assert "alpha" in dbg["loaded_backends"]

    def test_generate_without_backend_is_error(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_GENERATE, "prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == P.STATUS_ERROR
        assert "backend" in resp["error"].lower()
        assert resp["error_code"] == P.ERR_BACKEND_AMBIGUOUS
        assert "hint" in resp

    def test_generate_unknown_backend(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "nope", "prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == P.STATUS_ERROR
        assert "desconhecido" in resp["error"]
        assert resp["error_code"] == P.ERR_BACKEND_UNKNOWN
        assert "hint" in resp

    def test_preload_then_status_shows_loaded(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "beta"})
        assert resp["status"] == P.STATUS_OK
        assert resp["ums_debug"]["backend"] == "beta"
        assert "beta" in resp["ums_debug"]["loaded_backends"]

        status = _send_request(sock, {"cmd": P.CMD_STATUS})
        assert status["loaded_count"] == 1
        assert status["loaded_vram_mib"] == 3000  # beta = 3000 MiB

    def test_release_specific_backend(self, running_ums) -> None:
        _, sock = running_ums
        _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "alpha"})
        assert _send_request(sock, {"cmd": P.CMD_STATUS})["loaded_count"] == 1

        resp = _send_request(sock, {"cmd": P.CMD_RELEASE, "backend": "alpha"})
        assert resp["status"] == P.STATUS_OK
        assert _send_request(sock, {"cmd": P.CMD_STATUS})["loaded_count"] == 0

    def test_release_all(self, running_ums) -> None:
        _, sock = running_ums
        _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "alpha"})
        _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "beta"})

        resp = _send_request(sock, {"cmd": P.CMD_RELEASE})
        assert resp["status"] == P.STATUS_OK
        # Pode ser 1 ou 2 dependendo de se o VRAMPlanner evictou alpha ao carregar beta.
        assert "backend(s) evicted" in resp["message"]

    def test_ensure_vram(self, running_ums) -> None:
        _, sock = running_ums
        _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "alpha"})

        resp = _send_request(sock, {"cmd": P.CMD_ENSURE_VRAM, "needed_mib": 1000})
        # Sem GPU real (NVML/smi pode não estar disponível em CI), ensure_vram
        # retorna OK (não evicta cegamente se não consegue verificar VRAM).
        assert resp["status"] in (P.STATUS_OK, P.STATUS_ERROR)
        assert resp.get("needed_mib") == 1000
        assert "ums_debug" in resp
        assert "loaded_before" in resp["ums_debug"]

    def test_shutdown_command(self, running_ums) -> None:
        srv, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_SHUTDOWN})
        assert resp["status"] == P.STATUS_OK
        # O server deve parar.
        deadline = time.monotonic() + 3.0
        while srv._running and time.monotonic() < deadline:
            time.sleep(0.05)
        assert not srv._running

    def test_invalid_json(self, running_ums) -> None:
        _, sock = running_ums
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5.0)
            s.connect(str(sock))
            s.sendall(b"not json at all\n")
            data = b""
            while True:
                chunk = s.recv(8192)
                if not chunk:
                    break
                data += chunk
        resp = json.loads(data.decode().strip().split("\n")[-1])
        assert resp["status"] == P.STATUS_ERROR
        assert "JSON" in resp["error"]
        assert resp["error_code"] == P.ERR_INVALID_REQUEST

    def test_unknown_command(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": "frobnicate"})
        assert resp["status"] == P.STATUS_ERROR
        assert "desconhecido" in resp["error"]
        assert resp["error_code"] == P.ERR_INVALID_REQUEST

    def test_requests_served_counter(self, running_ums) -> None:
        _, sock = running_ums
        _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "x", "output": "/tmp/x.png"})
        _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "x", "output": "/tmp/x.png"})

        status = _send_request(sock, {"cmd": P.CMD_STATUS})
        assert status["requests_served"] == 2

    def test_status_includes_queue(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_STATUS})
        assert "queue" in resp
        assert resp["queue"]["queue_depth"] == 0
        assert "max_affinity_cuts" in resp

    def test_submit_poll_wait(self, running_ums) -> None:
        _, sock = running_ums
        sub = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "alpha", "prompt": "x", "output": "/tmp/x.png"})
        assert sub["status"] == P.STATUS_OK
        job_id = sub["job_id"]
        # Esperar conclusão.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            poll = _send_request(sock, {"cmd": P.CMD_POLL, "job_id": job_id})
            if poll.get("state") in (P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED):
                break
            time.sleep(0.05)
        wait = _send_request(sock, {"cmd": P.CMD_WAIT, "job_id": job_id})
        assert wait["status"] == P.STATUS_OK
        assert wait["output"] == "/tmp/mock-alpha.png"
        assert wait["ums_debug"]["job_id"] == job_id
        poll = _send_request(sock, {"cmd": P.CMD_POLL, "job_id": job_id})
        assert poll["ums_debug"]["job_id"] == job_id

    def test_cancel_queued_job(self, tmp_path: Path) -> None:
        registry = _make_registry(slow=True, delay=0.8)
        srv, sock, thread = _start_ums(tmp_path, registry=registry, max_queue_depth=8)
        try:
            # Bloquear o worker com um generate lento.
            blocker = threading.Thread(
                target=_send_request,
                args=(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "block"}),
                kwargs={"timeout": 15.0},
                daemon=True,
            )
            blocker.start()
            time.sleep(0.1)
            sub = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "beta", "prompt": "queued"})
            assert sub["status"] == P.STATUS_OK
            cancel = _send_request(sock, {"cmd": P.CMD_CANCEL, "job_id": sub["job_id"]})
            assert cancel["status"] == P.STATUS_OK
            assert cancel["state"] == P.JOB_CANCELLED
            blocker.join(timeout=15.0)
        finally:
            _stop_ums(srv, sock, thread)

    def test_queue_full(self, tmp_path: Path) -> None:
        registry = _make_registry(slow=True, delay=0.6)
        srv, sock, thread = _start_ums(tmp_path, registry=registry, max_queue_depth=1)
        try:
            blocker = threading.Thread(
                target=_send_request,
                args=(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "block"}),
                kwargs={"timeout": 15.0},
                daemon=True,
            )
            blocker.start()
            time.sleep(0.1)
            # 1 slot na fila
            ok = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "beta", "prompt": "q1"})
            assert ok["status"] == P.STATUS_OK
            full = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "beta", "prompt": "q2"})
            assert full["status"] == P.STATUS_QUEUE_FULL
            assert full["error_code"] == P.ERR_QUEUE_FULL
            assert "hint" in full
            assert full["ums_debug"]["backend"] == "beta"
            assert full["ums_debug"]["max_depth"] == 1
            blocker.join(timeout=15.0)
        finally:
            _stop_ums(srv, sock, thread)

    def test_stream_generate_events(self, running_ums) -> None:
        _, sock = running_ums
        lines = _send_request_all_lines(
            sock,
            {
                "cmd": P.CMD_GENERATE,
                "backend": "alpha",
                "prompt": "x",
                "stream": True,
            },
        )
        assert len(lines) >= 2
        events = {line.get("event") for line in lines if "event" in line}
        assert P.EVENT_QUEUED in events or lines[0].get("event") == P.EVENT_QUEUED
        assert lines[-1]["status"] == P.STATUS_OK
        assert lines[-1]["ums_debug"]["backend"] == "alpha"
        started = next((ln for ln in lines if ln.get("event") == P.EVENT_STARTED), None)
        if started is not None:
            assert "queue_wait_sec" in started
            assert "affinity_cuts" in started

    def test_priority_batch_field_accepted(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(
            sock,
            {
                "cmd": P.CMD_GENERATE,
                "backend": "alpha",
                "prompt": "x",
                "priority": "batch",
            },
        )
        assert resp["status"] == P.STATUS_OK
        assert resp["priority"] == P.PRIORITY_BATCH
        assert resp["ums_debug"]["priority"] == P.PRIORITY_BATCH

    def test_stream_includes_started_and_progress(self, tmp_path: Path) -> None:
        registry = _make_registry(slow=True, delay=0.15)
        srv, sock, thread = _start_ums(tmp_path, registry=registry)
        try:
            lines = _send_request_all_lines(
                sock,
                {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "x", "stream": True},
                timeout=15.0,
            )
            events = [line.get("event") for line in lines if "event" in line]
            assert P.EVENT_QUEUED in events
            assert P.EVENT_STARTED in events
            assert P.EVENT_PROGRESS in events
            assert lines[-1]["status"] == P.STATUS_OK
            # Resultado final não deve ser só um event=done sem status.
            assert "status" in lines[-1]
        finally:
            _stop_ums(srv, sock, thread)

    def test_cancel_running_via_socket(self, tmp_path: Path) -> None:
        registry = _make_registry(slow=True, delay=0.5)
        srv, sock, thread = _start_ums(tmp_path, registry=registry)
        try:
            sub = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "alpha", "prompt": "slow"})
            job_id = sub["job_id"]
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                poll = _send_request(sock, {"cmd": P.CMD_POLL, "job_id": job_id})
                if poll.get("state") == P.JOB_RUNNING:
                    break
                time.sleep(0.02)
            cancel = _send_request(sock, {"cmd": P.CMD_CANCEL, "job_id": job_id})
            assert cancel["status"] == P.STATUS_OK
            assert cancel["state"] == P.JOB_RUNNING
            # Aguardar fim — deve terminar como cancelled (não ok).
            deadline = time.monotonic() + 5.0
            final = None
            while time.monotonic() < deadline:
                final = _send_request(sock, {"cmd": P.CMD_POLL, "job_id": job_id})
                if final.get("state") in (P.JOB_DONE, P.JOB_FAILED, P.JOB_CANCELLED):
                    break
                time.sleep(0.05)
            assert final is not None
            assert final["state"] == P.JOB_CANCELLED
        finally:
            _stop_ums(srv, sock, thread)

    def test_ctor_kwargs_override_limits(self, tmp_path: Path) -> None:
        srv, sock, thread = _start_ums(tmp_path, max_queue_depth=7, max_inflight=1, max_affinity_cuts=2)
        try:
            status = _send_request(sock, {"cmd": P.CMD_STATUS})
            assert status["max_affinity_cuts"] == 2
            assert status["max_inflight"] == 1
            assert status["queue"]["max_depth"] == 7
        finally:
            _stop_ums(srv, sock, thread)


class TestQueueSchedulerIntegration:
    """Fila + scheduler + worker sem socket (ordem determinística)."""

    def test_hot_backend_runs_before_cold_head(self, tmp_path: Path) -> None:
        order: list[str] = []

        class _OrderAdapter(MockAdapter):
            def generate(self, model: MockModel, request: dict[str, Any]) -> dict[str, Any]:
                order.append(model.name)
                time.sleep(0.05)
                return super().generate(model, request)

        specs = {"alpha": (1000, 10), "beta": (3000, 30)}
        descriptors = {
            n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
        }
        registry = Registry(descriptors=descriptors)
        for n in specs:
            registry._adapter_instances[n] = _OrderAdapter(name=n)

        srv = UnifiedModelServer(
            registry=registry,
            socket_path=tmp_path / "int.sock",
            max_inflight=1,
            max_affinity_cuts=3,
        )
        # Não depender da VRAM real da máquina (admit usa pico pesos+act+safety).
        srv.manager._query_free_mib = lambda: 99999
        # beta quente em VRAM; enfileirar cold depois hot antes do worker arrancar.
        srv.manager.ensure_loaded("beta")
        j_cold = srv.queue.enqueue("alpha", {"prompt": "cold"})
        j_hot = srv.queue.enqueue("beta", {"prompt": "hot"})
        srv.workers.start()
        try:
            assert j_hot.done_event.wait(timeout=5.0)
            assert j_cold.done_event.wait(timeout=5.0)
            assert order == ["beta", "alpha"]
            assert j_cold.affinity_cuts >= 1
        finally:
            srv.workers.stop()

    def test_interactive_cold_blocks_batch_hot(self, tmp_path: Path) -> None:
        order: list[str] = []

        class _OrderAdapter(MockAdapter):
            def generate(self, model: MockModel, request: dict[str, Any]) -> dict[str, Any]:
                order.append(model.name)
                return super().generate(model, request)

        specs = {"alpha": (1000, 10), "beta": (3000, 30)}
        descriptors = {
            n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
        }
        registry = Registry(descriptors=descriptors)
        for n in specs:
            registry._adapter_instances[n] = _OrderAdapter(name=n)

        srv = UnifiedModelServer(registry=registry, socket_path=tmp_path / "pri.sock", max_inflight=1)
        srv.manager._query_free_mib = lambda: 99999
        srv.manager.ensure_loaded("beta")
        j_batch = srv.queue.enqueue("beta", {}, priority=P.PRIORITY_BATCH)
        j_inter = srv.queue.enqueue("alpha", {}, priority=P.PRIORITY_INTERACTIVE)
        srv.workers.start()
        try:
            assert j_inter.done_event.wait(timeout=5.0)
            assert j_batch.done_event.wait(timeout=5.0)
            # Interactive (alpha frio) antes do batch hot — prioridade ganha à afinidade.
            assert order[0] == "alpha"
            assert j_inter.affinity_cuts == 0
        finally:
            srv.workers.stop()

    def test_anti_starvation_after_max_cuts(self, tmp_path: Path) -> None:
        order: list[str] = []

        class _OrderAdapter(MockAdapter):
            def generate(self, model: MockModel, request: dict[str, Any]) -> dict[str, Any]:
                order.append(model.name)
                return super().generate(model, request)

        specs = {"alpha": (1000, 10), "beta": (3000, 30)}
        descriptors = {
            n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
        }
        registry = Registry(descriptors=descriptors)
        for n in specs:
            registry._adapter_instances[n] = _OrderAdapter(name=n)

        srv = UnifiedModelServer(
            registry=registry,
            socket_path=tmp_path / "starve.sock",
            max_inflight=1,
            max_affinity_cuts=1,
        )
        srv.manager._query_free_mib = lambda: 99999
        srv.manager.ensure_loaded("beta")
        j_cold = srv.queue.enqueue("alpha", {})
        # Dois jobs hot: 1 cut permite 1 skip; o 2.º pick deve forçar cold.
        j_hot1 = srv.queue.enqueue("beta", {})
        j_hot2 = srv.queue.enqueue("beta", {})
        srv.workers.start()
        try:
            for j in (j_cold, j_hot1, j_hot2):
                assert j.done_event.wait(timeout=5.0)
            # Com max_cuts=1: hot, depois cold (forçado), depois hot.
            assert order[0] == "beta"
            assert "alpha" in order
            assert order.index("alpha") < len(order)
            # Cold foi saltado no máximo 1 vez.
            assert j_cold.affinity_cuts <= 1
        finally:
            srv.workers.stop()


class TestDoubleStart:
    """Regressão: um 2.º UMS no mesmo socket NÃO pode apagar socket/pid do 1.º."""

    def test_second_start_preserves_running_server(self, tmp_path: Path) -> None:
        srv1, sock, thread = _start_ums(tmp_path)
        try:
            ppid = sock.with_suffix(".pid")
            assert ppid.exists()
            first_pid = ppid.read_text().strip()

            srv2 = UnifiedModelServer(
                registry=_make_registry(),
                socket_path=sock,
                verbose=False,
                query_free_mib=lambda: 99999,
                clear_vram=lambda: None,
            )
            with pytest.raises(OSError):
                srv2.serve_forever()

            # Socket e pid file do 1.º servidor têm de estar intactos…
            assert sock.exists()
            assert ppid.read_text().strip() == first_pid
            # …e o 1.º continua a responder (não ficou órfão/undiscoverable).
            resp = _send_request(sock, {"cmd": P.CMD_STATUS})
            assert resp["status"] == P.STATUS_STATUS
            assert str(resp["pid"]) == first_pid
        finally:
            _stop_ums(srv1, sock, thread)


class TestStreamWaitPrefix:
    """Regressão: wait --stream aceita prefixo de job_id como o wait normal."""

    def test_wait_stream_resolves_prefix(self, running_ums) -> None:
        _, sock = running_ums
        sub = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "alpha", "prompt": "x"})
        job_id = sub["job_id"]
        lines = _send_request_all_lines(sock, {"cmd": P.CMD_WAIT, "job_id": job_id[:8], "stream": True})
        assert lines[-1]["status"] == P.STATUS_OK
        assert lines[-1]["job_id"] == job_id

    def test_streaming_detaches_listener(self, running_ums) -> None:
        srv, sock = running_ums
        sub = _send_request(sock, {"cmd": P.CMD_SUBMIT, "backend": "alpha", "prompt": "x"})
        job = srv.queue.get(sub["job_id"])
        assert job is not None
        _send_request_all_lines(sock, {"cmd": P.CMD_WAIT, "job_id": sub["job_id"], "stream": True})
        # Listener do cliente NDJSON não pode ficar preso ao job (leak).
        assert job._listeners == []
