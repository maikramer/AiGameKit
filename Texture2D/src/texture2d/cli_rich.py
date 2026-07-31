"""Configuração Rich + rich-click para o CLI Texture2D."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Texture2D[/bold cyan] — texturas 2D seamless · Stable Diffusion v1.5 + circular padding"
_FOOTER: Final = "[dim]Documentação: README[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="texture2d", header=_HEADER, footer=_FOOTER)
