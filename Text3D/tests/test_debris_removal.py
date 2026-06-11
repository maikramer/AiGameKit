"""Teste do filtro de ilhas soltas (debris MC/quantização) no topology-fix."""

from __future__ import annotations

import numpy as np
import pytest
import trimesh

bpy = pytest.importorskip("bpy")

from text3d.utils.mesh_lod import prepare_mesh_topology  # noqa: E402


def _scene_with_debris() -> trimesh.Trimesh:
    """Esfera grande + 3 fragmentos minúsculos afastados (simula floaters)."""
    main = trimesh.creation.icosphere(subdivisions=4, radius=1.0)  # ~5120 faces
    parts = [main]
    for offset in ((3, 0, 0), (0, 3, 0), (0, 0, 3)):
        crumb = trimesh.creation.icosphere(subdivisions=0, radius=0.02)  # 20 faces
        crumb.apply_translation(np.array(offset, dtype=float))
        parts.append(crumb)
    return trimesh.util.concatenate(parts)


def test_topology_fix_removes_tiny_islands(tmp_path) -> None:
    mesh = _scene_with_debris()
    n_comps_before = len(mesh.split(only_watertight=False))
    assert n_comps_before == 4

    fixed = prepare_mesh_topology(mesh)
    comps = fixed.split(only_watertight=False)
    assert len(comps) == 1, f"debris sobreviveu: {len(comps)} componentes"
    # Esfera principal intacta (faces na mesma ordem de grandeza).
    assert len(fixed.faces) > 4000


def test_topology_fix_keeps_single_small_mesh(tmp_path) -> None:
    """Mesh pequena única não pode ser apagada pelo filtro (guarda anti-tudo)."""
    small = trimesh.creation.icosphere(subdivisions=1, radius=0.05)  # 80 faces
    fixed = prepare_mesh_topology(small)
    assert len(fixed.faces) > 0
