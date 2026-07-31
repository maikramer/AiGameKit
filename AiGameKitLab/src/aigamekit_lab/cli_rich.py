"""Configuração Rich + rich-click para o CLI AiGameKitLab."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]AiGameKitLab[/bold cyan] — benchmarking e inspeção"
_FOOTER: Final = "[dim]Ferramentas de diagnóstico e benchmarks[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="aigamekit-lab", header=_HEADER, footer=_FOOTER)
