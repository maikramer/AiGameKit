"""Builders de request vramd para Text2D generate (CLI + GameAssets)."""

from __future__ import annotations

from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body


def build_generate_request(
    *,
    prompt: str,
    output: str,
    width: int | None = 1024,
    height: int | None = 1024,
    steps: int = 16,
    guidance: float | None = None,
    seed: int | None = None,
    model_id: str | None = None,
    gpu_ids: list[int] | str | None = None,
    memory_efficient: bool | None = None,
    quant_preset: str | None = None,
    footprint_key: str | None = None,
    torch_compile: bool = False,
    torch_compile_mode: str | None = None,
    channels_last: bool = False,
    step_cache: str | None = None,
    allow_group_offload: bool | None = None,
    category: str | None = None,
    transparent: bool | None = None,
    refs: list[str] | None = None,
    boxes: list[dict[str, Any]] | None = None,
    num_images: int | None = None,
    upsample: bool | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta payload vramd text2d com peak/load opts.

    ``width``/``height`` ``None`` = auto (o pipeline deriva o tamanho da 1.ª
    referência no workflow image_conditioned). ``guidance`` None = omitido
    (FLUX.3 Klein é guidance-distilled; o parâmetro é inerte).
    """
    if allow_group_offload is not None:
        extra = {**(extra or {}), "allow_group_offload": bool(allow_group_offload)}
    optional: dict[str, Any] = {
        "model_id": model_id,
        "torch_compile_mode": torch_compile_mode,
        "step_cache": step_cache,
        "category": category,
        "guidance": float(guidance) if guidance is not None else None,
        "refs": list(refs) if refs else None,
        "boxes": list(boxes) if boxes else None,
        "num_images": int(num_images) if num_images else None,
        "upsample": bool(upsample) if upsample else None,
    }
    # Transparente só viaja quando True (contrato vramd: chave opcional).
    if transparent:
        optional["transparent"] = True
    core: dict[str, Any] = {
        "steps": int(steps),
        "seed": seed,
        "torch_compile": bool(torch_compile),
        "channels_last": bool(channels_last),
    }
    if width is not None:
        core["width"] = int(width)
    if height is not None:
        core["height"] = int(height)
    payload = build_request_body(
        prompt=prompt,
        output=output,
        core=core,
        optional=optional,
        extra=extra,
    )

    mem = bool(memory_efficient) if memory_efficient is not None else False
    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2d",
        memory_efficient=mem,
        quant_preset=quant_preset,
        footprint_key=footprint_key,
    )
