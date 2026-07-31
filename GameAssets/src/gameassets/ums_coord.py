"""Coordenação GameAssets ↔ UMS (preload, fila submit/wait, defer master)."""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Env keys propagados aos subprocessos GPU (além de AIGAMEKIT_UMS_PRIORITY).
UMS_CHILD_ENV_KEYS: tuple[str, ...] = (
    "AIGAMEKIT_UMS_PRIORITY",
    "AIGAMEKIT_UMS_AUTO_START",
    "AIGAMEKIT_UMS_DEBUG",
    "AIGAMEKIT_UMS_STREAM",
    "AIGAMEKIT_UMS_MAX_QUEUE_DEPTH",
    "AIGAMEKIT_UMS_MAX_AFFINITY_CUTS",
    "AIGAMEKIT_UMS_STARVATION_TIMEOUT_SEC",
    "AIGAMEKIT_UMS_MAX_INFLIGHT",
    "AIGAMEKIT_ALLOW_LEGACY_SERVER",
)

# Sentinel: caller deve usar subprocess generate-batch / texture-batch.
FALLBACK_SUBPROCESS: object = object()


@dataclass
class UmsJobSpec:
    """Um job GPU a enfileirar no UMS."""

    asset_id: str
    payload: dict[str, Any]
    output: str | None = None


@dataclass
class UmsJobResult:
    """Resultado por asset após wait UMS."""

    asset_id: str
    status: str  # ok | error | skipped | cancelled
    output: str | None = None
    error: str | None = None
    seconds: float | None = None
    job_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def as_batch_json(self) -> dict[str, Any]:
        """Formato compatível com JSONL de generate-batch / texture-batch."""
        out: dict[str, Any] = {"id": self.asset_id, "status": self.status}
        if self.output is not None:
            out["output"] = self.output
        if self.error is not None:
            out["error"] = self.error
        if self.seconds is not None:
            out["seconds"] = self.seconds
        if self.job_id is not None:
            out["job_id"] = self.job_id
        faces = self.raw.get("faces")
        if faces is not None:
            out["faces"] = faces
        return out


@dataclass
class MasterPendingItem:
    """Item adiado para ``run_master_pipeline`` após a wave GPU."""

    rec: dict[str, Any]
    mesh_final: Path
    row: Any


class MasterDeferQueue:
    """Recolhe finalizes e drena no fim da wave paint/shape."""

    def __init__(self) -> None:
        self.items: list[MasterPendingItem] = []

    def enqueue(self, rec: dict[str, Any], mesh_final: Path, row: Any) -> None:
        # painted/shape em _intermediate/ → ancora em meshes/{id}.glb
        from .paths import _canonical_mesh_final

        self.items.append(MasterPendingItem(rec=rec, mesh_final=_canonical_mesh_final(mesh_final), row=row))

    def drain(self, finalize_fn: Callable[[dict[str, Any], Path, Any], None]) -> int:
        """Corre ``finalize_fn`` para cada item; devolve quantos processou."""
        n = len(self.items)
        for item in self.items:
            finalize_fn(item.rec, item.mesh_final, item.row)
        self.items.clear()
        return n


