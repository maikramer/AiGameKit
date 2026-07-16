"""Exclusive volume partition — paradigma pós-X-Part.

Problema antigo
---------------
X-Part regenera cada AABB **independente** → volumes sobrepostos (sobra) ou
carve isotrópico que come a parede (corrosão). Face-paste em cima agrava.

Paradigma
---------
Partes = **partição**, não composição:

1. Gerar candidatos (X-Part / face) como antes.
2. Resolver ownership: cada face fica na parte cuja superfície está mais perto.
3. Sem carve por feature fina; sem overlay de duas malhas no mesmo sítio.

Próximo passo (ainda não aqui): voxelizar o mesh **uma** vez e extrair MC
por label exclusiva — elimina regen independente na origem.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import trimesh


def _sample_surface(mesh: trimesh.Trimesh, n: int) -> np.ndarray:
    n = max(1, int(n))
    if len(mesh.faces) == 0 or len(mesh.vertices) == 0:
        return np.zeros((0, 3), dtype=np.float64)
    try:
        pts, _ = trimesh.sample.sample_surface(mesh, n)
        return np.asarray(pts, dtype=np.float64)
    except Exception:
        v = np.asarray(mesh.vertices, dtype=np.float64)
        if len(v) <= n:
            return v
        idx = np.linspace(0, len(v) - 1, n, dtype=np.int64)
        return v[idx]


def _face_centroids(mesh: trimesh.Trimesh) -> np.ndarray:
    v = np.asarray(mesh.vertices, dtype=np.float64)
    f = np.asarray(mesh.faces)
    return v[f].mean(axis=1)


def exclusive_surface_partition(
    scene: trimesh.Scene,
    *,
    samples_per_part: int = 4000,
    min_keep_faces: int = 8,
) -> trimesh.Scene:
    """Drop faces closer to another part's surface than to own.

    Args:
        scene: Part meshes named ``part_*``.
        samples_per_part: Surface samples used as ownership probes.
        min_keep_faces: Drop part if fewer faces survive.

    Returns:
        New scene; parts with no surviving faces omitted.
    """
    items: list[tuple[str, trimesh.Trimesh]] = []
    for name, geom in scene.geometry.items():
        if isinstance(geom, trimesh.Trimesh) and len(geom.faces) > 0:
            items.append((str(name), geom))
    if len(items) <= 1:
        return scene

    samples: list[np.ndarray] = []
    for _, mesh in items:
        samples.append(_sample_surface(mesh, samples_per_part))

    try:
        from scipy.spatial import cKDTree
    except Exception as e:  # pragma: no cover
        raise ImportError("exclusive_surface_partition requires scipy") from e

    trees = [cKDTree(s) if len(s) else None for s in samples]

    out = trimesh.Scene()
    for i, (name, mesh) in enumerate(items):
        cents = _face_centroids(mesh)
        if len(cents) == 0 or trees[i] is None:
            continue
        own_d, _ = trees[i].query(cents, k=1, workers=-1)
        keep = np.ones(len(cents), dtype=bool)
        for j, tree in enumerate(trees):
            if j == i or tree is None:
                continue
            other_d, _ = tree.query(cents, k=1, workers=-1)
            # Empate → fica no dono actual (evita flicker).
            keep &= own_d <= other_d
        if int(keep.sum()) < int(min_keep_faces):
            continue
        faces = np.asarray(mesh.faces)[keep]
        part = trimesh.Trimesh(vertices=np.asarray(mesh.vertices).copy(), faces=faces, process=False)
        part.remove_unreferenced_vertices()
        if len(part.faces) == 0:
            continue
        out.add_geometry(part, geom_name=name, node_name=name)
    return out if len(out.geometry) else scene


def partition_stats(before: trimesh.Scene, after: trimesh.Scene) -> dict[str, Any]:
    """Face counts before/after for logging."""
    b = {n: len(g.faces) for n, g in before.geometry.items() if isinstance(g, trimesh.Trimesh)}
    a = {n: len(g.faces) for n, g in after.geometry.items() if isinstance(g, trimesh.Trimesh)}
    dropped = {n: b[n] - a.get(n, 0) for n in b}
    return {
        "parts_before": len(b),
        "parts_after": len(a),
        "faces_before": int(sum(b.values())),
        "faces_after": int(sum(a.values())),
        "faces_dropped": int(sum(dropped.values())),
        "per_part_dropped": dropped,
    }
