"""Configuração Rich + rich-click para o CLI Text2Icon."""

from __future__ import annotations

from typing import Final

from aigamekit_shared.cli_rich import setup_rich_click_module

_HEADER: Final = "[bold cyan]Text2Icon[/bold cyan] — text-to-icon · Sana Sprint 0.6B (NVlabs/Sana)"
_FOOTER: Final = "[dim]Documentação: README · Token: HF_TOKEN · Modelo: TEXT2ICON_MODEL_ID[/dim]"

click, RICH_CLICK = setup_rich_click_module(tool="text2icon", header=_HEADER, footer=_FOOTER)
