"""Kernel acceleration for Part3D — FlashVDM/hierarchical VAE + attention + compile.

Mirrors Text3D ``_configure_acceleration`` patterns for Hunyuan3D-Part ShapeVAE.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

VOLUME_DECODER_CHOICES = ("auto", "hierarchical", "flashvdm", "vanilla", "fast")


def resolve_volume_decoder(
    mode: str | None,
    *,
    quality: str | None = None,
    memory_efficient: bool = False,
) -> str:
    """Resolve volume decoder mode.

    - ``hierarchical``: sparse near-surface (best fidelity among sparse)
    - ``flashvdm``: hierarchical + top-k KV (~40% less decode time, slight detail loss)
    - ``vanilla``: dense volume via VAE ``latents2mesh``
    - ``fast``: Space ``latent2mesh_2`` / ``extract_geometry_fast`` (legacy)
    - ``auto``: hierarchical for high/highest; flashvdm if memory-efficient; else hierarchical
    """
    m = (mode or "auto").strip().lower()
    if m not in VOLUME_DECODER_CHOICES:
        raise ValueError(f"volume_decoder inválido: {mode!r}; escolha {VOLUME_DECODER_CHOICES}")
    if m != "auto":
        return m
    # VRAM baixa: FlashVDM mesmo em high/highest (hierarchical densifica pico decode).
    if memory_efficient:
        return "flashvdm"
    q = (quality or "").strip().lower()
    if q in ("high", "highest"):
        return "hierarchical"
    return "hierarchical"


def resolve_mc_algo(mc_algo: str | None, *, device: str = "cuda") -> str:
    """Resolve surface extractor; fall back to ``mc`` if ``dmc``/diso unavailable."""
    algo = (mc_algo or "mc").strip().lower()
    if algo != "dmc":
        return algo if algo in ("mc", "dmc") else "mc"
    if device != "cuda":
        return "mc"
    try:
        import diso  # noqa: F401
    except ImportError:
        return "mc"
    return "dmc"


def enable_sage_attention_env() -> str:
    """Set Hunyuan CA sage env if SageAttention available. Returns backend label."""
    from gamedev_shared.attention import get_attention_backend

    backend = get_attention_backend()
    if backend == "sage":
        os.environ.setdefault("CA_USE_SAGEATTN", "1")
        os.environ.setdefault("USE_SAGEATTN", "1")
    return backend


def configure_vae_acceleration(
    vae: Any,
    *,
    volume_decoder: str,
    mc_algo: str = "mc",
    log_fn: Callable[[str], None] | None = None,
) -> str:
    """Install volume decoder + surface extractor on Space ShapeVAE.

    Returns the effective decode path: ``latents2mesh`` or ``latent2mesh_2``.
    """

    def _log(msg: str) -> None:
        if log_fn:
            log_fn(msg)

    mode = volume_decoder.strip().lower()
    if mode == "fast":
        _log("Volume decode: fast (latent2mesh_2 / extract_geometry_fast)")
        return "latent2mesh_2"

    mc = resolve_mc_algo(mc_algo, device="cuda")
    try:
        from partgen.models.autoencoders.surface_extractors import SurfaceExtractors
        from partgen.models.autoencoders.volume_decoders import VanillaVolumeDecoder
    except ImportError as e:
        _log(f"AVISO: Space surface extractors indisponíveis ({e}); fallback fast path")
        return "latent2mesh_2"

    if mode == "flashvdm":
        from .flashvdm_decode import FlashVDMVolumeDecoding

        vae.volume_decoder = FlashVDMVolumeDecoding(topk_mode="mean")
    elif mode == "hierarchical":
        from .flashvdm_decode import HierarchicalVolumeDecoding

        vae.volume_decoder = HierarchicalVolumeDecoding()
    elif mode == "vanilla":
        vae.volume_decoder = VanillaVolumeDecoder()
    else:
        raise ValueError(f"volume_decoder desconhecido: {mode}")

    try:
        vae.surface_extractor = SurfaceExtractors[mc]()
    except Exception as e:
        _log(f"AVISO: surface extractor {mc} falhou ({e}); a usar mc")
        vae.surface_extractor = SurfaceExtractors["mc"]()
        mc = "mc"

    _log(f"Volume decode: {mode} via latents2mesh (mc_algo={mc})")
    return "latents2mesh"


def apply_channels_last_modules(
    modules: list[Any],
    *,
    log_fn: Callable[[str], None] | None = None,
) -> None:
    """Best-effort channels_last on CUDA modules (quality-neutral; may help VAE)."""
    from gamedev_shared.quantization import apply_channels_last

    names = []
    for mod in modules:
        if mod is None:
            continue
        try:
            apply_channels_last(mod)
            names.append(type(mod).__name__)
        except Exception:
            continue
    if names and log_fn:
        log_fn(f"channels_last: {', '.join(names)}")


def compile_modules(
    modules: dict[str, Any],
    *,
    mode: str = "default",
    cpu_offload: bool = False,
    log_fn: Callable[[str], None] | None = None,
) -> dict[str, Any]:
    """torch.compile nos módulos pedidos (tipicamente DiT e/ou ShapeVAE).

    Não passar Conditioner — ``torch_cluster.fps`` rebenta Dynamo (fake tensor).
    Com CPU offload, ``resolve_torch_compile_mode`` evita CUDA graphs.
    """
    from gamedev_shared.quantization import apply_torch_compile, resolve_torch_compile_mode

    offload = "sequential_cpu" if cpu_offload else "none"
    resolved = resolve_torch_compile_mode(
        mode,
        offload=offload,
        group_offload_active=False,
    )
    out = dict(modules)
    if offload in ("model_cpu", "sequential_cpu") and resolved != mode and log_fn:
        log_fn(f"torch.compile mode={mode} → {resolved} (offload={offload})")
    for name, mod in modules.items():
        if mod is None:
            continue
        try:
            compiled = apply_torch_compile(
                mod,
                mode=resolved,
                offload=offload,
                group_offload_active=False,
            )
            out[name] = compiled
        except Exception as e:
            if log_fn:
                log_fn(f"AVISO: torch.compile {name} falhou ({e})")
            out[name] = mod
    if log_fn:
        log_fn(f"torch.compile ({resolved}) activo: {', '.join(k for k, v in out.items() if v is not None)}")
    return out
