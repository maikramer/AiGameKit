"""Step caching — acelera inferência saltando transformer blocks entre steps.

Wrappers sobre os hooks nativos do diffusers (diffusers.hooks):
  - FirstBlockCache: cacheia o output do 1º block, usa-o para prever quando os
    blocks restantes podem ser skipped. ~2x speedup. Lossy mas visualmente
    imperceptível na maioria dos casos.
  - TaylorSeerCache: cache-then-forecast via Taylor series. Melhor qualidade/speed.

Estas técnicas NÃO reduzem VRAM (podem até aumentar ligeiramente — cached
activations). Pertencem ao tier de velocidade: só activar quando o modelo já cabe
na GPU (offload == "none").

Env vars:
  - ``GAMEDEV_STEP_CACHE``: ``"auto"`` (default) | ``"first_block"`` | ``"taylorseer"`` | ``"off"``
"""

from __future__ import annotations

import os
from typing import Any

from .logging import Logger

_logger = Logger()


def get_step_cache_mode() -> str:
    """Modo de step cache activo (env ``GAMEDEV_STEP_CACHE``)."""
    return os.environ.get("GAMEDEV_STEP_CACHE", "off").strip().lower()


def apply_step_cache(
    pipe: Any,
    *,
    method: str = "auto",
    threshold: float = 0.05,
    log_fn: Any | None = None,
) -> bool:
    """Aplica step caching ao transformer do pipeline.

    Args:
        pipe: pipeline diffusers.
        method: ``"auto"`` (detectar melhor), ``"first_block"``, ``"taylorseer"``,
            ou ``"off"``.
        threshold: threshold de skip (primeiro_block_cache). Maior = mais agressivo.
        log_fn: callback de logging.

    Returns:
        True se aplicado; False se indisponível ou method="off".
    """
    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    if method == "off":
        return False

    transformer = getattr(pipe, "transformer", None)
    if transformer is None:
        return False

    try:
        if method in ("auto", "first_block"):
            from diffusers.hooks import FirstBlockCacheConfig, apply_first_block_cache

            apply_first_block_cache(transformer, FirstBlockCacheConfig(threshold=threshold))
            _log(f"Step cache (FirstBlockCache, threshold={threshold}) aplicado ao transformer")
            return True

        if method == "taylorseer":
            from diffusers.hooks import TaylorSeerCacheConfig, apply_taylorseer_cache

            config = TaylorSeerCacheConfig()
            apply_taylorseer_cache(transformer, config)
            _log("Step cache (TaylorSeer) aplicado ao transformer")
            return True

    except ImportError as e:
        _log(f"Step cache indisponível (diffusers hooks: {e})")
    except Exception as e:
        _log(f"Step cache falhou ({e})")

    return False
