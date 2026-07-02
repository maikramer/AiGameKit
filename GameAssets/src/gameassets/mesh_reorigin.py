"""Reposicionar GLB para origem nos pés (base Y=0, XZ centrados) em espaço glTF Y-up."""

from __future__ import annotations

import contextlib
import fnmatch
import math
from pathlib import Path
from typing import Any


def _clean_bpy_scene() -> None:
    """Remove all objects, meshes, armatures, and actions from the current bpy scene."""
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in bpy.data.meshes:
        if block.users == 0:
            bpy.data.meshes.remove(block)
    for block in bpy.data.armatures:
        if block.users == 0:
            bpy.data.armatures.remove(block)
    for block in bpy.data.actions:
        bpy.data.actions.remove(block)


def _compute_reorigin_offset(
    min_corner: Any, max_corner: Any, mode: str, up_axis: int = 1
) -> tuple[float, float, float]:
    """Compute translation offset for a given AABB and mode.

    Args:
        min_corner: AABB minimum (x, y, z).
        max_corner: AABB maximum (x, y, z).
        mode: One of ``"feet"``, ``"center"``, ``"none"``.
        up_axis: Index of the *up* axis in the given coordinate space —
            1 (Y) for glTF-space bounds, 2 (Z) for Blender-world bounds.
            ``feet`` puts the AABB base at 0 along this axis and centers
            the other two.

    Returns:
        (dx, dy, dz) translation vector.
    """
    if mode == "none":
        return (0.0, 0.0, 0.0)
    center = [-0.5 * (min_corner[i] + max_corner[i]) for i in range(3)]
    if mode == "center":
        return (center[0], center[1], center[2])
    offset = list(center)
    offset[up_axis] = -float(min_corner[up_axis])
    return (offset[0], offset[1], offset[2])


def reorigin_glb_bpy(path: Path, mode: str = "feet") -> bool:
    """Reorigin a GLB file using bpy (preserves armatures, animations, materials).

    Args:
        path: Path to the GLB file (modified in-place).
        mode: ``"feet"`` (min Y=0, center XZ), ``"center"``, or ``"none"``.

    Returns:
        True on success, False if bpy is not available or on error.
    """
    try:
        import bpy
    except ImportError:
        return False

    path = path.resolve()
    if path.suffix.lower() != ".glb":
        return False

    try:
        bpy.ops.wm.read_factory_settings(use_empty=True)
        bpy.ops.import_scene.gltf(filepath=str(path))

        # Compute world-space AABB across all mesh objects
        min_x = min_y = min_z = math.inf
        max_x = max_y = max_z = -math.inf
        has_mesh = False

        for obj in bpy.data.objects:
            if obj.type != "MESH" or not obj.data.vertices:
                continue
            has_mesh = True
            for v in obj.data.vertices:
                world = obj.matrix_world @ v.co
                min_x = min(min_x, world.x)
                min_y = min(min_y, world.y)
                min_z = min(min_z, world.z)
                max_x = max(max_x, world.x)
                max_y = max(max_y, world.y)
                max_z = max(max_z, world.z)

        if not has_mesh:
            _clean_bpy_scene()
            return True

        # Bounds above are in Blender world space (Z-up); the glTF importer
        # maps glTF +Y (up) to Blender +Z. "feet" must therefore zero the
        # Blender *Z* minimum — zeroing Blender Y would (and, before this
        # fix, did) shift the model along glTF Z instead of dropping its
        # base to glTF Y=0.
        dx, dy, dz = _compute_reorigin_offset(
            (min_x, min_y, min_z),
            (max_x, max_y, max_z),
            mode,
            up_axis=2,
        )

        if dx == 0.0 and dy == 0.0 and dz == 0.0:
            _clean_bpy_scene()
            return True

        # Apply translation to ALL objects in the scene
        for obj in bpy.data.objects:
            obj.location.x += dx
            obj.location.y += dy
            obj.location.z += dz

        bpy.ops.export_scene.gltf(
            filepath=str(path),
            export_format="GLB",
            use_selection=False,
            export_normals=True,
            export_tangents=True,
        )
    except Exception:
        return False
    finally:
        with contextlib.suppress(Exception):
            _clean_bpy_scene()

    return True


def reorigin_glb_file(path: Path, mode: str = "feet") -> None:
    """Reorigin a GLB file using bpy (preserves armatures, animations, materials).

    Args:
        path: Path to the GLB file (modified in-place).
        mode: ``"feet"`` (default), ``"center"``, or ``"none"``.

    Raises:
        ImportError: If ``bpy`` is not available.
    """
    if not reorigin_glb_bpy(path, mode):
        raise ImportError("bpy is required for mesh reorigin but is not available")


def collect_glb_paths(root: Path, *, recursive: bool) -> list[Path]:
    root = root.resolve()
    if root.is_file():
        return [root] if root.suffix.lower() == ".glb" else []
    if not root.is_dir():
        return []
    if recursive:
        return sorted(root.rglob("*.glb"))
    return sorted(root.glob("*.glb"))


def filter_excluded_paths(paths: list[Path], excludes: tuple[str, ...]) -> list[Path]:
    """Remove caminhos cujo nome de ficheiro coincide com algum padrão ``fnmatch`` (case-insensitive)."""
    if not excludes:
        return paths
    patterns = tuple(p.strip() for p in excludes if p.strip())
    if not patterns:
        return paths
    out: list[Path] = []
    for p in paths:
        name = p.name
        if any(fnmatch.fnmatch(name.lower(), pat.lower()) for pat in patterns):
            continue
        out.append(p)
    return out
