"""Utilitários GPU, VRAM e memória — superset de Text2D + Text3D.

Todas as funções que dependem de ``torch`` fazem import lazy para que o
módulo possa ser importado sem torch instalado (falha apenas ao chamar
funções GPU sem o extra ``[gpu]``).

Consultas de VRAM / processos preferem **NVML** (``nvidia-ml-py`` / ``pynvml``)
— sem spawn de ``nvidia-smi``. Fallback automático para ``nvidia-smi`` se NVML
não inicializar (CI sem driver, libnvidia-ml ausente).
"""

from __future__ import annotations

import atexit
import contextlib
import gc
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# NVML marca memória desconhecida com este valor (uint64 max).
_NVML_VALUE_NOT_AVAILABLE = 0xFFFFFFFFFFFFFFFF


def _torch() -> types.ModuleType:
    """Import lazy de torch — falha clara se não instalado."""
    try:
        import torch

        return torch  # type: ignore[no-any-return]
    except ImportError:
        raise ImportError("torch não está instalado. Instale com: pip install aigamekit-shared[gpu]") from None


# ---------------------------------------------------------------------------
# NVML (nvidia-ml-py) — preferido para free/used/processos
# ---------------------------------------------------------------------------

_nvml_lock = threading.Lock()
_nvml_inited = False
_nvml_ok = False


def _nvml_shutdown() -> None:
    """Shutdown NVML no exit (idempotente)."""
    global _nvml_ok
    if not _nvml_ok:
        return
    with contextlib.suppress(Exception):
        import pynvml

        pynvml.nvmlShutdown()
    _nvml_ok = False


def _nvml_init() -> bool:
    """Inicializa NVML uma vez. ``True`` se utilizável."""
    global _nvml_inited, _nvml_ok
    with _nvml_lock:
        if _nvml_inited:
            return _nvml_ok
        _nvml_inited = True
        try:
            import pynvml

            pynvml.nvmlInit()
            atexit.register(_nvml_shutdown)
            _nvml_ok = True
        except Exception:
            _nvml_ok = False
        return _nvml_ok


def _nvml_reset_for_tests() -> None:
    """Reset estado NVML (só testes). Não chamar em produção."""
    global _nvml_inited, _nvml_ok
    with _nvml_lock:
        if _nvml_ok:
            with contextlib.suppress(Exception):
                import pynvml

                pynvml.nvmlShutdown()
        _nvml_inited = False
        _nvml_ok = False


def nvml_available() -> bool:
    """``True`` se NVML iniciou com sucesso neste processo."""
    return _nvml_init()


def _nvml_bytes_to_mib(raw: int | None) -> int | None:
    if raw is None:
        return None
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return None
    if n < 0 or n >= _NVML_VALUE_NOT_AVAILABLE:
        return None
    return n // (1024 * 1024)


def _process_basename(pid: int) -> str:
    """Nome curto do processo (psutil → /proc → ``pid:N``)."""
    with contextlib.suppress(Exception):
        import psutil

        return psutil.Process(pid).name() or f"pid:{pid}"
    with contextlib.suppress(OSError, UnicodeError):
        comm = Path(f"/proc/{pid}/comm").read_text().strip()
        if comm:
            return comm
    return f"pid:{pid}"


def _nvml_device_count() -> int | None:
    if not _nvml_init():
        return None
    try:
        import pynvml

        return int(pynvml.nvmlDeviceGetCount())
    except Exception:
        return None


def _nvml_handle(device: int) -> Any | None:
    if not _nvml_init():
        return None
    try:
        import pynvml

        return pynvml.nvmlDeviceGetHandleByIndex(int(device))
    except Exception:
        return None


def _nvml_memory_mib(device: int = 0) -> tuple[int, int, int] | None:
    """``(free_mib, total_mib, used_mib)`` via NVML, ou ``None``."""
    handle = _nvml_handle(device)
    if handle is None:
        return None
    try:
        import pynvml

        info = pynvml.nvmlDeviceGetMemoryInfo(handle)
        free_m = _nvml_bytes_to_mib(int(info.free))
        total_m = _nvml_bytes_to_mib(int(info.total))
        used_m = _nvml_bytes_to_mib(int(info.used))
        if free_m is None or total_m is None or used_m is None:
            return None
        return free_m, total_m, used_m
    except Exception:
        return None


