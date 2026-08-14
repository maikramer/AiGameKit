"""Builders de request vramd para Texture2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body


def _normalize_ground(ground: str | bool | None) -> str | None:
    """O gerador compara ``ground == "on"/"auto"`` — bool nunca correspondia.

    ``True`` → ``"on"``, ``False`` → ``"off"``, string passa como está
    (``auto|on|off``), ``None`` → chave omitida (o adapter aplica ``auto``).
    """
    if ground is None:
        return None
    if isinstance(ground, bool):
        return "on" if ground else "off"
    s = str(ground).strip().lower()
    return s if s in ("auto", "on", "off") else "auto"


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
    ground: str | bool | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd texture2d com peak/load opts."""
    norm_ground = _normalize_ground(ground)
    core: dict[str, Any] = {
        "width": int(width),
        "height": int(height),
        "steps": int(steps),
        "guidance": float(guidance),
        "seed": seed,
    }
    if norm_ground is not None:
        core["ground"] = norm_ground
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core=core,
        optional={
            "negative_prompt": negative_prompt,
            "preset": preset,
            "model_id": model_id,
        },
        extra=extra,
    )

    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="texture2d",
    )
