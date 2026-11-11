"""Coordenação GameAssets ↔ vramd (preload, fila submit/wait, defer master)."""

from __future__ import annotations

import contextlib
import os
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Env keys propagados aos subprocessos GPU (além de VRAMD_PRIORITY).
VRAMD_CHILD_ENV_KEYS: tuple[str, ...] = (
    "VRAMD_PRIORITY",
    "VRAMD_AUTO_START",
    "VRAMD_DEBUG",
    "VRAMD_STREAM",
    "VRAMD_MAX_QUEUE_DEPTH",
    "VRAMD_MAX_AFFINITY_CUTS",
    "VRAMD_STARVATION_TIMEOUT_SEC",
    "VRAMD_MAX_INFLIGHT",
    "VRAMD_ALLOW_LEGACY_SERVER",
    "VRAMD_GPU_MODE",
    "VRAMD_AUTO_GPU",
)

# Sentinel: caller deve usar subprocess generate-batch / texture-batch.
FALLBACK_SUBPROCESS: object = object()


# ``--force`` do batch/resume: as waves não saltam outputs já em disco. Sem
# isto o batch filtrava bem os itens mas o ``run_gpu_wave`` devolvia
# ``skipped`` a todos os PNG/GLB existentes — um regen ``--force`` reusava as
# imagens antigas sem aviso.
WAVE_FORCE: ContextVar[bool] = ContextVar("gameassets_wave_force", default=False)

# ``--force`` chega também ao master pipeline: sem isto rigged/animated/ladder/
# collision e as peças de split sobreviviam a um regen forçado (só shape/paint
# respeitavam o force).
MASTER_FORCE: ContextVar[bool] = ContextVar("gameassets_master_force", default=False)


@dataclass
class UmsJobSpec:
    """Um job GPU a enfileirar no vramd."""

    asset_id: str
    payload: dict[str, Any]
    output: str | None = None
    # remote | local | auto (campo ``gpu:`` do manifesto; auto = política global)
    gpu: str = "auto"
    # high | normal | low — o vramd põe os críticos na GPU com melhor tier.
    criticality: str = "normal"


def attach_gpu(items: list[dict[str, Any]], rows: Iterable[Any]) -> None:
    """Copia ``gpu:`` e a criticidade efectiva dos ManifestRow para os itens.

    Os itens passam por ficheiros JSON intermédios (shape/paint/text2d); sem
    isto o ``vramd_batch`` não sabe se um asset é remoto, local ou auto, nem
    quanto vale a qualidade dele face ao tempo.
    """
    from .vramd_routing import effective_criticality

    rows = list(rows)
    by_id = {str(getattr(row, "id", "")): row for row in rows}
    for item in items:
        row = by_id.get(str(item.get("id")))
        item.setdefault("gpu", str(getattr(row, "gpu", "auto") or "auto") if row is not None else "auto")
        if row is not None:
            item.setdefault("criticality", effective_criticality(row))


_CRIT_ORDER = {"high": 0, "normal": 1, "low": 2}

# Jobs submetidos antes da wave deles (ex.: o paint de um asset assim que o
# shape acaba, com várias GPUs). A wave encontra-os aqui pelo output e espera
# por eles em vez de os submeter outra vez.
_prefetched: dict[tuple[str, str], tuple[str, str]] = {}
_prefetched_lock = threading.Lock()


def _prefetch_key(backend: str, output: str | None) -> tuple[str, str] | None:
    if not output:
        return None
    return backend, os.path.realpath(str(output))


def register_prefetched(backend: str, output: str | None, job_id: str, gpu: str = "auto") -> None:
    key = _prefetch_key(backend, output)
    if key is not None:
        with _prefetched_lock:
            _prefetched[key] = (str(job_id), gpu)


def take_prefetched(backend: str, output: str | None) -> tuple[str, str] | None:
    """Job já submetido para este output (consumido: um retry submete de novo)."""
    key = _prefetch_key(backend, output)
    if key is None:
        return None
    with _prefetched_lock:
        return _prefetched.pop(key, None)


def is_prefetched(backend: str, output: str | None) -> bool:
    key = _prefetch_key(backend, output)
    with _prefetched_lock:
        return key is not None and key in _prefetched


