"""Textura rápida (cor sólida / ruído FBM) sem inferência Hunyuan."""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import trimesh

from .procedural_noise import fbm3, normalize_to_unit_cube
from .utils.mesh_io import load_mesh_trimesh, save_glb


def parse_hex_rgb(hex_raw: str) -> tuple[float, float, float]:
    s = hex_raw.strip()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        raise ValueError(f"Cor hex inválida: {hex_raw!r} (use #RRGGBB)")
    r = int(s[0:2], 16) / 255.0
    g = int(s[2:4], 16) / 255.0
    b = int(s[4:6], 16) / 255.0
    return (r, g, b)


def mesh_with_vertex_color(mesh: trimesh.Trimesh, rgb: np.ndarray) -> trimesh.Trimesh:
    """
    ``rgb`` (N,3) em [0,1]. Aplica ColorVisuals ao mesh (export glTF com cor por vértice).
    """
    m = mesh.copy()
    n = len(m.vertices)
    if rgb.shape[0] != n:
        raise ValueError(
            f"cores {rgb.shape[0]} ≠ {n} vértices"
        )
    rgba = np.concatenate([np.clip(rgb, 0, 1), np.ones((n, 1), dtype=np.float64)], axis=1)
    m.visual = trimesh.visual.color.ColorVisuals(vertex_colors=(rgba * 255).astype(np.uint8))
    return m


def _restore_feet_origin(mesh: trimesh.Trimesh) -> None:
    """Igual ao pós-Paint: base AABB em Y=0, XZ centrados."""
    bounds = mesh.bounds
    cx = (bounds[0][0] + bounds[1][0]) * 0.5
    cy = float(bounds[0][1])
    cz = (bounds[0][2] + bounds[1][2]) * 0.5
    mesh.apply_translation([-cx, -cy, -cz])


def bake_solid_color(
    mesh_in: str | Path,
    mesh_out: str | Path,
    *,
    color_hex: str = "#888888",
    preserve_origin: bool = True,
) -> Path:
    r, g, b = parse_hex_rgb(color_hex)
    mesh = load_mesh_trimesh(mesh_in)
    if preserve_origin:
        _restore_feet_origin(mesh)
    n = len(mesh.vertices)
    rgb = np.tile(np.array([[r, g, b]], dtype=np.float64), (n, 1))
    out = mesh_with_vertex_color(mesh, rgb)
    return save_glb(out, mesh_out)


def bake_perlin_vertex(
    mesh_in: str | Path,
    mesh_out: str | Path,
    *,
    frequency: float = 4.0,
    octaves: int = 4,
    seed: int = 0,
    tint_hex: str = "#7a7268",
    contrast: float = 0.55,
    preserve_origin: bool = True,
) -> Path:
    """
    Cor por vértice = ``tint`` × (variação FBM em torno de 1).
    ``contrast`` ∈ [0,1] controla quanto o ruído modula o tom (estilo pedra).
    """
    r0, g0, b0 = parse_hex_rgb(tint_hex)
    mesh = load_mesh_trimesh(mesh_in)
    if preserve_origin:
        _restore_feet_origin(mesh)
    v = mesh.vertices
    q = normalize_to_unit_cube(v)
    noise = fbm3(q, frequency=frequency, octaves=octaves, seed=seed)
    # map [-1,1] -> multiplicador [1-c, 1+c] do tom base
    c = float(np.clip(contrast, 0.01, 1.0))
    m = 1.0 + noise * c
    r = np.clip(r0 * m, 0, 1)
    g = np.clip(g0 * m, 0, 1)
    b = np.clip(b0 * m, 0, 1)
    rgb = np.stack([r, g, b], axis=1)
    out = mesh_with_vertex_color(mesh, rgb)
    return save_glb(out, mesh_out)
