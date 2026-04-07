"""Reposicionar GLB para origem nos pés (base Y=0, XZ centrados) em espaço glTF Y-up."""

from __future__ import annotations

import fnmatch
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


def _as_scene(loaded: Any) -> trimesh.Scene:
    if isinstance(loaded, trimesh.Scene):
        return loaded
    if isinstance(loaded, trimesh.Trimesh):
        return trimesh.Scene(geometry={"mesh": loaded})
    raise TypeError(f"Formato não suportado: {type(loaded)}")


def reorigin_scene_feet_yup(scene: trimesh.Scene) -> None:
    """Translada a cena inteira: AABB mundial com min Y=0 e centro em XZ."""
    bounds = scene.bounds
    if bounds is None:
        return
    dx = -0.5 * (bounds[0][0] + bounds[1][0])
    dy = -float(bounds[0][1])
    dz = -0.5 * (bounds[0][2] + bounds[1][2])
    T = trimesh.transformations.translation_matrix([dx, dy, dz])
    scene.apply_transform(T)


def reorigin_glb_file(path: Path) -> None:
    """
    Lê um GLB, aplica origem nos pés, grava por cima (ficheiro temporário + replace).
    """
    path = path.resolve()
    if path.suffix.lower() != ".glb":
        raise ValueError(f"Apenas .glb é suportado (recebido: {path.suffix})")

    loaded = trimesh.load(str(path), force=None)
    scene = _as_scene(loaded)
    reorigin_scene_feet_yup(scene)

    tmp = path.with_name(path.name + ".reorigin.tmp")
    try:
        scene.export(str(tmp), file_type="glb")
        tmp.replace(path)
    except OSError:
        if tmp.is_file():
            tmp.unlink(missing_ok=True)
        raise


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
