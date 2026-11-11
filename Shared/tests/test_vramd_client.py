"""Testes das APIs cliente vramd em aigamekit_shared.vramd_client."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from aigamekit_shared import vramd_client as ms
from aigamekit_shared import vramd_endpoint as endpoint


class TestDiscoverVramdPython:
    def test_vramd_venv_override_posix_layout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        python = tmp_path / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        python.write_text("#!/bin/sh\n")
        monkeypatch.setenv("VRAMD_VENV", str(tmp_path / "venv"))
        assert ms._discover_vramd_python() == python

    def test_vramd_venv_override_windows_layout(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        python = tmp_path / "venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True)
        python.write_text("")
        monkeypatch.setenv("VRAMD_VENV", str(tmp_path / "venv"))
        assert ms._discover_vramd_python() == python


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
            assert ms.submit_to_vramd("text2d", {"prompt": "x"}) is None

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
            # Transporte do SO: Unix no POSIX, TCP+token no Windows.
            srv, _info = endpoint.listen(sock_path)
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

        sub = ms.submit_to_vramd("text2d", {"prompt": "hi"}, priority="batch")
        assert sub is not None
        assert sub["job_id"] == "jid-1"
        assert any(r.get("cmd") == "submit" and r.get("priority") == "batch" for r in received)

        gen = ms.delegate_to_vramd("text2d", {"prompt": "hi", "output": "/tmp/x.png"}, priority="interactive")
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
            srv, _info = endpoint.listen(sock_path)
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

    def _stub_vram(self, monkeypatch: pytest.MonkeyPatch, mib: int = 6144) -> None:
        """`resolve_vramd_calibration` também sonda a VRAM total (torch/NVML).

        Sem GPU — CI e venvs sem CUDA — o probe devolve ``None`` e nenhuma
        calibração é escolhida, mesmo com o catálogo apontado. Fixa 6 GB
        (classe nominal do ficheiro ``backends-6g.yaml`` dos testes).
        """
        from aigamekit_shared import gpu as gpu_mod

        monkeypatch.setattr(gpu_mod, "gpu_total_mib", lambda *a, **k: mib)

    def test_signals_from_calibration(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path))
        self._stub_vram(monkeypatch)
        signals = ms.calibrated_peak_signals("paint3d")
        assert signals == {"sdnq_preset": "sdnq-uint8", "memory_efficient": True}

    def test_quant_mode_none_means_no_preset(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path, preset="none"))
        self._stub_vram(monkeypatch)
        signals = ms.calibrated_peak_signals("paint3d")
        assert signals.get("sdnq_preset") is None

    def test_unknown_backend_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", self._catalog(tmp_path))
        self._stub_vram(monkeypatch)
        assert ms.calibrated_peak_signals("text3d") is None

    def test_no_calibration_returns_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "CALIBRATED_DIR", tmp_path / "vazio")
        assert ms.calibrated_peak_signals("paint3d") is None


class TestGpuTarget:
    def test_explicit_remote_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        response = ms.delegate_to_vramd("text3d", {"output": "/tmp/x.glb"}, remote=True)
        assert response is not None
        assert response["error_code"] == "VRAMD_REMOTE_NOT_CONFIGURED"

    def test_explicit_local_ignores_remote_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        with (
            patch.object(ms, "ensure_vramd_running", return_value=True),
            patch.object(ms, "send_request", return_value={"status": "ok"}) as send,
        ):
            response = ms.delegate_to_vramd("text3d", {"output": "/tmp/x.glb"}, remote=False)
        assert response == {"status": "ok"}
        assert send.call_args.kwargs["force_local"] is True

    def test_gpu_mode_env_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        monkeypatch.setenv("VRAMD_GPU_MODE", "local")
        with (
            patch.object(ms, "ensure_vramd_running", return_value=True),
            patch.object(ms, "send_request", return_value={"status": "ok"}) as send,
        ):
            ms.delegate_to_vramd("text3d", {"output": "/tmp/x.glb"})
        assert send.call_args.kwargs["force_local"] is True

    def test_submit_remote_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        response = ms.submit_to_vramd("text3d", {"output": "/tmp/x.glb"}, remote=True)
        assert response is not None
        assert response["error_code"] == "VRAMD_REMOTE_NOT_CONFIGURED"

    def test_wait_remote_without_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        response = ms.wait_vramd_job("j1", remote=True)
        assert response is not None
        assert response["error_code"] == "VRAMD_REMOTE_NOT_CONFIGURED"

    def test_wait_reconnects_after_idle_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """O servidor fecha a ligação ociosa entre submit e wait; o job está vivo."""
        from aigamekit_shared import vramd_remote

        class Flaky:
            def __init__(self) -> None:
                self.calls = 0

            def rpc(self, request, *, timeout=60.0):
                self.calls += 1
                if self.calls == 1:
                    raise vramd_remote.TransferError("servidor fechou a ligação antes do END")
                return {"status": "ok", "output": "/remote/out/x.glb"}

        flaky = Flaky()
        staged = vramd_remote.StagedJob(client=flaky, job="a" * 32, root="/remote", request={})
        reconnects: list[object] = []
        monkeypatch.setattr(vramd_remote, "remote_enabled", lambda: True)
        monkeypatch.setattr(vramd_remote, "peek", lambda _job: staged)
        monkeypatch.setattr(vramd_remote, "reconnect", lambda item: reconnects.append(item) or True)
        monkeypatch.setattr(vramd_remote, "take", lambda _job: None)
        monkeypatch.setattr(vramd_remote, "finalize", lambda _staged, response: response)

        response = ms.wait_vramd_job("a" * 32, timeout_sec=1.0, remote=True)

        assert response is not None and response["status"] == "ok"
        assert flaky.calls == 2
        assert reconnects == [staged]


def _serve_once(sock_path: Path, response: bytes) -> threading.Thread:
    """Mini-servidor de uma ligação: lê uma linha e responde ``response``."""

    def _run() -> None:
        srv, _info = endpoint.listen(sock_path)
        srv.settimeout(5.0)
        try:
            conn, _ = srv.accept()
            with conn:
                data = b""
                while b"\n" not in data:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                conn.sendall(response)
        finally:
            srv.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread


def _wait_for_path(path: Path) -> None:
    deadline = time.monotonic() + 2.0
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert path.exists()


class TestExplicitSocketStaysLocal:
    def test_send_request_ignores_remote_env_with_explicit_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``socket_path`` explícito = aquele endpoint, não o VRAMD_REMOTE."""
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        sock = tmp_path / "local.sock"
        _serve_once(sock, b'{"status":"status","local":true}\n')
        _wait_for_path(sock)

        result = ms.send_request({"cmd": "status"}, sock, timeout_sec=3.0)

        assert result == {"status": "status", "local": True}

    def test_stream_ignores_remote_env_with_explicit_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        sock = tmp_path / "local-stream.sock"
        _serve_once(sock, b'{"event":"queued"}\n{"status":"ok"}\n')
        _wait_for_path(sock)

        lines = list(ms.send_request_stream({"cmd": "submit"}, sock, timeout_sec=3.0))

        assert [line.get("event") for line in lines] == ["queued", None]


