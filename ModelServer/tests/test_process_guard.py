"""Testes do ``process_guard`` — singleton, deteção e reap de órfãos.

Contexto real que motivou o módulo: três supervisores UMS vivos ao mesmo tempo
(o probe do socket falhou num supervisor ocupado, o novo apagou o socket e fez
bind por cima), um deles com um worker ``text3d`` a segurar 3.5 GiB invisíveis
no ``ums status``.

Estratégia: ``/proc`` falso em ``tmp_path`` (ficheiros ``cmdline``/``status``) e
``kill_fn``/``sleep_fn`` injetados — nada de processos reais.
"""

from __future__ import annotations

import errno
import os
import signal
from pathlib import Path

from modelserver.process_guard import (
    KIND_SUPERVISOR,
    KIND_WORKER,
    SingletonLock,
    UmsProcess,
    ancestors,
    classify_cmdline,
    descendants,
    find_strays,
    lock_path_for,
    pid_alive,
    reap,
)

# ---------------------------------------------------------------------------
# /proc falso
# ---------------------------------------------------------------------------


def _fake_proc(root: Path, pid: int, argv: list[str], *, ppid: int = 1) -> None:
    d = root / str(pid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "cmdline").write_bytes(b"\x00".join(a.encode() for a in argv) + b"\x00")
    (d / "status").write_text(f"Name:\tpython3\nPid:\t{pid}\nPPid:\t{ppid}\n")


SUPERVISOR_ARGV = ["/opt/ModelServer/.venv/bin/python", "-m", "modelserver", "start"]
WORKER_ARGV = ["/opt/Text3D/.venv/bin/python", "-m", "text3d", "serve", "--ums-worker"]


class TestClassifyCmdline:
    def test_supervisor_module(self) -> None:
        assert classify_cmdline(SUPERVISOR_ARGV) == (KIND_SUPERVISOR, None)

    def test_supervisor_console_script(self) -> None:
        assert classify_cmdline(["/home/u/.local/bin/aigamekit-model-server", "start", "-v"]) == (KIND_SUPERVISOR, None)
        assert classify_cmdline(["/home/u/.local/bin/ums", "start"]) == (KIND_SUPERVISOR, None)

    def test_worker_carries_backend(self) -> None:
        assert classify_cmdline(WORKER_ARGV) == (KIND_WORKER, "text3d")

    def test_unrelated_and_empty(self) -> None:
        assert classify_cmdline(["python", "-m", "pytest"]) is None
        assert classify_cmdline(["ums", "status"]) is None  # não é 'start'
        assert classify_cmdline([]) is None


class TestFindStrays:
    def test_ignores_self_and_own_children(self, tmp_path: Path) -> None:
        _fake_proc(tmp_path, 100, SUPERVISOR_ARGV)  # nós
        _fake_proc(tmp_path, 101, WORKER_ARGV, ppid=100)  # nosso worker
        _fake_proc(tmp_path, 102, WORKER_ARGV, ppid=101)  # neto (respawn encadeado)

        strays = find_strays(self_pid=100, proc_root=tmp_path, with_vram=False)

        assert strays == []

    def test_finds_zombie_supervisor_and_its_worker(self, tmp_path: Path) -> None:
        _fake_proc(tmp_path, 100, SUPERVISOR_ARGV)
        _fake_proc(tmp_path, 900, SUPERVISOR_ARGV)  # supervisor de run anterior
        _fake_proc(tmp_path, 901, WORKER_ARGV, ppid=900)  # worker dele (3.5 GiB)
        _fake_proc(tmp_path, 950, ["python", "-m", "pytest"])  # não-UMS

        strays = find_strays(self_pid=100, proc_root=tmp_path, with_vram=False)

        assert [(p.pid, p.kind, p.backend) for p in strays] == [
            (900, KIND_SUPERVISOR, None),
            (901, KIND_WORKER, "text3d"),
        ]

    def test_never_kills_own_launcher(self, tmp_path: Path) -> None:
        # ``timeout 900 python -m modelserver start`` carrega a nossa cmdline no
        # argv, e é o nosso pai — matá-lo mataria quem nos lançou.
        _fake_proc(tmp_path, 50, ["/usr/bin/timeout", "900", "python", "-m", "modelserver", "start"])
        _fake_proc(tmp_path, 100, SUPERVISOR_ARGV, ppid=50)

        strays = find_strays(self_pid=100, proc_root=tmp_path, with_vram=False)

        assert strays == []

    def test_keep_protects_explicit_pids(self, tmp_path: Path) -> None:
        _fake_proc(tmp_path, 100, SUPERVISOR_ARGV)
        _fake_proc(tmp_path, 900, WORKER_ARGV)

        assert find_strays(self_pid=100, keep=[900], proc_root=tmp_path, with_vram=False) == []

    def test_missing_proc_root_is_not_fatal(self, tmp_path: Path) -> None:
        assert find_strays(self_pid=1, proc_root=tmp_path / "nope", with_vram=False) == []

    def test_orphan_worker_reparented_to_init(self, tmp_path: Path) -> None:
        # Supervisor morreu com kill -9: o worker fica com PPid 1.
        _fake_proc(tmp_path, 100, SUPERVISOR_ARGV)
        _fake_proc(tmp_path, 777, WORKER_ARGV, ppid=1)

        strays = find_strays(self_pid=100, proc_root=tmp_path, with_vram=False)

        assert [p.pid for p in strays] == [777]