def discard_prefetched(backend: str, output: str | None, job_id: str) -> bool:
    """Remove a entrada **se ainda for este job**; ``False`` se já foi consumida.

    O ``close`` de um prefetcher não pode limpar o registo global: outro
    prefetcher (ou um retry) pode ter um job para o mesmo output. Só se apaga
    quando o ``job_id`` ainda é o nosso.
    """
    key = _prefetch_key(backend, output)
    if key is None:
        return False
    with _prefetched_lock:
        current = _prefetched.get(key)
        if current is None or current[0] != str(job_id):
            return False
        del _prefetched[key]
        return True


def clear_prefetched() -> list[tuple[str, str]]:
    """Esvazia o registo; devolve os ``(job_id, gpu)`` que ninguém consumiu."""
    with _prefetched_lock:
        left = list(_prefetched.values())
        _prefetched.clear()
    return left


def wave_window(capacity: int | None, max_depth: int | None = None) -> int:
    """Jobs em voo por wave: 16, ou 4 por slot quando há muitas GPUs/peers.

    O router do vramd só decide bem o que vê na fila — com 4 GPUs e 16 jobs em
    voo, uma réplica nova só compensa se houver fila para a amortizar. Nunca
    passa do ``max_depth - 1`` do servidor (fila cheia recusa submits).
    """
    window = max(16, 4 * max(1, int(capacity or 1)))
    if max_depth:
        window = min(window, max(1, int(max_depth) - 1))
    return window


@dataclass
class UmsJobResult:
    """Resultado por asset após wait vramd."""

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


def apply_vramd_child_env(
    child_env: dict[str, str],
    *,
    vramd_stream: bool = False,
    no_vramd: bool = False,
    parent_environ: dict[str, str] | None = None,
) -> dict[str, str]:
    """Define prioridade batch + herda/propaga flags vramd para subprocessos.

    Args:
        child_env: Env base (ex. ``subprocess_gpu_env``).
        vramd_stream: Se True, força ``VRAMD_STREAM=1``.
        no_vramd: Se True, desliga auto-start vramd nos filhos.
        parent_environ: Ambiente pai (default ``os.environ``).

    Returns:
        O mesmo dict ``child_env`` (mutado) para encadear.
    """
    env = parent_environ if parent_environ is not None else os.environ
    child_env.setdefault("VRAMD_PRIORITY", "batch")
    for key in VRAMD_CHILD_ENV_KEYS:
        if key in env:
            child_env.setdefault(key, env[key])
    if vramd_stream:
        child_env["VRAMD_STREAM"] = "1"
    if no_vramd:
        child_env["VRAMD_AUTO_START"] = "0"
    return child_env


def preload_backend(
    backend: str,
    *,
    load_opts: dict[str, Any] | None = None,
    timeout_sec: float = 1800.0,
    force_local: bool = False,
) -> dict[str, Any] | None:
    """Pré-carrega um backend no vramd. Best-effort: ``None`` se vramd down/erro.

    Args:
        backend: Nome (``text3d``, ``paint3d``, …).
        load_opts: Kwargs de load (``gpu_ids``, ``sdnq_preset``, …).
        timeout_sec: Timeout do RPC preload.
        force_local: Ignora ``VRAMD_REMOTE`` (wave ``gpu: local``).
    """
    try:
        from aigamekit_shared.vramd_client import ensure_vramd_running, send_to_vramd
    except ImportError:
        return None
    if not ensure_vramd_running(force_local=force_local):
        return None
    req: dict[str, Any] = {"cmd": "preload", "backend": backend}
    if load_opts:
        req.update(load_opts)
    try:
        return send_to_vramd(req, timeout_sec=timeout_sec, force_local=force_local)
    except Exception:
        return None


def _gpu_remote(gpu: str | None) -> bool | None:
    """``remote``/``local``/``auto`` → True/False/None (None = política global)."""
    mode = str(gpu or "auto").strip().lower()
    if mode == "remote":
        return True
    if mode == "local":
        return False
    auto = os.environ.get("VRAMD_AUTO_GPU", "").strip().lower()
    if not auto:
        # ``--gpu``/VRAMD_GPU_MODE das CLIs serve de default para assets auto.
        auto = os.environ.get("VRAMD_GPU_MODE", "").strip().lower()
    if auto == "remote":
        return True
    if auto == "local":
        return False
    return None


