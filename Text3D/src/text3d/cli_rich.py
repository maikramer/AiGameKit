"""Configuração Rich + rich-click para o CLI Text3D (delegate para aigamekit_shared)."""

from __future__ import annotations

from aigamekit_shared.cli_rich import setup_rich_click_module

click, RICH_CLICK = setup_rich_click_module(
    tool="text3d",
    header="[bold cyan]Text3D[/bold cyan] — Text2D + Hunyuan3D · mesh a partir de texto",
    footer="[dim]Primeira execução: downloads HF · text3d doctor · docs/[/dim]",
)
