"""Testes do corpo de request UMS partilhado (``gamedev_shared.ums_payload``).

Cobre a montagem por precedência (prompt/output → core → optional → extra).
Os builders por-tool (que usam este módulo) são exercitados nas suites de
cobertura de cada tool.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from gamedev_shared.ums_payload import build_request_body


class TestBuildRequestBody:
    def test_prompt_output_coerced_to_str(self) -> None:
        body = build_request_body(prompt=123, output=Path("o.png"))
        assert body == {"prompt": "123", "output": "o.png"}

    def test_core_verbatim(self) -> None:
        core: dict[str, Any] = {"width": 512, "guidance": 4.5, "seed": None}
        body = build_request_body(core=core)
        assert body == core

    def test_optional_skips_none(self) -> None:
        body = build_request_body(optional={"a": 1, "b": None, "c": "x"})
        assert body == {"a": 1, "c": "x"}

    def test_optional_keeps_false_and_zero(self) -> None:
        body = build_request_body(optional={"flag": False, "n": 0})
        assert body == {"flag": False, "n": 0}

    def test_extra_wins_over_everything(self) -> None:
        body = build_request_body(
            prompt="p",
            core={"a": "core", "b": "core"},
            optional={"b": "optional", "c": "optional"},
            extra={"b": "extra", "c": "extra", "d": "extra"},
        )
        assert body == {"prompt": "p", "a": "core", "b": "extra", "c": "extra", "d": "extra"}

    def test_empty_body(self) -> None:
        assert build_request_body() == {}

    def test_extra_none_is_noop(self) -> None:
        body = build_request_body(prompt="p", extra=None)
        assert body == {"prompt": "p"}

    def test_extra_none_values_kept(self) -> None:
        """``extra`` é merge bruto — valores None do caller passam (overrides)."""
        body = build_request_body(prompt="p", extra={"seed": None})
        assert body == {"prompt": "p", "seed": None}

    def test_mixed_pipeline_like(self) -> None:
        """Formato típico de um builder de tool (prompt + core + optional + extra)."""
        body = build_request_body(
            prompt="a castle",
            output="out.png",
            core={"width": 512, "steps": 4, "seed": None},
            optional={"negative_prompt": "blur", "model_id": None, "preset": "Wood"},
            extra={"ground": True},
        )
        assert body == {
            "prompt": "a castle",
            "output": "out.png",
            "width": 512,
            "steps": 4,
            "seed": None,
            "negative_prompt": "blur",
            "preset": "Wood",
            "ground": True,
        }

    def test_no_prompt_key_when_none(self) -> None:
        """Tools sem prompt (text3d/paint3d) passam prompt=None — sem chave."""
        body = build_request_body(core={"from_image": "f.png"})
        assert body == {"from_image": "f.png"}
        assert "prompt" not in body
