"""Guardas de processo do UMS — singleton, reap de órfãos e VRAM alheia.

Três problemas históricos que este módulo resolve:

1. **Supervisores duplicados.** ``serve_forever`` apagava o socket quando o
   probe ``is_server_running`` falhava (supervisor vivo mas ocupado) e fazia
   bind por cima. Resultado observado: 3 supervisores vivos, um deles com um
   worker ``text3d`` a segurar 3.5 GiB — invisível no ``ums status`` (que só
   fala com o supervisor dono do socket). :class:`SingletonLock` usa ``flock``
   exclusivo: o kernel liberta o lock na morte do processo, logo nunca fica
   stale e nunca há dois supervisores.

2. **Órfãos.** Workers descendem por stdin (EOF ⇒ saem), mas um supervisor
   zombie mantém-nos vivos, e um ``kill -9`` no supervisor deixa o worker sem
   quem lhe feche o pipe. :func:`find_strays` + :func:`reap` limpam de forma
   determinística: quem detém o lock é, por definição, o único legítimo.

3. **VRAM alheia invisível.** O admit contabilizava só o PID do próprio
   supervisor. :func:`gpu_vram_by_pid` / :func:`stray_report` expõem a VRAM
   presa por supervisores/workers estranhos, para o ``status`` mostrar e o
   ``ensure_vram`` poder recuperá-la em vez de recusar o job.

Linux-first (``/proc``); em plataformas sem ``/proc`` as funções de varrimento
devolvem listas vazias e o resto do UMS continua a funcionar.
"""

from __future__ import annotations

import contextlib
import errno
import os
import signal
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from aigamekit_shared.logging import Logger

_logger = Logger()

LOCK_FILENAME = "model-server.lock"

# Assinaturas de cmdline da família UMS.
_SUPERVISOR_TOKENS = ("modelserver", "aigamekit-model-server", "aigamekit_model_server")
_WORKER_FLAG = "--ums-worker"

KIND_SUPERVISOR = "supervisor"
KIND_WORKER = "worker"


@dataclass(frozen=True)
class UmsProcess:
    """Processo da família UMS encontrado no sistema."""

    pid: int
    kind: str
    backend: str | None
    cmdline: str
    vram_mib: int | None = None

    def describe(self) -> str:
        who = f"{self.kind}"
        if self.backend:
            who += f":{self.backend}"
        vram = f", {self.vram_mib} MiB" if self.vram_mib else ""
        return f"PID {self.pid} ({who}{vram})"


# ---------------------------------------------------------------------------
# Singleton por flock
# ---------------------------------------------------------------------------


def lock_path_for(socket_path: Path | str) -> Path:
    """Path do lockfile ao lado do socket."""
    return Path(socket_path).parent / LOCK_FILENAME


