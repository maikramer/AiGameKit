"""Weld por posição antes do xatlas (splits de normal do GLB)."""

from __future__ import annotations

import numpy as np

from paint3d.hy3dpaint.utils.uvwrap_utils import _atlas_padding, _weld_positions


def test_weld_merges_exact_position_duplicates() -> None:
    # Dois triângulos que partilham a aresta 0-1, mas com vértices duplicados
    # (o export glTF splita por normal): 6 verts / 2 tris → 4 verts.
    verts = np.array(
        [
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0],
            [1.0, 1.0, 0.0],
        ],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int32)
    v2, f2, stats = _weld_positions(verts, faces)
    assert len(v2) == 4
    assert len(f2) == 2
    assert stats == {"welded_verts": 2, "degenerate_faces": 0}
    # A aresta partilhada usa os mesmos índices depois do weld.
    shared = set(f2[0].tolist()) & set(f2[1].tolist())
    assert len(shared) == 2


def test_weld_drops_degenerate_faces() -> None:
    verts = np.array(
        [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [0, 3, 4]], dtype=np.int32)
    _v2, f2, stats = _weld_positions(verts, faces)
    assert stats["degenerate_faces"] == 1
    assert len(f2) == 1


def test_weld_keeps_nearby_distinct_vertices() -> None:
    # 1e-5 m de diferença é geometria vizinha, não um split de normal.
    verts = np.array(
        [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [1.0, 1e-5, 0.0]],
        dtype=np.float32,
    )
    faces = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int32)
    v2, _f2, stats = _weld_positions(verts, faces)
    assert stats["welded_verts"] == 0
    assert len(v2) == 4


def test_atlas_padding_scales_with_texture() -> None:
    assert _atlas_padding(1024) == 2
    assert _atlas_padding(2048) == 2
    assert _atlas_padding(4096) == 4
