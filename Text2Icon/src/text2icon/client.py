"""Cliente shim para o model server do text2icon (compat layer).

Todo o pesado vive em ``aigamekit_shared.model_server`` e ``text2icon.server``.
Este módulo existe para compatibilidade com ``cli.py`` que faz ``from . import client``.
"""

from __future__ import annotations

from .server import is_available, send_generate_request

__all__ = ["is_available", "send_generate_request"]
