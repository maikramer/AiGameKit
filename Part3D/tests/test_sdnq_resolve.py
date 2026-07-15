"""Testes do mapeamento CLI quantization → preset SDNQ."""

from __future__ import annotations

import pytest
from part3d.utils.sdnq_resolve import resolve_sdnq_preset


@pytest.mark.parametrize(
    ("mode", "mem_eff", "quantize", "expected"),
    [
        ("auto", True, True, "sdnq-uint8"),
        ("auto", False, True, None),
        ("auto", True, False, None),
        ("none", True, True, None),
        ("int8", False, True, "sdnq-int8"),
        ("int8", True, True, "sdnq-int8"),
        ("int4", False, True, "sdnq-int4"),
        ("INT4", True, True, "sdnq-int4"),
        ("sdnq-uint8", False, True, "sdnq-uint8"),
        ("sdnq-int8", True, True, "sdnq-int8"),
        ("fp16", True, True, None),
    ],
)
def test_resolve_sdnq_preset(mode: str, mem_eff: bool, quantize: bool, expected: str | None):
    assert resolve_sdnq_preset(mode, memory_efficient=mem_eff, quantize_dit=quantize) == expected
