"""Configuração Rich + rich-click para o CLI Animator3D."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Animator3D[/bold cyan] — animação 3D via Blender (bpy)"
_FOOTER: Final = "[dim]README · BLENDER_COMMAND · ANIMATOR3D_ROOT[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
