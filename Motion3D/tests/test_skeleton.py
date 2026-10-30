"""Skeleton contract: the 22-joint SprongMotion body and its retarget constants."""

from __future__ import annotations

import numpy as np
import pytest
from motion3d import skeleton as sk


class TestSkeletonLayout:
    def test_joint_count(self) -> None:
        assert sk.JOINT_COUNT == 22
        assert len(sk.SPRONG_JOINTS) == sk.JOINT_COUNT
        assert len(sk.SPRONG_PARENTS) == sk.JOINT_COUNT

    def test_root_is_pelvis(self) -> None:
        assert sk.SPRONG_JOINTS[0] == "pelvis"
        assert sk.SPRONG_PARENTS[0] == -1

    @pytest.mark.parametrize("index", list(range(sk.JOINT_COUNT)))
    def test_parent_comes_before_child(self, index: int) -> None:
        parent = sk.SPRONG_PARENTS[index]
        assert parent == -1 or 0 <= parent < index

    def test_only_one_root(self) -> None:
        roots = [i for i, parent in enumerate(sk.SPRONG_PARENTS) if parent < 0]
        assert roots == [0]

    @pytest.mark.parametrize(
        ("joint", "parent"),
        [
            ("l_hip", "pelvis"),
            ("r_hip", "pelvis"),
            ("spine1", "pelvis"),
            ("l_knee", "l_hip"),
            ("l_ankle", "l_knee"),
            ("l_foot", "l_ankle"),
            ("spine2", "spine1"),
            ("spine3", "spine2"),
            ("neck", "spine3"),
            ("head", "neck"),
            ("l_collar", "spine3"),
            ("r_collar", "spine3"),
            ("l_shoulder", "l_collar"),
            ("l_elbow", "l_shoulder"),
            ("l_wrist", "l_elbow"),
            ("r_shoulder", "r_collar"),
            ("r_elbow", "r_shoulder"),
            ("r_wrist", "r_elbow"),
        ],
    )
    def test_expected_topology(self, joint: str, parent: str) -> None:
        index = sk.SPRONG_JOINTS.index(joint)
        assert sk.SPRONG_JOINTS[sk.SPRONG_PARENTS[index]] == parent

    def test_left_right_mirror_symmetric(self) -> None:
        index = sk.SPRONG.index
        for left, right in (
            ("l_hip", "r_hip"),
            ("l_knee", "r_knee"),
            ("l_ankle", "r_ankle"),
            ("l_foot", "r_foot"),
            ("l_collar", "r_collar"),
            ("l_shoulder", "r_shoulder"),
            ("l_elbow", "r_elbow"),
            ("l_wrist", "r_wrist"),
        ):
            left_parent = sk.SPRONG_JOINTS[sk.SPRONG_PARENTS[index[left]]]
            right_parent = sk.SPRONG_JOINTS[sk.SPRONG_PARENTS[index[right]]]
            assert left_parent.replace("l_", "r_", 1) == right_parent


class TestAimTable:
    def test_aim_children_exist(self) -> None:
        for bone, child in sk.AIM_CHILD.items():
            assert 0 <= bone < sk.JOINT_COUNT
            assert 0 <= child < sk.JOINT_COUNT
            assert child != bone

    def test_aim_child_is_a_direct_child(self) -> None:
        for bone, child in sk.AIM_CHILD.items():
            assert sk.SPRONG_PARENTS[child] == bone

    def test_leaves_are_the_unaimed_joints(self) -> None:
        assert frozenset(set(range(sk.JOINT_COUNT)) - set(sk.AIM_CHILD)) == sk.LEAF_BONES
        assert frozenset({10, 11, 15, 20, 21}) == sk.LEAF_BONES

    def test_pelvis_aims_at_spine_not_a_clavicle(self) -> None:
        assert sk.AIM_CHILD[0] == sk.SPRONG_JOINTS.index("spine1")

    def test_aim_indices_are_the_hml22_layout(self) -> None:
        # Same body as HumanML3D: the index-based clamps carry over unchanged.
        assert sk.AIM_CHILD[16] == 18  # l_shoulder → l_elbow
        assert sk.AIM_CHILD[18] == 20  # l_elbow → l_wrist
        assert sk.AIM_CHILD[7] == 10  # l_ankle → l_foot


class TestReferenceSkeleton:
    def test_reference_offsets_shape_and_root(self) -> None:
        assert sk.REFERENCE_OFFSETS.shape == (sk.JOINT_COUNT, 3)
        assert np.allclose(sk.REFERENCE_OFFSETS[0], 0.0)

    def test_world_rest_is_upright(self) -> None:
        rest = sk.rest_positions_yup()
        # The head joint sits level with the neck and forward of it (no skull
        # top joint on this 22-joint body).
        head = rest[sk.SPRONG.index["head"]]
        neck = rest[sk.SPRONG.index["neck"]]
        assert head[1] == pytest.approx(neck[1], abs=1e-6)
        assert head[2] > neck[2]
        assert neck[1] > rest[sk.SPRONG.index["pelvis"], 1]
        # Arms hang below the shoulders, feet below the ankles.
        assert rest[sk.SPRONG.index["l_wrist"], 1] < rest[sk.SPRONG.index["l_shoulder"], 1]
        ankle = rest[sk.SPRONG.index["l_ankle"]]
        foot = rest[sk.SPRONG.index["l_foot"]]
        assert foot[1] == pytest.approx(ankle[1], abs=1e-6)
        assert foot[2] > ankle[2]  # toe forward of the ankle, flat foot

    def test_feet_are_near_the_floor_relative_to_pelvis(self) -> None:
        rest = sk.rest_positions_yup()
        ankle = rest[sk.SPRONG.index["l_ankle"], 1]
        assert -1.0 < ankle < -0.7

    def test_rest_directions_are_unit(self) -> None:
        for bone, direction in sk.rest_directions_yup().items():
            assert bone in sk.AIM_CHILD
            assert np.isclose(np.linalg.norm(direction), 1.0)

    def test_rest_direction_matches_child_offset(self) -> None:
        directions = sk.rest_directions_yup()
        knee = sk.SPRONG.index["l_knee"]
        expected = sk.REFERENCE_OFFSETS[knee] / np.linalg.norm(sk.REFERENCE_OFFSETS[knee])
        assert np.allclose(directions[sk.SPRONG.index["l_hip"]], expected)

    def test_rest_positions_rejects_bad_offsets(self) -> None:
        with pytest.raises(ValueError, match="offsets"):
            sk.rest_positions_yup(np.zeros((3, 3)))


