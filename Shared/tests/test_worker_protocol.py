"""Tests do protocolo JSONL partilhado entre UMS e workers subprocesso."""

from __future__ import annotations

import io
import json

import pytest

from gamedev_shared.worker_protocol import (
    CMD_GENERATE,
    CMD_LOAD,
    CMD_PING,
    CMD_SHUTDOWN,
    CMD_UNLOAD,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_PONG,
    EVENT_READY,
    decode,
    emit_event,
    encode,
    read_cmd,
    read_event,
    send_cmd,
    set_jsonl_stream,
)


@pytest.fixture(autouse=True)
def _reset_jsonl_stream():
    """Reset do stream JSONL entre testes (modo worker persiste entre chamadas)."""
    set_jsonl_stream(None)
    yield
    set_jsonl_stream(None)


class TestEncodeDecode:
    def test_roundtrip(self) -> None:
        msg = {"cmd": CMD_LOAD, "kwargs": {"sdnq_preset": "sdnq-int4", "octree_resolution": 256}}
        data = encode(msg)
        assert data.endswith(b"\n")
        decoded = decode(data)
        assert decoded == msg

    def test_decode_bytes_and_str(self) -> None:
        line = '{"event": "ready", "vram_mib": 1300}\n'
        assert decode(line) == {"event": "ready", "vram_mib": 1300}
        assert decode(line.encode()) == {"event": "ready", "vram_mib": 1300}

    def test_decode_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            decode("")
        with pytest.raises(ValueError):
            decode(b"")
        with pytest.raises(ValueError):
            decode("   \n  ")

    def test_decode_non_object_raises(self) -> None:
        with pytest.raises(ValueError):
            decode("[1, 2, 3]")
        with pytest.raises(ValueError):
            decode('"string"')

    def test_encode_unicode_preserved(self) -> None:
        msg = {"event": EVENT_ERROR, "error": "falha de VRAM em geração"}
        decoded = decode(encode(msg))
        assert decoded == msg
        # ensure_ascii=False → carácteres unicode não escapados
        assert "falha" in encode(msg).decode("utf-8")


class TestSendCmd:
    def test_writes_one_line_json(self) -> None:
        stream = io.StringIO()
        send_cmd(stream, CMD_PING)
        line = stream.getvalue()
        assert line.endswith("\n")
        assert json.loads(line) == {"cmd": "ping"}

    def test_payload_fields_included(self) -> None:
        stream = io.StringIO()
        send_cmd(stream, CMD_LOAD, kwargs={"sdnq_preset": "sdnq-int4"})
        msg = json.loads(stream.getvalue())
        assert msg["cmd"] == CMD_LOAD
        assert msg["kwargs"] == {"sdnq_preset": "sdnq-int4"}

    def test_generate_payload(self) -> None:
        stream = io.StringIO()
        send_cmd(stream, CMD_GENERATE, request={"prompt": "rock", "output": "/tmp/r.glb"})
        msg = json.loads(stream.getvalue())
        assert msg["request"]["output"] == "/tmp/r.glb"


class TestReadEvent:
    def test_returns_dict_or_none(self) -> None:
        stream = io.StringIO('{"event": "ready", "vram_mib": 500}\n{"event": "done"}\n')
        ev1 = read_event(stream)
        ev2 = read_event(stream)
        ev3 = read_event(stream)  # EOF
        assert ev1 == {"event": "ready", "vram_mib": 500}
        assert ev2 == {"event": "done"}
        assert ev3 is None


class TestEmitEvent:
    def test_writes_to_stdout(self, capsys: pytest.CaptureFixture[str]) -> None:
        emit_event(EVENT_READY, vram_mib=1234, backend="text3d")
        captured = capsys.readouterr()
        line = captured.out.strip()
        msg = json.loads(line)
        assert msg["event"] == EVENT_READY
        assert msg["vram_mib"] == 1234
        assert msg["backend"] == "text3d"

    def test_progress_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        emit_event("progress", pct=0.5, msg="step 25/50")
        msg = json.loads(capsys.readouterr().out.strip())
        assert msg["event"] == "progress"
        assert msg["pct"] == 0.5
        assert msg["msg"] == "step 25/50"

    def test_done_event_with_result(self, capsys: pytest.CaptureFixture[str]) -> None:
        emit_event(EVENT_DONE, result={"output": "/tmp/x.glb", "status": "ok"})
        msg = json.loads(capsys.readouterr().out.strip())
        assert msg["event"] == EVENT_DONE
        assert msg["result"]["output"] == "/tmp/x.glb"

    def test_pong_event(self, capsys: pytest.CaptureFixture[str]) -> None:
        emit_event(EVENT_PONG, version="1")
        msg = json.loads(capsys.readouterr().out.strip())
        assert msg["event"] == EVENT_PONG


class TestReadCmd:
    def test_reads_from_provided_stream(self) -> None:
        stream = io.StringIO('{"cmd": "shutdown"}\n')
        msg = read_cmd(stream)
        assert msg == {"cmd": CMD_SHUTDOWN}

    def test_returns_none_on_eof(self) -> None:
        stream = io.StringIO("")
        assert read_cmd(stream) is None

    def test_unload_and_generate(self) -> None:
        stream = io.StringIO('{"cmd": "unload"}\n{"cmd": "generate", "request": {"x": 1}}\n')
        assert read_cmd(stream) == {"cmd": CMD_UNLOAD}
        msg = read_cmd(stream)
        assert msg["cmd"] == CMD_GENERATE
        assert msg["request"] == {"x": 1}


class TestProtocolConstants:
    """Garantir que comandos/eventos são strings canónicas estáveis."""

    def test_all_cmds_known(self) -> None:
        from gamedev_shared.worker_protocol import ALL_CMDS

        assert {"load", "generate", "unload", "abort", "ping", "shutdown"} == ALL_CMDS

    def test_error_codes_aligned_with_protocol(self) -> None:
        from gamedev_shared.worker_protocol import (
            ERR_BACKEND_VENV_MISSING,
            ERR_CANCELLED,
            ERR_GENERATE_FAILED,
            ERR_LOAD_FAILED,
            ERR_VRAM_INSUFFICIENT,
        )

        # String canónicas — não mudar sem actualizar o modelserver.protocol.
        assert ERR_VRAM_INSUFFICIENT == "VRAM_INSUFFICIENT"
        assert ERR_CANCELLED == "CANCELLED"
        assert ERR_GENERATE_FAILED == "GENERATE_FAILED"
        assert ERR_BACKEND_VENV_MISSING == "BACKEND_VENV_MISSING"
        assert ERR_LOAD_FAILED == "LOAD_FAILED"
