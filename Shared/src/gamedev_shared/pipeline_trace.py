"""Trace JSONL detalhado do pipeline (shape→paint→lod→…).

Env:
    ``GAMEDEV_PIPELINE_TRACE`` — ``0`` desliga; default ligado.
    ``GAMEDEV_PIPELINE_TRACE_LOG`` — caminho do JSONL (default
    ``~/.cache/gamedev/logs/pipeline-trace-YYYY-MM-DD.jsonl``).
    ``GAMEDEV_PIPELINE_TRACE_ASSET`` — se definido, só regista eventos cujo
    ``asset``/``id`` bate (substring).
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_session_id: str | None = None


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _truthy("GAMEDEV_PIPELINE_TRACE", default=True)


def session_id() -> str:
    global _session_id
    if _session_id is None:
        _session_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    return _session_id


def default_log_path() -> Path:
    override = os.environ.get("GAMEDEV_PIPELINE_TRACE_LOG", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    base = os.environ.get("GAMEDEV_LOG_DIR", "").strip()
    root = Path(base).expanduser() if base else Path.home() / ".cache" / "gamedev" / "logs"
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return root / f"pipeline-trace-{day}.jsonl"


def trace_event(event: str, **fields: Any) -> Path | None:
    """Anexa um evento detalhado ao JSONL de trace.

    Returns:
        Path do ficheiro, ou ``None`` se desligado / filtrado.
    """
    if not enabled():
        return None

    asset_filter = os.environ.get("GAMEDEV_PIPELINE_TRACE_ASSET", "").strip().lower()
    if asset_filter:
        blob = " ".join(str(fields.get(k, "")) for k in ("asset", "id", "row_id", "path", "output")).lower()
        if asset_filter not in blob:
            return None

    rec: dict[str, Any] = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "mono_s": round(time.monotonic(), 3),
        "pid": os.getpid(),
        "session": session_id(),
        "event": event,
        **fields,
    }

    # Sanitize non-JSON values lightly
    def _safe(v: Any) -> Any:
        if isinstance(v, (str, int, float, bool)) or v is None:
            return v
        if isinstance(v, Path):
            return str(v)
        if isinstance(v, dict):
            return {str(k): _safe(x) for k, x in v.items()}
        if isinstance(v, (list, tuple)):
            return [_safe(x) for x in v]
        return str(v)

    rec = {k: _safe(v) for k, v in rec.items()}
    path = default_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _lock, path.open("a", encoding="utf-8") as fh:
        fh.write(line)
    return path


def trace_exception(event: str, exc: BaseException, **fields: Any) -> Path | None:
    """Regista excepção com traceback completo."""
    return trace_event(
        event,
        error=str(exc),
        error_type=type(exc).__name__,
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        **fields,
    )


def trace_stage(
    asset: str,
    stage: str,
    *,
    status: str,
    **fields: Any,
) -> Path | None:
    """Atalho para stages do master pipeline (shape/clean/paint/lod/…)."""
    return trace_event(
        "pipeline_stage",
        asset=asset,
        stage=stage,
        status=status,
        **fields,
    )