def apply_ums_child_env(
    child_env: dict[str, str],
    *,
    ums_stream: bool = False,
    no_ums: bool = False,
    parent_environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Define prioridade batch + herda/propaga flags UMS para subprocessos.

    Args:
        child_env: Env base (ex. ``subprocess_gpu_env``).
        ums_stream: Se True, força ``AIGAMEKIT_UMS_STREAM=1``.
        no_ums: Se True, desliga auto-start UMS nos filhos.
        parent_environ: Ambiente pai (default ``os.environ``).

    Returns:
        O mesmo dict ``child_env`` (mutado) para encadear.
    """
    env = parent_environ if parent_environ is not None else os.environ
    child_env.setdefault("AIGAMEKIT_UMS_PRIORITY", "batch")
    for key in UMS_CHILD_ENV_KEYS:
        if key in env:
            child_env.setdefault(key, env[key])
    if ums_stream:
        child_env["AIGAMEKIT_UMS_STREAM"] = "1"
    if no_ums:
        child_env["AIGAMEKIT_UMS_AUTO_START"] = "0"
    return child_env


def preload_backend(
    backend: str,
    *,
    load_opts: dict[str, Any] | None = None,
    timeout_sec: float = 1800.0,
) -> dict[str, Any] | None:
    """Pré-carrega um backend no UMS. Best-effort: ``None`` se UMS down/erro.

    Args:
        backend: Nome (``text3d``, ``paint3d``, …).
        load_opts: Kwargs de load (``gpu_ids``, ``sdnq_preset``, …).
        timeout_sec: Timeout do RPC preload.
    """
    try:
        from aigamekit_shared.model_server import ensure_ums_running, send_to_ums
    except ImportError:
        return None
    if not ensure_ums_running():
        return None
    req: dict[str, Any] = {"cmd": "preload", "backend": backend}
    if load_opts:
        req.update(load_opts)
    try:
        return send_to_ums(req, timeout_sec=timeout_sec)
    except Exception:
        return None


def run_gpu_wave(
    backend: str,
    items: list[UmsJobSpec],
    *,
    priority: str = "batch",
    stream: bool = False,
    preload: bool = True,
    preload_opts: dict[str, Any] | None = None,
    on_progress: Callable[[UmsJobResult], None] | None = None,
    timeout_sec: float = 1800.0,
    no_ums: bool = False,
) -> list[UmsJobResult] | object:
    """Enfileira N jobs no UMS e espera cada um.

    Returns:
        Lista de ``UmsJobResult``, ou ``FALLBACK_SUBPROCESS`` se UMS indisponível
        / ``no_ums`` / submit falhou de forma que o caller deve usar CLI batch.
    """
    if no_ums:
        return FALLBACK_SUBPROCESS
    if not items:
        return []

    try:
        from aigamekit_shared.model_server import (
            cancel_ums_job,
            ensure_ums_running,
            submit_to_ums,
            wait_ums_job,
        )
    except ImportError:
        return FALLBACK_SUBPROCESS

    if not ensure_ums_running():
        return FALLBACK_SUBPROCESS

    if preload:
        preload_backend(backend, load_opts=preload_opts)

    # Janela ≤ max_depth UMS — submit-all-then-wait com 60 jobs + depth=32
    # marcava os restantes como «fila cheia» sem nunca os correr.
    window = 16
    try:
        from aigamekit_shared.model_server import fetch_ums_queue_snapshot

        snap = fetch_ums_queue_snapshot()
        if isinstance(snap, dict):
            max_d = int(snap.get("max_depth") or 32)
            window = max(1, min(16, max_d - 1))
    except Exception:
        pass

    results: list[UmsJobResult] = []
    submitted: list[tuple[UmsJobSpec, str]] = []

    def _wait_one(spec: UmsJobSpec, job_id: str) -> UmsJobResult:
        t0 = time.time()
        wait_resp = wait_ums_job(job_id, timeout_sec=timeout_sec, stream=stream)
        elapsed = round(time.time() - t0, 2)
        if wait_resp is None:
            return UmsJobResult(
                asset_id=spec.asset_id,
                status="error",
                error="UMS wait sem resposta",
                job_id=job_id,
                seconds=elapsed,
            )
        st = str(wait_resp.get("status") or "error")
        out = wait_resp.get("output") or spec.output or spec.payload.get("output")
        if st == "ok":
            return UmsJobResult(
                asset_id=spec.asset_id,
                status="ok",
                output=str(out) if out else None,
                seconds=elapsed,
                job_id=job_id,
                raw=wait_resp,
            )
        if st in ("cancelled", "canceled"):
            return UmsJobResult(
                asset_id=spec.asset_id,
                status="cancelled",
                error=str(wait_resp.get("error") or "cancelled"),
                job_id=job_id,
                seconds=elapsed,
                raw=wait_resp,
            )
        return UmsJobResult(
            asset_id=spec.asset_id,
            status="error",
            error=str(wait_resp.get("error") or wait_resp.get("message") or st),
            job_id=job_id,
            seconds=elapsed,
            raw=wait_resp,
        )

    def _drain_submitted() -> None:
        nonlocal submitted
        for spec, job_id in submitted:
            jr = _wait_one(spec, job_id)
            results.append(jr)
            if on_progress:
                on_progress(jr)
        submitted = []

    try:
        for spec in items:
            out_path = spec.output or spec.payload.get("output")
            if out_path and Path(str(out_path)).is_file():
                skipped = UmsJobResult(
                    asset_id=spec.asset_id,
                    status="skipped",
                    output=str(out_path),
                )
                results.append(skipped)
                if on_progress:
                    on_progress(skipped)
                continue

            # Fila cheia → drena janela e tenta de novo (até 3x).
            resp: dict[str, Any] | None = None
            for _attempt in range(3):
                resp = submit_to_ums(backend, dict(spec.payload), priority=priority, timeout_sec=30.0)
                if resp is None:
                    for _s, jid in submitted:
                        with contextlib.suppress(Exception):
                            cancel_ums_job(jid)
                    return FALLBACK_SUBPROCESS
                if resp.get("status") == "ok" and resp.get("job_id"):
                    break
                err_msg = str(resp.get("error") or resp.get("message") or "")
                if "fila cheia" in err_msg.lower() or "queue full" in err_msg.lower():
                    _drain_submitted()
                    time.sleep(0.5)
                    continue
                break

            if resp is None or resp.get("status") != "ok" or not resp.get("job_id"):
                err = UmsJobResult(
                    asset_id=spec.asset_id,
                    status="error",
                    error=str((resp or {}).get("error") or (resp or {}).get("message") or "submit UMS falhou"),
                    raw=resp,
                )
                results.append(err)
                if on_progress:
                    on_progress(err)
                continue

            submitted.append((spec, str(resp["job_id"])))
            if len(submitted) >= window:
                _drain_submitted()

        _drain_submitted()
    except KeyboardInterrupt:
        for _s, jid in submitted:
            with contextlib.suppress(Exception):
                cancel_ums_job(jid)
        raise

    return results


def results_as_batch_jsonl(results: list[UmsJobResult]) -> list[dict[str, Any]]:
    """Converte resultados UMS para o formato JSONL das CLIs batch."""
    return [r.as_batch_json() for r in results]
