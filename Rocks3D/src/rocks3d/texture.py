"""Procedural albedo texture generation with Materialize CLI PBR integration.

Generates procedural rock textures using simplex noise, and optionally produces
full PBR map sets (normal, AO, smoothness) via the Materialize CLI.
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from rocks3d.defaults import RockPreset

logger = logging.getLogger(__name__)


def _parse_hex_color(hex_str: str) -> np.ndarray:
    """Parse a hex color string like ``'#7A7A6F'`` into an (3,) uint8 array.

    Args:
        hex_str: Hex color string with leading ``#``.

    Returns:
        ``(3,)`` uint8 numpy array with R, G, B values.
    """
    hex_str = hex_str.lstrip("#")
    return np.array([int(hex_str[i : i + 2], 16) for i in (0, 2, 4)], dtype=np.uint8)


def _smoothstep(t: np.ndarray) -> np.ndarray:
    """Hermite smoothstep used to interpolate value-noise lattices."""
    return t * t * (3.0 - 2.0 * t)


def _value_noise(resolution: int, cells: int, seed: int) -> np.ndarray:
    """Tileable 2D value noise in ``[0, 1]``, fully vectorised.

    Generates a ``cells x cells`` random lattice (wrapping for seamless
    tiling) and bilinearly interpolates it with smoothstep weights up to
    ``resolution``.  Far faster than per-pixel ``opensimplex`` calls.

    Args:
        resolution: Output square resolution.
        cells: Lattice resolution (frequency in cells per tile).
        seed: Random seed.

    Returns:
        ``(resolution, resolution)`` float array in ``[0, 1]``.
    """
    rng = np.random.RandomState(seed)
    lattice = rng.random((cells, cells))

    coords = np.linspace(0.0, cells, resolution, endpoint=False)
    i0 = np.floor(coords).astype(int)
    frac = coords - i0
    i0 = i0 % cells
    i1 = (i0 + 1) % cells

    wx = _smoothstep(frac)
    # Rows then columns.
    top = lattice[i0][:, i0] * (1 - wx)[None, :] + lattice[i0][:, i1] * wx[None, :]
    bot = lattice[i1][:, i0] * (1 - wx)[None, :] + lattice[i1][:, i1] * wx[None, :]
    wy = wx[:, None]
    return top * (1 - wy) + bot * wy


def _fbm_image(resolution: int, octaves: int, base_cells: int, seed: int) -> np.ndarray:
    """Multi-octave value-noise FBM image in ``[0, 1]``.

    Args:
        resolution: Output square resolution.
        octaves: Number of octaves to sum.
        base_cells: Lattice frequency of the first octave.
        seed: Base random seed (each octave offsets it).

    Returns:
        ``(resolution, resolution)`` float array normalised to ``[0, 1]``.
    """
    out = np.zeros((resolution, resolution), dtype=np.float64)
    amp = 1.0
    cells = base_cells
    total = 0.0
    for i in range(octaves):
        out += amp * _value_noise(resolution, cells, seed + i * 17)
        total += amp
        amp *= 0.5
        cells *= 2
    return out / total


def generate_albedo_texture(
    mesh: object,
    preset: RockPreset,
    seed: int = 0,
    resolution: int = 1024,
) -> np.ndarray:
    """Generate procedural albedo texture as (H, W, 3) uint8 array.

    Uses layered simplex noise to create spatially varying color between
    the two colors defined in ``preset.color_range``. A second noise octave
    adds subtle grain for a more natural rock appearance.

    Args:
        mesh: Trimesh object (used for future UV-aware generation; currently unused).
        preset: Rock preset providing ``color_range`` and noise parameters.
        seed: Random seed for reproducibility.
        resolution: Output texture resolution (square).

    Returns:
        ``(H, W, 3)`` uint8 numpy array representing the albedo texture.
    """
    color_low = _parse_hex_color(preset.color_range[0]).astype(np.float64)
    color_high = _parse_hex_color(preset.color_range[1]).astype(np.float64)

    # Low/mid-frequency FBM drives the broad color variation between the
    # two preset colors.
    t = _fbm_image(resolution, octaves=5, base_cells=4, seed=seed)
    t = np.clip((t - 0.5) * 1.6 + 0.5, 0.0, 1.0)
    albedo = color_low[None, None, :] * (1.0 - t[:, :, None]) + color_high[None, None, :] * t[:, :, None]

    # High-frequency grain breaks up flat areas.
    grain = _fbm_image(resolution, octaves=3, base_cells=32, seed=seed + 100)
    albedo += (grain - 0.5)[:, :, None] * 26.0

    # Cavity / ambient-occlusion darkening: low-frequency dark blotches
    # mimic dirt and self-shadowed crevices, the biggest cue that a flat
    # texture is wrapped on stone rather than plastic.
    cavity = _fbm_image(resolution, octaves=4, base_cells=6, seed=seed + 200)
    cavity = np.clip(cavity, 0.0, 1.0)
    darken = 0.55 + 0.45 * cavity  # multiplier in [0.55, 1.0]
    albedo *= darken[:, :, None]

    return np.clip(albedo, 0.0, 255.0).astype(np.uint8)


def generate_pbr_with_materialize(
    albedo_path: Path,
    output_dir: Path,
) -> dict[str, Path]:
    """Call Materialize CLI to generate PBR maps from an albedo image.

    Uses the ``materialize`` binary (resolved via ``MATERIALIZE_BIN`` env var
    or ``PATH``) with the ``stone`` preset appropriate for rock textures.

    Args:
        albedo_path: Path to the albedo/diffuse PNG image.
        output_dir: Directory where PBR maps will be written.

    Returns:
        Dict mapping map names (``"normal"``, ``"ao"``, ``"smoothness"``)
        to their file paths. Falls back to an empty dict if Materialize is
        not found or fails.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    materialize_bin: str
    try:
        from gamedev_shared.env import MATERIALIZE_BIN
        from gamedev_shared.subprocess_utils import resolve_binary

        materialize_bin = resolve_binary(MATERIALIZE_BIN, "materialize")
    except (ImportError, FileNotFoundError):
        logger.warning("Materialize CLI not found; skipping PBR generation")
        return {}

    # materialize <input> -o <dir> -p stone (rock-appropriate preset)
    cmd = [
        materialize_bin,
        str(albedo_path),
        "-o",
        str(output_dir),
        "-p",
        "stone",
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            logger.warning("Materialize failed (exit %d): %s", result.returncode, result.stderr.strip())
            return {}
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Materialize execution error: %s", exc)
        return {}

    # Materialize outputs <stem>_<map>.png
    stem = albedo_path.stem
    maps: dict[str, Path] = {}
    for map_name in ("normal", "ao", "smoothness"):
        path = output_dir / f"{stem}_{map_name}.png"
        if path.exists():
            maps[map_name] = path
        else:
            logger.debug("Expected Materialize output not found: %s", path)

    return maps


def _generate_fallback_normal_map(albedo: np.ndarray, output_path: Path) -> Path:
    """Generate a simple normal map from albedo luminance using Sobel-like gradients.

    Args:
        albedo: ``(H, W, 3)`` uint8 albedo array.
        output_path: Where to save the normal map PNG.

    Returns:
        Path to the generated normal map.
    """
    # BT.601 luminance weights
    luminance = (
        albedo[:, :, 0].astype(np.float64) * 0.2126
        + albedo[:, :, 1].astype(np.float64) * 0.7152
        + albedo[:, :, 2].astype(np.float64) * 0.0722
    ) / 255.0

    # Sobel-like gradient
    dx = np.zeros_like(luminance)
    dy = np.zeros_like(luminance)
    dx[:, 1:-1] = luminance[:, 2:] - luminance[:, :-2]
    dy[1:-1, :] = luminance[2:, :] - luminance[:-2, :]

    # Normal from gradient (strength factor)
    strength = 2.0
    normal = np.stack([-dx * strength, -dy * strength, np.ones_like(luminance)], axis=-1)
    norm = np.linalg.norm(normal, axis=-1, keepdims=True)
    normal = normal / norm

    # Map from [-1, 1] to [0, 255]
    normal_rgb = ((normal + 1.0) * 0.5 * 255.0).astype(np.uint8)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    img = Image.fromarray(normal_rgb, mode="RGB")
    img.save(str(output_path))

    return output_path


def _generate_fallback_roughness_map(albedo: np.ndarray, output_path: Path, seed: int = 0) -> Path:
    """Generate a grayscale roughness map when Materialize is unavailable.

    Darker (cavity/dirt) areas of the albedo are made rougher, with a touch
    of high-frequency noise so the surface is not uniformly glossy.

    Args:
        albedo: ``(H, W, 3)`` uint8 albedo array.
        output_path: Where to save the roughness PNG.
        seed: Random seed for the noise layer.

    Returns:
        Path to the generated roughness map.
    """
    luminance = albedo.mean(axis=2) / 255.0
    resolution = luminance.shape[0]
    # Rougher where darker; rocks are quite rough overall (~0.7-0.95).
    rough = 0.95 - 0.25 * luminance
    rough += (_fbm_image(resolution, octaves=3, base_cells=24, seed=seed + 300) - 0.5) * 0.10
    rough = np.clip(rough, 0.0, 1.0)
    rough_u8 = (rough * 255.0).astype(np.uint8)

    output_path.parent.mkdir(parents=True, exist_ok=True)

    from PIL import Image

    Image.fromarray(rough_u8, mode="L").save(str(output_path))
    return output_path


def generate_pbr_textures(
    mesh: object,
    preset: RockPreset,
    seed: int = 0,
    output_dir: Path | None = None,
) -> dict[str, Path]:
    """High-level PBR texture generation: albedo → Materialize → fallback.

    Generates a procedural albedo texture from the rock preset, saves it as a
    temporary PNG, then attempts to generate PBR maps via Materialize. If
    Materialize is unavailable, produces a basic fallback normal map derived
    from the albedo luminance.

    Args:
        mesh: Trimesh object (passed through to :func:`generate_albedo_texture`).
        preset: Rock preset with color range and noise parameters.
        seed: Random seed for reproducibility.
        output_dir: Directory for output files. Defaults to a temp directory.

    Returns:
        Dict mapping map names to file paths. Always includes ``"albedo"``.
        May include ``"normal"``, ``"ao"``, ``"smoothness"`` if Materialize
        succeeds, or just ``"normal"`` (fallback) if it does not.
    """
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="rocks3d_pbr_"))
    else:
        output_dir.mkdir(parents=True, exist_ok=True)

    albedo = generate_albedo_texture(mesh, preset, seed=seed)
    albedo_path = output_dir / "albedo.png"

    from PIL import Image

    img = Image.fromarray(albedo, mode="RGB")
    img.save(str(albedo_path))

    results: dict[str, Path] = {"albedo": albedo_path}

    pbr_maps = generate_pbr_with_materialize(albedo_path, output_dir)
    if pbr_maps:
        results.update(pbr_maps)
    else:
        fallback_normal_path = output_dir / "albedo_normal.png"
        _generate_fallback_normal_map(albedo, fallback_normal_path)
        results["normal"] = fallback_normal_path
        fallback_rough_path = output_dir / "albedo_roughness.png"
        _generate_fallback_roughness_map(albedo, fallback_rough_path, seed=seed)
        results["roughness"] = fallback_rough_path
        logger.info("Generated fallback normal + roughness maps in %s", output_dir)

    return results
