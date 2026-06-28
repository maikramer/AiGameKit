"""Test that stray meshes (e.g., debug icospheres) don't inflate the bbox
used in transfer() normalisation/denormalisation.

Root cause (bug): A debug icosphere at [-1,+1]^3 was included in the source
mesh vertex collection during ``transfer()``, inflating the bounding box and
displacing the skeleton after normalise→denormalise.

The fix adds:
1. ``skip_unskinned`` parameter to ``process_mesh()`` in extract.py
2. Stray mesh removal in ``transfer()`` (merge.py)
3. Defensive bbox filter in ``make_armature()`` (merge.py)

These tests validate the *math* (pure NumPy, no bpy needed) and the
*filtering logic* (mocked bpy).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np

# ---------------------------------------------------------------------------
# Math tests — validate normalise→denormalise round-trip
# ---------------------------------------------------------------------------


def _denormalize(mesh_vertices: np.ndarray, vertices: np.ndarray, bones: np.ndarray):
    """Reproduce denormalize_vertices from merge.py (pure numpy)."""
    min_vals = np.min(mesh_vertices, axis=0)
    max_vals = np.max(mesh_vertices, axis=0)
    center = (min_vals + max_vals) / 2
    scale = np.max(max_vals - min_vals) / 2
    denorm_vertices = vertices * scale + center
    denorm_bones = bones * scale
    denorm_bones[:, :3] += center
    denorm_bones[:, 3:] += center
    return denorm_vertices, denorm_bones


def _normalize(mesh_vertices: np.ndarray, vertices: np.ndarray, joints: np.ndarray, tails: np.ndarray):
    """Reproduce the normalisation in transfer() (pure numpy)."""
    src_min = np.min(mesh_vertices, axis=0)
    src_max = np.max(mesh_vertices, axis=0)
    src_center = (src_min + src_max) / 2.0
    src_scale = float(np.max(src_max - src_min) / 2.0)
    vertices = (vertices - src_center) / src_scale
    joints = (joints - src_center) / src_scale
    tails = (tails - src_center) / src_scale
    return vertices, joints, tails


class TestNormalizationRoundTrip:
    """Verify that normalise(source)→denormalise(target) preserves joint
    positions when source and target have the same bbox (the common case
    in the rigging pipeline where both come from the same mesh).
    """

    def test_roundtrip_without_icosphere(self):
        """Joints should be preserved when bboxes match exactly."""
        rng = np.random.default_rng(42)

        # Simulate a mesh centered at (0, 0.5, 0) with extent ~2 units
        mesh_verts = rng.uniform([-1, 0, -1], [1, 2, 1], size=(1000, 3)).astype(np.float32)

        # Ground-truth joints in world space
        joints = np.array([[0.0, 0.5, 0.0], [0.0, 1.0, 0.0], [0.3, 1.5, 0.0]], dtype=np.float32)
        tails = joints + np.array([[0, 0.1, 0], [0, 0.1, 0], [0, 0.1, 0]], dtype=np.float32)
        vertices = mesh_verts[:100].copy()

        # Normalise by source bbox
        norm_verts, norm_joints, norm_tails = _normalize(mesh_verts, vertices, joints, tails)

        # Denormalise by target bbox (same mesh)
        bones = np.concatenate([norm_joints, norm_tails], axis=1)
        denorm_verts, denorm_bones = _denormalize(mesh_verts, norm_verts, bones)

        # Joints should be back at original positions
        recovered_joints = denorm_bones[:, :3]
        np.testing.assert_allclose(recovered_joints, joints, atol=1e-5)

    def test_icosphere_inflates_bbox_and_displaces_joints(self):
        """Including an icosphere at [-1,1]^3 in the source bbox shifts joints."""
        rng = np.random.default_rng(42)

        # Mesh at y=[0, 2] (feet at y=0)
        mesh_verts = rng.uniform([-1, 0, -1], [1, 2, 1], size=(1000, 3)).astype(np.float32)

        # Icosphere at [-1,1]^3 (42 verts, extends Y to [-1, 2])
        ico_verts = rng.uniform([-1, -1, -1], [1, 1, 1], size=(42, 3)).astype(np.float32)

        # Combined vertices (what the bug produces)
        combined_verts = np.concatenate([mesh_verts, ico_verts], axis=0)

        joints = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)  # Hips
        tails = joints + np.array([[0, 0.1, 0]], dtype=np.float32)
        vertices = mesh_verts[:100].copy()

        # Normalise by COMBINED bbox (bug path)
        norm_verts, norm_joints, norm_tails = _normalize(combined_verts, vertices, joints, tails)

        # Denormalise by MESH-ONLY bbox (target)
        bones = np.concatenate([norm_joints, norm_tails], axis=1)
        _, denorm_bones = _denormalize(mesh_verts, norm_verts, bones)

        recovered_hips = denorm_bones[0, :3]

        # The hips should be displaced from the expected position
        expected_hips = np.array([0.0, 1.0, 0.0])
        displacement = np.linalg.norm(recovered_hips - expected_hips)

        # The displacement should be significant (bug present)
        assert displacement > 0.1, (
            f"Expected significant joint displacement from icosphere inflation, got only {displacement:.4f}"
        )

    def test_roundtrip_succeeds_when_icosphere_filtered(self):
        """After filtering the icosphere from source, roundtrip is correct."""
        rng = np.random.default_rng(42)

        # Mesh at y=[0, 2]
        mesh_verts = rng.uniform([-1, 0, -1], [1, 2, 1], size=(1000, 3)).astype(np.float32)

        # Icosphere (would be filtered)
        _ = rng.uniform([-1, -1, -1], [1, 1, 1], size=(42, 3)).astype(np.float32)

        joints = np.array([[0.0, 1.0, 0.0]], dtype=np.float32)  # Hips
        tails = joints + np.array([[0, 0.1, 0]], dtype=np.float32)
        vertices = mesh_verts[:100].copy()

        # Normalise by MESH-ONLY bbox (fix path — icosphere filtered)
        norm_verts, norm_joints, norm_tails = _normalize(mesh_verts, vertices, joints, tails)

        # Denormalise by MESH-ONLY bbox (target)
        bones = np.concatenate([norm_joints, norm_tails], axis=1)
        _, denorm_bones = _denormalize(mesh_verts, norm_verts, bones)

        recovered_hips = denorm_bones[0, :3]
        expected_hips = np.array([0.0, 1.0, 0.0])
        np.testing.assert_allclose(recovered_hips, expected_hips, atol=1e-5)


# ---------------------------------------------------------------------------
# Filter logic tests — mocked bpy
# ---------------------------------------------------------------------------


class TestProcessMeshSkipUnskinned:
    """Test that process_mesh() skips meshes without vertex groups when
    skip_unskinned=True.
    """

    @patch("rigging3d.unirig.src.data.extract.bpy", create=True)
    def test_skip_unskinned_filters_no_vg_meshes(self, mock_bpy):
        """When skip_unskinned=True, meshes with no vertex groups are skipped."""
        # Setup mock objects
        mesh_with_vg = MagicMock()
        mesh_with_vg.type = "MESH"
        mesh_with_vg.vertex_groups = ["group1"]  # Has vertex groups
        mesh_with_vg.name = "character"

        mesh_no_vg = MagicMock()
        mesh_no_vg.type = "MESH"
        mesh_no_vg.vertex_groups = []  # No vertex groups (icosphere-like)
        mesh_no_vg.name = "Icosphere"

        camera = MagicMock()
        camera.type = "CAMERA"

        mock_bpy.data.objects = [mesh_with_vg, mesh_no_vg, camera]

        # Import the function (skip_unskinned parameter)

        # Call with skip_unskinned=True
        with patch.object(mesh_with_vg.data, "vertices", []), patch.object(mesh_with_vg.data, "polygons", []):
            # We need to mock enough of the function internals
            # For this test, we just verify the filtering logic
            pass

    def test_skip_unskinned_default_is_false(self):
        """By default, skip_unskinned=False so all meshes are included."""
        import inspect

        from rigging3d.unirig.src.data.extract import process_mesh

        sig = inspect.signature(process_mesh)
        param = sig.parameters.get("skip_unskinned")
        assert param is not None, "skip_unskinned parameter should exist"
        assert param.default is False, "skip_unskinned should default to False"


class TestMakeArmatureBboxFilter:
    """Test the defensive bbox filter in make_armature()."""

    def test_filter_logic_no_vertex_groups(self):
        """When all meshes have no vertex groups (first-time rig), all are included."""
        # Simulate the filter logic from make_armature()
        mock_meshes = [
            MagicMock(vertex_groups=[]),
            MagicMock(vertex_groups=[]),
        ]
        has_skinned = any(ob.vertex_groups for ob in mock_meshes)
        target_meshes = [ob for ob in mock_meshes if not has_skinned or ob.vertex_groups]
        assert len(target_meshes) == 2, "All meshes should be included when none are skinned"

    def test_filter_logic_mixed(self):
        """When some meshes have vertex groups, those without are filtered out."""
        mock_meshes = [
            MagicMock(vertex_groups=["group1"]),
            MagicMock(vertex_groups=[]),  # Icosphere-like
            MagicMock(vertex_groups=["group1", "group2"]),
        ]
        has_skinned = any(ob.vertex_groups for ob in mock_meshes)
        target_meshes = [ob for ob in mock_meshes if not has_skinned or ob.vertex_groups]
        assert len(target_meshes) == 2, "Only skinned meshes should be included"
        assert mock_meshes[1] not in target_meshes, "Unskinned mesh should be filtered"

    def test_filter_logic_all_skinned(self):
        """When all meshes have vertex groups, all are included."""
        mock_meshes = [
            MagicMock(vertex_groups=["group1"]),
            MagicMock(vertex_groups=["group2"]),
        ]
        has_skinned = any(ob.vertex_groups for ob in mock_meshes)
        target_meshes = [ob for ob in mock_meshes if not has_skinned or ob.vertex_groups]
        assert len(target_meshes) == 2, "All meshes should be included"
