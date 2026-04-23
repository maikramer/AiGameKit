"""Tests for seed utilities."""

from __future__ import annotations

from gamedev_shared.seed_utils import generate_seed, resolve_effective_seed, seed_everything


def test_generate_seed_range():
    for _ in range(100):
        s = generate_seed()
        assert 0 <= s < 2**32


def test_resolve_effective_seed_with_value():
    assert resolve_effective_seed(42) == 42


def test_resolve_effective_seed_none():
    s = resolve_effective_seed(None)
    assert isinstance(s, int)
    assert 0 <= s < 2**32


def test_seed_everything_deterministic():
    import random

    seed_everything(123)
    a = random.random()
    seed_everything(123)
    b = random.random()
    assert a == b
