"""Configuração Rich + rich-click para o CLI Text2D (delegate para gamedev_shared)."""

from __future__ import annotations

from gamedev_shared.cli_rich import setup_rich_click_module

click, RICH_CLICK = setup_rich_click_module(
    tool="text2d",
    header="[bold cyan]Text2D[/bold cyan] — FLUX.2 Klein · texto → imagem",
    footer="[dim]Documentação: README / docs · Cache HF: ~/.cache/huggingface[/dim]",
)
