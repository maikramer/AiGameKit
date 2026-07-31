"""Snapshots de CPU e RAM do processo (sem importar torch no nível do módulo)."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from types import ModuleType
from typing import Any

_resource_mod: ModuleType | None = None
try:
    import resource

    _resource_mod = resource
except ModuleNotFoundError:
    pass  # Unix-only stdlib module (missing on Windows)

try:
    import psutil

    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


def _linux_proc_rss_bytes() -> int | None:
    """Lê VmRSS de /proc/self/status (Linux) em bytes; None se indisponível."""
    if sys.platform != "linux":
        return None
    try:
        with open("/proc/self/status", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # VmRSS:     12345 kB
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) * 1024
    except OSError:
        return None
    return None


@dataclass
class ResourceSnapshot:
    """Estado de recursos do processo num instante."""

    cpu_user_s: float | None
    cpu_system_s: float | None
    rss_bytes: int | None
    source: str  # "psutil" | "rusage" | "linux_proc" | "none"

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"resource_source": self.source}
        if self.cpu_user_s is not None:
            d["cpu_user_s"] = self.cpu_user_s
        if self.cpu_system_s is not None:
            d["cpu_system_s"] = self.cpu_system_s
        if self.rss_bytes is not None:
            d["rss_bytes"] = self.rss_bytes
            d["rss_mb"] = round(self.rss_bytes / (1024 * 1024), 3)
        return d


def resource_snapshot() -> ResourceSnapshot:
    """
    Captura tempo de CPU (user/system acumulado) e RSS aproximado do processo.

    Ordem de preferência: ``psutil`` → ``resource.getrusage`` + ``/proc`` (Linux).
    """
    if _HAS_PSUTIL:
        try:
            p = psutil.Process(os.getpid())
            t = p.cpu_times()
            rss = p.memory_info().rss
            return ResourceSnapshot(
                cpu_user_s=t.user,
                cpu_system_s=t.system,
                rss_bytes=rss,
                source="psutil",
            )
        except Exception:
            pass

    if _resource_mod is not None:
        try:
            # typeshed omits Unix-only symbols on some platforms (e.g. Windows).
            getrusage = getattr(_resource_mod, "getrusage", None)
            rusage_self = getattr(_resource_mod, "RUSAGE_SELF", None)
            if getrusage is not None and rusage_self is not None:
                ru = getrusage(rusage_self)
                cpu_u = float(ru.ru_utime)
                cpu_s = float(ru.ru_stime)
                # ru_maxrss: macOS bytes, Linux/Unix kilobytes — não é RSS corrente; usar só CPU
                rss = _linux_proc_rss_bytes()
                return ResourceSnapshot(
                    cpu_user_s=cpu_u,
                    cpu_system_s=cpu_s,
                    rss_bytes=rss,
                    source="rusage" if rss is None else "linux_proc",
                )
        except Exception:
            pass

    rss = _linux_proc_rss_bytes()
    return ResourceSnapshot(
        cpu_user_s=None,
        cpu_system_s=None,
        rss_bytes=rss,
        source="linux_proc" if rss is not None else "none",
    )


def subtract_cpu(
    before: ResourceSnapshot,
    after: ResourceSnapshot,
) -> tuple[float | None, float | None]:
    """Devolve (delta_user_s, delta_system_s)."""
    if before.cpu_user_s is None or after.cpu_user_s is None:
        return None, None
    if before.cpu_system_s is None or after.cpu_system_s is None:
        return None, None
    return (
        after.cpu_user_s - before.cpu_user_s,
        after.cpu_system_s - before.cpu_system_s,
    )
