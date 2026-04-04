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


def clear_cuda_memory() -> None:
    """Força GC e esvazia cache CUDA — útil entre fases pesadas."""
    torch = _torch()
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB = 300


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
    max_used_mib: int = DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB,
    allow_shared: bool = False,
) -> None:
    """Garante GPU quase livre antes de carregar modelos grandes.

    Raises:
        RuntimeError: VRAM já ocupada acima de ``max_used_mib``.
    """
    if allow_shared:
        return
    used = gpu_bytes_in_use(device)
    if used is None:
        return
    max_bytes = max_used_mib * 1024 * 1024
    if used > max_bytes:
        mib = used / (1024 * 1024)
        raise RuntimeError(
            f"GPU com ~{mib:.0f} MiB já em uso (limite: {max_used_mib} MiB). "
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


def kill_gpu_compute_processes_aggressive(
    *,
    exclude_pid: int,
    term_wait_seconds: float = 2.0,
) -> list[str]:
    """SIGTERM + SIGKILL em processos GPU (excluindo PID actual e protegidos).

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
