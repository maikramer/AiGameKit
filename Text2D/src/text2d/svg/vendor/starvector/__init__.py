"""Vendored StarVector inference code (Apache-2.0, see LICENSE/NOTICE).

Imports are lazy so ``text2d --help`` does not pull torch.
"""

from __future__ import annotations

__all__ = ["StarVectorConfig", "StarVectorForCausalLM"]


def __getattr__(name: str):
    if name == "StarVectorConfig":
        from .starvector_arch import StarVectorConfig

        return StarVectorConfig
    if name == "StarVectorForCausalLM":
        from .starvector_arch import StarVectorForCausalLM

        return StarVectorForCausalLM
    raise AttributeError(name)
