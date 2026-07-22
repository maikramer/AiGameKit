"""Loop canónico do worker subprocesso — lado tool.

Cada tool GPU do monorepo expõe um subcomando ``serve --ums-worker`` que
invoca :func:`run_worker_loop` com o seu adapter local (não o adapter do UMS).
O loop lê comandos JSONL do stdin (UMS) e emite eventos no stdout (UMS);

O adapter da tool é uma classe que implementa o contrato ``BackendAdapter``
(de :mod:`modelserver.adapters.base` no UMS, ou equivalente local na tool).
Como o adapter corre no venv da tool, tem acesso aos módulos (ex.:
``from paint3d.painter import PaintBatchProcessor``).

Lifecycle do worker:
1. Arranque: loop à espera de ``{"cmd":"load","kwargs":{...}}``.
2. ``load``: ``adapter.load(**kwargs)`` → guarda o model object → emite ``ready``.
3. ``generate``: ``adapter.generate(model, request)`` com hooks progress/abort
   que emitem eventos → emite ``done`` com o result.
4. ``unload``: ``adapter.unload(model)``; model = None; emite ``unloaded``.
5. ``shutdown`` / EOF no stdin: descarrega se carregado e sai (exit 0).
6. Erro não-fatal (ex.: generate falhou): emite ``error`` e mantém-se vivo.
7. Erro fatal (ex.: ImportError no load): emite ``error`` e sai (exit 1).
"""

from __future__ import annotations

import sys
import traceback
from typing import Any

from .worker_protocol import (
    CMD_ABORT,
    CMD_GENERATE,
    CMD_LOAD,
    CMD_PING,
    CMD_SHUTDOWN,
    CMD_UNLOAD,
    ERR_CANCELLED,
    ERR_GENERATE_FAILED,
    ERR_LOAD_FAILED,
    ERR_VRAM_INSUFFICIENT,
    EVENT_DONE,
    EVENT_ERROR,
    EVENT_PONG,
    EVENT_PROGRESS,
    EVENT_READY,
    EVENT_UNLOADED,
    emit_event,
    read_cmd,
)


def _is_vram_error(exc: Exception) -> bool:
    """Heurística: erro de VRAM (RuntimeError torch OOM ou texto)."""
    msg = str(exc).lower()
    return "out of memory" in msg or "cuda oom" in msg or ("vram" in msg and "insuf" in msg)


