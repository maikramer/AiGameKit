"""Attention backend selection — escolhe o kernel de attention mais rápido.

Wrappers sobre diffusers attention backends + detecção de disponibilidade:
  - SageAttention: atenção int8 quantizada. 2-2.7x speedup. Requer pip install
    sageattention. Ampere (sm_80)+.
  - FlashAttention2: O(N) memory via tiling. Largamente sobreposto por SDPA.
  - SDPA (default): PyTorch scaled_dot_product_attention — auto-dispatcha para
    FlashAttention/Memory-Efficient conforme disponível.

Aplicar SÓ ao transformer (NÃO ao VAE — SageAttention pode causar stuck decode
em alguns pipelines de vídeo/VAE).

Env vars:
  - ``AIGAMEKIT_ATTENTION_BACKEND``: ``"auto"`` (default) | ``"sage"`` | ``"flash"`` | ``"sdpa"``
"""

from __future__ import annotations

import os
from typing import Any

from .logging import Logger

_logger = Logger()


def is_sage_available() -> bool:
    """Verifica se sageattention está instalado."""
    try:
        import sageattention  # noqa: F401

        return True
    except ImportError:
        return False


def is_flash_available() -> bool:
    """Verifica se flash_attn está instalado."""
    try:
        import flash_attn  # noqa: F401

        return True
    except ImportError:
        return False


def get_attention_backend() -> str:
    """Backend de attention preferido (env ``AIGAMEKIT_ATTENTION_BACKEND``).

    Auto-detect: sage → flash → sdpa (default seguro).
    """
    requested = os.environ.get("AIGAMEKIT_ATTENTION_BACKEND", "auto").strip().lower()
    if requested == "auto":
        if is_sage_available():
            return "sage"
        if is_flash_available():
            return "flash"
        return "sdpa"
    return requested


def select_attention_backend(
    pipe: Any,
    *,
    backend: str = "auto",
    apply_to_vae: bool = False,
    log_fn: Any | None = None,
) -> str:
    """Selecciona e aplica o backend de attention ao pipeline.

    Args:
        pipe: pipeline diffusers.
        backend: ``"auto"``, ``"sage"``, ``"flash"``, ou ``"sdpa"``.
        apply_to_vae: se True, aplica também ao VAE (default False — SageAttention
            pode causar stuck decode em VAEs de vídeo).
        log_fn: callback de logging.

    Returns:
        Nome do backend efectivamente aplicado.
    """

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    if backend == "auto":
        backend = get_attention_backend()

    # Tentar diffusers set_attention_backend se disponível (diffusers 0.34+).
    try:
        from diffusers.hooks import AttentionBackendName

        backend_map = {
            "sage": getattr(AttentionBackendName, "SAGE_ATTENTION", None),
            "flash": getattr(AttentionBackendName, "FLASH_ATTENTION", None),
            "sdpa": getattr(AttentionBackendName, "SDPA", None),
        }
        backend_enum = backend_map.get(backend)
        if backend_enum is not None:
            transformer = getattr(pipe, "transformer", None)
            if transformer is not None and hasattr(transformer, "set_attention_backend"):
                transformer.set_attention_backend(backend_enum)
                _log(f"Attention backend '{backend}' aplicado ao transformer")
                if apply_to_vae:
                    vae = getattr(pipe, "vae", None)
                    if vae is not None and hasattr(vae, "set_attention_backend"):
                        vae.set_attention_backend(backend_enum)
                return backend
    except ImportError:
        pass

    # Fallback: env vars (alguns modelos vendored lêem env diretamente).
    if backend == "sage":
        os.environ["USE_SAGEATTN"] = "1"
        _log("Attention backend 'sage' via env USE_SAGEATTN=1")
    elif backend == "flash":
        os.environ["USE_FLASH_ATTN"] = "1"
        _log("Attention backend 'flash' via env USE_FLASH_ATTN=1")

    return backend
