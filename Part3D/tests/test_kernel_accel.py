"""Testes do kernel accel Part3D (FlashVDM / hierarchical / auto)."""

from __future__ import annotations

import pytest
from part3d.utils.kernel_accel import resolve_mc_algo, resolve_volume_decoder


@pytest.mark.parametrize(
    ("mode", "quality", "mem_eff", "expected"),
    [
        ("auto", "highest", False, "hierarchical"),
        ("auto", "high", True, "flashvdm"),
        ("auto", "medium", True, "flashvdm"),
        ("auto", "fast", True, "flashvdm"),
        ("auto", "medium", False, "hierarchical"),
        ("flashvdm", "highest", False, "flashvdm"),
        ("hierarchical", "fast", True, "hierarchical"),
        ("vanilla", None, True, "vanilla"),
        ("fast", "highest", False, "fast"),
        ("auto", "highest", True, "flashvdm"),
    ],
)
def test_resolve_volume_decoder(mode: str, quality: str | None, mem_eff: bool, expected: str):
    assert resolve_volume_decoder(mode, quality=quality, memory_efficient=mem_eff) == expected


def test_resolve_volume_decoder_invalid():
    with pytest.raises(ValueError, match="volume_decoder"):
        resolve_volume_decoder("bogus")


def test_resolve_mc_algo_mc():
    assert resolve_mc_algo("mc") == "mc"
    assert resolve_mc_algo(None) == "mc"


def test_resolve_mc_algo_dmc_cpu_fallback():
    assert resolve_mc_algo("dmc", device="cpu") == "mc"
