from __future__ import annotations

import os
import platform
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

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
    coarse_window: int = 4  # number of coarse tiles to generate (each ~7.7km for 30m model)
    prompt: str | None = None  # stored as metadata only (model is unconditional)


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

    pipeline = WorldPipeline.from_pretrained(
        model_id,
        seed=config.seed,
        latents_batch_size=[1, 2, 4, 8, 16],
        native_resolution=native_resolution,
        caching_strategy="direct",
        cache_limit=cache_limit,
        torch_compile=should_compile,
        dtype=config.dtype,
    )

    try:
        pipeline.to(device)
        pipeline.bind()

        # Sample the terrain region.  The residual InfiniteTensor coordinates
        # are in decoder-pixel space.  ``pipeline.get()`` runs the full
        # decode pipeline (laplacian denoise + decode) and returns elevation
        # in meters.
        result = pipeline.get(0, 0, config.size, config.size, with_climate=False)
        elev = result["elev"]  # torch.Tensor, shape (size, size), meters

        # Convert to float64 numpy and normalize to 0-1
        heightmap = elev.cpu().numpy().astype(np.float64)
        h_min = float(heightmap.min())
        h_max = float(heightmap.max())
        if h_max - h_min > 1e-12:
            heightmap = (heightmap - h_min) / (h_max - h_min)
        else:
            heightmap = np.zeros_like(heightmap, dtype=np.float64)
    finally:
        pipeline.close()

    elapsed = time.perf_counter() - t0

    stats: dict[str, Any] = {
        "generation_time_seconds": round(elapsed, 3),
        "model_id": model_id,
        "device": str(device),
        "native_resolution": native_resolution,
        "torch_compile": should_compile,
        "height_min_raw_meters": round(h_min, 4),
        "height_max_raw_meters": round(h_max, 4),
        "height_mean": round(float(heightmap.mean()), 6),
        "height_std": round(float(heightmap.std()), 6),
    }

    return TerrainResult(heightmap=heightmap, config=config, stats=stats)
