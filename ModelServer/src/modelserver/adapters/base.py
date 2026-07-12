"""Contrato canónico do adapter de backend.

As tools do monorepo têm APIs heterogéneas para carregar/usar/libertar modelos:

  - Text2D/Text2Icon/Texture2D/Skymap2D: ``warmup()`` / ``generate()`` / ``unload()``
  - Part3D/Text2Sound: ``load()`` / ``generate()`` / ``unload()``
  - Text3D: ``_load_hunyuan()`` / ``generate()`` / ``unload_hunyuan()``
  - Paint3D: context-managed (``__enter__`` / ``__exit__``)
  - Terrain3D: função procedural ``generate_terrain()``

O ``BackendAdapter`` normaliza tudo num contrato único (``load`` / ``generate`` /
``unload``) que o ``BackendManager`` invoca. Cada adapter concreto faz a tradução.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


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
        """Carrega e devolve o model object (pipeline/gerador pronto a gerar).

        Args:
            **kwargs: Parâmetros opcionais passados do request (ex: verbose).

        Returns:
            O objeto modelo carregado (será guardado pelo BackendManager).
        """

    @abstractmethod
    def generate(self, model: Any, request: dict[str, Any]) -> dict[str, Any]:
        """Executa uma geração sobre o model object.

        Args:
            model: Objeto retornado por ``load``.
            request: Dict do pedido (prompt, output, parâmetros, ...).

        Returns:
            Dict de resposta. Convenção: ``{"status": "ok", "output": ..., ...}``
            ou ``{"status": "error", "error": "..."}``.
        """

    @abstractmethod
    def unload(self, model: Any) -> None:
        """Liberta o model object (VRAM). Idempotente e à prova de exceções.

        Chamado pelo BackendManager ao evictar. Não deve levantar — se falhar,
        o BackendManager faz fallback para ``clear_cuda_memory``.
        """
