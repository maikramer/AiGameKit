"""Worker JSONL e paridade de protocolo com a Shared (venv 3.13)."""

from __future__ import annotations

import ast
import io
import json
import threading
from pathlib import Path
from typing import Any

import pytest
from hi3d import ums

REPO_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PY = REPO_ROOT / "Shared/src/aigamekit_shared/worker_protocol.py"


class _Recorder:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def emit(self, event: str, **fields: Any) -> None:
        self.events.append({"event": event, **fields})


class _OkAdapter:
    name = "hi3d"

    def __init__(self) -> None:
        self.loaded: list[dict[str, Any]] = []
        self.unloaded = 0

    def load(self, **kwargs: Any) -> dict[str, Any]:
        self.loaded.append(kwargs)
        return {"model": "fake"}

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        progress = request.get("_progress")
        if progress is not None:
            progress(0.5, "meio")
        return {"output": request.get("output", "")}

    def unload(self, model: Any) -> None:
        self.unloaded += 1

    def vram_mib(self) -> int:
        return 123


class _LoadFailAdapter(_OkAdapter):
    def load(self, **kwargs: Any) -> Any:
        raise RuntimeError("sem VRAM")


class _GenerateFailAdapter(_OkAdapter):
    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        raise ValueError("mesh inválido")


def _install_fake_reader(monkeypatch: pytest.MonkeyPatch, cmds: list[dict[str, Any] | None]) -> None:
    def _start(cmd_q: Any, _abort: dict[str, bool]) -> None:
        for cmd in cmds:
            cmd_q.put(cmd)

    monkeypatch.setattr(ums, "_start_reader", _start)