class TestResponseValidation:
    def test_non_dict_response_is_none(self, tmp_path: Path) -> None:
        sock = tmp_path / "list.sock"
        _serve_once(sock, b"[1, 2, 3]\n")
        _wait_for_path(sock)
        assert ms.send_request({"cmd": "status"}, sock, timeout_sec=3.0) is None

    def test_oversized_response_is_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "MAX_RESPONSE_BYTES", 32)
        sock = tmp_path / "big.sock"
        _serve_once(sock, b'{"status":"ok","x":"' + b"a" * 64 + b'"}\n')
        _wait_for_path(sock)
        assert ms.send_request({"cmd": "status"}, sock, timeout_sec=3.0) is None

    def test_stream_oversized_line_emits_protocol_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ms, "MAX_RESPONSE_BYTES", 16)
        sock = tmp_path / "big-line.sock"
        _serve_once(sock, b"x" * 64)  # sem newline: buffer não pode crescer
        _wait_for_path(sock)

        lines = list(ms.send_request_stream({"cmd": "generate"}, sock, timeout_sec=3.0))

        assert lines and lines[-1]["error_code"] == "VRAMD_PROTOCOL"

    def test_stream_yields_final_line_without_newline(self, tmp_path: Path) -> None:
        """Um servidor que fecha sem ``\\n`` não pode perder o resultado."""
        sock = tmp_path / "no-newline.sock"
        _serve_once(sock, b'{"status":"ok"}')
        _wait_for_path(sock)

        lines = list(ms.send_request_stream({"cmd": "status"}, sock, timeout_sec=3.0))

        assert lines == [{"status": "ok"}]