def _nvml_device_name(device: int = 0) -> str | None:
    handle = _nvml_handle(device)
    if handle is None:
        return None
    try:
        import pynvml

        raw = pynvml.nvmlDeviceGetName(handle)
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)
    except Exception:
        return None


def _nvml_compute_apps_for_device(device: int) -> list[tuple[int, str, int | None]] | None:
    """Processos compute numa GPU, ou ``None`` se NVML falhar."""
    handle = _nvml_handle(device)
    if handle is None:
        return None
    try:
        import pynvml

        getter = getattr(pynvml, "nvmlDeviceGetComputeRunningProcesses_v3", None)
        if getter is None:
            getter = getattr(pynvml, "nvmlDeviceGetComputeRunningProcesses", None)
        if getter is None:
            return None
        procs = getter(handle) or []
    except Exception:
        return None

    out: list[tuple[int, str, int | None]] = []
    for proc in procs:
        try:
            pid = int(proc.pid)
        except (AttributeError, TypeError, ValueError):
            continue
        used_raw = getattr(proc, "usedGpuMemory", None)
        out.append((pid, _process_basename(pid), _nvml_bytes_to_mib(used_raw)))
    return out


def _nvml_list_compute_apps() -> list[tuple[int, str, int | None]] | None:
    """Todos os processos compute (todas as GPUs). ``None`` = NVML indisponível."""
    n = _nvml_device_count()
    if n is None:
        return None
    # Dedup por PID (mesmo processo em várias GPUs / MIG).
    by_pid: dict[int, tuple[int, str, int | None]] = {}
    for idx in range(n):
        apps = _nvml_compute_apps_for_device(idx)
        if apps is None:
            return None
        for pid, name, mib in apps:
            prev = by_pid.get(pid)
            if prev is None:
                by_pid[pid] = (pid, name, mib)
            elif mib is not None:
                prev_mib = prev[2] or 0
                by_pid[pid] = (pid, name, prev_mib + mib)
    return list(by_pid.values())


