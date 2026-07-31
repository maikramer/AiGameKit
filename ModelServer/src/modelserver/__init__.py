"""Unified Model Server (UMS) — supervisor único de VRAM para o monorepo AiGameKit.

Um único processo detém toda a VRAM e roteia pedidos de geração para backends
(ferramentas GPU) carregados sob procura. Evicção inteligente peso+LRU quando
a VRAM escasseia. Retrocompatível com ``aigamekit_shared.model_server``.
"""

from __future__ import annotations

__version__ = "0.1.0"