class TestAutoStartSerialization:
    def test_two_threads_spawn_one_supervisor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        monkeypatch.delenv("VRAMD_GPU_MODE", raising=False)
        monkeypatch.delenv("VRAMD_AUTO_START", raising=False)
        state = {"up": False}
        calls: list[float] = []

        def _running() -> bool:
            return state["up"]

        def _start(*, timeout_sec: float) -> bool:
            calls.append(timeout_sec)
            time.sleep(0.2)  # simula o arranque do supervisor
            state["up"] = True
            return True

        monkeypatch.setattr(ms, "is_vramd_running", _running)
        monkeypatch.setattr(ms, "_start_vramd_process", _start)
        results: list[bool] = []
        barrier = threading.Barrier(2)

        def _race() -> None:
            barrier.wait()
            results.append(ms.ensure_vramd_running())

        threads = [threading.Thread(target=_race) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5.0)

        assert results == [True, True]
        assert calls == [30.0]  # um único Popen

    def test_dead_child_aborts_wait_early(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class DeadProc:
            pid = 4242
            returncode = 1

            def poll(self) -> int:
                return 1

        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        monkeypatch.setattr(ms, "is_vramd_running", lambda: False)
        monkeypatch.setattr(ms, "_resolve_vramd_start_cmd", lambda **_k: (["/bin/vramd", "start"], ""))
        monkeypatch.setattr(ms, "resolve_vramd_calibration", lambda *a, **k: None)
        monkeypatch.setattr(endpoint, "autostart_log_path", lambda: tmp_path / "autostart.log")
        monkeypatch.setattr("subprocess.Popen", lambda *a, **k: DeadProc())

        started = time.monotonic()
        assert ms._start_vramd_process(timeout_sec=5.0) is False
        assert time.monotonic() - started < 3.0


class TestRemoteModeAdmission:
    def test_ensure_vram_available_skips_local_probe(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Em modo remoto a GPU local não decide a admissão do job."""
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        monkeypatch.delenv("VRAMD_GPU_MODE", raising=False)
        monkeypatch.setattr("aigamekit_shared.gpu.query_gpu_free_mib", lambda: 999_999)
        monkeypatch.setattr(ms, "is_vramd_running", lambda: True)
        calls: list[dict] = []

        def _send(req: dict, *, timeout_sec: float, auto_start: bool = True, force_local: bool = False) -> dict:
            calls.append({"req": req, "force_local": force_local})
            return {"status": "ok"}

        monkeypatch.setattr(ms, "send_to_vramd", _send)
        assert ms.ensure_vram_available(4000, backend="paint3d") is True
        assert calls and calls[0]["req"]["cmd"] == "ensure-vram"
        assert calls[0]["force_local"] is False

    def test_ensure_vram_available_uses_local_with_gpu_mode_local(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        monkeypatch.setenv("VRAMD_GPU_MODE", "local")
        monkeypatch.setattr("aigamekit_shared.gpu.query_gpu_free_mib", lambda: 999_999)
        monkeypatch.setattr(ms, "send_to_vramd", lambda *_a, **_k: pytest.fail("não devia consultar o remoto"))
        assert ms.ensure_vram_available(4000, backend="paint3d") is True

    def test_cancel_remote_without_env_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        monkeypatch.delenv("VRAMD_GPU_MODE", raising=False)
        response = ms.cancel_vramd_job("abc", remote=True)
        assert response is not None and response["error_code"] == "VRAMD_REMOTE_NOT_CONFIGURED"

    def test_cancel_all_remote_mode_without_env_is_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("VRAMD_REMOTE", raising=False)
        monkeypatch.setenv("VRAMD_GPU_MODE", "remote")
        response = ms.cancel_vramd_all()
        assert response is not None and response["error_code"] == "VRAMD_REMOTE_NOT_CONFIGURED"


class TestCanonicalSocketFollowsRemote:
    def test_send_request_uses_remote_for_vramd_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """O socket canónico (VRAMD_SOCKET) segue VRAMD_REMOTE — só os
        endpoints per-tool passados explicitamente ficam locais."""
        from aigamekit_shared import vramd_remote

        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")
        seen: list[dict] = []

        class FakeRemote:
            def rpc(self, request, *, timeout: float = 60.0) -> dict:
                seen.append(request)
                return {"status": "ok", "remote": True}

        monkeypatch.setattr(vramd_remote, "RemoteClient", FakeRemote)

        result = ms.send_request({"cmd": "status"}, ms.VRAMD_SOCKET, timeout_sec=2.0)

        assert result == {"status": "ok", "remote": True}
        assert seen == [{"cmd": "status"}]

    def test_stream_uses_remote_for_vramd_socket(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from aigamekit_shared import vramd_remote

        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")

        class FakeRemote:
            def rpc(self, request, *, timeout: float = 60.0) -> dict:
                return {"status": "ok", "remote": True}

        monkeypatch.setattr(vramd_remote, "RemoteClient", FakeRemote)

        events = list(ms.send_request_stream({"cmd": "generate"}, ms.VRAMD_SOCKET, timeout_sec=2.0))
        assert events == [{"status": "ok", "remote": True}]

    def test_send_to_vramd_uses_remote_when_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``send_to_vramd`` passa VRAMD_SOCKET explícito; não pode cair no local."""
        from aigamekit_shared import vramd_remote

        monkeypatch.setenv("VRAMD_REMOTE", "127.0.0.1:9")

        class FakeRemote:
            def is_up(self, *, timeout: float = 3.0) -> bool:
                return True

            def rpc(self, request, *, timeout: float = 60.0) -> dict:
                return {"status": "ok", "remote": True, "cmd": request["cmd"]}

        monkeypatch.setattr(vramd_remote, "RemoteClient", FakeRemote)

        response = ms.send_to_vramd({"cmd": "queue"}, auto_start=False)

        assert response == {"status": "ok", "remote": True, "cmd": "queue"}


class _BrokenSock:
    """Socket cujo ``sendall`` rebenta (servidor fechou a meio do stream)."""

    def __enter__(self) -> _BrokenSock:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False

    def sendall(self, _data: bytes) -> None:
        raise ConnectionResetError("reset")

    def close(self) -> None:
        pass


def test_stream_ends_quietly_on_socket_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um reset a meio do NDJSON não pode rebentar o gerador/CLI."""
    monkeypatch.delenv("VRAMD_REMOTE", raising=False)
    sock = tmp_path / "broken.sock"
    sock.write_text("stale", encoding="utf-8")  # só para passar o exists()
    monkeypatch.setattr(endpoint, "connect", lambda *_a, **_k: (_BrokenSock(), None))

    assert list(ms.send_request_stream({"cmd": "generate"}, sock, timeout_sec=1.0)) == []


class TestRemoteCleanupOnError:
    @staticmethod
    def _staged(rpc_result: dict | Exception):
        from aigamekit_shared import vramd_remote

        closed: list[bool] = []

        class Client:
            def rpc(self, _request, *, timeout: float = 60.0) -> dict:
                if isinstance(rpc_result, Exception):
                    raise rpc_result
                return rpc_result

            def close(self) -> None:
                closed.append(True)

            def clean(self, _job: str) -> None:
                raise AssertionError("não devia limpar o sandbox num erro incerto")

        staged = vramd_remote.StagedJob(client=Client(), job="a" * 32, root="/srv/remote/a", request={})
        return staged, closed

    def test_submit_refused_closes_staging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from aigamekit_shared import vramd_remote

        staged, closed = self._staged({"status": "error", "error": "fila cheia"})
        monkeypatch.setattr(vramd_remote, "remote_enabled", lambda: True)
        monkeypatch.setattr(vramd_remote, "stage_request", lambda *_a, **_k: staged)

        response = ms.submit_to_vramd("text3d", {"output": "/tmp/x.glb"}, remote=True)

        assert response == {"status": "error", "error": "fila cheia"}
        assert closed == [True]  # sem job_id não há wait que a feche

    def test_delegate_rpc_down_closes_staging(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from aigamekit_shared import vramd_remote

        staged, closed = self._staged(vramd_remote.RemoteDown("caiu"))
        monkeypatch.setattr(vramd_remote, "remote_enabled", lambda: True)
        monkeypatch.setattr(vramd_remote, "stage_request", lambda *_a, **_k: staged)

        response = ms.delegate_to_vramd("text3d", {"output": "/tmp/x.glb"}, remote=True)

        assert response is not None and response["error_code"] == "VRAMD_REMOTE_DOWN"
        assert closed == [True]
