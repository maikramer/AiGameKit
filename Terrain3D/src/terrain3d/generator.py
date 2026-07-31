from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from terrain3d.postprocess import apply_postprocess_chain

DEFAULT_MODEL_ID = "xandergos/terrain-diffusion-30m"


def _resolve_model_id() -> str:
    return os.environ.get("TERRAIN3D_MODEL_ID", DEFAULT_MODEL_ID)


@dataclass
class TerrainConfig:
    """Configuration for terrain generation."""

    model_id: str = ""  # resolved lazily via _resolve_model_id
    seed: int | None = None
    size: int = 2048
    world_size: float = 512.0
    max_height: float = 50.0
    device: str | None = None
    num_inference_steps: int = 20
    dtype: str | None = None  # "fp32", "bf16", "fp16"
    cache_size: str = "100M"
    coarse_window: int = 4  # legacy: 1 coarse cell ≈ 256 px de saída;
    # a CLI deriva size = coarse_window x 256 quando --size é omitido
    offset_i: int = 0  # deslocamento da região amostrada no mundo infinito (px de saída, linhas)
    offset_j: int = 0  # idem, colunas — permite explorar outras regiões com o mesmo seed
    prompt: str | None = None  # stored as metadata only (model is unconditional)
    # --- Post-processing ---
    mode: str = "island"  # "island" | "continental"
    island_falloff: float = 0.35
    island_noise_scale: float = 0.15
    island_noise_freq: float = 3.0
    smooth_iterations: int = 3
    elevation_gamma: float = 1.2
    elevation_contrast: float = 0.1


@dataclass
class TerrainResult:
    """Result from terrain generation."""

    heightmap: np.ndarray  # float64, normalized 0-1, shape (size, size)
    config: TerrainConfig
    stats: dict[str, Any] = field(default_factory=dict)


def _native_resolution_from_model(model_id: str) -> float:
    """Derive the native resolution in meters from the model ID string.

    Args:
        model_id: HuggingFace model ID, e.g. ``xandergos/terrain-diffusion-30m``.

    Returns:
        Resolution in meters (30.0 or 90.0).
    """
    model_lower = model_id.lower()
    if "90m" in model_lower:
        return 90.0
    return 30.0


# Limites do guard-rail de escala horizontal (ratio = size x native_res / world_size).
# Acima de _SCALE_RATIO_STEEP os declives ficam artificiais (montanhas "esmagadas");
# abaixo de _SCALE_RATIO_SOFT o heightmap fica sub-resolvido (detalhe esticado).
_SCALE_RATIO_STEEP = 32.0
_SCALE_RATIO_SOFT = 1.5


def check_scale_coherence(size: int, world_size: float, native_resolution: float) -> str | None:
    """Check horizontal-scale coherence between the sampled region and the game world.

    The diffusion model produces geographically-real terrain at
    ``native_resolution`` meters per pixel.  Squeezing that region into a much
    smaller ``world_size`` multiplies every slope by the same factor, turning
    ranges into spiky walls; a much larger world just stretches the detail.

    Args:
        size: Heightmap resolution in pixels (sampled region side).
        world_size: Game-world extent in meters.
        native_resolution: Meters per output pixel of the model (30/90).

    Returns:
        Human-readable warning in Portuguese when the ratio is outside the
        sane band, ``None`` otherwise.
    """
    if world_size <= 0:
        return None
    native_extent = float(size) * float(native_resolution)
    ratio = native_extent / float(world_size)
    if ratio > _SCALE_RATIO_STEEP:
        return (
            f"escala horizontal {ratio:.0f}x — a região nativa ({native_extent / 1000:.1f} km) fica comprimida em "
            f"{world_size:.0f} m: declives {ratio:.0f}x mais íngremes que o relevo real (montanhas artificiais). "
            f"Aumente --world-size para ~{native_extent / 16:.0f} m (16x) ou reduza --size."
        )
    if ratio < _SCALE_RATIO_SOFT:
        return (
            f"escala horizontal {ratio:.2f}x — o mundo ({world_size:.0f} m) é maior que a região amostrada "
            f"({native_extent / 1000:.2f} km): detalhe esticado/suave demais. Aumente --size ou reduza --world-size."
        )
    return None


