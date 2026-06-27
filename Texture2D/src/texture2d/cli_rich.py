"""Configuração Rich + rich-click para o CLI Texture2D."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Texture2D[/bold cyan] — texturas 2D seamless · HF Inference API"
_FOOTER: Final = "[dim]Documentação: README · Token: HF_TOKEN ou HUGGINGFACEHUB_API_TOKEN[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
