"""Execução de text2d / text3d via subprocess — delegate para aigamekit_shared."""

from __future__ import annotations

from aigamekit_shared.subprocess_utils import (
    RunResult,
    merge_subprocess_output,
    resolve_binary,
    run_cmd,
)

__all__ = ["RunResult", "merge_subprocess_output", "resolve_binary", "run_cmd"]
