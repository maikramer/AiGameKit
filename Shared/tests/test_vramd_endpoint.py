"""Endpoint do vramd no cliente — Unix no POSIX, TCP+token no Windows.

O caminho TCP é forçado em Linux com ``VRAMD_TRANSPORT=tcp``: é exatamente o
que corre no Windows, por isso o CI Linux cobre a lógica sem esperar por um
runner Windows.
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
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

    def test_read_bad_version_is_rejected(self, tmp_path: Path) -> None:
        path = tmp_path / "x.sock"
        path.write_text(
            json.dumps({"version": "1", "transport": "tcp", "port": 8790}),
            encoding="utf-8",
        )
        assert endpoint.read_endpoint(path) is None
        path.write_text(
            json.dumps({"version": True, "transport": "tcp", "port": 8790}),
            encoding="utf-8",
        )
        assert endpoint.read_endpoint(path) is None

    def test_read_future_version_is_forward_compatible(self, tmp_path: Path) -> None:
        path = tmp_path / "x.sock"
        path.write_text(
            json.dumps({"version": 2, "transport": "tcp", "host": "127.0.0.1", "port": 8790}),
            encoding="utf-8",
        )
        info = endpoint.read_endpoint(path)
        assert info is not None and info.version == 2 and info.port == 8790

    def test_read_oversized_descriptor_is_rejected(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(endpoint, "MAX_DESCRIPTOR_BYTES", 8)
        path = tmp_path / "x.sock"
        path.write_text(
            json.dumps({"transport": "tcp", "port": 8790}),
            encoding="utf-8",
        )
        assert endpoint.read_endpoint(path) is None

    def test_empty_token_is_treated_as_none(self, tmp_path: Path) -> None:
        path = tmp_path / "x.sock"
        path.write_text(
            json.dumps({"transport": "tcp", "port": 8790, "token": ""}),
            encoding="utf-8",
        )
        info = endpoint.read_endpoint(path)
        assert info is not None and info.token is None
        assert endpoint.with_auth({"cmd": "status"}, info.token) == {"cmd": "status"}

    @pytest.mark.skipif(endpoint.IS_WINDOWS, reason="permissões POSIX")
    def test_descriptor_is_owner_only(self, tmp_path: Path) -> None:
        path = tmp_path / "d.sock"
        endpoint._write_descriptor(path, port=8790, token="segredo")
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

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


class TestParseHostPort:
    def test_ipv6_bracketed(self) -> None:
        assert endpoint.parse_host_port("[::1]:8790") == ("::1", 8790)

    def test_ipv6_without_colon_is_rejected(self) -> None:
        # ``[::1]8790`` era aceite pelo lstrip(":") antigo.
        with pytest.raises(ValueError, match="HOST"):
            endpoint.parse_host_port("[::1]8790")

    def test_port_range(self) -> None:
        with pytest.raises(ValueError, match="porta"):
            endpoint.parse_host_port("127.0.0.1:0")
        with pytest.raises(ValueError, match="porta"):
            endpoint.parse_host_port("127.0.0.1:70000")

    def test_remote_endpoints_skips_invalid_and_keeps_valid(self, monkeypatch) -> None:
        monkeypatch.setenv("VRAMD_REMOTE", "bad,127.0.0.1:8790,[::1]:8791")
        monkeypatch.setenv("VRAMD_REMOTE_TOKEN", "tok")
        infos = endpoint.remote_endpoints()
        assert [(i.host, i.port, i.token) for i in infos] == [
            ("127.0.0.1", 8790, "tok"),
            ("::1", 8791, "tok"),
        ]


class TestTcpFamily:
    @staticmethod
    def _fake(monkeypatch):
        calls: list[tuple[tuple[str, int], float | None]] = []

        class FakeSock:
            def setsockopt(self, *_args) -> None:
                pass

            def close(self) -> None:
                pass

        def _create(address, timeout=None):
            calls.append((address, timeout))
            return FakeSock()

        monkeypatch.setattr(endpoint.socket, "create_connection", _create)
        return calls

    def test_connect_remote_accepts_ipv6(self, monkeypatch) -> None:
        calls = self._fake(monkeypatch)
        sock, token = endpoint.connect_remote(
            endpoint.EndpointInfo(transport="tcp", host="::1", port=8791, token="tok"),
            timeout=2.0,
        )
        assert isinstance(sock, object)
        assert calls == [(("::1", 8791), 2.0)]
        assert token == "tok"

    def test_connect_descriptor_with_ipv6_host(self, tmp_path: Path, monkeypatch) -> None:
        calls = self._fake(monkeypatch)
        path = tmp_path / "d.sock"
        path.write_text(
            json.dumps({"transport": "tcp", "host": "::1", "port": 8790}),
            encoding="utf-8",
        )
        _sock, token = endpoint.connect(path, timeout=1.5)
        assert calls == [(("::1", 8790), 1.5)]
        assert token is None


class TestPidExists:
    @pytest.mark.skipif(endpoint.IS_WINDOWS, reason="semântica POSIX de os.kill")
    def test_permission_error_means_alive(self, monkeypatch) -> None:
        # Simular um psutil ausente para exercitar o fallback os.kill.
        monkeypatch.setitem(sys.modules, "psutil", None)

        def _deny(_pid: int, _sig: int) -> None:
            raise PermissionError("processo de outro utilizador")

        monkeypatch.setattr(os, "kill", _deny)
        assert endpoint.pid_exists(1234) is True


class TestUnixSocketPermissions:
    @pytest.mark.skipif(endpoint.IS_WINDOWS, reason="AF_UNIX/POSIX")
    def test_socket_is_owner_only(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delenv(endpoint.ENV_TRANSPORT, raising=False)
        path = tmp_path / "per-tool.sock"
        sock, info = endpoint.listen(path)
        try:
            assert info.transport == endpoint.TRANSPORT_UNIX
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
        finally:
            sock.close()


class TestTcpListenerOptions:
    @pytest.mark.skipif(endpoint.IS_WINDOWS, reason="SO_REUSEADDR é opção POSIX")
    def test_reuseaddr_enabled(self, tmp_path: Path, tcp_mode) -> None:
        path = tmp_path / "tcp.sock"
        sock, _info = endpoint.listen(path)
        try:
            assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
        finally:
            sock.close()
            endpoint.remove_endpoint(path)


def test_is_server_running_tcp_without_pidfile(tmp_path: Path, tcp_mode) -> None:
    """O descritor TCP (host/porta/token) é suficiente para liveness."""
    sock = tmp_path / "tcp-nopid.sock"
    server, _thread = _start_server(sock)
    try:
        sock.with_suffix(".pid").unlink()
        assert is_server_running(sock) is True
    finally:
        server._running = False
        server._cleanup()
