"""Tests do SubprocessWorkerPool com Popen fake (sem GPU / subprocesso real).

A estratégia é injectar uma ``spawn_fn`` que devolve um fake ``Popen`` com
stdin/stdout em ``io.StringIO`` pré-carregado com os eventos que um worker real
emitiria. Assim testamos o protocolo, o lock, o re-spawn, o timeout e o
abort sem nunca spawnar um subprocesso.
"""

from __future__ import annotations

import contextlib
import io
import subprocess
from typing import Any
from unittest.mock import MagicMock

import pytest
from modelserver.subprocess_pool import (
    SubprocessWorkerError,
    SubprocessWorkerPool,
    _progress_is_terminal,
    _readline_nonblocking,
    _shape_mismatch,
)


class _FakeStdout:
    """Stdout do FakePopen: readline() preserva posição entre push_events.

    Implementa só o subconjunto que o SubprocessWorkerPool usa:
    ``readline()`` (lê 1 linha, avançando o cursor; "" se sem linha nova).
    Push append-only: novo conteúdo aparece após o já lido.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._pos = 0

    def push(self, *events: str) -> None:
        for ev in events:
            self._buf += ev if ev.endswith("\n") else ev + "\n"

    def readline(self) -> str:
        # Procurar o próximo newline a partir da posição actual.
        idx = self._buf.find("\n", self._pos)
        if idx < 0:
            return ""  # sem linha completa (EOF aparente)
        line = self._buf[self._pos : idx + 1]
        self._pos = idx + 1
        return line


class FakePopen:
    """Popen com stdin/stdout em memória; eventos são pushed pelo teste."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._fake_stdout = _FakeStdout()
        self.stdin = io.StringIO()
        self.stderr = MagicMock()
        self._returncode: int | None = None
        self._killed = False
        self._terminated = False
        self._waited = False

    @property
    def stdout(self) -> _FakeStdout:
        return self._fake_stdout

    @stdout.setter
    def stdout(self, value: Any) -> None:
        # _spawn_fn pode atribuir um pipe; ignorar (já temos _FakeStdout).
        pass

    # API Popen usada pelo pool -------------------------------------------------
    def poll(self) -> int | None:
        return self._returncode

    def wait(self, timeout: float | None = None) -> int:
        self._waited = True
        if self._returncode is None:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout or 0)
        return self._returncode

    def terminate(self) -> None:
        self._terminated = True
        self._returncode = -15

    def kill(self) -> None:
        self._killed = True
        self._returncode = -9

    # Helpers para o teste pushes eventos no stdout -----------------------------
    def push_events(self, *events: str) -> None:
        """Empilha eventos para o pool ler (cada um é uma linha JSON)."""
        self._fake_stdout.push(*events)

    def read_cmds(self) -> list[dict[str, Any]]:
        """Lê todos os comandos que o pool escreveu no stdin."""
        import json

        return [json.loads(line) for line in self.stdin.getvalue().splitlines() if line.strip()]


def _make_pool(fake_popen: FakePopen, **overrides: Any) -> SubprocessWorkerPool:
    """Cria um pool cujo spawn_fn retorna sempre o mesmo FakePopen."""

    def fake_spawn(cmd: list[str], stdin: Any, stdout: Any, stderr: Any) -> Any:
        # FakePopen já tem os seus StringIO internos (stdin/stdout); ignorar os
        # inteiros PIPE passados pelo _spawn.
        return fake_popen

    kwargs: dict[str, Any] = {
        "spawn_fn": fake_spawn,
        "load_timeout_sec": 1.0,
        "event_timeout_sec": 1.0,
        "abort_timeout_sec": 1.0,
        "ping_timeout_sec": 1.0,
        "python_override": {"paint3d": "/fake/paint3d/.venv/bin/python"},
    }
    kwargs.update(overrides)
    return SubprocessWorkerPool(**kwargs)


