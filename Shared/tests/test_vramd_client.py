"""Testes das APIs cliente vramd em aigamekit_shared.vramd_client."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from aigamekit_shared import vramd_client as ms


class TestResolveUmsPriority:
    def test_explicit(self) -> None:
        assert ms.resolve_vramd_priority("batch") == "batch"
        assert ms.resolve_vramd_priority("INTERACTIVE") == "interactive"

    def test_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_PRIORITY", "batch")
        assert ms.resolve_vramd_priority(None) == "batch"

    def test_invalid_env_defaults_interactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_PRIORITY", "turbo")
        assert ms.resolve_vramd_priority(None) == "interactive"

    def test_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_PRIORITY", raising=False)
        assert ms.resolve_vramd_priority(None) == "interactive"


class TestUmsHoldingHelpers:
    def test_vramd_is_busy_false_on_empty(self) -> None:
        assert ms.vramd_is_busy({"inflight": 0, "queue_depth": 0, "running": [], "queued": []}) is False

    def test_vramd_is_busy_true_on_inflight(self) -> None:
        assert ms.vramd_is_busy({"inflight": 1, "queue_depth": 0, "running": [{}], "queued": []}) is True

    def test_format_holding_summary(self) -> None:
        snap = {
            "inflight": 1,
            "queue_depth": 2,
            "running": [{"job_id": "abcdefghijklmnop", "backend": "skymap2d", "progress_pct": 0.5}],
            "queued": [{}, {}],
        }
        line = ms.format_vramd_holding_summary(snap)
        assert "HOLDING:" in line
        assert "skymap2d" in line
        assert "QUEUE: 2 waiting" in line
        assert "abcdefghijkl" in line  # truncated id prefix

    def test_do_not_kill_tip_mentions_queue(self) -> None:
        assert "queue" in ms.VRAMD_DO_NOT_KILL_TIP.lower() or "queue" in ms.VRAMD_DO_NOT_KILL_TIP


class TestUmsClientDown:
    def test_submit_none_when_ums_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_AUTO_START", "0")
        with patch.object(ms, "ensure_vramd_running", return_value=False):
            assert ms.submit_to_vramd("text2icon", {"prompt": "x"}) is None

    def test_poll_none_when_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(ms, "ensure_vramd_running", return_value=False) as ens:
            assert ms.poll_vramd_job("abc") is None
            ens.assert_called_once()
            assert ens.call_args.kwargs.get("auto_start") is False

    def test_cancel_none_when_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with patch.object(ms, "ensure_vramd_running", return_value=False) as ens:
            assert ms.cancel_vramd_job("abc") is None
            assert ens.call_args.kwargs.get("auto_start") is False

    def test_poll_does_not_auto_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """poll/wait/cancel nunca arrancam vramd vazio (perderiam job_id)."""
        calls: list[bool] = []

        def _ens(*, auto_start: bool = True, **_k: object) -> bool:
            calls.append(auto_start)
            return False

        monkeypatch.setattr(ms, "ensure_vramd_running", _ens)
        assert ms.poll_vramd_job("abc") is None
        assert ms.wait_vramd_job("abc") is None
        assert ms.cancel_vramd_job("abc") is None
        assert calls == [False, False, False]


class TestZeroUmsVram:
    def test_sends_zero_cmd_without_auto_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[dict] = []

        def _send(req: dict, *, timeout_sec: float, auto_start: bool) -> dict:
            seen.append({"req": req, "timeout_sec": timeout_sec, "auto_start": auto_start})
            return {"status": "ok", "workers_killed": 2, "free_mib_before": 1000, "free_mib_after": 3400}

        monkeypatch.setattr(ms, "send_to_vramd", _send)
        resp = ms.zero_vramd_vram()
        assert resp is not None
        assert resp["workers_killed"] == 2
        assert seen == [
            {"req": {"cmd": "zero"}, "timeout_sec": 120.0, "auto_start": False},
        ]

    def test_none_when_ums_down(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "send_to_vramd", lambda *_a, **_k: None)
        assert ms.zero_vramd_vram() is None


class TestUmsClientAgainstMockSocket:
    """Sobe um mini-socket que fala o protocolo vramd mínimo."""

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

        monkeypatch.setattr(ms, "VRAMD_SOCKET", sock_path)
        monkeypatch.setattr(ms, "ensure_vramd_running", lambda **_k: True)
        monkeypatch.setattr(ms, "is_vramd_running", lambda: True)

        sub = ms.submit_to_vramd("text2icon", {"prompt": "hi"}, priority="batch")
        assert sub is not None
        assert sub["job_id"] == "jid-1"
        assert any(r.get("cmd") == "submit" and r.get("priority") == "batch" for r in received)

        gen = ms.delegate_to_vramd("text2icon", {"prompt": "hi", "output": "/tmp/x.png"}, priority="interactive")
        assert gen is not None
        assert gen["status"] == "ok"

        poll = ms.poll_vramd_job("jid-1")
        assert poll is not None
        assert poll["state"] == "done"

        cancel = ms.cancel_vramd_job("jid-1")
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


class TestResolveVramdCalibration:
    """Calibração atrelada à VRAM: escolhe o ficheiro certo para o hardware."""

    def _make_catalog(self, tmp_path: Path, gb_labels: list[int]) -> Path:
        cal_dir = tmp_path / "calibrated"
        cal_dir.mkdir()
        for gb in gb_labels:
            (cal_dir / f"backends-{gb}g.yaml").write_text("backends: []\n", encoding="utf-8")
        return cal_dir

    def test_uses_calibration_for_own_vram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6]))
        assert ms.resolve_vramd_calibration(vram_total_mib=6 * 1024).name == "backends-6g.yaml"

    def test_largest_calibration_not_exceeding_vram(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6, 16]))
        # 10 GB → 6 GB (o maior que não excede); 24 GB → 16 GB (o maior disponível).
        assert ms.resolve_vramd_calibration(vram_total_mib=10 * 1024).name == "backends-6g.yaml"
        assert ms.resolve_vramd_calibration(vram_total_mib=24 * 1024).name == "backends-16g.yaml"

    def test_more_vram_than_largest_uses_largest_for_safety(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """24 GB com só 16 GB calibrado → usa o de 16 GB (cenário mais restritivo)."""
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6, 16]))
        assert ms.resolve_vramd_calibration(vram_total_mib=24 * 1024).name == "backends-16g.yaml"

    def test_no_calibration_for_smaller_gpu_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """GPU mais pequena que todas as calibrações → None (hw-auto)."""
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6, 16]))
        assert ms.resolve_vramd_calibration(vram_total_mib=4 * 1024) is None

    def test_no_catalog_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", tmp_path / "inexistente")
        assert ms.resolve_vramd_calibration(vram_total_mib=6 * 1024) is None

    def test_vram_read_from_nvml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6, 16]))
        with patch("aigamekit_shared.gpu.gpu_total_mib", return_value=6 * 1024):
            assert ms.resolve_vramd_calibration().name == "backends-6g.yaml"

    def test_nominal_label_matches_driver_vram_variants(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A etiqueta é a classe NOMINAL (6 GB): NVML total (6141 MiB) e o
        utilizável de uma RTX 4050 (~5772 MiB) continuam a casar com 6g."""
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6]))
        assert ms.resolve_vramd_calibration(vram_total_mib=6141).name == "backends-6g.yaml"
        assert ms.resolve_vramd_calibration(vram_total_mib=5772).name == "backends-6g.yaml"
        # GPU realmente mais pequena continua sem calibração (hw-auto).
        assert ms.resolve_vramd_calibration(vram_total_mib=4096) is None

    def test_unreadable_vram_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._make_catalog(tmp_path, [6]))
        with patch("aigamekit_shared.gpu.gpu_total_mib", return_value=None):
            assert ms.resolve_vramd_calibration() is None


class TestCalibratedPeakSignals:
    """hw-auto acertado: os sinais de pico vêm da calibração selecionada."""

    def _catalog(self, tmp_path: Path, *, backend: str = "paint3d", preset: str = "sdnq-uint8") -> Path:
        cal_dir = tmp_path / "calibrated"
        cal_dir.mkdir()
        (cal_dir / "backends-6g.yaml").write_text(
            "version: 2\nbackends:\n"
            f"- name: {backend}\n"
            f"  peak_profile:\n    quant_mode: {preset}\n    load_kwargs:\n"
            "      memory_efficient: true\n",
            encoding="utf-8",
        )
        return cal_dir

    def test_signals_from_calibration(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path))
        signals = ms.calibrated_peak_signals("paint3d")
        assert signals == {"sdnq_preset": "sdnq-uint8", "memory_efficient": True}

    def test_quant_mode_none_means_no_preset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path, preset="none"))
        signals = ms.calibrated_peak_signals("paint3d")
        assert signals.get("sdnq_preset") is None

    def test_unknown_backend_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path))
        assert ms.calibrated_peak_signals("text3d") is None

    def test_no_calibration_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", tmp_path / "vazio")
        assert ms.calibrated_peak_signals("paint3d") is None
