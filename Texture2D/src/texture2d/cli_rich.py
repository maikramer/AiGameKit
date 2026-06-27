"""Configuração Rich + rich-click para o CLI Texture2D."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = (
    "[bold cyan]Texture2D[/bold cyan] — texturas 2D seamless · pattern-diffusion + Materialize PBR · local GPU"
)
_FOOTER: Final = "[dim]Documentação: README · PBR: MATERIALIZE_BIN (opcional)[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
