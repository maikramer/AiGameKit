"""Classe base partilhada para adapters de worker subprocesso (Fase 4).

Réplica standalone de :class:`modelserver.adapters.base.BackendAdapter`
que **não depende do package modelserver`` — cada tool pode herdar desta
classe no seu venv próprio. Tem os mesmos helpers estáticos
(report_progress / should_abort / cancelled_response / abort_hooks /
apply_runtime_budget) e o mesmo contrato (load/generate/unload).

Cada tool cria ``<Tool>/src/<tool>/worker_serve_adapter.py`` com::

    from gamedev_shared.worker_serve_adapter_base import WorkerAdapter

    class Adapter(WorkerAdapter):
        name = "text3d"
        def load(self, **kwargs): ...
        def generate(self, model, request): ...
        def unload(self, model): ...

E regista o subcomando ``serve --ums-worker`` no CLI que chama
``gamedev_shared.worker_serve.run_worker_loop(Adapter, backend_name=...)``.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any


class WorkerAdapter(ABC):
    """Contrato canónico do adapter do worker subprocesso (standalone).

    Gémeo de :class:`modelserver.adapters.base.BackendAdapter` mas sem depender
    do supervisor — vive no venv da tool e é invocado por
    :func:`gamedev_shared.worker_serve.run_worker_loop`.

    Implementações devem ser instanciáveis sem argumentos e exportar a classe
    com o nome ``Adapter``.
    """

    name: str = ""

    @abstractmethod
    def load(self, **kwargs: Any) -> Any:
        """Carrega e devolve o model object (pipeline/gerador pronto a gerar)."""

    @abstractmethod
    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        """Executa uma geração sobre o model object.

        O request pode incluir:
          - ``_progress``: ``(pct|None, msg|None) -> None``
          - ``_abort``: ``() -> bool`` (True = cancel pedido)
        """

    @abstractmethod
    def unload(self, model: Any) -> None:
        """Liberta o model object (VRAM). Idempotente e à prova de exceções."""

    @staticmethod
    def report_progress(request: dict[str, Any], pct: float | None = None, msg: str | None = None) -> None:
        """Helper: reporta progresso se o request trouxer ``_progress``."""
        cb = request.get("_progress")
        if callable(cb):
            with contextlib.suppress(Exception):
                cb(pct, msg)

    @staticmethod
    def should_abort(request: dict[str, Any]) -> bool:
        """True se o UMS pediu cancel (``request["_abort"]``)."""
        cb = request.get("_abort")
        if not callable(cb):
            return False
        try:
            return bool(cb())
        except Exception:
            return False

    @staticmethod
    def cancelled_response(reason: str = "cancelled") -> dict[str, Any]:
        """Resposta canónica de cancel cooperativo."""
        return {
            "status": "error",
            "error": reason,
            "error_code": "CANCELLED",
        }

    @classmethod
    def abort_hooks(
        cls,
        request: dict[str, Any],
        *,
        num_inference_steps: int,
    ) -> tuple[Callable[[], bool] | None, Callable[[int, int], None] | None]:
        """Constrói ``should_abort`` / ``on_step`` para generators 2D."""

        def _abort() -> bool:
            return cls.should_abort(request)

        def _on_step(step: int, total: int) -> None:
            pct = step / max(1, total)
            cls.report_progress(request, pct, f"step {step}/{total}")

        has_abort = callable(request.get("_abort"))
        has_progress = callable(request.get("_progress"))
        return (
            _abort if has_abort else None,
            _on_step if has_progress else None,
        )

    @classmethod
    def apply_runtime_budget(
        cls,
        model: Any,
        request: dict[str, Any],
        *,
        progress_pct: float | None = None,
        **hints: Any,
    ) -> dict[str, Any] | None:
        """Reaplica o runtime VRAM budget do model object, se suportado.

        Idêntico a :meth:`BackendAdapter.apply_runtime_budget`.
        """
        refresh = getattr(model, "refresh_runtime_budget", None)
        if not callable(refresh):
            return None
        try:
            try:
                budget = refresh(**hints) if hints else refresh()
            except TypeError:
                budget = refresh()
        except (RuntimeError, MemoryError):
            raise
        except Exception:
            return None
        if budget and progress_pct is not None:
            summary = ", ".join(
                f"{k}={v}" for k, v in budget.items() if k in ("num_chunks", "max_views", "dino_device")
            )
            cls.report_progress(request, progress_pct, f"vram_budget {summary}" if summary else "vram_budget")
        return budget if isinstance(budget, dict) else None
