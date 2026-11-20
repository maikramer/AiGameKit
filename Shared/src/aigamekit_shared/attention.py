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


def _enable_sdpa_backends() -> None:
    """Garante Flash/Mem-Efficient SDPA activos no PyTorch sem acordar contexto CUDA."""
    try:
        import torch

        cuda_backends = getattr(torch.backends, "cuda", None)
        if cuda_backends is not None:
            if hasattr(cuda_backends, "enable_flash_sdp"):
                cuda_backends.enable_flash_sdp(True)
            if hasattr(cuda_backends, "enable_mem_efficient_sdp"):
                cuda_backends.enable_mem_efficient_sdp(True)
    except Exception:
        pass


def _resolve_diffusers_attention_enum(backend: str) -> Any | None:
    """Resolve enum ``AttentionBackendName`` em diffusers 0.34+ e 0.42+."""
    enum_cls = None
    for module_path in ("diffusers.models.attention_dispatch", "diffusers.hooks"):
        try:
            mod = __import__(module_path, fromlist=["AttentionBackendName"])
            enum_cls = getattr(mod, "AttentionBackendName", None)
            if enum_cls is not None:
                break
        except ImportError:
            continue
    if enum_cls is None:
        return None

    # diffusers >=0.42 usa SAGE / FLASH / NATIVE / FLEX; versões 0.34-0.41 usavam
    # SAGE_ATTENTION / FLASH_ATTENTION / SDPA.
    candidates: dict[str, tuple[str, ...]] = {
        "sage": ("SAGE", "SAGE_ATTENTION"),
        "flash": ("FLASH", "FLASH_ATTENTION"),
        "sdpa": ("NATIVE", "SDPA"),
        "native": ("NATIVE", "SDPA"),
        "flex": ("FLEX",),
    }
    for attr in candidates.get(backend, ()):
        val = getattr(enum_cls, attr, None)
        if val is not None:
            return val
    return None


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
        backend: ``"auto"``, ``"sage"``, ``"flash"``, ``"flex"``, ou ``"sdpa"``.
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

    if backend in ("sdpa", "native"):
        _enable_sdpa_backends()

    # Tentar diffusers set_attention_backend (diffusers 0.34+ / 0.42+ attention_dispatch).
    backend_enum = _resolve_diffusers_attention_enum(backend)
    if backend_enum is not None:
        transformer = getattr(pipe, "transformer", None)
        if transformer is not None and hasattr(transformer, "set_attention_backend"):
            try:
                transformer.set_attention_backend(backend_enum)
                _log(f"Attention backend '{backend}' aplicado ao transformer")
                if apply_to_vae:
                    vae = getattr(pipe, "vae", None)
                    if vae is not None and hasattr(vae, "set_attention_backend"):
                        vae.set_attention_backend(backend_enum)
                return backend
            except Exception as exc:
                _log(f"Attention backend '{backend}' recusado pelo transformer ({exc}); fallback SDPA")
                _enable_sdpa_backends()
                return "sdpa"

    # Fallback: env vars (alguns modelos vendored lêem env diretamente).
    if backend == "sage":
        os.environ["USE_SAGEATTN"] = "1"
        _log("Attention backend 'sage' via env USE_SAGEATTN=1")
    elif backend == "flash":
        os.environ["USE_FLASH_ATTN"] = "1"
        _log("Attention backend 'flash' via env USE_FLASH_ATTN=1")

    return backend
