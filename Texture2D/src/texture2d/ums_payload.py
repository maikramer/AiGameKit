"""Builders de request UMS para Texture2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_ums_load_opts, with_ums_peak_opts
from aigamekit_shared.ums_payload import build_request_body


def build_generate_request(
    *,
    prompt: str,
    output: str,
    width: int = 512,
    height: int = 512,
    steps: int = 20,
    guidance: float = 7.5,
    seed: int | None = None,
    negative_prompt: str | None = None,
    preset: str | None = None,
    ground: bool = False,
    model_id: str | None = None,
    gpu_ids: list[int] | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload UMS texture2d com peak/load opts."""
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core={
            "width": int(width),
            "height": int(height),
            "steps": int(steps),
            "guidance": float(guidance),
            "seed": seed,
            "ground": bool(ground),
        },
        optional={
            "negative_prompt": negative_prompt,
            "preset": preset,
            "model_id": model_id,
        },
        extra=extra,
    )

    return with_ums_peak_opts(
        with_ums_load_opts(payload, gpu_ids=gpu_ids),
        backend="texture2d",
    )
