"""Configuração Rich + rich-click para o CLI Skymap2D."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Skymap2D[/bold cyan] — skymaps equirectangular 360° · FLUX.1-dev + LoRA local"
_FOOTER: Final = "[dim]Documentação: README · Token: HF_TOKEN ou HUGGINGFACEHUB_API_TOKEN[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="skymap2d", header=_HEADER, footer=_FOOTER)
