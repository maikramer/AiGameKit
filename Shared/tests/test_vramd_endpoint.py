"""Endpoint do vramd no cliente — Unix no POSIX, TCP+token no Windows.

O caminho TCP é forçado em Linux com ``VRAMD_TRANSPORT=tcp``: é exatamente o
que corre no Windows, por isso o CI Linux cobre a lógica sem esperar por um
runner Windows.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from aigamekit_shared import vramd_endpoint as endpoint
from aigamekit_shared.vramd_client import ModelServer, is_server_running, send_request


def _loader():
    class _Gen:
        def warmup(self) -> None:
            pass

        def generate(self, **kwargs):
            return None, {}

        def unload(self) -> None:
            pass

    return _Gen()


def _generator(_gen, _request):
    return {"status": "ok", "output": "/tmp/mock.png", "seed": 42}


def _start_server(sock_path: Path) -> tuple[ModelServer, threading.Thread]:
    server = ModelServer(
        socket_path=sock_path,
        loader=_loader,
        generator=_generator,
        idle_timeout_min=60,
        verbose=False,
        tool_name="test",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not sock_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert sock_path.exists(), "servidor não arrancou"
    return server, thread


class TestDescriptor:
    def test_read_bad_descriptor(self, tmp_path: Path) -> None:
        path = tmp_path / "x.sock"
        path.write_text("não json", encoding="utf-8")
        assert endpoint.read_endpoint(path) is None
        path.write_text(json.dumps({"transport": "tcp", "port": 0}), encoding="utf-8")
        assert endpoint.read_endpoint(path) is None

    def test_auth_helpers(self) -> None:
        req = {"cmd": "status"}
        assert endpoint.with_auth(req, None) is req
        assert endpoint.with_auth(req, "tok") == {"cmd": "status", "auth": "tok"}
        assert endpoint.is_authorized({"auth": "tok"}, "tok")
        assert not endpoint.is_authorized({}, "tok")

    def test_cache_dirs_env_override(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("VRAMD_CACHE_DIR", str(tmp_path / "cache"))
        assert endpoint.aigamekit_cache_dir() == tmp_path / "cache"
        assert endpoint.vramd_cache_dir() == tmp_path / "cache"
        assert endpoint.default_vramd_socket() == tmp_path / "cache" / "vramd.sock"


@pytest.fixture
def tcp_mode(monkeypatch):
    monkeypatch.setenv(endpoint.ENV_TRANSPORT, endpoint.TRANSPORT_TCP)


class TestTcpModelServer:
    def test_roundtrip_and_unauthorized(self, tmp_path: Path, tcp_mode) -> None:
        sock = tmp_path / "tcp-server.sock"
        server, _thread = _start_server(sock)
        try:
            info = endpoint.read_endpoint(sock)
            assert info is not None and info.port and info.token
            assert is_server_running(sock)

            status = send_request({"cmd": "status"}, sock, timeout_sec=5.0)
            assert status is not None and status["status"] == "status"
            assert status["transport"] == endpoint.TRANSPORT_TCP

            # Sem token: recusado.
            client, _token = endpoint.connect(sock, timeout=5.0)
            with client:
                client.sendall((json.dumps({"cmd": "status"}) + "\n").encode())
                raw = client.recv(8192).decode()
            denied = json.loads(raw.strip().split("\n")[-1])
            assert denied["status"] == "error"
            assert denied["error_code"] == "UNAUTHORIZED"
        finally:
            server._running = False
            server._cleanup()

    def test_shutdown_removes_descriptor(self, tmp_path: Path, tcp_mode) -> None:
        sock = tmp_path / "tcp-shutdown.sock"
        server, thread = _start_server(sock)
        try:
            assert send_request({"cmd": "shutdown"}, sock, timeout_sec=5.0) is not None
            thread.join(timeout=5.0)
        finally:
            server._running = False
            server._cleanup()
        assert not sock.exists()


def test_detached_kwargs_shape() -> None:
    kwargs = endpoint.detached_popen_kwargs()
    if endpoint.IS_WINDOWS:
        assert "creationflags" in kwargs
    else:
        assert kwargs.get("start_new_session") is True
