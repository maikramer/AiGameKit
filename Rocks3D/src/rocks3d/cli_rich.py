"""Configuração Rich + rich-click para o CLI Rocks3D."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Rocks3D[/bold cyan] — procedural 3D rock generation"
_FOOTER: Final = "[dim]README · docs/ · ROCKS3D_ROOT[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
