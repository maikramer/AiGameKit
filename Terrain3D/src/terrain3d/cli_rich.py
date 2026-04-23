from __future__ import annotations

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER = "[bold cyan]Terrain3D[/bold cyan] — AI terrain generation via diffusion models"
_FOOTER = "[dim]Documentation: README[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
