"""End-to-end rock build pipeline shared by the CLI commands.

A single :func:`build_rock_glb` runs the full pipeline — mesh generation,
erosion, scaling, texturing and GLB export — so ``generate`` and ``batch``
stay in lockstep instead of duplicating (and drifting from) the steps.

Two texturing backends:

* **bpy** (default when Blender's ``bpy`` is importable) — bakes a procedural
  object-space material to UV with a bake margin, giving *seamless* textures
  plus exported normals + tangents. See :mod:`rocks3d.bake_bpy`.
* **trimesh** (fallback) — atlas UV + 2D procedural textures via Materialize.
  Lighter, but textures can seam across UV islands.
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
    use_bpy: bool | None = None,
) -> Mapping[str, object]:
    """Generate one rock and write it to *output_path* as a GLB.

    Args:
        type_name: ``"pebble"`` or ``"boulder"``.
        output_path: Destination ``.glb`` path.
        seed: Reproducible seed (``None`` → random).
        quality: Quality tier (``fast|low|medium|high|highest``).
        scale: Uniform scale factor applied to the final mesh.
        erosion: Whether to run erosion when the preset requests passes.
        use_bpy: Texturing backend. ``None`` auto-selects bpy when available,
            else the trimesh fallback. ``True``/``False`` force the choice.

    Returns:
        A summary mapping with ``vertices``, ``faces``, ``textures`` (the map
        names embedded), ``backend`` and ``output`` (the written path).
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

    # Pebbles get the cheap spherical UV; every larger/angular type (boulder and
    # the scenery rocks) needs an atlas unwrap to texture cleanly.
    return _texture_and_export(
        mesh, preset, output_path, seed=seed or 0, use_bpy=use_bpy, spherical_uv=(type_name == "pebble")
    )


def build_formation_glb(
    style: str,
    output_path: Path,
    *,
    seed: int | None = None,
    quality: str = "medium",
    scale: float = 1.0,
    chunks: int | None = None,
    use_bpy: bool | None = None,
) -> Mapping[str, object]:
    """Generate one rock *formation* (a multi-chunk rochedo) and write it as GLB.

    Args:
        style: Formation style (see :data:`rocks3d.formation.STYLES`).
        output_path: Destination ``.glb`` path.
        seed: Reproducible seed (``None`` → random).
        quality: Quality tier driving chunk subdivision.
        scale: Uniform scale factor applied to the final mesh.
        chunks: Override the chunk count for the style.
        use_bpy: Texturing backend (``None`` auto-selects bpy when available).

    Returns:
        Summary mapping (same shape as :func:`build_rock_glb`) plus ``style``.
    """
    from rocks3d.formation import generate_formation

    # Reuse the outcrop preset purely for colour/material; geometry is the union.
    preset = get_preset("outcrop", quality)
    mesh = generate_formation(style, seed=seed, quality=quality, chunks=chunks)

    if scale != 1.0:
        mesh.apply_scale(scale)

    summary = _texture_and_export(mesh, preset, output_path, seed=seed or 0, use_bpy=use_bpy, spherical_uv=False)
    summary["style"] = style
    return summary


def _texture_and_export(
    mesh,
    preset,
    output_path: Path,
    *,
    seed: int,
    use_bpy: bool | None,
    spherical_uv: bool,
) -> dict[str, object]:
    """Shared texturing + GLB export tail for both rocks and formations."""
    if use_bpy is None:
        from rocks3d.bake_bpy import bpy_available

        use_bpy = bpy_available()

    if use_bpy:
        from rocks3d.bake_bpy import bake_and_export

        bake_and_export(mesh.vertices, mesh.faces, mesh.vertex_normals, preset, output_path, seed=seed)
        return {
            "vertices": len(mesh.vertices),
            "faces": len(mesh.faces),
            "textures": ["albedo", "normal", "roughness", "ao"],
            "backend": "bpy",
            "output": output_path,
        }

    mesh = apply_uv_spherical(mesh) if spherical_uv else apply_uv_xatlas(mesh)
    textures = generate_pbr_textures(mesh, preset, seed=seed)
    export_glb(mesh, textures, output_path)
    return {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "textures": list(textures.keys()),
        "backend": "trimesh",
        "output": output_path,
    }
