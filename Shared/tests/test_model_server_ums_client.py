"""Testes das APIs cliente UMS em gamedev_shared.model_server."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from gamedev_shared import model_server as ms


class TestResolveUmsPriority:
    def test_explicit(self) -> None:
        assert ms.resolve_ums_priority("batch") == "batch"
        assert ms.resolve_ums_priority("INTERACTIVE") == "interactive"

    def test_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_PRIORITY", "batch")
        assert ms.resolve_ums_priority(None) == "batch"

    def test_invalid_env_defaults_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_PRIORITY", "turbo")
        assert ms.resolve_ums_priority(None) == "interactive"

    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GAMEDEV_UMS_PRIORITY", raising=False)
        assert ms.resolve_ums_priority(None) == "interactive"


class TestUmsHoldingHelpers:
    def test_ums_is_busy_false_on_empty(self) -> None:
        assert ms.ums_is_busy({"inflight": 0, "queue_depth": 0, "running": [], "queued": []}) is False

    def test_ums_is_busy_true_on_inflight(self) -> None:
        assert ms.ums_is_busy({"inflight": 1, "queue_depth": 0, "running": [{}], "queued": []}) is True

    def test_format_holding_summary(self) -> None:
        snap = {
            "inflight": 1,
            "queue_depth": 2,
            "running": [{"job_id": "abcdefghijklmnop", "backend": "skymap2d", "progress_pct": 0.5}],
            "queued": [{}, {}],
        }
        line = ms.format_ums_holding_summary(snap)
        assert "HOLDING:" in line
        assert "skymap2d" in line
        assert "QUEUE: 2 waiting" in line
        assert "abcdefghijkl" in line  # truncated id prefix

    def test_do_not_kill_tip_mentions_queue(self) -> None:
        assert "queue" in ms.UMS_DO_NOT_KILL_TIP.lower() or "queue" in ms.UMS_DO_NOT_KILL_TIP


class TestUmsClientDown:
    def test_submit_none_when_ums_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_AUTO_START", "0")
        with patch.object(ms, "ensure_ums_running", return_value=False):
            assert ms.submit_to_ums("text2icon", {"prompt": "x"}) is None

    def test_poll_none_when_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(ms, "ensure_ums_running", return_value=False):
            assert ms.poll_ums_job("abc") is None

    def test_cancel_none_when_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(ms, "ensure_ums_running", return_value=False):
            assert ms.cancel_ums_job("abc") is None


class TestUmsClientAgainstMockSocket:
    """Sobe um mini-socket que fala o protocolo UMS mínimo."""

    def test_delegate_and_submit_roundtrip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sock_path = tmp_path / "ums.sock"
        pid_path = sock_path.with_suffix(".pid")
        pid_path.write_text(str(__import__("os").getpid()))

        received: list[dict] = []

        def _serve() -> None:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            srv.listen(16)
            srv.settimeout(5.0)
            # is_server_running faz connect+close sem enviar — ignorar ligações vazias.
            for _ in range(16):
                try:
                    conn, _ = srv.accept()
                except TimeoutError:
                    break
                with conn:
                    conn.settimeout(1.0)
                    data = b""
                    try:
                        while b"\n" not in data:
                            chunk = conn.recv(4096)
                            if not chunk:
                                break
                            data += chunk
                    except TimeoutError:
                        continue
                    if not data.strip():
                        continue
                    req = json.loads(data.decode().strip())
                    received.append(req)
                    cmd = req.get("cmd")
                    if cmd == "submit":
                        resp = {"status": "ok", "job_id": "jid-1", "backend": req.get("backend")}
                    elif cmd == "generate":
                        resp = {"status": "ok", "output": "/tmp/out.png", "job_id": "jid-g"}
                    elif cmd == "poll":
                        resp = {"status": "ok", "job_id": req.get("job_id"), "state": "done"}
                    elif cmd == "cancel":
                        resp = {"status": "ok", "job_id": req.get("job_id"), "state": "cancelled"}
                    else:
                        resp = {"status": "ok"}
                    conn.sendall((json.dumps(resp) + "\n").encode())
            srv.close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while not sock_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        monkeypatch.setattr(ms, "UMS_SOCKET", sock_path)
        monkeypatch.setattr(ms, "ensure_ums_running", lambda **_k: True)
        monkeypatch.setattr(ms, "is_ums_running", lambda: True)

        sub = ms.submit_to_ums("text2icon", {"prompt": "hi"}, priority="batch")
        assert sub is not None
        assert sub["job_id"] == "jid-1"
        assert any(r.get("cmd") == "submit" and r.get("priority") == "batch" for r in received)

        gen = ms.delegate_to_ums("text2icon", {"prompt": "hi", "output": "/tmp/x.png"}, priority="interactive")
        assert gen is not None
        assert gen["status"] == "ok"

        poll = ms.poll_ums_job("jid-1")
        assert poll is not None
        assert poll["state"] == "done"

        cancel = ms.cancel_ums_job("jid-1")
        assert cancel is not None
        assert cancel["state"] == "cancelled"

        t.join(timeout=3.0)

    def test_send_request_stream_yields_lines(self, tmp_path: Path) -> None:
        sock_path = tmp_path / "stream.sock"

        def _serve() -> None:
            srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            srv.bind(str(sock_path))
            srv.listen(1)
            conn, _ = srv.accept()
            with conn:
                while b"\n" not in conn.recv(4096):
                    pass
                conn.sendall(b'{"event":"queued","job_id":"1"}\n')
                conn.sendall(b'{"event":"started","job_id":"1"}\n')
                conn.sendall(b'{"status":"ok","output":"/tmp/x.png"}\n')
            srv.close()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        deadline = time.monotonic() + 2.0
        while not sock_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)

        lines = list(ms.send_request_stream({"cmd": "generate", "stream": True}, sock_path, timeout_sec=5.0))
        assert len(lines) == 3
        assert lines[0]["event"] == "queued"
        assert lines[-1]["status"] == "ok"
        t.join(timeout=3.0)
