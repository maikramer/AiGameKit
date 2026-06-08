"""UV mapping algorithms for procedural rock meshes.

Provides spherical projection (suited for pebbles) and xatlas-based
automatic unwrapping (suited for boulders), with a graceful fallback
when xatlas is not installed.
"""

from __future__ import annotations

import logging
import math

log = logging.getLogger(__name__)


def apply_uv_spherical(mesh: "trimesh.Trimesh") -> trimesh.Trimesh:  # noqa: F821, UP037
    """Apply spherical UV projection to mesh vertices.

    Projects vertices onto a unit sphere centered at the mesh centroid
    and maps spherical coordinates to the [0, 1] UV range.

    Args:
        mesh: A :class:`trimesh.Trimesh` instance without UV coordinates.

    Returns:
        The same mesh with ``TextureVisuals`` carrying the computed UVs.
    """
    import numpy as np
    import trimesh.visual.texture

    centroid = mesh.vertices.mean(axis=0)
    centered = mesh.vertices - centroid

    x = centered[:, 0]
    y = centered[:, 1]
    z = centered[:, 2]

    u = 0.5 + np.arctan2(z, x) / (2.0 * math.pi)
    r = np.linalg.norm(centered, axis=1)
    r = np.where(r < 1e-12, 1e-12, r)
    v = 0.5 + np.arcsin(np.clip(y / r, -1.0, 1.0)) / math.pi

    uv = np.stack([u, v], axis=1).clip(0.0, 1.0)

    mesh.visual = trimesh.visual.texture.TextureVisuals(uv=uv)
    return mesh


def apply_uv_xatlas(mesh: "trimesh.Trimesh") -> trimesh.Trimesh:  # noqa: F821, UP037
    """Apply xatlas UV unwrapping, with spherical fallback.

    Attempts to use the ``xatlas`` library for atlas-based UV
    generation.  If ``xatlas`` is not installed, a warning is logged
    and spherical projection is used instead.

    Args:
        mesh: A :class:`trimesh.Trimesh` instance.

    Returns:
        The mesh with UV coordinates applied via ``TextureVisuals``.
    """
    import trimesh
    import trimesh.visual.texture

    try:
        import xatlas
    except ImportError:
        log.warning("xatlas not available — falling back to spherical UV projection")
        return apply_uv_spherical(mesh)

    vmapping, indices, uvs = xatlas.parametrize(mesh.vertices, mesh.faces)

    # Rebuild mesh to match xatlas vertex remapping
    new_vertices = mesh.vertices[vmapping]
    new_faces = indices

    unwrapped = trimesh.Trimesh(vertices=new_vertices, faces=new_faces, process=False)
    unwrapped.visual = trimesh.visual.texture.TextureVisuals(uv=uvs)
    return unwrapped
