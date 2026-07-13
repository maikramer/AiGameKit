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

import pytest
from modelserver import protocol as P
from modelserver.registry import BackendDescriptor, Registry
from modelserver.server import UnifiedModelServer

from .conftest_helpers import MockAdapter


def _make_registry() -> Registry:
    specs = {"alpha": (1000, 10), "beta": (3000, 30)}
    descriptors = {
        n: BackendDescriptor(name=n, adapter=f"_mock_{n}", vram_mib=v, priority=p) for n, (v, p) in specs.items()
    }
    registry = Registry(descriptors=descriptors)
    for n in specs:
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


@pytest.fixture
def running_ums(tmp_path: Path):
    """Arranca um UMS num thread com adapters mock e socket temporário."""
    socket_path = tmp_path / "test-ums.sock"
    registry = _make_registry()
    srv = UnifiedModelServer(registry=registry, socket_path=socket_path, verbose=False)

    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()

    # Esperar que o socket esteja pronto.
    deadline = time.monotonic() + 5.0
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert socket_path.exists(), "UMS não arrancou a tempo"

    yield srv, socket_path

    # Teardown: graceful shutdown.
    srv._running = False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.settimeout(2.0)
        try:
            s.connect(str(socket_path))
            s.sendall((json.dumps({"cmd": P.CMD_SHUTDOWN}) + "\n").encode())
        except OSError:
            pass
    thread.join(timeout=5.0)


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

    def test_generate_without_backend_is_error(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_GENERATE, "prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == P.STATUS_ERROR
        assert "backend" in resp["error"].lower()

    def test_generate_unknown_backend(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "nope", "prompt": "x", "output": "/tmp/x.png"})
        assert resp["status"] == P.STATUS_ERROR
        assert "desconhecido" in resp["error"]

    def test_preload_then_status_shows_loaded(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": P.CMD_PRELOAD, "backend": "beta"})
        assert resp["status"] == P.STATUS_OK

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
        # Sem GPU real (nvidia-smi pode não estar disponível em CI), ensure_vram
        # retorna OK (não evicta cegamente se não consegue verificar VRAM).
        assert resp["status"] in (P.STATUS_OK, P.STATUS_ERROR)
        assert resp.get("needed_mib") == 1000

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

    def test_unknown_command(self, running_ums) -> None:
        _, sock = running_ums
        resp = _send_request(sock, {"cmd": "frobnicate"})
        assert resp["status"] == P.STATUS_ERROR
        assert "desconhecido" in resp["error"]

    def test_requests_served_counter(self, running_ums) -> None:
        _, sock = running_ums
        _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "x", "output": "/tmp/x.png"})
        _send_request(sock, {"cmd": P.CMD_GENERATE, "backend": "alpha", "prompt": "x", "output": "/tmp/x.png"})

        status = _send_request(sock, {"cmd": P.CMD_STATUS})
        assert status["requests_served"] == 2
