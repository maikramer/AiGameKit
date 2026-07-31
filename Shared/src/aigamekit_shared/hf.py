"""Hugging Face Hub — token e cache (sem dependência de huggingface_hub)."""

from __future__ import annotations

import os

from .env import HF_HOME

HF_TOKEN = "HF_TOKEN"
HUGGINGFACEHUB_API_TOKEN = "HUGGINGFACEHUB_API_TOKEN"


def get_hf_token() -> str | None:
    """Token HF: ``HF_TOKEN`` ou ``HUGGINGFACEHUB_API_TOKEN``; vazio → ``None``."""
    for key in (HF_TOKEN, HUGGINGFACEHUB_API_TOKEN):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def hf_home_display_rich(
    *,
    default_label: str = "[dim]~/.cache/huggingface (defeito)[/dim]",
) -> str:
    """Valor de ``HF_HOME`` para tabelas Rich, ou etiqueta por defeito."""
    raw = os.environ.get(HF_HOME, "").strip()
    return raw if raw else default_label
