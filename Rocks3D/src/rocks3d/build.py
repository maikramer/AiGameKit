"""End-to-end rock build pipeline shared by the CLI commands.

A single :func:`build_rock_glb` runs the full pipeline — mesh generation,
erosion, scaling, UV mapping, PBR texture generation and GLB export — so
``generate`` and ``batch`` stay in lockstep instead of duplicating (and
drifting from) the steps.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from rocks3d.defaults import get_preset
from rocks3d.erosion import apply_erosion
from rocks3d.exporter import export_glb
from rocks3d.generator import generate_rock
from rocks3d.texture import generate_pbr_textures
from rocks3d.uv_mapping import apply_uv_spherical, apply_uv_xatlas

if TYPE_CHECKING:
    from collections.abc import Mapping


def build_rock_glb(
    type_name: str,
    output_path: Path,
    *,
    seed: int | None = None,
    quality: str = "medium",
    scale: float = 1.0,
    erosion: bool = True,
) -> Mapping[str, object]:
    """Generate one rock and write it to *output_path* as a GLB.

    Args:
        type_name: ``"pebble"`` or ``"boulder"``.
        output_path: Destination ``.glb`` path.
        seed: Reproducible seed (``None`` → random).
        quality: Quality tier (``fast|low|medium|high|highest``).
        scale: Uniform scale factor applied to the final mesh.
        erosion: Whether to run erosion when the preset requests passes.

    Returns:
        A summary mapping with ``vertices``, ``faces``, ``textures`` (the map
        names embedded) and ``output`` (the written path).
    """
    preset = get_preset(type_name, quality)

    mesh = generate_rock(type_name, seed=seed, quality=quality)

    if erosion and preset.erosion_passes > 0:
        mesh = apply_erosion(
            mesh,
            seed=seed or 0,
            passes=preset.erosion_passes,
            strength=preset.erosion_strength,
        )

    if scale != 1.0:
        mesh.apply_scale(scale)

    mesh = apply_uv_xatlas(mesh) if type_name == "boulder" else apply_uv_spherical(mesh)

    textures = generate_pbr_textures(mesh, preset, seed=seed or 0)

    export_glb(mesh, textures, output_path)

    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "textures": list(textures.keys()),
        "output": output_path,
    }
