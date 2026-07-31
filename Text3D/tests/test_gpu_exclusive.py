"""Verificação de GPU quase exclusiva antes de carregar modelos."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from text3d.utils.memory import DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT, enforce_exclusive_gpu

_TOTAL_MIB = 8192
_THRESHOLD_MIB = int(_TOTAL_MIB * DEFAULT_EXCLUSIVE_GPU_MAX_USED_PCT)


def test_enforce_noop_when_allow_shared() -> None:
    enforce_exclusive_gpu(allow_shared=True, max_used_pct=0.01)


def test_enforce_noop_when_mem_unknown() -> None:
    with patch("aigamekit_shared.gpu.gpu_bytes_in_use", return_value=None):
        enforce_exclusive_gpu(allow_shared=False, max_used_pct=0.01)


def test_enforce_ok_when_under_limit() -> None:
    mib = _THRESHOLD_MIB - 50
    with (
        patch("aigamekit_shared.gpu.gpu_bytes_in_use", return_value=mib * 1024 * 1024),
        patch("aigamekit_shared.gpu.gpu_total_mib", return_value=_TOTAL_MIB),
    ):
        enforce_exclusive_gpu(allow_shared=False)


def test_enforce_raises_when_over_limit() -> None:
    mib = _THRESHOLD_MIB + 50
    with (
        patch("aigamekit_shared.gpu.gpu_bytes_in_use", return_value=mib * 1024 * 1024),
        patch("aigamekit_shared.gpu.gpu_total_mib", return_value=_TOTAL_MIB),
        pytest.raises(RuntimeError, match="GPU com"),
    ):
        enforce_exclusive_gpu(allow_shared=False)
