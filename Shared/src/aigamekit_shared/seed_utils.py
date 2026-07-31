"""Seed utilities for reproducible generation."""

from __future__ import annotations

import os
import random
import secrets


def generate_seed() -> int:
    """Generate a random seed in ``[0, 2**32)``."""
    return secrets.randbelow(2**32)


def resolve_effective_seed(seed: int | None) -> int:
    """Return *seed* if given, otherwise generate a new random one."""
    return seed if seed is not None else generate_seed()


def seed_everything(seed: int) -> None:
    """Set seeds for random, numpy, torch, and env for reproducibility.

    Safe to call without numpy/torch installed — those imports are skipped
    if the packages are unavailable.
    """
    random.seed(seed)
    os.environ["PL_GLOBAL_SEED"] = str(seed)
    try:
        import numpy as np

        np.random.seed(seed)
    except ImportError:
        pass
    try:
        import torch

        torch.manual_seed(seed)
    except ImportError:
        pass