def run_worker_loop(
    adapter_class: type,
    *,
    backend_name: str,
    version: str = "1",
) -> None:
    """Loop principal do worker subprocesso.

    Lê comandos do stdin até EOF ou ``shutdown``. Mantém o model object vivo
    entre ``generate`` (worker persistente).

    Args:
        adapter_class: classe ``BackendAdapter`` concreta da tool (instância sem
            args; tem métodos ``load/generate/unload``).
        backend_name: Nome do backend (ex.: ``text3d``) — só para diagnóstico.
        version: Versão do protocolo esperada (para quebra-graceful entre UMS
            e worker de versões diferentes).
    """
    adapter = adapter_class()
    model: Any = None
    # Caixa mutável para o flag de abort — closures que o adapter chama durante
    # o generate precisam de ver o valor corrente (B023-safe).
    state = {"abort": False}

    while True:
        try:
            cmd_msg = read_cmd()
        except Exception as exc:
            emit_event(
                EVENT_ERROR,
                error=f"comando inválido: {exc}",
                error_code="BAD_CMD",
                backend=backend_name,
            )
            continue

        if cmd_msg is None:
            # EOF no stdin = UMS fechou = shutdown gracioso.
            break

        cmd = cmd_msg.get("cmd")
        if cmd == CMD_PING:
            emit_event(EVENT_PONG, backend=backend_name, version=version)
            continue

        if cmd == CMD_SHUTDOWN:
            if model is not None:
                with _safe_unload(adapter, model, backend_name):
                    pass
            break

        if cmd == CMD_LOAD:
            if model is not None:
                with _safe_unload(adapter, model, backend_name):
                    pass
                model = None
            kwargs = cmd_msg.get("kwargs", {}) or {}
            try:
                model = adapter.load(**kwargs)
            except Exception as exc:
                tb = traceback.format_exc()
                emit_event(
                    EVENT_ERROR,
                    error=f"load: {exc}",
                    error_code=ERR_LOAD_FAILED,
                    backend=backend_name,
                    traceback=tb,
                )
                # Falha de load é fatal: o UMS re-spawn ou marca broken.
                sys.exit(1)
            # Reportar VRAM depois do load (se disponível).
            vram = _probe_vram_mib()
            emit_event(EVENT_READY, backend=backend_name, vram_mib=vram)
            continue

        if cmd == CMD_UNLOAD:
            if model is not None:
                with _safe_unload(adapter, model, backend_name):
                    pass
                model = None
            emit_event(EVENT_UNLOADED, backend=backend_name)
            continue

        if cmd == CMD_ABORT:
            # Marca abort; o generate em curso vai checar e cooperar.
            state["abort"] = True
            continue

        if cmd == CMD_GENERATE:
            if model is None:
                emit_event(
                    EVENT_ERROR,
                    error="generate sem modelo carregado (load necessário)",
                    error_code=ERR_GENERATE_FAILED,
                    backend=backend_name,
                )
                continue
            state["abort"] = False
            request = cmd_msg.get("request", {}) or {}

            # Hooks que emitem eventos — o adapter chama-os durante o generate.
            def _on_progress(pct: float | None = None, msg: str | None = None) -> None:
                emit_event(EVENT_PROGRESS, pct=pct, msg=msg, backend=backend_name)

            def _should_abort() -> bool:
                return state["abort"]

            request["_progress"] = _on_progress
            request["_abort"] = _should_abort
            try:
                result = adapter.generate(model, request)
            except Exception as exc:
                tb = traceback.format_exc()
                code = ERR_VRAM_INSUFFICIENT if _is_vram_error(exc) else ERR_GENERATE_FAILED
                if state["abort"] and code == ERR_GENERATE_FAILED:
                    code = ERR_CANCELLED
                emit_event(
                    EVENT_ERROR,
                    error=f"generate: {exc}",
                    error_code=code,
                    backend=backend_name,
                    traceback=tb,
                )
                continue
            # Limpar hooks antes de enviar o result (não devem serializar).
            result = _scrub_result(result)
            emit_event(EVENT_DONE, result=result, backend=backend_name)
            continue

        # Comando desconhecido.
        emit_event(
            EVENT_ERROR,
            error=f"comando desconhecido: {cmd!r}",
            error_code="BAD_CMD",
            backend=backend_name,
        )


# ---------------------------------------------------------------------------
# Internos
# ---------------------------------------------------------------------------


class _safe_unload:
    """Context manager que engole exceções do unload (worker deve sobreviver)."""

    def __init__(self, adapter: Any, model: Any, backend_name: str) -> None:
        self._adapter = adapter
        self._model = model
        self._backend = backend_name

    def __enter__(self) -> _safe_unload:
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            self._adapter.unload(self._model)
        except Exception as exc:
            emit_event(
                EVENT_ERROR,
                error=f"unload: {exc}",
                error_code="UNLOAD_FAILED",
                backend=self._backend,
            )


def _scrub_result(result: Any) -> Any:
    """Remove callbacks/não-serializáveis do result antes de o emitir."""
    if not isinstance(result, dict):
        return result
    scrubbed = {}
    for k, v in result.items():
        if k.startswith("_"):
            continue
        if callable(v):
            continue
        scrubbed[k] = v
    return scrubbed


def _probe_vram_mib() -> int | None:
    """Tenta reportar a VRAM usada por este processo (NVML ou torch).

    O UMS usa o seu próprio NVML também (soma dos PIDs filho); este valor é
    só informativo — o planeamento de VRAM no UMS não depende dele.
    """
    try:
        from gamedev_shared.gpu import process_vram_mib

        v = process_vram_mib()
        if v is not None:
            return int(v)
    except Exception:
        pass
    try:
        import torch

        if torch.cuda.is_available():
            return int(torch.cuda.memory_allocated() // (1024 * 1024))
    except Exception:
        pass
    return None
