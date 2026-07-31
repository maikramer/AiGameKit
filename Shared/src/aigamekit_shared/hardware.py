"""Detecção genérica de hardware CUDA para perfis automáticos por ferramenta.

Cada ferramenta (text3d, text2d, paint3d, …) define os próprios tiers a partir
das specs devolvidas aqui; este módulo só responde "que GPUs existem", "o
kill-switch de auto-detecção está ligado?" e fornece a base comum dos perfis
(:class:`HardwareProfileBase` + :func:`detect_profile`) que as 9 tools com
``hardware.py`` replicavam (~35 linhas de boilerplate cada).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

GIB = 1024**3

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


def hw_auto_enabled(env_var: str) -> bool:
    """True salvo se ``env_var`` estiver em 0/false/no/off (kill-switch)."""
    return os.environ.get(env_var, "1").strip().lower() not in _FALSE_VALUES


def cuda_gpu_specs() -> list[tuple[int, int]]:
    """Lista (índice, VRAM total em bytes) das GPUs CUDA visíveis.

    Respeita ``CUDA_VISIBLE_DEVICES``. Vazia sem torch/CUDA disponível.
    """
    try:
        import torch
    except ImportError:
        return []

    if not torch.cuda.is_available():
        return []
    specs: list[tuple[int, int]] = []
    for i in range(torch.cuda.device_count()):
        props = torch.cuda.get_device_properties(i)
        specs.append((i, int(props.total_memory)))
    return specs


@dataclass(frozen=True)
class HardwareProfileBase:
    """Base partilhada dos perfis de hardware por-tool (``hardware.py``).

    Campos comuns às 8 tools com ``hardware.py`` (T2D, T2Icon, Texture2D,
    Skymap2D, T2Sound, Text3D, Paint3D, Part3D): identidade + GPUs — todos
    obrigatórios (as 8 tools tinham-nos sem default). Cada tool estende com
    os campos do seu planner (``memory_efficient``, ``sdnq_preset``, clamps,
    …) e implementa ``summary()`` com os campos que lhe interessam::

        @dataclass(frozen=True)
        class Text2IconHardwareProfile(HardwareProfileBase):
            cpu_offload: bool
            max_width: int | None
            max_height: int | None
            transformer_id: str = ""
            transformer_sdnq_preset: str | None = None

    A construção é sempre por kwargs (``profile_from_specs``), por isso a
    ordem dos campos herdados não é uma restrição.
    """

    name: str
    device: str  # "cuda" | "cpu"
    gpu_ids: list[int] | None  # >1 GPU: split multi-GPU; senão None
    total_vram_gib: float


def detect_profile(
    profile_from_specs: Callable[[list[tuple[int, int]]], Any],
) -> Any:
    """Deteta GPUs CUDA e resolve o perfil da tool (padrão das 9 tools).

    Args:
        profile_from_specs: Função pura da tool que mapeia
            ``list[(índice, VRAM bytes)]`` → perfil. Testável sem GPU.

    Returns:
        O perfil resolvido (tipo da tool).
    """
    return profile_from_specs(cuda_gpu_specs())


def cuda_gpu_free_specs() -> list[tuple[int, int, int]]:
    """Lista (índice, VRAM livre, VRAM total) em bytes das GPUs CUDA visíveis.

    Livre = ``torch.cuda.mem_get_info`` (conta consumo de outros processos,
    ex. desktop). Útil para escolher a GPU menos ocupada em rigs multi-GPU.
    """
    try:
        import torch
    except ImportError:
        return []

    if not torch.cuda.is_available():
        return []
    specs: list[tuple[int, int, int]] = []
    for i in range(torch.cuda.device_count()):
        try:
            free, total = torch.cuda.mem_get_info(i)
        except RuntimeError:
            props = torch.cuda.get_device_properties(i)
            free, total = props.total_memory, props.total_memory
        specs.append((i, int(free), int(total)))
    return specs
