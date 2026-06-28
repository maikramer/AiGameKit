"""Textura rápida (cor sólida / ruído FBM) sem inferência Hunyuan."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import numpy as np

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


def _apply_vertex_colors(obj: Any, rgb: np.ndarray) -> None:
    """Aplica cores por vértice ``(N, 3)`` em ``[0, 1]`` ao objecto bpy via color attribute."""
    mesh = obj.data
    n = len(mesh.vertices)
    if rgb.shape[0] != n:
        raise ValueError(f"cores {rgb.shape[0]} ≠ {n} vértices")

    rgb_clamped = np.clip(rgb, 0, 1)

    # Modern color_attributes API (Blender 4.x+ / bpy 4.x+)
    if hasattr(mesh, "color_attributes") and hasattr(mesh.color_attributes, "new"):
        color_attr = mesh.color_attributes.new(name="Col", type="FLOAT_COLOR", domain="CORNER")
    elif hasattr(mesh, "vertex_colors") and hasattr(mesh.vertex_colors, "new"):
        color_attr = mesh.vertex_colors.new(name="Col")
    else:
        raise RuntimeError("bpy mesh has no color_attributes or vertex_colors API")

    for poly in mesh.polygons:
        for loop_idx in poly.loop_indices:
            vi = mesh.loops[loop_idx].vertex_index
            r, g, b = float(rgb_clamped[vi, 0]), float(rgb_clamped[vi, 1]), float(rgb_clamped[vi, 2])
            color_attr.data[loop_idx].color = (r, g, b, 1.0)


def _get_combined_bounds(objects: list) -> tuple[np.ndarray, np.ndarray]:
    """AABB combinado ``(min_corner, max_corner)`` de uma lista de objectos bpy."""
    from gamedev_shared.bpy_mesh import get_bounds

    all_mins = []
    all_maxs = []
    for obj in objects:
        mn, mx = get_bounds(obj)
        all_mins.append(np.array(mn))
        all_maxs.append(np.array(mx))
    if not all_mins:
        return np.zeros(3), np.zeros(3)
    min_corner = np.min(all_mins, axis=0)
    max_corner = np.max(all_maxs, axis=0)
    return min_corner, max_corner


def _apply_translation(objects: list, offset: np.ndarray) -> None:
    """Desloca vértices de todos os objectos (in-place, sem alterar location)."""
    for obj in objects:
        for v in obj.data.vertices:
            v.co[0] += float(offset[0])
            v.co[1] += float(offset[1])
            v.co[2] += float(offset[2])
        obj.data.update()


def bake_solid_color(
    mesh_in: str | Path,
    mesh_out: str | Path,
    *,
    color_hex: str = "#888888",
    preserve_origin: bool = True,
) -> Path:
    r, g, b = parse_hex_rgb(color_hex)
    objects = load_mesh_trimesh(mesh_in)
    if not objects:
        raise ValueError(f"Sem mesh objects em {mesh_in}")

    if preserve_origin:
        bounds_min_before, bounds_max_before = _get_combined_bounds(objects)

    obj = objects[0]
    n = len(obj.data.vertices)
    rgb = np.tile(np.array([[r, g, b]], dtype=np.float64), (n, 1))
    _apply_vertex_colors(obj, rgb)

    if preserve_origin:
        bounds_min_after, bounds_max_after = _get_combined_bounds(objects)
        centroid_before = (bounds_min_before + bounds_max_before) * 0.5
        centroid_after = (bounds_min_after + bounds_max_after) * 0.5
        offset = centroid_before - centroid_after
        if np.dot(offset, offset) > 1e-12:
            _apply_translation(objects, offset)

    return save_glb(objects, mesh_out)


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
    Cor por vértice = ``tint`` x (variação FBM em torno de 1).
    ``contrast`` ∈ [0,1] controla quanto o ruído modula o tom (estilo pedra).
    """
    r0, g0, b0 = parse_hex_rgb(tint_hex)
    objects = load_mesh_trimesh(mesh_in)
    if not objects:
        raise ValueError(f"Sem mesh objects em {mesh_in}")

    if preserve_origin:
        bounds_min_before, bounds_max_before = _get_combined_bounds(objects)

    obj = objects[0]
    mesh_data = obj.data
    v = np.array([tuple(vv.co) for vv in mesh_data.vertices], dtype=np.float64)
    q = normalize_to_unit_cube(v)
    noise = fbm3(q, frequency=frequency, octaves=octaves, seed=seed)
    # map [-1,1] -> multiplicador [1-c, 1+c] do tom base
    c = float(np.clip(contrast, 0.01, 1.0))
    m = 1.0 + noise * c
    r = np.clip(r0 * m, 0, 1)
    g = np.clip(g0 * m, 0, 1)
    b = np.clip(b0 * m, 0, 1)
    rgb = np.stack([r, g, b], axis=1)
    _apply_vertex_colors(obj, rgb)

    if preserve_origin:
        bounds_min_after, bounds_max_after = _get_combined_bounds(objects)
        centroid_before = (bounds_min_before + bounds_max_before) * 0.5
        centroid_after = (bounds_min_after + bounds_max_after) * 0.5
        offset = centroid_before - centroid_after
        if np.dot(offset, offset) > 1e-12:
            _apply_translation(objects, offset)

    return save_glb(objects, mesh_out)