def cuda_visible_for_children(gpu_ids: list[int] | None) -> list[int | str] | None:
    """IDs lógicos (torch) → entradas do ``CUDA_VISIBLE_DEVICES`` do pai.

    ``detect_gpu_ids`` devolve IDs **lógicos**: com
    ``CUDA_VISIBLE_DEVICES=2,3`` no pai, devolve ``[0, 1]`` (as GPUs que o torch
    numera 0/1). Passá-los crus a ``subprocess_gpu_env`` reescrevia o CVD do
    filho para ``0,1`` e mudava-o para as GPUs físicas 0/1. Aqui cada ID lógico
    é traduzido para a entrada correspondente do CVD do pai (índice físico ou
    UUID); sem CVD do pai os IDs já são físicos.

    Um ID fora do intervalo visível fica como está (não há entrada para o
    traduzir) — mantém o comportamento antigo em vez de rebentar o batch.
    """
    if not gpu_ids:
        return None
    raw = os.environ.get("CUDA_VISIBLE_DEVICES")
    if raw is None:
        return list(gpu_ids)
    entries: list[str] = []
    for chunk in raw.split(","):
        entry = chunk.strip()
        if not entry:
            continue
        with contextlib.suppress(ValueError):
            if int(entry) < 0:
                continue
        entries.append(entry)
    if not entries:
        return list(gpu_ids)
    mapped: list[int | str] = []
    for gpu_id in gpu_ids:
        idx = int(gpu_id)
        if 0 <= idx < len(entries):
            entry = entries[idx]
            try:
                mapped.append(int(entry))
            except ValueError:
                mapped.append(entry)  # UUID/MIG
        else:
            mapped.append(idx)
    return mapped


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
    no_vramd: bool = False,
) -> list[UmsJobResult] | object:
    """Enfileira N jobs no vramd e espera cada um.

    Returns:
        Lista de ``UmsJobResult``, ou ``FALLBACK_SUBPROCESS`` se vramd indisponível
        / ``no_vramd`` / submit falhou de forma que o caller deve usar CLI batch.
    """
    if no_vramd:
        return FALLBACK_SUBPROCESS
    if not items:
        return []

    try:
        from aigamekit_shared.vramd_client import (
            cancel_vramd_job,
            ensure_vramd_running,
            submit_to_vramd,
            wait_vramd_job,
        )
    except ImportError:
        return FALLBACK_SUBPROCESS

    from aigamekit_shared.vramd_remote import remote_enabled

    modes = {_gpu_remote(spec.gpu) for spec in items}
    need_remote = True in modes or (None in modes and remote_enabled())
    need_local = False in modes or (None in modes and not remote_enabled())
    if need_remote and not remote_enabled():
        raise RuntimeError(
            "a wave pede GPU remota (`gpu: remote`) mas VRAMD_REMOTE/VRAMD_REMOTE_TOKEN não estão definidos."
        )
    if need_local and not ensure_vramd_running(force_local=True):
        if need_remote:
            raise RuntimeError("wave mista: o vramd local não está disponível para os assets `gpu: local`.")
        return FALLBACK_SUBPROCESS
    if need_remote and not ensure_vramd_running():
        # Remoto: o fallback local correria na GPU do laptop (ou numa máquina
        # sem GPU). Falha alto em vez de mudar de hardware em silêncio.
        raise RuntimeError(
            "vramd remoto inacessível — sem fallback local (VRAMD_REMOTE ativo). "
            "Verifica VRAMD_REMOTE/VRAMD_REMOTE_TOKEN e `vramd status`."
        )

    if preload:
        mode = next(iter(modes)) if len(modes) == 1 else "mixed"
        if mode != "mixed":
            preload_backend(backend, load_opts=preload_opts, force_local=(mode is False))

    # Janela ≤ max_depth vramd — submit-all-then-wait com 60 jobs + depth=32
    # marcava os restantes como «fila cheia» sem nunca os correr. Com várias
    # GPUs/peers a janela cresce com a capacidade (o router precisa de fila).
    capacity = 1
    try:
        from aigamekit_shared.vramd_topology import fetch_topology

        topo = fetch_topology(remote=False if not need_remote else None)
        if topo is not None:
            capacity = topo.capacity
    except Exception:
        pass
    max_d: int | None = None
    try:
        from aigamekit_shared.vramd_client import fetch_vramd_queue_snapshot

        snap = fetch_vramd_queue_snapshot()
        if isinstance(snap, dict) and snap.get("max_depth"):
            max_d = int(snap["max_depth"])
    except Exception:
        pass
    window = wave_window(capacity, max_d or 32)

    # Críticos primeiro: chegam à fila antes e apanham a GPU grande livre.
    items = sorted(items, key=lambda s: _CRIT_ORDER.get(s.criticality, 1))

    results: list[UmsJobResult] = []
    submitted: deque[tuple[UmsJobSpec, str]] = deque()

    def _cancel_orphan(spec: UmsJobSpec, job_id: str) -> None:
        """Melhor esforço: um job que o wait não confirmou pode ficar a correr.

        Num retry (paint) o mesmo output seria submetido outra vez e dois jobs
        escreviam o mesmo GLB; num shape ficava VRAM ocupada sem ninguém à
        espera. O cancel é idempotente e não falha o batch.
        """
        with contextlib.suppress(Exception):
            cancel_vramd_job(job_id, remote=_gpu_remote(spec.gpu))

    def _wait_one(spec: UmsJobSpec, job_id: str) -> UmsJobResult:
        t0 = time.time()
        wait_resp = wait_vramd_job(
            job_id,
            timeout_sec=timeout_sec,
            stream=stream,
            remote=_gpu_remote(spec.gpu),
        )
        elapsed = round(time.time() - t0, 2)
        if wait_resp is None:
            _cancel_orphan(spec, job_id)
            return UmsJobResult(
                asset_id=spec.asset_id,
                status="error",
                error="vramd wait sem resposta",
                job_id=job_id,
                seconds=elapsed,
            )
        st = str(wait_resp.get("status") or "error")
        out = wait_resp.get("output") or spec.output or spec.payload.get("output")
        if st in ("ok", "skipped"):
            # ``skipped`` = o worker encontrou o output já feito (outro processo
            # ou retry). Não é erro: o batch trata os dois como concluídos.
            return UmsJobResult(
                asset_id=spec.asset_id,
                status=st,
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
        _cancel_orphan(spec, job_id)
        return UmsJobResult(
            asset_id=spec.asset_id,
            status="error",
            error=str(wait_resp.get("error") or wait_resp.get("message") or st),
            job_id=job_id,
            seconds=elapsed,
            raw=wait_resp,
        )

    def _wait_oldest() -> None:
        spec, job_id = submitted.popleft()
        jr = _wait_one(spec, job_id)
        results.append(jr)
        if on_progress:
            on_progress(jr)

    def _drain_submitted() -> None:
        while submitted:
            _wait_oldest()

    try:
        for spec in items:
            out_path = spec.output or spec.payload.get("output")
            # Já submetido antes da wave (prefetch): só esperar por ele.
            pre = take_prefetched(backend, out_path)
            if pre is not None:
                submitted.append((spec, pre[0]))
                if len(submitted) >= window:
                    _wait_oldest()
                continue
            if out_path and not WAVE_FORCE.get() and Path(str(out_path)).is_file():
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
                resp = submit_to_vramd(
                    backend,
                    dict(spec.payload),
                    priority=priority,
                    timeout_sec=30.0,
                    remote=_gpu_remote(spec.gpu),
                )
                if resp is None:
                    for _s, jid in submitted:
                        with contextlib.suppress(Exception):
                            cancel_vramd_job(jid, remote=_gpu_remote(_s.gpu))
                    if _gpu_remote(spec.gpu) is True:
                        raise RuntimeError(
                            f"asset {spec.asset_id}: vramd remoto sem resposta no submit "
                            "(`gpu: remote`) — sem fallback local."
                        )
                    if _gpu_remote(spec.gpu) is False:
                        return FALLBACK_SUBPROCESS
                    if remote_enabled():
                        raise RuntimeError(
                            "vramd remoto sem resposta no submit — sem fallback local (VRAMD_REMOTE ativo)."
                        )
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
                    error=str((resp or {}).get("error") or (resp or {}).get("message") or "submit vramd falhou"),
                    raw=resp,
                )
                results.append(err)
                if on_progress:
                    on_progress(err)
                continue

            submitted.append((spec, str(resp["job_id"])))
            # Janela rolante: espera só pelo mais antigo e volta a submeter. O
            # drain completo a cada 16 deixava as GPUs paradas na cauda de
            # cada bloco à espera do job mais lento.
            if len(submitted) >= window:
                _wait_oldest()

        _drain_submitted()
    except KeyboardInterrupt:
        for _s, jid in submitted:
            with contextlib.suppress(Exception):
                cancel_vramd_job(jid, remote=_gpu_remote(_s.gpu))
        raise

    return results


def results_as_batch_jsonl(results: list[UmsJobResult]) -> list[dict[str, Any]]:
    """Converte resultados vramd para o formato JSONL das CLIs batch."""
    return [r.as_batch_json() for r in results]
