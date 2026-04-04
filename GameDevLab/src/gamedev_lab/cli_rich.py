"""Configuração Rich + rich-click para o CLI GameDevLab."""

from __future__ import annotations

from typing import Final

from gamedev_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]GameDevLab[/bold cyan] — benchmarking e inspeção"
_FOOTER: Final = "[dim]Ferramentas de diagnóstico e benchmarks[/dim]"

click, RICH_CLICK = setup_rich_click_module(header=_HEADER, footer=_FOOTER)
