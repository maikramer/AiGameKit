from __future__ import annotations

import numpy as np
import pytest

trimesh = pytest.importorskip("trimesh")

from part3d.utils.face_split import (  # noqa: E402
    merge_xpart_with_face_fallback,
    thin_part_mask,
    xpart_candidate_mask,
)


def _box_mesh() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=[1.0, 1.0, 1.0]).subdivide()


def test_thin_part_mask_flags_flat_slab() -> None:
    # Cubo grosso (label 0) + painel fino XY (label 1).
    thick = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    thin = trimesh.creation.box(extents=[1.0, 1.0, 0.05])
    thin.apply_translation([2.0, 0.0, 0.0])
    mesh = trimesh.util.concatenate([thick, thin])
    labels = np.concatenate(
        [
            np.zeros(len(thick.faces), dtype=np.int64),
            np.ones(len(thin.faces), dtype=np.int64),
        ]
    )
    mask = thin_part_mask(mesh, labels, max_thin_ratio=0.20, min_aspect=5.0)
    # label order ascending: 0, 1
    assert mask.tolist() == [False, True]


def test_thin_part_mask_soft_small_feature() -> None:
    """Bandeira/mastro: pouco alongado mas área pequena + relativamente fino."""
    body = trimesh.creation.box(extents=[1.0, 1.0, 2.0])
    flag = trimesh.creation.box(extents=[0.15, 0.12, 0.40])
    flag.apply_translation([0.0, 0.0, 1.5])
    mesh = trimesh.util.concatenate([body, flag])
    labels = np.concatenate(
        [
            np.zeros(len(body.faces), dtype=np.int64),
            np.ones(len(flag.faces), dtype=np.int64),
        ]
    )
    mask = thin_part_mask(
        mesh,
        labels,
        max_thin_ratio=0.20,
        min_aspect=5.0,
        soft_thin_ratio=0.35,
        soft_max_area_frac=0.05,
    )
    assert mask.tolist() == [False, True]


def test_xpart_candidate_mask_large_area() -> None:
    mesh = _box_mesh()
    labels = np.zeros(len(mesh.faces), dtype=np.int64)
    # single label = 100% area → not compact
    assert xpart_candidate_mask(mesh, labels, max_area_frac=0.10).tolist() == [False]


def test_merge_prefer_face_overrides_xpart() -> None:
    thick = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    thin = trimesh.creation.box(extents=[0.2, 1.0, 0.05])
    thin.apply_translation([2.0, 0.0, 0.0])
    mesh = trimesh.util.concatenate([thick, thin])
    face_ids = np.concatenate(
        [
            np.zeros(len(thick.faces), dtype=np.int64),
            np.ones(len(thin.faces), dtype=np.int64),
        ]
    )
    # Fake X-Part "success" with a melted blob for label 1
    melted = trimesh.creation.icosphere(subdivisions=1, radius=0.3)
    melted.apply_translation([2.0, 0.0, 0.0])
    xpart = trimesh.Scene()
    xpart.add_geometry(thick.copy(), geom_name="part_0", node_name="part_0")
    xpart.add_geometry(melted, geom_name="part_1", node_name="part_1")

    out = merge_xpart_with_face_fallback(
        mesh,
        face_ids,
        xpart,
        succeeded_labels={0, 1},
        prefer_face_labels={1},
    )
    assert "part_0" in out.geometry and "part_1" in out.geometry
    # Face topology for thin label ≈ original thin face count, not icosphere
    assert len(out.geometry["part_1"].faces) == len(thin.faces)
    assert len(out.geometry["part_1"].faces) != len(melted.faces)


def test_carve_removes_xpart_hallucination_in_thin_aabb() -> None:
    """X-Part body with a blob overlapping the thin AABB is carved before face paste."""
    from part3d.utils.face_split import carve_meshes_outside_aabbs

    body = trimesh.creation.box(extents=[1.0, 1.0, 2.0])
    # Hallucinated blob at thin feature location
    blob = trimesh.creation.icosphere(subdivisions=1, radius=0.15)
    blob.apply_translation([0.55, 0.0, 0.5])
    fused = trimesh.util.concatenate([body, blob])
    carve_boxes = [(np.array([0.4, -0.2, 0.3]), np.array([0.7, 0.2, 0.7]))]
    carved = carve_meshes_outside_aabbs(fused, carve_boxes, margin_frac=0.05)
    assert len(carved.faces) < len(fused.faces)
    # Remaining verts should mostly be outside the carve box
    verts = np.asarray(carved.vertices)
    lo, hi = carve_boxes[0]
    inside = np.all((verts >= lo) & (verts <= hi), axis=1).sum()
    assert inside < len(verts) * 0.05


def test_carve_near_points_removes_offset_blob() -> None:
    """Blob ligeiramente fora da AABB ainda é removido por proximidade."""
    from part3d.utils.face_split import carve_meshes_near_points

    body = trimesh.creation.box(extents=[1.0, 1.0, 2.0])
    blob = trimesh.creation.icosphere(subdivisions=1, radius=0.12)
    blob.apply_translation([0.62, 0.05, 0.55])  # offset from preserve cloud
    fused = trimesh.util.concatenate([body, blob])
    preserve = np.array([[0.55, 0.0, 0.5], [0.55, 0.05, 0.55], [0.50, 0.0, 0.45]])
    carved = carve_meshes_near_points(fused, preserve, radius=0.25)
    assert len(carved.faces) < len(fused.faces)


def test_carve_outward_only_spares_inward_wall() -> None:
    """Carve outward-only remove fantasma à frente, não a parede atrás."""
    from part3d.utils.face_split import carve_meshes_near_points

    # Parede em z≤0.1; fantasma desligado em z≈0.5; preserve na face +Z.
    wall = trimesh.creation.box(extents=[0.8, 0.8, 0.2])
    ghost = trimesh.creation.box(extents=[0.25, 0.25, 0.12])
    ghost.apply_translation([0.0, 0.0, 0.55])
    fused = trimesh.util.concatenate([wall, ghost])
    preserve = np.array([[0.0, 0.0, 0.10], [0.05, 0.0, 0.10], [-0.05, 0.0, 0.10]])
    out = carve_meshes_near_points(
        fused,
        preserve,
        radius=0.55,
        outward_from=np.array([0.0, 0.0, -1.0]),
        outward_only=True,
    )
    assert len(out.faces) < len(fused.faces)
    z = np.asarray(out.vertices)[:, 2]
    assert (z > 0.35).sum() == 0  # fantasma fora
    assert (z < 0.15).sum() > 0  # parede fica


def test_carve_thin_components_drops_near_blob() -> None:
    from part3d.utils.face_split import carve_thin_components_near_points

    body = trimesh.creation.box(extents=[1.0, 1.0, 2.0])
    blob = trimesh.creation.box(extents=[0.15, 0.8, 0.05])
    blob.apply_translation([0.7, 0.0, 0.0])
    fused = trimesh.util.concatenate([body, blob])
    preserve = np.array([[0.7, 0.0, 0.0], [0.7, 0.2, 0.0]])
    carved = carve_thin_components_near_points(fused, preserve, radius=0.3)
    assert len(carved.faces) < len(fused.faces)
    assert len(carved.split(only_watertight=False)) == 1
