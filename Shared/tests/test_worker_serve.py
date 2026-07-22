"""Testes do loop worker_serve com adapter mock (sem GPU/subprocesso).

Valida que ``run_worker_loop`` responde correctamente aos comandos JSONL:
load → ready, generate → progress + done, unload → unloaded, ping → pong,
shutdown → exit 0, abort coopera com generate, erro de load é fatal (exit 1).
"""

from __future__ import annotations

import io
import json
import sys
from typing import Any

import pytest

from gamedev_shared.worker_serve import run_worker_loop


class MockAdapter:
    """Adapter mock: carrega gera e descarrega sem GPU."""

    name = "mock"

    def __init__(self) -> None:
        self.model: Any = None
        self.load_calls: list[dict[str, Any]] = []
        self.generate_calls: list[dict[str, Any]] = []
        self.unload_calls = 0

    def load(self, **kwargs: Any) -> Any:
        self.load_calls.append(dict(kwargs))
        if kwargs.get("fail_load"):
            raise ImportError("mock import fail")
        self.model = {"loaded": True, **kwargs}
        return self.model

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        # Hooks no request.
        progress = request.get("_progress")
        abort = request.get("_abort")
        if callable(progress):
            progress(0.5, "mid")
        self.generate_calls.append(
            {"mesh_path": request.get("mesh_path"), "abort": abort() if callable(abort) else None}
        )
        if callable(abort) and abort():
            raise RuntimeError("cancelled by abort")
        return {"status": "ok", "output": "/tmp/out.glb", "mesh_path": request.get("mesh_path")}

    def unload(self, model: Any) -> None:
        self.unload_calls += 1
        self.model = None


class FailingAdapter:
    """Adapter que sempre falha no load (simula venv sem deps)."""

    name = "fail"

    def load(self, **kwargs: Any) -> Any:
        raise ImportError("No module named 'paint3d'")

    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("never reached")

    def unload(self, model: Any) -> None:
        pass


def _emit_line(line: str) -> str:
    """Constrói uma linha JSON válida."""
    return line if line.endswith("\n") else line + "\n"


def _run_with_cmds(cmds: list[str], *, adapter_class: type = MockAdapter) -> tuple[list[dict[str, Any]], int]:
    """Corre ``run_worker_loop`` com cmds no stdin; retorna eventos + exit code.

    Usa SystemExit para o caso fatal (load fail); envolve try/except.
    """
    stdin_data = "".join(cmds) if cmds else ""
    fake_stdin = io.StringIO(stdin_data)
    fake_stdout = io.StringIO()
    exit_code = 0
    try:
        original_stdin = sys.stdin
        original_stdout = sys.stdout
        sys.stdin = fake_stdin
        sys.stdout = fake_stdout
        run_worker_loop(adapter_class, backend_name="mock")
    except SystemExit as exc:
        exit_code = int(exc.code) if exc.code is not None else 0
    finally:
        sys.stdin = original_stdin
        sys.stdout = original_stdout
    # Parse dos eventos emitidos.
    events: list[dict[str, Any]] = []
    for line in fake_stdout.getvalue().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events, exit_code


# ---------------------------------------------------------------------------
# Ciclo básico
# ---------------------------------------------------------------------------


class TestLoadGenerate:
    def test_load_emits_ready(self) -> None:
        events, _ = _run_with_cmds([_emit_line('{"cmd": "load", "kwargs": {"sdnq_preset": "x"}}')])
        assert events[0]["event"] == "ready"
        assert events[0]["backend"] == "mock"

    def test_generate_after_load_emits_progress_and_done(self) -> None:
        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "generate", "request": {"mesh_path": "/m.glb"}}'),
        ]
        events, _ = _run_with_cmds(cmds)
        events_by_type = {e["event"] for e in events}
        assert "ready" in events_by_type
        assert "progress" in events_by_type
        assert "done" in events_by_type
        done = next(e for e in events if e["event"] == "done")
        assert done["result"]["status"] == "ok"
        assert done["result"]["output"] == "/tmp/out.glb"

    def test_multiple_generates_reuse_model(self) -> None:
        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "generate", "request": {"mesh_path": "/a.glb"}}'),
            _emit_line('{"cmd": "generate", "request": {"mesh_path": "/b.glb"}}'),
        ]
        # Override para o adapter guardar estado entre chamadas.
        shared_adapter = {}

        original_init = MockAdapter.__init__

        def capture_init(self, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            shared_adapter["inst"] = self

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(MockAdapter, "__init__", capture_init)
            events, _ = _run_with_cmds(cmds)
        done_events = [e for e in events if e["event"] == "done"]
        assert len(done_events) == 2
        # Mesma instância reutilizada (1 load call).
        adapter_inst = shared_adapter["inst"]
        assert len(adapter_inst.load_calls) == 1
        assert len(adapter_inst.generate_calls) == 2


class TestUnload:
    def test_unload_emits_unloaded(self) -> None:
        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "unload"}'),
        ]
        events, _ = _run_with_cmds(cmds)
        unloaded = next(e for e in events if e["event"] == "unloaded")
        assert unloaded["backend"] == "mock"

    def test_generate_after_unload_emits_error(self) -> None:
        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "unload"}'),
            _emit_line('{"cmd": "generate", "request": {}}'),
        ]
        events, _ = _run_with_cmds(cmds)
        err = next(e for e in events if e["event"] == "error")
        assert "sem modelo carregado" in err["error"]


