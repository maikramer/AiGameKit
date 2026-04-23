"""Proteção de batch: lock exclusivo e verificação de VRAM (evita OOM por corridas paralelas)."""

from __future__ import annotations

import os
import sys
from collections.abc import Generator
from contextlib import contextmanager, suppress
from pathlib import Path

import click

from gamedev_shared.env import subprocess_gpu_env  # noqa: F401
from gamedev_shared.gpu import detect_gpu_ids, query_gpu_free_mib  # noqa: F401


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_lock_pid(path: Path) -> int | None:
    try:
        t = path.read_text(encoding="utf-8").strip()
        return int(t.split()[0])
    except (OSError, ValueError):
        return None


@contextmanager
def batch_directory_lock(
    manifest_path: Path,
    *,
    skip: bool = False,
) -> Generator[None, None, None]:
    """
    Um único `gameassets batch` por pasta do manifest (ficheiro `.gameassets_batch.lock`).

    Evita dois batches em paralelo na mesma pasta (disputa de VRAM com text2d/text3d).
    Se o PID no lock já não existir, o lock é recuperado automaticamente.
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
    fd: int | None = None
    for _attempt in range(2):
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.ftruncate(fd, 0)
            os.write(fd, str(os.getpid()).encode("ascii"))
            os.fsync(fd)
            break
        except BlockingIOError:
            os.close(fd)
            fd = None
            old = _read_lock_pid(lock_path)
            if old is not None and not _pid_alive(old):
                with suppress(OSError):
                    lock_path.unlink(missing_ok=True)
                continue
            hint = f" PID {old}" if old is not None else ""
            raise click.ClickException(
                f"Já existe um batch a correr nesta pasta (lock: {lock_path}{hint}).\n"
                "Termina o outro processo ou usa [bold]--skip-batch-lock[/bold] se tiveres a certeza."
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
            if lock_path.is_file():
                lp = _read_lock_pid(lock_path)
                if lp == os.getpid():
                    lock_path.unlink(missing_ok=True)
        except OSError:
            pass
