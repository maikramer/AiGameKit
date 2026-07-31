"""Configuração Rich + rich-click para o CLI Rigging3D."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Rigging3D[/bold cyan] — auto-rigging 3D (UniRig: skeleton, skinning, merge)"
_FOOTER: Final = "[dim]README · RIGGING3D_ROOT · pipeline · bash no Windows (Git Bash)[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="rigging3d", header=_HEADER, footer=_FOOTER)
