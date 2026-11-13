"""Peças mínimas que o upstream StarVector espera do stack de treino.

O pacote original (`joanrod/star-vector`) fixa ``transformers==4.49`` e
``torch==2.5.1`` e arrasta ``fairscale``/``omegaconf``/``taming``. Aqui só
corre o inference path, no venv do Text2D (transformers 5.x), por isso as
dependências de treino são substituídas por stubs sem efeito.
"""

from __future__ import annotations

from typing import Any

import torch


def checkpoint_wrapper(module: Any, *args: Any, **kwargs: Any) -> Any:
    """No-op do ``fairscale``: não há gradient checkpointing em inferência."""

    return module


DTYPE_MAPPING: dict[str, torch.dtype] = {
    "fp16": torch.float16,
    "bf16": torch.bfloat16,
    "fp32": torch.float32,
}


def print_trainable_parameters(model: Any) -> None:
    """O upstream imprime no construtor; o worker tem stdout JSONL — silêncio."""

    return None
