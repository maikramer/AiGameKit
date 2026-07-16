"""Configuração Rich + rich-click para o CLI Paint3D (delegate para gamedev_shared)."""

from __future__ import annotations

from gamedev_shared.cli_rich import setup_rich_click_module

click, RICH_CLICK = setup_rich_click_module(
    tool="paint3d",
    header="[bold cyan]Paint3D[/bold cyan] — Textura 3D · Hunyuan3D-Paint + Materialize PBR",
    footer="[dim]paint3d doctor · docs/PAINT_SETUP.md[/dim]",
)