class SingletonLock:
    """Lock exclusivo de supervisor via ``flock`` (não bloqueante).

    O fd fica aberto enquanto o processo vive; o kernel liberta o lock quando
    o processo morre (inclusive com ``SIGKILL``), pelo que não existe estado
    stale a limpar — ao contrário de pid-files.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        """Tenta obter o lock. ``False`` se outro supervisor o detém."""
        try:
            import fcntl
        except ImportError:  # pragma: no cover — não-Linux
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.truncate(fd, 0)
        os.write(fd, f"{os.getpid()}\n".encode())
        os.fsync(fd)
        self._fd = fd
        return True

    def owner_pid(self) -> int | None:
        """PID escrito no lockfile pelo detentor actual (best-effort)."""
        try:
            raw = self.path.read_text().strip()
            return int(raw) if raw else None
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        """Liberta o lock (idempotente)."""
        if self._fd is None:
            return
        with contextlib.suppress(ImportError, OSError):
            import fcntl

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(self._fd)
        self._fd = None


# ---------------------------------------------------------------------------
# Varrimento de /proc
# ---------------------------------------------------------------------------


def _read_cmdline(pid: int, proc_root: Path) -> list[str]:
    try:
        raw = (proc_root / str(pid) / "cmdline").read_bytes()
    except OSError:
        return []
    return [part for part in raw.split(b"\x00") if part]  # type: ignore[misc]


def _decode_cmdline(pid: int, proc_root: Path) -> list[str]:
    return [p.decode("utf-8", "replace") if isinstance(p, bytes) else str(p) for p in _read_cmdline(pid, proc_root)]


def _ppid(pid: int, proc_root: Path) -> int | None:
    try:
        for line in (proc_root / str(pid) / "status").read_text().splitlines():
            if line.startswith("PPid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def classify_cmdline(parts: Sequence[str]) -> tuple[str, str | None] | None:
    """Classifica um cmdline como ``(kind, backend)`` da família UMS, ou ``None``.

    Worker: ``<python> -m <tool> serve --ums-worker`` → ``("worker", tool)``.
    Supervisor: ``-m modelserver start`` / ``aigamekit-model-server start`` / ``ums start``.
    """
    if not parts:
        return None
    if _WORKER_FLAG in parts:
        backend = None
        if "-m" in parts:
            idx = parts.index("-m")
            if idx + 1 < len(parts):
                backend = parts[idx + 1]
        return KIND_WORKER, backend
    joined = " ".join(parts)
    if "start" not in parts:
        return None
    if "-m" in parts:
        idx = parts.index("-m")
        if idx + 1 < len(parts) and parts[idx + 1] in ("modelserver", "aigamekit_model_server"):
            return KIND_SUPERVISOR, None
    exe = Path(parts[0]).name
    if exe in ("aigamekit-model-server", "ums"):
        return KIND_SUPERVISOR, None
    if any(tok in joined for tok in _SUPERVISOR_TOKENS) and exe.startswith("python"):
        # ex.: python /path/aigamekit-model-server start
        return KIND_SUPERVISOR, None
    return None


def list_processes(*, proc_root: Path | str = Path("/proc")) -> list[UmsProcess]:
    """Lista todos os processos da família UMS (supervisores + workers)."""
    root = Path(proc_root)
    if not root.is_dir():
        return []
    out: list[UmsProcess] = []
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        parts = _decode_cmdline(pid, root)
        kind_backend = classify_cmdline(parts)
        if kind_backend is None:
            continue
        kind, backend = kind_backend
        out.append(UmsProcess(pid=pid, kind=kind, backend=backend, cmdline=" ".join(parts)))
    return sorted(out, key=lambda p: p.pid)


def descendants(pid: int, *, proc_root: Path | str = Path("/proc")) -> set[int]:
    """PIDs descendentes de ``pid`` (transitivo, via ``PPid`` em /proc)."""
    root = Path(proc_root)
    if not root.is_dir():
        return set()
    parent_of: dict[int, int] = {}
    for entry in root.iterdir():
        if not entry.name.isdigit():
            continue
        child = int(entry.name)
        parent = _ppid(child, root)
        if parent is not None:
            parent_of[child] = parent
    out: set[int] = set()
    for child in parent_of:
        cursor: int | None = child
        seen: set[int] = set()
        while cursor is not None and cursor > 1 and cursor not in seen:
            seen.add(cursor)
            parent = parent_of.get(cursor)
            if parent == pid:
                out.add(child)
                break
            cursor = parent
    return out


def ancestors(pid: int, *, proc_root: Path | str = Path("/proc")) -> set[int]:
    """PIDs ascendentes de ``pid`` (até ``init``).

    Lançadores como ``timeout 900 python -m modelserver start``, ``nohup`` ou um
    ``bash -c`` carregam a nossa cmdline no argv deles e seriam classificados
    como supervisores — matá-los mataria quem nos lançou.
    """
    root = Path(proc_root)
    out: set[int] = set()
    cursor = _ppid(int(pid), root)
    while cursor is not None and cursor > 1 and cursor not in out:
        out.add(cursor)
        cursor = _ppid(cursor, root)
    return out


def find_strays(
    *,
    self_pid: int | None = None,
    keep: Iterable[int] = (),
    proc_root: Path | str = Path("/proc"),
    with_vram: bool = True,
) -> list[UmsProcess]:
    """Processos UMS que não somos nós, nem descendentes, nem ascendentes.

    Chamado pelo supervisor **depois** de ganhar o :class:`SingletonLock`: nesse
    ponto qualquer outro supervisor/worker é lixo de uma run anterior.
    """
    me = os.getpid() if self_pid is None else int(self_pid)
    protected = (
        {me, *(int(p) for p in keep)} | descendants(me, proc_root=proc_root) | ancestors(me, proc_root=proc_root)
    )
    strays = [p for p in list_processes(proc_root=proc_root) if p.pid not in protected]
    if with_vram and strays:
        strays = annotate_vram(strays)
    return strays


# ---------------------------------------------------------------------------
# VRAM por PID
# ---------------------------------------------------------------------------


def gpu_vram_by_pid() -> dict[int, int]:
    """``{pid: MiB}`` dos processos compute na GPU (NVML → nvidia-smi)."""
    try:
        from aigamekit_shared.gpu import list_nvidia_compute_apps

        apps = list_nvidia_compute_apps()
    except Exception:
        return {}
    out: dict[int, int] = {}
    for pid, _name, used in apps or []:
        if used is None:
            continue
        out[int(pid)] = out.get(int(pid), 0) + int(used)
    return out


def annotate_vram(procs: Sequence[UmsProcess]) -> list[UmsProcess]:
    """Preenche ``vram_mib`` de cada processo com a leitura da GPU."""
    by_pid = gpu_vram_by_pid()
    return [replace(p, vram_mib=by_pid.get(p.pid)) for p in procs]


def stray_report(
    *,
    self_pid: int | None = None,
    keep: Iterable[int] = (),
    proc_root: Path | str = Path("/proc"),
) -> dict[str, Any]:
    """Sumário de órfãos + VRAM que eles seguram (para ``status`` / ``doctor``)."""
    strays = find_strays(self_pid=self_pid, keep=keep, proc_root=proc_root)
    total = sum(p.vram_mib or 0 for p in strays)
    return {
        "count": len(strays),
        "vram_mib": total,
        "processes": [
            {
                "pid": p.pid,
                "kind": p.kind,
                "backend": p.backend,
                "vram_mib": p.vram_mib,
                "cmdline": p.cmdline,
            }
            for p in strays
        ],
    }


# ---------------------------------------------------------------------------
# Reap
# ---------------------------------------------------------------------------


def pid_alive(pid: int, *, kill_fn: Callable[[int, int], None] = os.kill) -> bool:
    """True se o PID existe (``signal 0``)."""
    try:
        kill_fn(pid, 0)
    except OSError as exc:
        return exc.errno == errno.EPERM
    return True


def reap(
    procs: Sequence[UmsProcess],
    *,
    term_wait_sec: float = 3.0,
    kill_fn: Callable[[int, int], None] = os.kill,
    sleep_fn: Callable[[float], None] = time.sleep,
    poll_sec: float = 0.2,
) -> list[dict[str, Any]]:
    """Termina os processos (SIGTERM → SIGKILL). Devolve o que foi feito por PID."""
    results: list[dict[str, Any]] = []
    pending: list[UmsProcess] = []
    for proc in procs:
        try:
            kill_fn(proc.pid, signal.SIGTERM)
            pending.append(proc)
        except OSError as exc:
            results.append({"pid": proc.pid, "kind": proc.kind, "signal": "none", "error": str(exc)})
    deadline = term_wait_sec
    while pending and deadline > 0:
        sleep_fn(poll_sec)
        deadline -= poll_sec
        still = [p for p in pending if pid_alive(p.pid, kill_fn=kill_fn)]
        for gone in [p for p in pending if p not in still]:
            results.append({"pid": gone.pid, "kind": gone.kind, "signal": "SIGTERM", "vram_mib": gone.vram_mib})
        pending = still
    for proc in pending:
        with contextlib.suppress(OSError):
            kill_fn(proc.pid, signal.SIGKILL)
        results.append({"pid": proc.pid, "kind": proc.kind, "signal": "SIGKILL", "vram_mib": proc.vram_mib})
    return results


def reap_strays(
    *,
    self_pid: int | None = None,
    keep: Iterable[int] = (),
    proc_root: Path | str = Path("/proc"),
    dry_run: bool = False,
    term_wait_sec: float = 3.0,
) -> dict[str, Any]:
    """Encontra e mata órfãos UMS; devolve sumário para logs/CLI/protocolo."""
    strays = find_strays(self_pid=self_pid, keep=keep, proc_root=proc_root)
    freed = sum(p.vram_mib or 0 for p in strays)
    if not strays:
        return {"reaped": [], "count": 0, "vram_mib_freed": 0, "dry_run": dry_run}
    if dry_run:
        return {
            "reaped": [],
            "count": len(strays),
            "vram_mib_freed": freed,
            "dry_run": True,
            "would_reap": [p.describe() for p in strays],
        }
    for proc in strays:
        _logger.warn(f"[UMS] reap órfão: {proc.describe()} — {proc.cmdline[:120]}")
    results = reap(strays, term_wait_sec=term_wait_sec)
    return {
        "reaped": results,
        "count": len(results),
        "vram_mib_freed": freed,
        "dry_run": False,
    }
