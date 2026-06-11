"""Detecção genérica de hardware CUDA para perfis automáticos por ferramenta.

Cada ferramenta (text3d, text2d, paint3d, …) define os próprios tiers a partir
das specs devolvidas aqui; este módulo só responde "que GPUs existem" e "o
kill-switch de auto-detecção está ligado?".
"""

from __future__ import annotations

import os

GIB = 1024**3

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def hw_auto_enabled(env_var: str) -> bool:
    """True salvo se ``env_var`` estiver em 0/false/no/off (kill-switch)."""
    return os.environ.get(env_var, "1").strip().lower() not in _FALSE_VALUES


def cuda_gpu_specs() -> list[tuple[int, int]]:
    """Lista (índice, VRAM total em bytes) das GPUs CUDA visíveis.

    Respeita ``CUDA_VISIBLE_DEVICES``. Vazia sem CUDA disponível.
    """
    import torch

    if not torch.cuda.is_available():
        return []
    specs: list[tuple[int, int]] = []
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        specs.append((i, int(props.total_memory)))
    return specs
