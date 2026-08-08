"""Testes do formatador de debug vramd em cli_helpers."""

from __future__ import annotations

import pytest
from click.exceptions import ClickException

from aigamekit_shared.cli_helpers import format_vramd_debug_line, raise_if_vramd_queue_full


class TestFormatUmsDebugLine:
    def test_from_ums_debug_block(self) -> None:
        line = format_vramd_debug_line(
            {
                "ums_debug": {
                    "backend": "text2icon",
                    "priority": "interactive",
                    "job_id": "abcdef12-3456-7890",
                    "queue_wait_sec": 0.12,
                    "generate_sec": 1.5,
                    "affinity_cuts": 2,
                    "loaded_backends": ["text2icon"],
                }
            }
        )
        assert "backend=text2icon" in line
        assert "pri=interactive" in line
        assert "job=abcdef12…" in line
        assert "wait=0.12s" in line
        assert "gen=1.5s" in line
        assert "cuts=2" in line
        assert "loaded=['text2icon']" in line

    def test_falls_back_to_top_level(self) -> None:
        line = format_vramd_debug_line({"backend": "alpha", "priority": "batch", "job_id": "zzzzzzzz"})
        assert "backend=alpha" in line
        assert "pri=batch" in line
        assert "job=zzzzzzzz…" in line


class TestRaiseIfUmsQueueFull:
    def test_noop_on_ok(self) -> None:
        raise_if_vramd_queue_full({"status": "ok"})

    def test_raises_with_code_and_hint(self) -> None:
        with pytest.raises(ClickException) as exc:
            raise_if_vramd_queue_full(
                {
                    "status": "queue_full",
                    "error": "fila cheia (1)",
                    "error_code": "QUEUE_FULL",
                    "queue_depth": 1,
                    "max_depth": 1,
                    "hint": "aumenta VRAMD_MAX_QUEUE_DEPTH",
                    "ums_debug": {"backend": "beta", "priority": "batch"},
                }
            )
        msg = str(exc.value)
        assert "QUEUE_FULL" in msg
        assert "1/1" in msg
        assert "backend=beta" in msg
