"""Helpers para controlos geométricos Hunyuan3D-Omni (bbox / pose / point / voxel).

OmniEncoder exige exactamente um sinal de controlo por forward.
``bbox_to_corners``: ``half=bbox/2``, cantos ``±half``. Docs Omni: bbox em
``[0,1]`` (eixo maior=1 → cantos ±0.5). O espaço mesh/MC é ``[-1,1]`` (2u) —
há margem. Bbox com eixo maior=2 enche o cubo MC (±1) e **corta** a malha.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import torch

# Aspect Omni (docs 0-1). NÃO usar 2.0 — isso enche o grid MC e clipa.
OMNI_BBOX_AXIS_MAX: float = 1.0

# Fallback ``control_type=none``: aspect humanoid (não cubo — engorda bipedes).
DEFAULT_OMNI_BBOX: tuple[float, float, float] = (0.45, 1.0, 0.35)

CONTROL_TYPES = frozenset({"none", "bbox", "pose", "point", "voxel"})


def normalize_mesh_vertices(vertices: np.ndarray, scale: float = 0.9999) -> np.ndarray:
    """Centra e escala vértices para caber em ``[-scale, scale]`` (como demos Omni)."""
    bbox_min = vertices.min(axis=0)
    bbox_max = vertices.max(axis=0)
    center = (bbox_max + bbox_min) / 2.0
    extent = float((bbox_max - bbox_min).max())
    if extent <= 0:
        return vertices - center
    return (vertices - center) * (2.0 * scale / extent)


def bbox_tensor(
    values: list[float] | tuple[float, ...],
    *,
    device: torch.device | str,
    dtype: torch.dtype,
) -> torch.Tensor:
    """Converte ``[L, H, W]`` (3 floats) ou ``[xmin,ymin,zmin,xmax,ymax,zmax]`` (6) em tensor Omni.

    Formato Omni demo: ``(1, 1, 3)`` com length/height/width normalizados 0-1.
    Se forem 6 floats, usa as dimensões do AABB (max-min) normalizadas pelo maior eixo.
    """
    arr = [float(v) for v in values]
    if len(arr) == 3:
        dims = arr
    elif len(arr) == 6:
        mins = np.asarray(arr[:3], dtype=np.float64)
        maxs = np.asarray(arr[3:], dtype=np.float64)
        size = np.maximum(maxs - mins, 1e-8)
        dims = (size / float(size.max())).tolist()
    else:
        raise ValueError(f"bbox espera 3 ou 6 floats, recebeu {len(arr)}")
    return torch.tensor(dims, dtype=dtype, device=device).view(1, 1, 3)


def pose_tensor(
    path: str | Path,
    *,
    device: torch.device | str,
    dtype: torch.dtype,
) -> torch.Tensor:
    """Carrega bone points (txt whitespace) → ``(1, N, C)``."""
    pts = np.loadtxt(str(path))
    if pts.ndim == 1:
        pts = pts.reshape(1, -1)
    return torch.from_numpy(pts).to(device=device, dtype=dtype).unsqueeze(0)


def point_cloud_tensor(
    path: str | Path,
    *,
    device: torch.device | str,
    dtype: torch.dtype,
    max_points: int = 81920,
) -> torch.Tensor:
    """Carrega mesh/PLY e devolve vértices normalizados ``(1, N, 3)``."""
    import trimesh

    mesh = trimesh.load(str(path), force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    verts = normalize_mesh_vertices(verts, scale=0.98)
    if len(verts) > max_points:
        idx = np.random.default_rng(0).choice(len(verts), size=max_points, replace=False)
        verts = verts[idx]
    return torch.from_numpy(verts.astype(np.float32)).to(device=device, dtype=dtype).unsqueeze(0)


def voxel_tensor(
    path: str | Path,
    *,
    device: torch.device | str,
    dtype: torch.dtype,
    num_samples: int = 81920,
) -> torch.Tensor:
    """Amostra superfície de mesh (com rotação -90° X como demo Omni) → ``(1, N, 3)``."""
    import trimesh

    mesh = trimesh.load(str(path), force="mesh")
    if isinstance(mesh, trimesh.Scene):
        mesh = mesh.dump(concatenate=True)
    rot = trimesh.transformations.rotation_matrix(angle=np.radians(-90), direction=[1, 0, 0])
    mesh.apply_transform(rot)
    verts = np.asarray(mesh.sample(num_samples), dtype=np.float64)
    verts = normalize_mesh_vertices(verts)
    return torch.from_numpy(verts.astype(np.float32)).to(device=device, dtype=dtype).unsqueeze(0)


def resolve_control_kwargs(
    control_type: str | None,
    *,
    bbox: list[float] | None = None,
    pose_file: str | Path | None = None,
    point_cloud: str | Path | None = None,
    voxel_mesh: str | Path | None = None,
    device: torch.device | str = "cpu",
    dtype: torch.dtype = torch.float16,
) -> dict[str, Any]:
    """Devolve kwargs ``bbox|pose|point|voxel`` para ``pipeline(...)``.

    ``control_type=None/'none'`` → bbox neutro default (image-led).
    """
    ct = (control_type or "none").strip().lower()
    if ct not in CONTROL_TYPES:
        raise ValueError(f"control_type inválido: {control_type!r} (válidos: {sorted(CONTROL_TYPES)})")

    if ct == "none":
        values = list(bbox) if bbox else list(DEFAULT_OMNI_BBOX)
        return {"bbox": bbox_tensor(values, device=device, dtype=dtype)}

    if ct == "bbox":
        if not bbox:
            raise ValueError("--bbox é obrigatório quando --control-type=bbox")
        return {"bbox": bbox_tensor(bbox, device=device, dtype=dtype)}

    if ct == "pose":
        if not pose_file:
            raise ValueError("--pose-file é obrigatório quando --control-type=pose")
        return {"pose": pose_tensor(pose_file, device=device, dtype=dtype)}

    if ct == "point":
        if not point_cloud:
            raise ValueError("--point-cloud é obrigatório quando --control-type=point")
        return {"point": point_cloud_tensor(point_cloud, device=device, dtype=dtype)}

    if ct == "voxel":
        if not voxel_mesh:
            raise ValueError("--voxel-mesh é obrigatório quando --control-type=voxel")
        return {"voxel": voxel_tensor(voxel_mesh, device=device, dtype=dtype)}

    raise ValueError(f"control_type não suportado: {ct}")
