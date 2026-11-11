"""Proteção de batch: lock exclusivo e verificação de VRAM (evita OOM por corridas paralelas)."""

from __future__ import annotations

import json
import os
import socket
import sys
import time
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

import click

from aigamekit_shared.env import subprocess_gpu_env  # noqa: F401
from aigamekit_shared.gpu import detect_gpu_ids, query_gpu_free_mib  # noqa: F401


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock_meta(path: Path) -> dict[str, object]:
    """Metadados do lock; aceita o formato legado (só PID) e o JSON novo."""
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return {}
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except ValueError:
        pass
    try:
        return {"pid": int(text.split()[0])}
    except (IndexError, ValueError):
        return {}


def _read_lock_pid(path: Path) -> int | None:
    pid = _read_lock_meta(path).get("pid")
    try:
        return int(pid) if pid is not None else None
    except (TypeError, ValueError):
        return None


def _lock_owner_hint(meta: dict[str, object]) -> str:
    bits = []
    for key, label in (("command", "comando"), ("mode", "gpu"), ("host", "host"), ("pid", "PID")):
        value = meta.get(key)
        if value not in (None, ""):
            bits.append(f"{label}={value}")
    manifest = meta.get("manifest")
    if manifest:
        bits.append(f"manifest={manifest}")
    return ", ".join(bits)


@contextmanager
def batch_directory_lock(
    manifest_path: Path,
    *,
    skip: bool = False,
    command: str = "batch",
    mode: str = "",
) -> Generator[None, None, None]:
    """
    Um único `gameassets batch|resume` por pasta do manifest (``.gameassets_batch.lock``).

    O lock é por pasta (não por manifesto) porque o ``_work_dir`` e os
    ``_intermediate/`` são partilhados pelos manifestos da mesma pasta. Cobre
    também o mesmo manifesto lançado uma vez local e outra remoto: em ambos os casos
    quem corre é um cliente local com o mesmo output_dir.

    O ficheiro guarda JSON com PID/host/comando/modo/manifesto. O lock só é
    recuperado automaticamente se o dono for **deste host** e o PID já não
    existir — um PID morto noutra máquina (FS partilhado) não prova que o
    workload remoto acabou. Se o PID no lock já não existir, o lock é
    recuperado automaticamente.
    """
    if skip or sys.platform == "win32":
        yield
        return
    try:
        import fcntl
    except ImportError:
        yield
        return

    lock_path = manifest_path.resolve().parent / ".gameassets_batch.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    host = socket.gethostname()
    meta = {
        "pid": os.getpid(),
        "host": host,
        "command": command,
        "mode": mode or os.environ.get("VRAMD_GPU_MODE", "auto"),
        "manifest": str(manifest_path.resolve()),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    fd: int | None = None
    for _attempt in range(2):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
            os.fsync(fd)
            break
        except BlockingIOError:
            os.close(fd)
            fd = None
            old_meta = _read_lock_meta(lock_path)
            old = _read_lock_pid(lock_path)
            same_host = str(old_meta.get("host") or host) == host
            if old is not None and same_host and not _pid_alive(old):
                with suppress(OSError):
                    lock_path.unlink(missing_ok=True)
                continue
            hint = _lock_owner_hint(old_meta)
            detail = f" ({hint})" if hint else (f" PID {old}" if old is not None else "")
            raise click.ClickException(
                f"Já existe um batch/resume a correr nesta pasta (lock: {lock_path}{detail}).\n"
                "Termina o outro processo (local ou remoto) ou usa "
                "[bold]--skip-batch-lock[/bold] se tiveres a certeza."
            ) from None
    else:
        raise click.ClickException(f"Não foi possível obter o lock: {lock_path}")

    try:
        yield
    finally:
        if fd is not None:
            with suppress(OSError):
                fcntl.flock(fd, fcntl.LOCK_UN)
            with suppress(OSError):
                os.close(fd)
        try:
            if lock_path.is_file() and _read_lock_pid(lock_path) == os.getpid():
                lock_path.unlink(missing_ok=True)
        except OSError:
            pass
