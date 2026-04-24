"""Utilitários GPU, VRAM e memória — superset de Text2D + Text3D.

Todas as funções que dependem de ``torch`` fazem import lazy para que o
módulo possa ser importado sem torch instalado (falha apenas ao chamar
funções GPU sem o extra ``[gpu]``).
"""

from __future__ import annotations

import contextlib
import gc
import os
import shutil
import signal
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any


def _torch() -> types.ModuleType:
    """Import lazy de torch — falha clara se não instalado."""
    try:
        import torch

        return torch  # type: ignore[no-any-return]
    except ImportError:
        raise ImportError("torch não está instalado. Instale com: pip install gamedev-shared[gpu]") from None


# ---------------------------------------------------------------------------
# Formatação
# ---------------------------------------------------------------------------


def format_bytes(bytes_val: int | float) -> str:
    """Formata bytes para representação legível (ex: ``4.5 GB``)."""
    val = float(bytes_val)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024.0:
            return f"{val:.1f} {unit}"
        val /= 1024.0
    return f"{val:.1f} PB"


# ---------------------------------------------------------------------------
# Informações do sistema / GPU
# ---------------------------------------------------------------------------


def get_gpu_info() -> list[dict[str, Any]]:
    """Lista GPUs disponíveis com VRAM, nome e capacidade de compute."""
    torch = _torch()
    gpus: list[dict[str, Any]] = []
    if not torch.cuda.is_available():
        return gpus

    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        try:
            free_memory = torch.cuda.mem_get_info(i)[0] if hasattr(torch.cuda, "mem_get_info") else 0
            total_memory = props.total_memory
        except Exception:
            free_memory = 0
            total_memory = props.total_memory

        gpus.append(
            {
                "id": i,
                "name": props.name,
                "total_memory": total_memory,
                "free_memory": free_memory,
                "compute_capability": f"{props.major}.{props.minor}",
                "multi_processor_count": props.multi_processor_count,
            }
        )

    return gpus