@dataclass(frozen=True)
class GpuSnapshot:
    """Estado resumido duma GPU (NVML ou nvidia-smi)."""

    index: int
    name: str
    free_mib: int
    total_mib: int
    used_mib: int
    source: Literal["nvml", "nvidia-smi"]


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
            f"VRAM pode ser insuficiente (máx. {max_vram:.1f} GB). "
            "O hw-auto engatará o modo memory-efficient automaticamente.",
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

    Também tenta ``ipc_collect`` (quando existe) para libertar blocos partilhados
    que ``empty_cache`` sozinho deixa no processo (VRAM «morta» no UMS).

    Se o processo **nunca inicializou CUDA** (``torch.cuda.is_initialized()``),
    faz apenas ``gc.collect()`` e retorna: não há tensores nem caches para
    limpar, e ``torch.cuda.synchronize()`` chamaria ``_lazy_init()`` — criando
    um contexto CUDA primário (~0.3-1.3 GiB) que **só morre com o processo**.
    Era assim que o supervisor UMS (modo subprocesso, sem tensores próprios)
    ficava a segurar VRAM residual para sempre após o primeiro scrub.

    Args:
        devices: Lista de índices GPU para limpar. Se ``None``, limpa apenas
            o dispositivo atual (comportamento original).
    """
    torch = _torch()
    gc.collect()
    if not torch.cuda.is_available() or not torch.cuda.is_initialized():
        return

    def _scrub_device() -> None:
        torch.cuda.synchronize()
        torch.cuda.empty_cache()
        ipc = getattr(torch.cuda, "ipc_collect", None)
        if callable(ipc):
            with contextlib.suppress(Exception):
                ipc()
        torch.cuda.empty_cache()

    if devices is None:
        _scrub_device()
        return
    original = torch.cuda.current_device()
    for d in devices:
        torch.cuda.set_device(d)
        _scrub_device()
    torch.cuda.set_device(original)


def process_vram_mib(pid: int | None = None) -> int | None:
    """VRAM (MiB) reportada pelo driver para o processo ``pid`` (default: self).

    Soma entradas de :func:`list_nvidia_compute_apps` com o mesmo PID.
    ``None`` se o processo não aparecer na lista (sem contexto CUDA / NVML down).
    """
    target = os.getpid() if pid is None else int(pid)
    total = 0
    found = False
    for app_pid, _name, mib in list_nvidia_compute_apps():
        if app_pid != target or mib is None:
            continue
        total += int(mib)
        found = True
    return total if found else None


def torch_reserved_mib(device: int = 0) -> int | None:
    """MiB reservados pelo allocator PyTorch no dispositivo (fallback sem NVML).

    Retorna ``None`` sem tocar em CUDA quando o processo nunca a inicializou —
    ``memory_reserved()`` faz ``_lazy_init()`` e criaria um contexto primário
    permanente (~0.3-1.3 GiB) só para reportar zero.
    """
    torch = _torch()
    if not torch.cuda.is_available() or not torch.cuda.is_initialized():
        return None
    with contextlib.suppress(Exception):
        return int(torch.cuda.memory_reserved(device) // (1024 * 1024))
    return None


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
# Processos GPU (NVML → nvidia-smi) e kill agressivo
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
                capture_output=True,
                text=True,
                timeout=5,
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


def _smi_list_compute_apps() -> list[tuple[int, str, int | None]]:
    """Fallback ``nvidia-smi --query-compute-apps``."""
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


def list_nvidia_compute_apps() -> list[tuple[int, str, int | None]]:
    """Lista processos compute: ``(pid, name, used_mib|None)``.

    Preferência NVML; fallback ``nvidia-smi``. Lista vazia se ambos falharem.
    """
    nvml_apps = _nvml_list_compute_apps()
    if nvml_apps is not None:
        return nvml_apps
    return _smi_list_compute_apps()


def warn_if_vram_occupied(threshold_mib: int = 1024) -> list[str]:
    """Warn if significant GPU VRAM is in use by other processes.

    Checks NVML (fallback ``nvidia-smi``) for compute processes using more
    than *threshold_mib* MiB.  Prints a yellow warning via :mod:`rich` if
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
            c = None
        tip = ""
        try:
            from .model_server import (
                UMS_DO_NOT_KILL_TIP,
                fetch_ums_queue_snapshot,
                format_ums_holding_summary,
                is_ums_running,
            )

            if is_ums_running():
                snap = fetch_ums_queue_snapshot()
                hold = format_ums_holding_summary(snap) if snap else "UMS ativo"
                tip = f"\nUMS: {hold}\n{UMS_DO_NOT_KILL_TIP}"
        except Exception:
            tip = ""
        msg = (
            f"\u26a0 VRAM preflight: {len(big)} GPU process(es) detected using {total_mib} MiB total:\n"
            + "\n".join(f"  - {line}" for line in big)
            + "\nProceeding anyway \u2014 if OOM occurs, close other GPU apps."
            + tip
        )
        if c is not None:
            c.print(f"[yellow]{msg}[/yellow]")
        else:
            print(msg)
    return big


