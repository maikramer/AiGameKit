"""Configuração Rich + rich-click para o CLI GameAssets (delegate para gamedev_shared)."""

from __future__ import annotations

from gamedev_shared.cli_rich import setup_rich_click_module

click, RICH_CLICK = setup_rich_click_module(
    tool="gameassets",
    header="[bold cyan]GameAssets[/bold cyan] — batch de prompts e assets alinhados ao jogo",
    footer="[dim]TEXT2D_BIN / TEXT3D_BIN / RIGGING3D_BIN / MATERIALIZE_BIN se os comandos não estiverem no PATH[/dim]",
)
