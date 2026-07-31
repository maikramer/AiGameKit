"""Hooks de progresso / cancel cooperativo para pipelines Diffusers.

Usado pelos generators 2D (text2icon, text2d, texture2d, skymap2d) e pelos
adapters UMS. O cancel a meio do CUDA não mata o kernel — interrompe no próximo
``callback_on_step_end`` via ``GenerationAborted``.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any


class GenerationAborted(Exception):
    """Geração abortada por cancel UMS (cooperativo)."""


def attach_step_hooks(
    pipe_kwargs: dict[str, Any],
    *,
    num_inference_steps: int,
    should_abort: Callable[[], bool] | None = None,
    on_step: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """Injeta ``callback_on_step_end`` em ``pipe_kwargs`` (in-place + return).

    Args:
        pipe_kwargs: Kwargs a passar ao ``pipe(...)``.
        num_inference_steps: Total de steps (para pct).
        should_abort: Se devolver True, levanta ``GenerationAborted``.
        on_step: ``(step_1based, total) -> None`` para progresso.
    """
    if should_abort is None and on_step is None:
        return pipe_kwargs

    total = max(1, int(num_inference_steps))

    def callback_on_step_end(pipeline: Any, step: Any, timestep: Any, callback_kwargs: dict) -> dict:
        cur = int(step) + 1
        if on_step is not None:
            with contextlib.suppress(Exception):
                on_step(cur, total)
        if should_abort is not None and should_abort():
            # Prefer interrupt nativo quando existir (diffusers recente).
            if hasattr(pipeline, "_interrupt"):
                pipeline._interrupt = True
            raise GenerationAborted("cancelled during diffusion")
        return callback_kwargs

    pipe_kwargs["callback_on_step_end"] = callback_on_step_end
    return pipe_kwargs
