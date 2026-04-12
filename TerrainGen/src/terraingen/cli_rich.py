from __future__ import annotations

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER = "[bold cyan]TerrainGen[/bold cyan] — geração procedural de terrenos"
_FOOTER = "[dim]Documentação: README[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