def generate_terrain(config: TerrainConfig) -> TerrainResult:
    """Generate an AI terrain heightmap via the vendored WorldPipeline.

    Loads the diffusion pipeline, binds a direct-caching context, samples a
    region of ``config.size x config.size`` pixels, and normalizes the
    elevation to 0-1 for PNG export.

    Args:
        config: Generation parameters (model, seed, size, device, dtype, etc.).

    Returns:
        TerrainResult with the normalized heightmap and timing stats.

    Raises:
        RuntimeError: If CUDA is requested but not available.
    """
    # --- Heavy imports (deferred) ---
    import torch

    from terrain3d.vendor.common.cli_helpers import parse_cache_size
    from terrain3d.vendor.inference.world_pipeline import WorldPipeline

    # --- Resolve device ---
    if config.device is not None:
        device = config.device
    elif torch.cuda.is_available():
        device = "cuda"
    else:
        device = "cpu"

    if device != "cpu" and not torch.cuda.is_available():
        raise RuntimeError(f"Device '{device}' requested but CUDA is not available")

    if device == "cpu":
        print("WARNING: Running on CPU — generation will be very slow")

    # --- Resolve parameters ---
    model_id = config.model_id or _resolve_model_id()

    native_resolution = _native_resolution_from_model(model_id)
    cache_limit = parse_cache_size(config.cache_size)
    should_compile = platform.system() == "Linux" and device != "cpu" and torch.cuda.is_available()

    t0 = time.perf_counter()

    # Preflight best-effort: garante o repo do modelo em disco (download com resume/
    # progresso) antes do load. Se falhar, o from_pretrained trata on-demand.
    if model_id and "/" in model_id and not os.path.exists(model_id):
        try:
            from aigamekit_shared.model_download import ensure_model

            ensure_model(model_id)
        except Exception:
            pass

    pipeline = WorldPipeline.from_pretrained(
        model_id,
        seed=config.seed,
        latents_batch_size=[1, 2, 4, 8, 16],
        native_resolution=native_resolution,
        num_inference_steps=config.num_inference_steps,
        caching_strategy="direct",
        cache_limit=cache_limit,
        torch_compile=should_compile,
        dtype=config.dtype,
    )

    scale_warning = check_scale_coherence(config.size, config.world_size, native_resolution)
    if scale_warning:
        print(f"WARNING: {scale_warning}")

    try:
        pipeline.to(device)
        pipeline.bind()

        # Sample the terrain region.  The residual InfiniteTensor coordinates
        # are in decoder-pixel space.  ``pipeline.get()`` runs the full
        # decode pipeline (laplacian denoise + decode) and returns elevation
        # in meters.  Offsets select which region of the infinite world is
        # sampled (same seed + different offset = different geography).
        i1 = int(config.offset_i)
        j1 = int(config.offset_j)
        result = pipeline.get(i1, j1, i1 + config.size, j1 + config.size, with_climate=False)
        elev = result["elev"]  # torch.Tensor, shape (size, size), meters

        # Convert to float64 numpy and normalize to 0-1
        heightmap = elev.cpu().numpy().astype(np.float64)
        h_min = float(heightmap.min())
        h_max = float(heightmap.max())
        if h_max - h_min > 1e-12:
            heightmap = (heightmap - h_min) / (h_max - h_min)
        else:
            heightmap = np.zeros_like(heightmap, dtype=np.float64)

        # --- Post-processing chain ---
        heightmap = apply_postprocess_chain(
            heightmap,
            mode=config.mode,
            seed=config.seed if config.seed is not None else 0,
            island_falloff_radius=config.island_falloff,
            island_noise_scale=config.island_noise_scale,
            island_noise_freq=config.island_noise_freq,
            smooth_iterations=config.smooth_iterations,
            elevation_gamma=config.elevation_gamma,
            elevation_contrast=config.elevation_contrast,
        )
    finally:
        pipeline.close()

    elapsed = time.perf_counter() - t0

    stats: dict[str, Any] = {
        "generation_time_seconds": round(elapsed, 3),
        "model_id": model_id,
        "device": str(device),
        "native_resolution": native_resolution,
        "torch_compile": should_compile,
        "num_inference_steps": config.num_inference_steps,
        "offset_i": int(config.offset_i),
        "offset_j": int(config.offset_j),
        "native_extent_m": round(float(config.size) * native_resolution, 1),
        "horizontal_scale_ratio": (
            round(float(config.size) * native_resolution / float(config.world_size), 3)
            if config.world_size > 0
            else None
        ),
        "scale_warning": scale_warning,
        "height_min_raw_meters": round(h_min, 4),
        "height_max_raw_meters": round(h_max, 4),
        "height_mean": round(float(heightmap.mean()), 6),
        "height_std": round(float(heightmap.std()), 6),
    }

    return TerrainResult(heightmap=heightmap, config=config, stats=stats)