class TestAimDirections:
    def test_shape_and_units(self, walk_joints: np.ndarray) -> None:
        dirs = sk.aim_directions(walk_joints)
        assert dirs.shape == walk_joints.shape
        for bone in sk.AIM_CHILD:
            assert np.allclose(np.linalg.norm(dirs[:, bone], axis=-1), 1.0)

    def test_leaves_are_zeroed(self, walk_joints: np.ndarray) -> None:
        dirs = sk.aim_directions(walk_joints)
        for leaf in sk.LEAF_BONES:
            assert np.allclose(dirs[:, leaf], 0.0)

    def test_degenerate_frame_falls_back_to_rest(self) -> None:
        joints = sk.rest_positions_yup()[None].repeat(2, axis=0)
        joint = sk.SPRONG.index["l_hip"]
        joints[:, joint] = joints[:, sk.SPRONG.index["l_knee"]]
        dirs = sk.aim_directions(joints)
        assert np.allclose(dirs[:, joint], sk.rest_directions_yup()[joint])
        assert np.isclose(np.linalg.norm(dirs[0, joint]), 1.0)

    def test_rejects_wrong_shape(self) -> None:
        with pytest.raises(ValueError, match="joints must be"):
            sk.aim_directions(np.zeros((4, 21, 3)))


class TestClampTables:
    def test_neutral_aim_is_unit_and_skips_feet(self) -> None:
        for _bone, direction in sk.NEUTRAL_AIM.items():
            assert abs(np.linalg.norm(direction) - 1.0) < 0.01
        assert 7 not in sk.NEUTRAL_AIM and 8 not in sk.NEUTRAL_AIM

    def test_neutral_arms_mirror(self) -> None:
        assert sk.NEUTRAL_AIM[16][0] == -sk.NEUTRAL_AIM[17][0]
        assert sk.NEUTRAL_AIM[18][0] == -sk.NEUTRAL_AIM[19][0]

    def test_splay_mirrors(self) -> None:
        assert sk.LEG_SPLAY_DEG[1] == -sk.LEG_SPLAY_DEG[2]
        assert sk.LEG_SPLAY_DEG[4] == -sk.LEG_SPLAY_DEG[5]

    def test_hinges_reference_valid_joints(self) -> None:
        for proximal, joint, distal, low, high in sk.HINGE_LIMITS:
            assert low < high
            for index in (proximal, joint, distal):
                assert 0 <= index < sk.JOINT_COUNT

    def test_hinge_followers_are_children_of_the_hinge(self) -> None:
        for _distal, children in sk.HINGE_FOLLOWERS.items():
            for child in children:
                assert child in sk.LEAF_BONES

    def test_upper_body_excludes_legs_and_pelvis(self) -> None:
        for leg in (0, 1, 2, 4, 5, 7, 8, 10, 11):
            assert leg not in sk.UPPER_BODY_INDICES
        assert 12 in sk.UPPER_BODY_INDICES


class TestCheckSkeleton:
    def test_accepts_the_pinned_layout(self) -> None:
        sk.check_skeleton(list(sk.SPRONG_JOINTS), list(sk.SPRONG_PARENTS))

    def test_rejects_wrong_count(self) -> None:
        with pytest.raises(sk.SkeletonMismatchError, match="21 joints"):
            sk.check_skeleton(list(sk.SPRONG_JOINTS[:21]), list(sk.SPRONG_PARENTS[:21]))

    def test_rejects_renamed_joint(self) -> None:
        names = list(sk.SPRONG_JOINTS)
        names[1] = "left_hip"
        with pytest.raises(sk.SkeletonMismatchError, match="diferente"):
            sk.check_skeleton(names, list(sk.SPRONG_PARENTS))

    def test_rejects_different_parents(self) -> None:
        parents = list(sk.SPRONG_PARENTS)
        parents[4] = 2
        with pytest.raises(sk.SkeletonMismatchError):
            sk.check_skeleton(list(sk.SPRONG_JOINTS), parents)


class TestSkeletonFacade:
    def test_index_and_bone_name(self) -> None:
        assert sk.SPRONG.index["pelvis"] == 0
        assert sk.SPRONG.bone_name(15) == "head"

    def test_name_is_the_retarget_profile(self) -> None:
        assert sk.SPRONG.name == "sprong22"
        assert sk.SOURCE_TRACK == "sprong_motion"
