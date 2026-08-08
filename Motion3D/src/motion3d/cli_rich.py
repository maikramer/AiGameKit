"""Configuração Rich + rich-click para o CLI Motion3D."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Motion3D[/bold cyan] — text-to-motion · HY-Motion-1.0"
_FOOTER: Final = "[dim]README · tencent/HY-Motion-1.0 · vramd · bpy GLB · motion3d doctor[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="motion3d", header=_HEADER, footer=_FOOTER)