class TestSpawn:
    def test_load_emits_load_cmd_and_consumes_ready(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        # Worker emite ready após load.
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        info = pool.load("paint3d", "paint3d", {"sdnq_preset": "sdnq-uint8"})
        assert info["event"] == "ready"
        assert info["vram_mib"] == 1300
        cmds = fake.read_cmds()
        assert cmds[0] == {"cmd": "load", "kwargs": {"sdnq_preset": "sdnq-uint8"}}

    def test_load_reused_when_already_loaded_same_shape(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {"sdnq_preset": "sdnq-uint8"})
        # Segundo load com mesma shape → reutiliza (sem novo spawn, sem novo cmd).
        info = pool.load("paint3d", "paint3d", {"sdnq_preset": "sdnq-uint8"})
        assert info.get("reused") is True
        # Apenas um comando load no stdin.
        cmds = fake.read_cmds()
        assert len(cmds) == 1

    def test_load_reload_when_shape_changes(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {"sdnq_preset": "sdnq-uint8"})
        # Mudou sdnq_preset → reload.
        fake.push_events('{"event": "ready", "vram_mib": 1800}')
        info = pool.load("paint3d", "paint3d", {"sdnq_preset": "sdnq-int4"})
        assert info["vram_mib"] == 1800
        assert not info.get("reused")
        cmds = fake.read_cmds()
        assert len(cmds) == 2  # dois loads

    def test_load_error_raises_and_marks_not_loaded(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "error", "error": "ImportError: paint3d", "error_code": "LOAD_FAILED"}')
        with pytest.raises(SubprocessWorkerError, match="load falhou"):
            pool.load("paint3d", "paint3d", {})
        assert not pool.is_loaded("paint3d")

    def test_load_eof_raises(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        # Sem eventos — EOF imediato.
        with pytest.raises(SubprocessWorkerError, match="EOF"):
            pool.load("paint3d", "paint3d", {})


class TestGenerate:
    def test_generate_emits_done(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        fake.push_events('{"event": "progress", "pct": 0.5, "msg": "painting"}')
        fake.push_events('{"event": "done", "result": {"output": "/tmp/x.glb", "status": "ok"}}')
        result = pool.generate("paint3d", {"mesh_path": "/tmp/m.glb"})
        assert result == {"output": "/tmp/x.glb", "status": "ok"}
        cmds = fake.read_cmds()
        assert cmds[1] == {"cmd": "generate", "request": {"mesh_path": "/tmp/m.glb"}}

    def test_progress_callback_invoked(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        progresses: list[tuple[float | None, str | None]] = []
        fake.push_events('{"event": "progress", "pct": 0.3, "msg": "step 1"}')
        fake.push_events('{"event": "progress", "pct": 0.6, "msg": "step 2"}')
        fake.push_events('{"event": "done", "result": {}}')
        pool.generate("paint3d", {}, on_progress=lambda pct, msg: progresses.append((pct, msg)))
        assert progresses == [(0.3, "step 1"), (0.6, "step 2")]

    def test_generate_error_raises(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        fake.push_events('{"event": "error", "error": "OOM", "error_code": "VRAM_INSUFFICIENT"}')
        with pytest.raises(SubprocessWorkerError, match="OOM"):
            pool.generate("paint3d", {})

    def test_generate_without_load_raises(self) -> None:
        pool = _make_pool(FakePopen())
        with pytest.raises(SubprocessWorkerError, match="faz load"):
            pool.generate("paint3d", {})


class TestAbort:
    def test_abort_sent_when_should_abort_true(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        # Worker segura e depois responde done(cancelled) — entre enquanto, o
        # caller already returned True em should_abort.
        fake.push_events('{"event": "done", "result": {"error_code": "CANCELLED"}}')
        result = pool.generate("paint3d", {}, should_abort=lambda: True)
        assert result == {"error_code": "CANCELLED"}
        cmds = fake.read_cmds()
        assert {"cmd": "abort"} in cmds

    def test_abort_ignored_escalates_to_sigterm(self) -> None:
        """Worker ignora CMD_ABORT → após abort_timeout, SIGTERM (não esperar 10 min)."""
        fake = FakePopen()
        pool = _make_pool(fake, event_timeout_sec=30.0, abort_timeout_sec=0.25)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        # Sem done/error — só silêncio (como text3d mid image_to_3d).
        with pytest.raises(SubprocessWorkerError, match="abort timeout"):
            pool.generate("paint3d", {}, should_abort=lambda: True)
        assert fake._terminated or fake._killed
        cmds = fake.read_cmds()
        assert {"cmd": "abort"} in cmds

    def test_silence_while_alive_does_not_mean_worker_dead(self) -> None:
        """readline vazio com proc vivo → continua; done chega depois."""
        fake = FakePopen()
        pool = _make_pool(fake, event_timeout_sec=2.0, abort_timeout_sec=5.0)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})

        import threading
        import time

        def feeder() -> None:
            time.sleep(0.35)  # silêncio > poll 1x; proc ainda vivo
            fake.push_events('{"event": "done", "result": {"status": "ok"}}')

        t = threading.Thread(target=feeder, daemon=True)
        t.start()
        result = pool.generate("paint3d", {})
        t.join(timeout=3.0)
        assert result.get("status") == "ok"


class TestUnloadShutdown:
    def test_unload_emits_unload_cmd(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        fake.push_events('{"event": "unloaded"}')
        ok = pool.unload("paint3d")
        assert ok is True
        assert not pool.is_loaded("paint3d")
        cmds = fake.read_cmds()
        assert {"cmd": "unload"} in cmds

    def test_shutdown_sends_shutdown_and_clears_state(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        # shutdown espera wait() — simulamos returncode 0 no wait.
        fake._returncode = 0
        ok = pool.shutdown("paint3d")
        assert ok is True
        assert not pool.is_alive("paint3d")
        cmds = fake.read_cmds()
        assert {"cmd": "shutdown"} in cmds

    def test_shutdown_kills_after_timeout(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        # wait() sempre raises TimeoutExpired → terminate → wait → kill.
        ok = pool.shutdown("paint3d")
        assert ok is True
        assert fake._terminated is True


class TestStatusQueries:
    def test_is_loaded_requires_loaded_and_alive(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        assert not pool.is_loaded("paint3d")
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        assert pool.is_loaded("paint3d")
        # Simular worker morto.
        fake._returncode = 1
        assert not pool.is_loaded("paint3d")

    def test_loaded_backends_lists_loaded_only(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        assert "paint3d" in pool.loaded_backends()

    def test_vram_mib_from_ready(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1500}')
        pool.load("paint3d", "paint3d", {})
        assert pool.vram_mib("paint3d") == 1500


class TestPing:
    def test_ping_returns_true_on_pong(self) -> None:
        fake = FakePopen()
        pool = _make_pool(fake)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        fake.push_events('{"event": "pong", "version": "1"}')
        assert pool.ping("paint3d") is True
        cmds = fake.read_cmds()
        assert {"cmd": "ping"} in cmds

    def test_ping_returns_false_on_dead_worker(self) -> None:
        pool = _make_pool(FakePopen())
        assert pool.ping("paint3d") is False


class TestShapeMismatch:
    def test_no_mismatch_when_relevant_keys_equal(self) -> None:
        assert not _shape_mismatch({"sdnq_preset": "x"}, {"sdnq_preset": "x"})

    def test_mismatch_on_relevant_key(self) -> None:
        assert _shape_mismatch({"sdnq_preset": "x"}, {"sdnq_preset": "y"})
        assert _shape_mismatch({"max_num_view": 4}, {"max_num_view": 6})

    def test_no_mismatch_on_irrelevant_keys(self) -> None:
        # prompt/output podem mudar entre jobs sem reload.
        assert not _shape_mismatch({"output": "/a.glb"}, {"output": "/b.glb"})


class TestProgressTerminal:
    def test_pct_one_is_terminal(self) -> None:
        assert _progress_is_terminal(1.0, "painting")
        assert _progress_is_terminal(1, None)
        assert _progress_is_terminal("1.0", "x")

    def test_msg_done_is_terminal(self) -> None:
        assert _progress_is_terminal(0.99, "done")
        assert _progress_is_terminal(None, "DONE")

    def test_mid_progress_not_terminal(self) -> None:
        assert not _progress_is_terminal(0.5, "painting")
        assert not _progress_is_terminal(None, "image_to_3d")


class TestBufferedStdoutLine:
    def test_nonblocking_readline_drains_user_buffer(self) -> None:
        """Após 1º readline, DONE fica no TextIO; select no fd fica vazio; O_NONBLOCK drena."""
        import os
        import select

        r_fd, w_fd = os.pipe()
        try:
            payload = b'{"event":"progress","pct":1.0,"msg":"done"}\n{"event":"done","result":{"status":"ok"}}\n'
            os.write(w_fd, payload)
            # NÃO fechar w_fd — hang real = worker vivo, pipe aberto.
            stdout = os.fdopen(r_fd, "r", encoding="utf-8", buffering=4096)
            r_fd = -1
            first = stdout.readline()
            assert '"progress"' in first
            fd = stdout.fileno()
            ready, _, _ = select.select([fd], [], [], 0.0)
            assert not ready, "select não deve ver linha já no TextIO buffer"
            second = _readline_nonblocking(stdout, fd)
            assert '"done"' in second
            assert _readline_nonblocking(stdout, fd) == ""
            stdout.close()
        finally:
            if w_fd >= 0:
                os.close(w_fd)
            if r_fd >= 0:
                os.close(r_fd)

    def test_generate_drains_done_buffered_after_terminal_progress(self) -> None:
        """Regression: progress+done no mesmo chunk, write-end aberto → sem hang."""
        import os

        from modelserver.subprocess_pool import _WorkerState

        r_fd, w_fd = os.pipe()
        try:
            # Um único write: progress + done (mesmo read do kernel).
            # Write-end fica ABERTO (worker vivo) — select sozinho nunca acorda.
            os.write(
                w_fd,
                b'{"event":"progress","pct":1.0,"msg":"done"}\n{"event":"done","result":{"status":"ok"}}\n',
            )
            stdout = os.fdopen(r_fd, "r", encoding="utf-8", buffering=4096)
            r_fd = -1

            class _PipeProc:
                def __init__(self) -> None:
                    self.stdout = stdout
                    self.stdin = io.StringIO()
                    self._rc: int | None = None

                def poll(self) -> int | None:
                    return self._rc

            pool = SubprocessWorkerPool(
                event_timeout_sec=30.0,
                post_done_timeout_sec=2.0,
                abort_timeout_sec=1.0,
                load_timeout_sec=1.0,
            )
            state = _WorkerState(backend="text3d", proc=_PipeProc())  # type: ignore[arg-type]
            p1 = pool._read_event_with_timeout(state, timeout=1.0)
            assert p1 is not None and p1["event"] == "progress"
            # Sem drain do _decoded_chars, select no fd falharia (~timeout 1s).
            p2 = pool._read_event_with_timeout(state, timeout=1.0)
            assert p2 is not None and p2["event"] == "done"
            assert p2["result"]["status"] == "ok"
            stdout.close()
        finally:
            if w_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(w_fd)
            if r_fd >= 0:
                with contextlib.suppress(OSError):
                    os.close(r_fd)

    def test_terminal_progress_uses_short_idle_not_full_event_timeout(self) -> None:
        """Após progress done, silêncio → idle curto (post_done), não 600s."""
        fake = FakePopen()
        pool = _make_pool(fake, event_timeout_sec=30.0, post_done_timeout_sec=0.35)
        fake.push_events('{"event": "ready", "vram_mib": 1300}')
        pool.load("paint3d", "paint3d", {})
        fake.push_events('{"event": "progress", "pct": 1.0, "msg": "done"}')
        # Sem EVENT_DONE — deve falhar em ~post_done, não esperar 30s.
        import time

        t0 = time.monotonic()
        with pytest.raises(SubprocessWorkerError, match="timeout idle"):
            pool.generate("paint3d", {})
        elapsed = time.monotonic() - t0
        assert elapsed < 5.0, f"esperava post_done curto, demorou {elapsed:.1f}s"
