"""Cliente shim para o model server do Texture2D (compat layer)."""

from __future__ import annotations

from .server import is_available, send_generate_request

__all__ = ["is_available", "send_generate_request"]