def kill_gpu_compute_processes_aggressive(
    *,
    exclude_pid: int,
    extra_exclude_pids: set[int] | None = None,
    protect_model_servers: bool = True,
    term_wait_seconds: float = 2.0,
    respect_ums_queue: bool = True,
) -> list[str]:
    """SIGTERM + SIGKILL em processos GPU do utilizador actual (excluindo PID actual e protegidos).

    Only targets processes owned by the **current user** — system / root /
    other-user processes are never touched.

    Args:
        exclude_pid: PID do processo caller (nunca é morto).
        extra_exclude_pids: PIDs adicionais a proteger (além do caller).
        protect_model_servers: Se ``True`` (default), descobre e protege
            automaticamente os PIDs de todos os model servers ativos
            (``aigamekit_shared.model_server.discover_server_pids``).
            Isto evita que o text3d/paint3d matem um model server que está
            a segurar VRAM para outras ferramentas.
        term_wait_seconds: Tempo entre SIGTERM e SIGKILL.
        respect_ums_queue: Se ``True`` (default) e o UMS tem jobs inflight/queued,
            **recusa** matar processos — a fila UMS é a autoridade de VRAM.

    Returns:
        Linhas de log legíveis.
    """
    logs: list[str] = []

    if respect_ums_queue:
        try:
            from .model_server import (
                UMS_DO_NOT_KILL_TIP,
                fetch_ums_queue_snapshot,
                format_ums_holding_summary,
                is_ums_running,
                ums_is_busy,
            )

            # UMS up + snapshot falhou → fail-closed (unknown ≠ idle).
            if is_ums_running():
                snap = fetch_ums_queue_snapshot()
                if snap is None or ums_is_busy(snap):
                    hold = format_ums_holding_summary(snap) if snap else "UMS ativo (snapshot indisponível)"
                    logs.append(f"[recusado] kill GPU — UMS tem jobs / estado incerto ({hold})")
                    logs.append(f"[recusado] {UMS_DO_NOT_KILL_TIP}")
                    return logs
        except Exception:
            # Cliente UMS rebenta: se o socket ainda existir, não matar às cegas.
            with contextlib.suppress(Exception):
                from .model_server import UMS_DO_NOT_KILL_TIP, is_ums_running

                if is_ums_running():
                    logs.append("[recusado] kill GPU — UMS ativo mas cliente falhou")
                    logs.append(f"[recusado] {UMS_DO_NOT_KILL_TIP}")
                    return logs

    # Construir set de PIDs a proteger
    protected_pids = {exclude_pid}
    if extra_exclude_pids:
        protected_pids |= set(extra_exclude_pids)
    if protect_model_servers:
        try:
            from .model_server import discover_server_pids

            server_pids = discover_server_pids()
            if server_pids:
                logs.append(f"[protegido] {len(server_pids)} model server(s): {sorted(server_pids)}")
                protected_pids |= server_pids
        except Exception:
            pass  # model_server indisponível; continuar sem proteção extra

    apps = list_nvidia_compute_apps()
    targets: list[tuple[int, str]] = []
    for pid, name, mib in apps:
        if pid in protected_pids:
            extra = f" ~{mib} MiB" if mib is not None else ""
            logs.append(f"[ignorado] PID {pid} ({name}){extra} — protegido (caller/server)")
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
            logs.append("NVML/nvidia-smi não listou compute apps.")
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
# VRAM livre / detecção de GPUs (NVML → nvidia-smi, sem torch)
# ---------------------------------------------------------------------------


def _smi_query_free_mib(device: int = 0) -> int | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                f"--id={device}",
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


def _smi_detect_gpu_ids() -> list[int] | None:
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