def _run(monkeypatch: pytest.MonkeyPatch, adapter: Any, cmds: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    recorder = _Recorder()
    monkeypatch.setattr(ums, "_install_jsonl_stdout", lambda: recorder)
    _install_fake_reader(monkeypatch, [*cmds, None])
    rc = ums.run_worker(adapter, backend_name="hi3d")
    assert rc in (0, 1)
    return recorder.events


class TestProtocolDrift:
    """As constantes locais têm de bater com o contrato canónico da Shared."""

    @pytest.mark.skipif(not PROTOCOL_PY.is_file(), reason="monorepo Shared ausente")
    def test_constants_match_shared(self) -> None:
        tree = ast.parse(PROTOCOL_PY.read_text(encoding="utf-8"))
        shared: dict[str, str] = {}
        for node in tree.body:
            if (
                isinstance(node, ast.Assign)
                and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            ):
                shared[node.targets[0].id] = node.value.value
        pairs = {
            "CMD_LOAD": ums.CMD_LOAD,
            "CMD_GENERATE": ums.CMD_GENERATE,
            "CMD_UNLOAD": ums.CMD_UNLOAD,
            "CMD_ABORT": ums.CMD_ABORT,
            "CMD_PING": ums.CMD_PING,
            "CMD_SHUTDOWN": ums.CMD_SHUTDOWN,
            "EVENT_READY": ums.EVENT_READY,
            "EVENT_PROGRESS": ums.EVENT_PROGRESS,
            "EVENT_DONE": ums.EVENT_DONE,
            "EVENT_UNLOADED": ums.EVENT_UNLOADED,
            "EVENT_PONG": ums.EVENT_PONG,
            "EVENT_ERROR": ums.EVENT_ERROR,
            "ERR_CANCELLED": ums.ERR_CANCELLED,
            "ERR_GENERATE_FAILED": ums.ERR_GENERATE_FAILED,
            "ERR_LOAD_FAILED": ums.ERR_LOAD_FAILED,
        }
        for name, local in pairs.items():
            assert shared.get(name) == local, f"deriva no protocolo: {name}"


class TestReadCmd:
    def _read(self, monkeypatch: pytest.MonkeyPatch, text: str) -> dict[str, Any] | None:
        monkeypatch.setattr(ums.sys, "stdin", io.StringIO(text))
        return ums._read_cmd()

    def test_eof_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._read(monkeypatch, "") is None

    def test_empty_line_returns_empty_dict(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._read(monkeypatch, "\n") == {}

    def test_valid_json(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._read(monkeypatch, '{"cmd":"ping"}\n') == {"cmd": "ping"}

    def test_invalid_json_becomes_bad_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._read(monkeypatch, "not json\n")["cmd"] == "__bad_cmd__"

    def test_json_array_becomes_bad_cmd(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert self._read(monkeypatch, "[1,2]\n")["cmd"] == "__bad_cmd__"

    def test_unicode_payload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cmd = self._read(monkeypatch, '{"cmd":"generate","request":{"output":"ação"}}\n')
        assert cmd is not None and cmd["request"]["output"] == "ação"


class TestJsonlStream:
    def test_writes_one_line_per_event(self) -> None:
        buf = io.StringIO()
        stream = ums._JsonlStream(buf)
        stream.emit("ready", vram_mib=10)
        stream.emit("done", result={"ok": True})
        lines = buf.getvalue().strip().splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0]) == {"event": "ready", "vram_mib": 10}
        assert json.loads(lines[1])["event"] == "done"

    def test_unicode_is_preserved(self) -> None:
        buf = io.StringIO()
        ums._JsonlStream(buf).emit("error", error="malha inválida")
        assert json.loads(buf.getvalue())["error"] == "malha inválida"


class TestWorkerLoop:
    def test_load_ready(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(monkeypatch, _OkAdapter(), [{"cmd": "load", "kwargs": {"model": "x"}}])
        assert events[0]["event"] == "ready"
        assert events[0]["vram_mib"] == 123

    def test_load_kwargs_forwarded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _OkAdapter()
        _run(monkeypatch, adapter, [{"cmd": "load", "kwargs": {"model": "abc", "offload": True}}])
        assert adapter.loaded == [{"model": "abc", "offload": True}]

    def test_generate_emits_progress_and_done(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(
            monkeypatch,
            _OkAdapter(),
            [
                {"cmd": "load"},
                {"cmd": "generate", "request": {"output": "a.glb"}},
            ],
        )
        names = [e["event"] for e in events]
        assert names == ["ready", "progress", "done"]
        assert events[-1]["result"]["output"] == "a.glb"

    def test_generate_without_load_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(monkeypatch, _OkAdapter(), [{"cmd": "generate", "request": {}}])
        assert events[0]["event"] == "error"
        assert events[0]["error_code"] == ums.ERR_GENERATE_FAILED

    def test_load_failure_exits_1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(ums, "_install_jsonl_stdout", lambda: recorder)
        _install_fake_reader(monkeypatch, [{"cmd": "load"}, None])
        rc = ums.run_worker(_LoadFailAdapter())
        assert rc == 1
        assert recorder.events[-1]["error_code"] == ums.ERR_LOAD_FAILED

    def test_generate_failure_keeps_worker_alive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(
            monkeypatch,
            _GenerateFailAdapter(),
            [{"cmd": "load"}, {"cmd": "generate", "request": {}}, {"cmd": "unload"}],
        )
        names = [e["event"] for e in events]
        assert names == ["ready", "error", "unloaded"]
        assert events[1]["error_code"] == ums.ERR_GENERATE_FAILED

    def test_ping_pong(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(monkeypatch, _OkAdapter(), [{"cmd": "ping"}])
        assert events == [{"event": "pong"}]

    def test_unload_calls_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _OkAdapter()
        events = _run(monkeypatch, adapter, [{"cmd": "load"}, {"cmd": "unload"}])
        assert adapter.unloaded == 1
        assert events[-1]["event"] == "unloaded"

    def test_shutdown_stops_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        adapter = _OkAdapter()
        events = _run(monkeypatch, adapter, [{"cmd": "load"}, {"cmd": "shutdown"}, {"cmd": "ping"}])
        assert [e["event"] for e in events] == ["ready"]
        assert adapter.unloaded == 1  # unload no shutdown

    def test_unknown_cmd_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events = _run(monkeypatch, _OkAdapter(), [{"cmd": "wat"}])
        assert events[0]["event"] == "error"
        assert "unknown cmd" in events[0]["error"]

    def test_abort_flag_reaches_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: dict[str, Any] = {}

        class _AbortAdapter(_OkAdapter):
            def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
                seen["abort"] = request["_abort"]()
                return {}

        _run(monkeypatch, _AbortAdapter(), [{"cmd": "load"}, {"cmd": "generate", "request": {}}])
        assert seen["abort"] is False

    def test_empty_queue_exits_cleanly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(ums, "_install_jsonl_stdout", lambda: recorder)
        _install_fake_reader(monkeypatch, [None])
        assert ums.run_worker(_OkAdapter()) == 0
        assert recorder.events == []


class TestStartReader:
    def test_abort_sets_flag_without_queueing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import queue as _queue

        lines = ['{"cmd":"abort"}\n', '{"cmd":"ping"}\n', ""]
        stream = io.StringIO("".join(lines))
        monkeypatch.setattr(ums.sys, "stdin", stream)
        cmd_q: _queue.Queue = _queue.Queue()
        abort = {"abort": False}
        ums._start_reader(cmd_q, abort)
        first = cmd_q.get(timeout=5)
        # O abort não entra na fila; o ping seguinte sim, seguido do EOF (None).
        assert first == {"cmd": "ping"}
        assert cmd_q.get(timeout=5) is None
        assert abort["abort"] is True

    def test_reader_thread_is_daemon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import os
        import queue as _queue

        read_fd, write_fd = os.pipe()
        reader = os.fdopen(read_fd, "r")
        try:
            monkeypatch.setattr(ums.sys, "stdin", reader)
            before = {t.name for t in threading.enumerate()}
            ums._start_reader(_queue.Queue(), {"abort": False})
            after = {t.name for t in threading.enumerate()}
            assert "hi3d-ums-reader" in after - before
            assert all(t.daemon for t in threading.enumerate() if t.name == "hi3d-ums-reader")
        finally:
            os.write(write_fd, b"")
            os.close(write_fd)
