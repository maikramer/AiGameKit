"""Contrato canónico do adapter de backend.

As tools do monorepo têm APIs heterogéneas para carregar/usar/libertar modelos:

  - Text2D/Text2Icon/Texture2D/Skymap2D: ``warmup()`` / ``generate()`` / ``unload()``
  - Text2Sound: ``load()`` / ``generate()`` / ``unload()``
  - Text3D: ``_load_hunyuan()`` / ``generate()`` / ``unload_hunyuan()``
  - Paint3D: context-managed (``__enter__`` / ``__exit__``)
  - Terrain3D: função procedural ``generate_terrain()``

O ``BackendAdapter`` normaliza tudo num contrato único (``load`` / ``generate`` /
``unload``) que o ``BackendManager`` invoca. Cada adapter concreto faz a tradução.
"""

from __future__ import annotations

import contextlib
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from gamedev_shared.diffusion_control import GenerationAborted

from .. import protocol as P

__all__ = ["BackendAdapter", "GenerationAborted"]


class BackendAdapter(ABC):
    """Contrato canónico que cada adapter de tool implementa.

    O adapter é **stateless quanto ao modelo**: não retém o objeto carregado.
    O ciclo de vida (criar/manter/evictar o model object) é responsabilidade do
    ``BackendManager``. O adapter apenas sabe *como* carregar/usar/libertar um
    modelo dessa tool.

    Implementações concretas devem ser instanciáveis sem argumentos (o Registry
    faz ``Adapter()``) e exportar a classe com o nome ``Adapter``.
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
            "status": P.STATUS_ERROR,
            "error": reason,
            "error_code": P.ERR_CANCELLED,
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

    @staticmethod
    def should_use_low_vram_mode(threshold_mib: int = 7000) -> bool:
        """Deteta se a GPU tem pouca VRAM e deve ativar offload/sequential."""
        try:
            from gamedev_shared.gpu import gpu_total_mib

            total = gpu_total_mib()
            return total is not None and total < threshold_mib
        except Exception:
            return False