def _smi_gpu_snapshot(device: int = 0) -> GpuSnapshot | None:
    if not shutil.which("nvidia-smi"):
        return None
    try:
        r = subprocess.run(
            [
                "nvidia-smi",
                f"--id={device}",
                "--query-gpu=name,memory.total,memory.free,memory.used",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=8,
        )
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        # Nome pode conter vírgulas — partir só nas 3 últimas colunas numéricas.
        line = (r.stdout or "").strip().splitlines()[0].strip()
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 4:
            return None
        used_m = int(float(parts[-1]))
        free_m = int(float(parts[-2]))
        total_m = int(float(parts[-3]))
        name = ",".join(parts[:-3]).strip() or f"GPU {device}"
        return GpuSnapshot(
            index=int(device),
            name=name,
            free_mib=free_m,
            total_mib=total_m,
            used_mib=used_m,
            source="nvidia-smi",
        )
    except (OSError, ValueError, subprocess.TimeoutExpired, IndexError):
        return None


def query_gpu_free_mib(device: int = 0) -> int | None:
    """VRAM livre numa GPU (MiB), ou ``None`` se NVML e nvidia-smi falharem.

    Args:
        device: Índice da GPU (0 por omissão). Em rigs multi-GPU, especificar o
            dispositivo alvo para que a coordenação de VRAM mire o correto.
    """
    mem = _nvml_memory_mib(device)
    if mem is not None:
        return mem[0]
    return _smi_query_free_mib(device)


def detect_gpu_ids() -> list[int] | None:
    """Detecta GPUs disponíveis (NVML → nvidia-smi). Lista de IDs ou ``None``."""
    n = _nvml_device_count()
    if n is not None and n > 0:
        return list(range(n))
    return _smi_detect_gpu_ids()


def query_gpu_snapshot(device: int = 0) -> GpuSnapshot | None:
    """Nome + memória duma GPU. NVML primeiro, depois ``nvidia-smi``."""
    mem = _nvml_memory_mib(device)
    if mem is not None:
        free_m, total_m, used_m = mem
        name = _nvml_device_name(device) or f"GPU {device}"
        return GpuSnapshot(
            index=int(device),
            name=name,
            free_mib=free_m,
            total_mib=total_m,
            used_mib=used_m,
            source="nvml",
        )
    return _smi_gpu_snapshot(device)


def list_gpu_snapshots() -> list[GpuSnapshot]:
    """Snapshots de todas as GPUs detectadas (lista vazia se nenhuma)."""
    ids = detect_gpu_ids()
    if not ids:
        return []
    out: list[GpuSnapshot] = []
    for i in ids:
        snap = query_gpu_snapshot(i)
        if snap is not None:
            out.append(snap)
    return out


def _parse_nvidia_version_token(text: str) -> str | None:
    """Extrai ``595.84`` / ``595.71.05`` do primeiro token semântico na string."""
    import re

    m = re.search(r"\b(\d{3}(?:\.\d+){1,3})\b", text or "")
    return m.group(1) if m else None


def read_nvidia_kernel_module_version() -> str | None:
    """Versão do módulo NVIDIA carregado (``/proc/driver/nvidia/version``)."""
    proc = Path("/proc/driver/nvidia/version")
    if not proc.is_file():
        return None
    with contextlib.suppress(OSError, UnicodeError):
        return _parse_nvidia_version_token(proc.read_text(errors="replace").splitlines()[0])
    return None


def read_nvidia_userspace_version() -> str | None:
    """Versão das libs userspace (NVML / ``libnvidia-ml.so.*`` / ``nvidia-smi``)."""
    if _nvml_init():
        with contextlib.suppress(Exception):
            import pynvml

            ver = pynvml.nvmlSystemGetDriverVersion()
            if isinstance(ver, bytes):
                ver = ver.decode("utf-8", errors="replace")
            parsed = _parse_nvidia_version_token(str(ver))
            if parsed:
                return parsed
    # Soname tipicamente ``libnvidia-ml.so.595.84`` quando NVML init falha (mismatch).
    for lib_dir in (Path("/usr/lib/x86_64-linux-gnu"), Path("/usr/lib64"), Path("/usr/lib")):
        link = lib_dir / "libnvidia-ml.so.1"
        with contextlib.suppress(OSError):
            target = link.resolve().name if link.exists() else ""
            parsed = _parse_nvidia_version_token(target)
            if parsed:
                return parsed
    smi = shutil.which("nvidia-smi")
    if smi:
        with contextlib.suppress(Exception):
            import subprocess

            out = subprocess.run(
                [smi, "--query-gpu=driver_version", "--format=csv,noheader"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0:
                return _parse_nvidia_version_token(out.stdout.strip().splitlines()[0])
    return None


def check_nvidia_driver_match() -> tuple[bool, str]:
    """Kernel module vs userspace NVML/libs.

    Returns:
        ``(ok, detail)``. ``ok=False`` quando versões diferem (clássico
        ``Driver/library version mismatch`` após apt upgrade sem reboot) —
        PyTorch/UMS falham no ``nvmlInit`` mesmo com CUDA a times funcionar.
    """
    kernel = read_nvidia_kernel_module_version()
    user = read_nvidia_userspace_version()
    if kernel is None and user is None:
        return False, "NVIDIA não detectada (/proc + libnvidia-ml ausentes)"
    if kernel is None:
        return False, f"módulo kernel não carregado; userspace={user}"
    if user is None:
        return False, f"userspace ilegível; kernel={kernel}"
    if kernel == user:
        return True, f"kernel={kernel} userspace={user}"
    # Aceitar patch extra num lado (595.71.05 ≈ 595.71) mas NÃO 595.71 vs 595.84.
    k_mm = ".".join(kernel.split(".")[:2])
    u_mm = ".".join(user.split(".")[:2])
    if k_mm == u_mm:
        return True, f"kernel={kernel} userspace={user}"
    tip = (
        f"MISMATCH kernel={kernel} userspace={user} — reboot (ou reload módulos nvidia) "
        f"após upgrade do driver; PyTorch/UMS falham no nvmlInit até alinhar"
    )
    return False, tip