def get_system_info() -> dict[str, Any]:
    """Python, PyTorch, CUDA e GPUs."""
    torch = _torch()
    info: dict[str, Any] = {
        "python_version": (f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"),
        "torch_version": torch.__version__,
        "cuda_available": torch.cuda.is_available(),
    }
    if torch.cuda.is_available():
        info["cuda_version"] = torch.version.cuda
        info["gpus"] = get_gpu_info()
    return info


def check_gpu_compatibility(min_vram_gb: float = 6.0) -> tuple[bool, str]:
    """Verifica VRAM mínima.

    Returns:
        ``(compatível, mensagem)``
    """
    torch = _torch()
    if not torch.cuda.is_available():
        return False, "CUDA não disponível. Usando CPU (mais lento)."

    gpus = get_gpu_info()
    for gpu in gpus:
        vram_gb = gpu["total_memory"] / (1024**3)
        if vram_gb >= min_vram_gb:
            return True, f"GPU {gpu['name']} com {vram_gb:.1f} GB (compatível)."

    if gpus:
        max_vram = max(g["total_memory"] for g in gpus) / (1024**3)
        return (
            False,
            f"VRAM pode ser insuficiente (máx. {max_vram:.1f} GB). Use --low-vram.",
        )

    return False, "Nenhuma GPU detectada."


def estimate_vram_requirement(
    frame_size: int = 256,
    batch_size: int = 1,
    model_size_gb: float = 4.9,
) -> float:
    """Heurística de VRAM necessária (GB) para geração."""
    size_multiplier = (frame_size / 256) ** 2
    return model_size_gb * size_multiplier * batch_size * 1.2


# ---------------------------------------------------------------------------
# Gestão de memória CUDA
# ---------------------------------------------------------------------------


def clear_cuda_memory(devices: list[int] | None = None) -> None:
    """Força GC e esvazia cache CUDA — útil entre fases pesadas.

    Args:
        devices: Lista de índices GPU para limpar. Se ``None``, limpa apenas
            o dispositivo atual (comportamento original).
    """
    torch = _torch()
    gc.collect()
    if not torch.cuda.is_available():
        return
    if devices is None:
        torch.cuda.empty_cache()
        return
    original = torch.cuda.current_device()
    for d in devices:
        torch.cuda.set_device(d)
        torch.cuda.empty_cache()
    torch.cuda.set_device(original)


DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT = 0.15


def gpu_total_mib(device: int = 0) -> int | None:
    """Total VRAM (MiB) on *device*, or ``None`` if unavailable."""
    torch = _torch()
    if not torch.cuda.is_available():
        return None
    if not hasattr(torch.cuda, "mem_get_info"):
        props = torch.cuda.get_device_properties(device)
        return int(props.total_memory // (1024 * 1024))
    _free, total = torch.cuda.mem_get_info(device)
    return int(total // (1024 * 1024))


def gpu_bytes_in_use(device: int = 0) -> int | None:
    """Bytes de VRAM em uso (total - livre).

    Devolve ``None`` se ``mem_get_info`` não existir (PyTorch antigo).
    """
    torch = _torch()
    if not torch.cuda.is_available():
        return 0
    if not hasattr(torch.cuda, "mem_get_info"):
        return None
    free, total = torch.cuda.mem_get_info(device)
    return int(total - free)


def enforce_exclusive_gpu(
    *,
    device: int = 0,
    max_used_pct: float = DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT,
    allow_shared: bool = False,
) -> None:
    """Garante GPU quase livre antes de carregar modelos grandes.

    Uses a **percentage of total VRAM** as the threshold (default 15 %).
    If occupied VRAM is below the threshold, a warning is printed but
    execution proceeds.  Above the threshold a :class:`RuntimeError` is
    raised so the caller can decide to kill competing processes.

    Args:
        device: CUDA device index.
        max_used_pct: Fraction of total VRAM (0.0-1.0) that is the
            "occupied" threshold.  Default: 0.15 (15 %).
        allow_shared: Skip the check entirely.

    Raises:
        RuntimeError: VRAM ocupação acima do limiar.
    """
    if allow_shared:
        return
    used = gpu_bytes_in_use(device)
    if used is None:
        return
    total_mib = gpu_total_mib(device)
    used_mib = used / (1024 * 1024)
    threshold_mib = total_mib * max_used_pct if total_mib is not None else 1024
    if used_mib > threshold_mib:
        pct = (used_mib / total_mib * 100) if total_mib else 0
        raise RuntimeError(
            f"GPU com ~{used_mib:.0f} MiB já em uso ({pct:.0f}% de {total_mib} MiB; "
            f"limite: {max_used_pct:.0%}). "
            "Fecha outras aplicações ou usa --allow-shared-gpu."
        )


# ---------------------------------------------------------------------------
# nvidia-smi: listar e matar processos GPU
# ---------------------------------------------------------------------------

_GPU_KILL_PROTECTED_NAMES = frozenset(
    {
        "xorg",
        "x",
        "gnome-shell",
        "plasmashell",
        "kwin",
        "kwin_x11",
        "kwin_wayland",
        "sddm",
        "gdm",
        "gdm-wayland",
        "dbus-daemon",
        "pipewire",
        "wireplumber",
        "nvidia-egl",
        "nvidia-persistenced",
        "nvidia-gridd",
        "nvidia-modeset",
        "gsd-xsettings",
        "mutter",
        "cinnamon",
        "xfwm4",
        "budgie-wm",
        "muffin",
    }
)


def _gpu_kill_basename(proc_name: str) -> str:
    s = proc_name.strip()
    if not s:
        return ""
    return Path(s.split()[0]).name.lower()


def _is_protected_gpu_process(proc_name: str) -> bool:
    b = _gpu_kill_basename(proc_name)
    if b in _GPU_KILL_PROTECTED_NAMES:
        return True
    return "xwayland" in proc_name.lower()


def _current_uid() -> int:
    return os.getuid() if hasattr(os, "getuid") else os.getpid()


def _process_uid(pid: int) -> int | None:
    """UID do processo *pid*, ou ``None`` se não conseguir determinar."""
    try:
        status = Path(f"/proc/{pid}/status").read_text()
        for line in status.splitlines():
            if line.startswith("Uid:"):
                return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    if sys.platform == "win32":
        try:
            r = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return None
        except (OSError, subprocess.TimeoutExpired):
            pass
    return None


def _is_user_process(pid: int) -> bool:
    uid = _process_uid(pid)
    if uid is None:
        return False
    return uid == _current_uid()


def list_nvidia_compute_apps() -> list[tuple[int, str, int | None]]:
    """Lista processos compute (nvidia-smi): ``(pid, name, used_mib|None)``."""
    if not shutil.which("nvidia-smi"):
        return []
    r = subprocess.run(
        [
            "nvidia-smi",
            "--query-compute-apps=pid,process_name,used_gpu_memory",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if r.returncode != 0 or not (r.stdout or "").strip():
        return []
    out: list[tuple[int, str, int | None]] = []
    for line in r.stdout.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        name = parts[1]
        mib: int | None = None
        if len(parts) >= 3 and parts[2] and parts[2].upper() not in ("N/A", "[N/A]"):
            with contextlib.suppress(ValueError):
                mib = int(float(parts[2].replace(" MiB", "").strip()))
        out.append((pid, name, mib))
    return out


def warn_if_vram_occupied(threshold_mib: int = 1024) -> list[str]:
    """Warn if significant GPU VRAM is in use by other processes.

    Checks ``nvidia-smi`` for compute processes using more than
    *threshold_mib* MiB.  Prints a yellow warning via :mod:`rich` if
    any are found, but **never blocks or kills** — the caller should
    proceed regardless.

    Args:
        threshold_mib: Minimum VRAM usage (MiB) per process to trigger warning.

    Returns:
        List of process descriptions (name, PID, VRAM) for testing.
    """
    apps = list_nvidia_compute_apps()
    big: list[str] = []
    total_mib = 0
    for pid, name, mib in apps:
        if mib is not None and mib > threshold_mib:
            big.append(f"{name} (PID {pid}): {mib} MiB")
            total_mib += mib
    if big:
        try:
            from rich.console import Console

            c = Console()
        except ImportError:
            c = None  # type: ignore[assignment]
        msg = (
            f"\u26a0 VRAM preflight: {len(big)} GPU process(es) detected using {total_mib} MiB total:\n"
            + "\n".join(f"  - {line}" for line in big)
            + "\nProceeding anyway \u2014 if OOM occurs, close other GPU apps."
        )
        if c is not None:
            c.print(f"[yellow]{msg}[/yellow]")
        else:
            print(msg)
    return big


def kill_gpu_compute_processes_aggressive(
    *,
    exclude_pid: int,
    term_wait_seconds: float = 2.0,
) -> list[str]:
    """SIGTERM + SIGKILL em processos GPU do utilizador actual (excluindo PID actual e protegidos).

    Only targets processes owned by the **current user** — system / root /
    other-user processes are never touched.

    Returns:
        Linhas de log legíveis.
    """
    logs: list[str] = []
    apps = list_nvidia_compute_apps()
    targets: list[tuple[int, str]] = []
    for pid, name, mib in apps:
        if pid == exclude_pid:
            continue
        if _is_protected_gpu_process(name):
            logs.append(f"[ignorado] PID {pid} ({name}) — protegido")
            continue
        if not _is_user_process(pid):
            uid_info = _process_uid(pid)
            logs.append(f"[ignorado] PID {pid} ({name}) — UID {uid_info} ≠ actual")
            continue
        extra = f" ~{mib} MiB" if mib is not None else ""
        targets.append((pid, name))
        logs.append(f"[alvo] PID {pid} ({name}){extra}")

    if not targets:
        if not apps:
            logs.append("nvidia-smi não listou compute apps.")
        else:
            logs.append("Sem alvos para terminar.")
        return logs

    for pid, name in targets:
        try:
            os.kill(pid, signal.SIGTERM)
            logs.append(f"SIGTERM → PID {pid} ({name})")
        except ProcessLookupError:
            logs.append(f"PID {pid} já terminou")
        except PermissionError:
            logs.append(f"PID {pid} ({name}): sem permissão (SIGTERM)")
        except OSError as e:
            logs.append(f"PID {pid}: {e}")

    time.sleep(term_wait_seconds)

    sigkill = getattr(signal, "SIGKILL", None)

    for pid, name in targets:
        try:
            os.kill(pid, 0)
        except (ProcessLookupError, OSError):
            continue
        if sigkill is None:
            logs.append(f"PID {pid} ({name}): SIGKILL indisponível neste SO; ignorado após SIGTERM")
            continue
        try:
            os.kill(pid, sigkill)
            logs.append(f"SIGKILL → PID {pid} ({name})")
        except ProcessLookupError:
            pass
        except PermissionError:
            logs.append(f"PID {pid} ({name}): sem permissão (SIGKILL)")

    return logs


# ---------------------------------------------------------------------------
# nvidia-smi: VRAM livre e detecção de GPUs (sem torch)
# ---------------------------------------------------------------------------


def query_gpu_free_mib() -> int | None:
    """VRAM livre na GPU 0 (MiB), ou ``None`` se nvidia-smi não existir / falhar."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        line = (r.stdout or "").strip().splitlines()[0].strip()
        return int(float(line))
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        return None


def detect_gpu_ids() -> list[int] | None:
    """Detecta GPUs disponíveis via nvidia-smi. Retorna lista de IDs ou ``None``."""
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=index",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        ids: list[int] = []
        for line in (r.stdout or "").strip().splitlines():
            line = line.strip()
            if line:
                ids.append(int(line))
        return ids if ids else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
