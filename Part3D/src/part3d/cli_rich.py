"""Configuração Rich + rich-click para o CLI Part3D."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Part3D[/bold cyan] — assets 3D modulares (PartSeg + Blender)"
_FOOTER: Final = "[dim]README · docs/ · PART3D_ROOT[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
