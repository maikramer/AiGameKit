"""Tests for aigamekit_shared.vramd_client — shared model server + VRAM coordination."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from aigamekit_shared.vramd_client import (
    ModelServer,
    _resolve_vramd_start_cmd,
    discover_active_sockets,
    discover_server_pids,
    ensure_vram_available,
    get_server_pid,
    get_server_status,
    is_server_running,
    request_release,
    send_request,
    server_socket_path,
    stop_server,
)

# ---------------------------------------------------------------------------
# vramd auto-start command resolution (precedência do venv canónico)
# ---------------------------------------------------------------------------


class TestResolveVramdStartCmd:
    """``_resolve_vramd_start_cmd`` deve priorizar o venv canónico do Vramd."""

    def test_vramd_bin_override_wins(self, tmp_path: Path) -> None:
        bin_path = tmp_path / "vramd-bin"
        bin_path.write_text("#!/bin/sh\n")
        cmd, warning = _resolve_vramd_start_cmd(
            vramd_bin=str(bin_path),
            canonical_python=Path("/fake/Vramd/.venv/bin/python"),
            path_lookup=lambda _: "/usr/bin/vramd",
            import_probe=lambda: True,
            sys_executable="/wrong/venv/python",
        )
        assert cmd == [str(bin_path), "start"]
        assert warning == ""

    def test_canonical_venv_beats_path_and_sys_executable(self) -> None:
        """Vramd/.venv canónico tem prioridade sobre PATH e venv actual."""
        cmd, warning = _resolve_vramd_start_cmd(
            canonical_python=Path("/repo/Vramd/.venv/bin/python"),
            path_lookup=lambda _: "/usr/bin/vramd",
            import_probe=lambda: True,
            sys_executable="/some/tool/.venv/bin/python",
        )
        assert cmd == ["/repo/Vramd/.venv/bin/python", "-m", "vramd", "start"]
        assert warning == ""

    def test_path_lookup_used_when_no_canonical(self) -> None:
        cmd, warning = _resolve_vramd_start_cmd(
            canonical_python=None,
            path_lookup=lambda name: "/usr/local/bin/vramd" if name == "vramd" else None,
            import_probe=lambda: True,
            sys_executable="/wrong/python",
        )
        assert cmd == ["/usr/local/bin/vramd", "start"]
        assert warning == ""

    def test_sys_executable_is_last_resort_with_warning(self) -> None:
        cmd, warning = _resolve_vramd_start_cmd(
            canonical_python=None,
            path_lookup=lambda _: None,
            import_probe=lambda: True,
            sys_executable="/tool-venv/bin/python",
        )
        assert cmd == ["/tool-venv/bin/python", "-m", "vramd", "start"]
        assert "INCORRECTO" in warning
        assert "install.sh vramd" in warning

    def test_returns_none_when_nothing_available(self) -> None:
        cmd, warning = _resolve_vramd_start_cmd(
            canonical_python=None,
            path_lookup=lambda _: None,
            import_probe=lambda: False,
            sys_executable="/python",
        )
        assert cmd is None
        assert warning == ""

    def test_vramd_bin_nonexistent_falls_through(self) -> None:
        cmd, _ = _resolve_vramd_start_cmd(
            vramd_bin="/nonexistent/vramd",
            canonical_python=Path("/repo/Vramd/.venv/bin/python"),
            path_lookup=lambda _: None,
            import_probe=lambda: False,
        )
        assert cmd == ["/repo/Vramd/.venv/bin/python", "-m", "vramd", "start"]


# ---------------------------------------------------------------------------
# Socket path resolution
# ---------------------------------------------------------------------------


class TestSocketPath:
    def test_text2icon_socket_path(self) -> None:
        path = server_socket_path("text2icon")
        assert path.name == "text2icon-server.sock"
        assert "aigamekit" in str(path)

    def test_text2d_socket_path(self) -> None:
        path = server_socket_path("text2d")
        assert path.name == "text2d-server.sock"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_CLIENT_SOCKET", "/tmp/custom.sock")
        path = server_socket_path("text2icon")
        assert path == Path("/tmp/custom.sock")


# ---------------------------------------------------------------------------
# Liveness (sem server real)
# ---------------------------------------------------------------------------


class TestLiveness:
    def test_not_running_when_no_pid_file(self, tmp_path: Path) -> None:
        sock = tmp_path / "test.sock"
        assert not is_server_running(sock)

    def test_not_running_when_pid_dead(self, tmp_path: Path) -> None:
        sock = tmp_path / "test.sock"
        pid_file = sock.with_suffix(".pid")
        pid_file.write_text("999999")  # PID que não existe
        assert not is_server_running(sock)

    def test_get_server_pid_none_when_no_file(self, tmp_path: Path) -> None:
        sock = tmp_path / "test.sock"
        assert get_server_pid(sock) is None

    def test_discover_empty_when_no_dir(self, tmp_path: Path) -> None:
        with patch("aigamekit_shared.vramd_client.DEFAULT_SERVER_DIR", tmp_path / "nonexistent"):
            assert discover_server_pids() == set()
            assert discover_active_sockets() == []


# ---------------------------------------------------------------------------
# Client: send_request retorna None quando server down
# ---------------------------------------------------------------------------


class TestClient:
    def test_send_request_none_when_no_socket(self, tmp_path: Path) -> None:
        result = send_request({"cmd": "status"}, tmp_path / "missing.sock", timeout_sec=1.0)
        assert result is None

    def test_request_release_false_when_no_socket(self, tmp_path: Path) -> None:
        assert request_release(tmp_path / "missing.sock") is False

    def test_stop_server_false_when_no_socket(self, tmp_path: Path) -> None:
        assert stop_server(tmp_path / "missing.sock") is False

    def test_get_status_none_when_no_socket(self, tmp_path: Path) -> None:
        assert get_server_status(tmp_path / "missing.sock") is None


# ---------------------------------------------------------------------------
# ModelServer com loader/generator mock (server real num socket temporário)
# ---------------------------------------------------------------------------


def _mock_loader():
    """Loader que devolve um objeto mock com warmup/generate/unload."""

    class _MockGen:
        def __init__(self) -> None:
            self.loaded = True
            self.unloaded = False

        def warmup(self) -> None:
            pass

        def generate(self, **kwargs):
            return None, {"seed": kwargs.get("seed", 0)}

        def unload(self) -> None:
            self.loaded = False
            self.unloaded = True

    return _MockGen()


def _mock_generator(gen, request):
    return {"status": "ok", "output": "/tmp/mock.png", "seconds": 0.1, "seed": 42}


class TestModelServer:
    @pytest.fixture()
    def running_server(self, tmp_path: Path):
        """Arranca um ModelServer mock num thread e devolve o socket path."""
        sock_path = tmp_path / "test-server.sock"
        server = ModelServer(
            socket_path=sock_path,
            loader=_mock_loader,
            generator=_mock_generator,
            idle_timeout_min=60,
            verbose=False,
            tool_name="test",
        )
        # Override DEFAULT_SERVER_DIR para que discover_* encontre este server
        with patch("aigamekit_shared.vramd_client.DEFAULT_SERVER_DIR", tmp_path):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            # Esperar que o socket fique disponível
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if sock_path.exists():
                    break
                time.sleep(0.05)

            yield sock_path

            # Cleanup
            server._running = False
            with patch("aigamekit_shared.vramd_client.DEFAULT_SERVER_DIR", tmp_path):
                server._cleanup()

    def test_is_running_after_start(self, running_server: Path) -> None:
        assert is_server_running(running_server)

    def test_get_pid(self, running_server: Path) -> None:
        pid = get_server_pid(running_server)
        assert pid is not None
        assert pid > 0

    def test_status_command(self, running_server: Path) -> None:
        status = get_server_status(running_server)
        assert status is not None
        assert status["status"] == "status"
        assert status["tool"] == "test"
        assert "pid" in status

    def test_generate_command(self, running_server: Path) -> None:
        request = {"cmd": "generate", "prompt": "test", "output": "/tmp/x.png"}
        result = send_request(request, running_server, timeout_sec=5.0)
        assert result is not None
        assert result["status"] == "ok"
        assert result["output"] == "/tmp/mock.png"

    def test_release_command(self, running_server: Path) -> None:
        ok = request_release(running_server)
        assert ok is True
        # Após release, model_loaded deve ser False
        status = get_server_status(running_server)
        assert status is not None
        assert status["model_loaded"] is False

    def test_shutdown_command(self, running_server: Path) -> None:
        ok = stop_server(running_server)
        assert ok is True
        # Esperar que o processo termine completamente (socket + PID file removidos)
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if not running_server.exists():
                break
            time.sleep(0.1)
        assert not is_server_running(running_server)

    def test_discover_finds_server(self, running_server: Path) -> None:
        pids = discover_server_pids()
        assert len(pids) >= 1

    def test_discover_finds_socket(self, running_server: Path) -> None:
        sockets = discover_active_sockets()
        assert running_server in sockets


# ---------------------------------------------------------------------------
# ensure_vram_available (com query_gpu_free_mib mockado)
# ---------------------------------------------------------------------------


class TestEnsureVram:
    def test_returns_true_when_enough_vram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("aigamekit_shared.gpu.query_gpu_free_mib", lambda: 8000)
        assert ensure_vram_available(5000) is True

    def test_requests_release_when_low_vram(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        # Path legacy é opt-in (vramd é a autoridade por defeito).
        monkeypatch.setenv("VRAMD_ALLOW_LEGACY_SERVER", "1")
        # Hermético: não falar com um vramd real a correr nesta máquina.
        monkeypatch.setattr("aigamekit_shared.vramd_client.is_vramd_running", lambda: False)
        # Simular VRAM baixa
        call_count = {"release": 0}

        def _mock_query_free():
            # Depois do release, simular que há VRAM
            if call_count["release"] > 0:
                return 8000
            return 1000

        def _mock_request_release(sock):
            call_count["release"] += 1
            return True

        monkeypatch.setattr("aigamekit_shared.gpu.query_gpu_free_mib", _mock_query_free)
        monkeypatch.setattr("aigamekit_shared.vramd_client.request_release", _mock_request_release)
        monkeypatch.setattr("aigamekit_shared.vramd_client.discover_active_sockets", lambda: [tmp_path / "fake.sock"])

        result = ensure_vram_available(5000, timeout_sec=5.0)
        assert result is True
        assert call_count["release"] >= 1

    def test_returns_true_when_cannot_check(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("aigamekit_shared.gpu.query_gpu_free_mib", lambda: None)
        # Hermético: sem vramd real nesta máquina (senão o ensure-vram ia ao daemon).
        monkeypatch.setattr("aigamekit_shared.vramd_client.is_vramd_running", lambda: False)
        monkeypatch.setattr("aigamekit_shared.vramd_client.discover_active_sockets", lambda: [])
        assert ensure_vram_available(5000) is True