class TestDescendants:
    def test_transitive_children(self, tmp_path: Path) -> None:
        _fake_proc(tmp_path, 10, SUPERVISOR_ARGV)
        _fake_proc(tmp_path, 11, WORKER_ARGV, ppid=10)
        _fake_proc(tmp_path, 12, WORKER_ARGV, ppid=11)
        _fake_proc(tmp_path, 20, WORKER_ARGV, ppid=1)

        assert descendants(10, proc_root=tmp_path) == {11, 12}

    def test_ancestors_walk_up_to_init(self, tmp_path: Path) -> None:
        _fake_proc(tmp_path, 10, SUPERVISOR_ARGV, ppid=1)
        _fake_proc(tmp_path, 11, WORKER_ARGV, ppid=10)
        _fake_proc(tmp_path, 12, WORKER_ARGV, ppid=11)

        assert ancestors(12, proc_root=tmp_path) == {10, 11}


class TestReap:
    def test_sigterm_when_process_exits(self) -> None:
        sent: list[tuple[int, int]] = []
        alive = {900: True}

        def kill_fn(pid: int, sig: int) -> None:
            sent.append((pid, sig))
            if sig == 0 and not alive.get(pid):
                raise OSError(errno.ESRCH, "no such process")
            if sig == signal.SIGTERM:
                alive[pid] = False

        results = reap(
            [UmsProcess(pid=900, kind=KIND_WORKER, backend="text3d", cmdline="w", vram_mib=3470)],
            kill_fn=kill_fn,
            sleep_fn=lambda _s: None,
        )

        assert results == [{"pid": 900, "kind": KIND_WORKER, "signal": "SIGTERM", "vram_mib": 3470}]
        assert (900, signal.SIGTERM) in sent

    def test_sigkill_when_sigterm_ignored(self) -> None:
        signals: list[int] = []

        def kill_fn(pid: int, sig: int) -> None:
            if sig != 0:
                signals.append(sig)

        results = reap(
            [UmsProcess(pid=900, kind=KIND_SUPERVISOR, backend=None, cmdline="s")],
            term_wait_sec=0.4,
            kill_fn=kill_fn,
            sleep_fn=lambda _s: None,
            poll_sec=0.2,
        )

        assert signals == [signal.SIGTERM, signal.SIGKILL]
        assert results[0]["signal"] == "SIGKILL"

    def test_kill_permission_error_is_reported_not_raised(self) -> None:
        def kill_fn(pid: int, sig: int) -> None:
            raise OSError(errno.EPERM, "operation not permitted")

        results = reap(
            [UmsProcess(pid=1, kind=KIND_SUPERVISOR, backend=None, cmdline="s")],
            kill_fn=kill_fn,
            sleep_fn=lambda _s: None,
        )

        assert results[0]["signal"] == "none"
        assert "not permitted" in results[0]["error"]

    def test_pid_alive_treats_eperm_as_alive(self) -> None:
        def kill_fn(pid: int, sig: int) -> None:
            raise OSError(errno.EPERM, "operation not permitted")

        assert pid_alive(4242, kill_fn=kill_fn) is True


class TestSingletonLock:
    def test_second_acquire_in_other_fd_fails(self, tmp_path: Path) -> None:
        path = tmp_path / "model-server.lock"
        first = SingletonLock(path)
        second = SingletonLock(path)

        assert first.acquire() is True
        assert first.held is True
        assert first.owner_pid() == os.getpid()
        assert second.acquire() is False
        assert second.held is False

        first.release()
        assert second.acquire() is True
        second.release()

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        lock = SingletonLock(tmp_path / "l.lock")
        lock.release()  # nunca adquirido
        assert lock.acquire() is True
        lock.release()
        lock.release()
        assert lock.held is False

    def test_lock_path_sits_next_to_socket(self, tmp_path: Path) -> None:
        assert lock_path_for(tmp_path / "model-server.sock") == tmp_path / "model-server.lock"
