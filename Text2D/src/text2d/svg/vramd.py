"""Builder do request vramd do backend ``text2svg``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aigamekit_shared.cli_helpers import with_vramd_load_opts, with_vramd_peak_opts
from aigamekit_shared.vramd_payload import build_request_body

from .options import SvgOptions


def build_vectorize_request(
    *,
    image: str | Path,
    output: str | Path,
    opts: SvgOptions,
    gpu_ids: list[int] | str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Monta o payload do backend ``text2svg``.

    Os campos ``task``/``engine``/``model_id``/``device`` viajam no request
    porque o descriptor declara-os em ``load_keys`` — o worker ramifica por
    ``task == "vectorize"`` no ``load``.
    """

    engine = opts.engine
    if engine == "auto":
        # O backend text2svg é o motor neural; ``auto`` já foi resolvido pelo
        # chamador para vtracer/starvector, mas não deixar "auto" no load.
        engine = "starvector"
    core: dict[str, Any] = {
        "task": "vectorize",
        "image": str(Path(image).resolve()),
        "output": str(Path(output).resolve()),
        "engine": engine,
        "style": opts.style,
        "colors": int(opts.colors),
        "detail": opts.detail,
        "max_tokens": int(opts.max_tokens),
    }
    optional: dict[str, Any] = {
        "seed": opts.seed,
        "device": opts.device,
        "model_id": opts.model_id,
        "strict": bool(opts.strict),
        "max_kb": int(opts.max_kb),
        "meta": bool(opts.meta),
        "transparent": bool(opts.transparent),
    }
    payload = build_request_body(core=core, optional=optional, extra=extra)
    # O descriptor ``text2svg`` tem um bloco ``vram:`` estimado; não enviar
    # ``footprint_key`` (a 0.8.0 não conhece ``starvector-1b`` e cairia no
    # fallback de 8 GiB). O planner in-process usa ``Shared/lowvram``.
    return with_vramd_peak_opts(
        with_vramd_load_opts(payload, gpu_ids=gpu_ids),
        backend="text2svg",
    )