class TestPing:
    def test_ping_returns_pong(self) -> None:
        events, _ = _run_with_cmds([_emit_line('{"cmd": "ping"}')])
        assert events[0]["event"] == "pong"


class TestShutdown:
    def test_shutdown_exits_cleanly(self) -> None:
        _events, code = _run_with_cmds([_emit_line('{"cmd": "shutdown"}')])
        assert code == 0

    def test_eof_exits_cleanly(self) -> None:
        _events, code = _run_with_cmds([])
        assert code == 0

    def test_shutdown_after_load_unloads_first(self) -> None:
        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "shutdown"}'),
        ]
        shared_adapter = {}
        original_init = MockAdapter.__init__

        def capture_init(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            shared_adapter["inst"] = self

        with pytest.MonkeyPatch().context() as mp:
            mp.setattr(MockAdapter, "__init__", capture_init)
            _run_with_cmds(cmds)
        # O adapter faz unload no shutdown.
        assert shared_adapter["inst"].unload_calls == 1


class TestError:
    def test_load_failure_exits_nonzero(self) -> None:
        events, code = _run_with_cmds(
            [_emit_line('{"cmd": "load", "kwargs": {"fail_load": true}}')], adapter_class=FailingAdapter
        )
        assert code == 1
        err = next(e for e in events if e["event"] == "error")
        assert err["error_code"] == "LOAD_FAILED"
        assert "No module named 'paint3d'" in err["error"]

    def test_generate_failure_emits_error_continues(self) -> None:
        class GenFail(MockAdapter):
            def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
                raise RuntimeError("boom")

        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "generate", "request": {}}'),
            _emit_line('{"cmd": "ping"}'),  # worker ainda vivo
        ]
        events, _ = _run_with_cmds(cmds, adapter_class=GenFail)
        err = next(e for e in events if e["event"] == "error")
        assert err["error_code"] == "GENERATE_FAILED"
        assert "boom" in err["error"]
        # Continua: pong recebido.
        assert any(e["event"] == "pong" for e in events)


class TestAbort:
    def test_abort_during_generate_returns_cancelled(self) -> None:
        """Generate coopera com abort: quando o adapter chama o hook ``_abort``
        a meio da inferência e este retorna True, o adapter lança; o worker
        converte para ``error_code=CANCELLED``.

        Simulamos o cenário real (UMS manda abort a meio) forçando o hook a
        ``True`` e chamando-o via ``request["_abort"]()``.
        """

        class CooperativeAdapter(MockAdapter):
            def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
                # Simula UMS enviou abort a meio: substituir o hook.
                request["_abort"] = lambda: True
                # Adapter chama o hook corrente (em produção, dentro do loop).
                abort = request["_abort"]
                if callable(abort) and abort():
                    raise RuntimeError("cancelled mid-generate")
                return {"status": "ok"}

        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "generate", "request": {}}'),
        ]
        events, _ = _run_with_cmds(cmds, adapter_class=CooperativeAdapter)
        err = next((e for e in events if e["event"] == "error"), None)
        assert err is not None
        assert err["error_code"] == "CANCELLED"


class TestUnknownCmd:
    def test_unknown_cmd_emits_error_and_continues(self) -> None:
        cmds = [
            _emit_line('{"cmd": "bogus"}'),
            _emit_line('{"cmd": "ping"}'),
        ]
        events, _ = _run_with_cmds(cmds)
        err = next(e for e in events if e["event"] == "error")
        assert "comando desconhecido" in err["error"]
        assert any(e["event"] == "pong" for e in events)


class TestBadInput:
    def test_invalid_json_emits_error_continues(self) -> None:
        cmds = [
            _emit_line("not valid json\n"),
            _emit_line('{"cmd": "ping"}'),
        ]
        events, _ = _run_with_cmds(cmds)
        err = next(e for e in events if e["event"] == "error")
        assert "comando inválido" in err["error"]
        assert any(e["event"] == "pong" for e in events)


class TestProgressScrub:
    def test_done_result_excludes_callbacks(self) -> None:
        """``_scrub_result`` remove chaves começadas por ``_`` e callables."""

        class WithHooks(MockAdapter):
            def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
                return {
                    "status": "ok",
                    "output": "/tmp/x.glb",
                    "_progress": lambda: None,  # callable → removido
                    "_internal": "secret",  # _ → removido
                    "public": "kept",
                }

        cmds = [
            _emit_line('{"cmd": "load", "kwargs": {}}'),
            _emit_line('{"cmd": "generate", "request": {}}'),
        ]
        events, _ = _run_with_cmds(cmds, adapter_class=WithHooks)
        done = next(e for e in events if e["event"] == "done")
        result = done["result"]
        assert result == {"status": "ok", "output": "/tmp/x.glb", "public": "kept"}
