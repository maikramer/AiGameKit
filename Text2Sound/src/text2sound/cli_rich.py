"""Configuração Rich + rich-click para o CLI Text2Sound."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Text2Sound[/bold cyan] — text-to-audio · Open 1.0 (música) / Open Small (efeitos)"
_FOOTER: Final = "[dim]README · HF_TOKEN · --profile music|effects · modelos no Hugging Face[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="text2sound", header=_HEADER, footer=_FOOTER)
