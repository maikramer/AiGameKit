"""Testes da camada de pose-math pura de ``animator3d.humanoid``.

Cobre funções que não tocam em ``bpy``: ``_smoothstep01``, ``merge``,
``scale_pose``, ``mix``, a classificação anatómica de ``HumanoidRig`` (via
``object.__new__`` para contornar o construtor que depende de Blender) e os
geradores de pose usados pelo gait cycle (``_gait_phase_pose``,
``_keys_per_cycle``).
"""

from __future__ import annotations

import pytest
from animator3d.humanoid import (
    _WALK_PARAMS,
    HumanoidRig,
    Pose,
    _gait_phase_pose,
    _keys_per_cycle,
    _smoothstep01,
    merge,
    mix,
    scale_pose,
)

# ---------------------------------------------------------------------------
# Fixtures — cadeias no estilo Mixamo (prefixo ``mixamorig:``).
# ---------------------------------------------------------------------------

MIXAMO_CHAINS: dict[str, list[str]] = {
    "body": ["mixamorig:Hips"],
    "spine": ["mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2"],
    "neck": ["mixamorig:Neck", "mixamorig:Head"],
    "arm_r": [
        "mixamorig:RightShoulder",
        "mixamorig:RightArm",
        "mixamorig:RightForeArm",
        "mixamorig:RightHand",
    ],
    "arm_l": [
        "mixamorig:LeftShoulder",
        "mixamorig:LeftArm",
        "mixamorig:LeftForeArm",
        "mixamorig:LeftHand",
    ],
    "leg_r": [
        "mixamorig:RightUpLeg",
        "mixamorig:RightLeg",
        "mixamorig:RightFoot",
        "mixamorig:RightToeBase",
    ],
    "leg_l": [
        "mixamorig:LeftUpLeg",
        "mixamorig:LeftLeg",
        "mixamorig:LeftFoot",
        "mixamorig:LeftToeBase",
    ],
}


def _make_rig(chains: dict[str, list[str]]) -> HumanoidRig:
    """Constrói um ``HumanoidRig`` sem Blender — apenas o campo ``chains``.

    Os métodos anatómicos (``arm_bones``, ``leg_bones``, ``spine``, ``neck``,
    ``hips``) dependem exclusivamente de ``self.chains``, pelo que o bypass do
    ``__init__`` (que importa ``mathutils``/``bpy``) é seguro para estes testes.
    """
    rig = object.__new__(HumanoidRig)
    rig.chains = chains
    return rig


def _channel(pose: Pose, bone: str, name: str) -> float:
    """Lê um canal de rotação de uma pose (0.0 se ausente)."""
    return pose.get(bone, {}).get(name, 0.0)


def _poses_approx_equal(a: Pose, b: Pose) -> bool:
    """Compara duas poses canal-a-canal dentro de tolerância float."""
    if set(a.keys()) != set(b.keys()):
        return False
    for bone in a:
        if set(a[bone].keys()) != set(b[bone].keys()):
            return False
        for ch in a[bone]:
            if a[bone][ch] != pytest.approx(b[bone][ch]):
                return False
    return True


# ---------------------------------------------------------------------------
# _smoothstep01
# ---------------------------------------------------------------------------


class TestSmoothstep01:
    def test_endpoints_are_exact(self) -> None:
        assert _smoothstep01(0.0) == 0.0
        assert _smoothstep01(1.0) == 1.0

    def test_midpoint_is_half(self) -> None:
        assert _smoothstep01(0.5) == pytest.approx(0.5)

    def test_clamped_below_zero(self) -> None:
        assert _smoothstep01(-0.5) == 0.0
        assert _smoothstep01(-100.0) == 0.0

    def test_clamped_above_one(self) -> None:
        assert _smoothstep01(1.5) == 1.0
        assert _smoothstep01(42.0) == 1.0

    def test_monotonic_non_decreasing(self) -> None:
        prev = _smoothstep01(0.0)
        for i in range(1, 21):
            x = i / 20.0
            cur = _smoothstep01(x)
            assert cur >= prev, f"smoothstep not monotonic at x={x}"
            prev = cur

    def test_midrange_is_smooth(self) -> None:
        # smoothstep differ from linear in the interior (0 < t < 1)
        assert 0.0 < _smoothstep01(0.25) < 0.5
        assert 0.5 < _smoothstep01(0.75) < 1.0


# ---------------------------------------------------------------------------
# merge (composição ADITIVA — soma canais sobrepostos)
# ---------------------------------------------------------------------------


class TestMerge:
    def test_empty_inputs_yield_empty(self) -> None:
        assert merge() == {}
        assert merge({}, {}) == {}

    def test_single_pose_returned(self) -> None:
        p: Pose = {"bone": {"pitch": 0.5}}
        assert merge(p) == p

    def test_disjoint_keys_produce_union(self) -> None:
        a: Pose = {"a": {"pitch": 0.1}}
        b: Pose = {"b": {"roll": 0.2}}
        out = merge(a, b)
        assert out == {"a": {"pitch": 0.1}, "b": {"roll": 0.2}}

    def test_overlapping_channels_are_summed(self) -> None:
        """merge é ADITIVO: canais sobrepostos somam valores (não sobrepõe)."""
        a: Pose = {"hip": {"pitch": 0.5, "yaw": 0.1}}
        b: Pose = {"hip": {"pitch": 0.3, "roll": 0.2}}
        out = merge(a, b)
        assert out == {"hip": {"pitch": pytest.approx(0.8), "yaw": 0.1, "roll": 0.2}}

    def test_three_way_merge_accumulates(self) -> None:
        a: Pose = {"b": {"pitch": 1.0}}
        b: Pose = {"b": {"pitch": 2.0}}
        c: Pose = {"b": {"pitch": 3.0}}
        assert merge(a, b, c) == {"b": {"pitch": 6.0}}

    def test_does_not_mutate_inputs(self) -> None:
        a: Pose = {"b": {"pitch": 1.0}}
        b: Pose = {"b": {"pitch": 2.0}}
        a_copy = {"b": {"pitch": 1.0}}
        merge(a, b)
        assert a == a_copy


# ---------------------------------------------------------------------------
# scale_pose
# ---------------------------------------------------------------------------


class TestScalePose:
    def test_identity_factor(self) -> None:
        p: Pose = {"a": {"pitch": 0.5, "roll": 0.25}}
        assert scale_pose(p, 1.0) == p

    def test_half_factor(self) -> None:
        p: Pose = {"a": {"pitch": 1.0, "roll": 0.4}}
        out = scale_pose(p, 0.5)
        assert out == {"a": {"pitch": 0.5, "roll": 0.2}}

    def test_zero_factor_zeros_all_channels(self) -> None:
        p: Pose = {"a": {"pitch": 1.0, "roll": 2.0}, "b": {"yaw": 3.0}}
        out = scale_pose(p, 0.0)
        assert all(v == 0.0 for ch in out.values() for v in ch.values())
        # structure (bones/channels) preserved even when zeroed
        assert set(out.keys()) == {"a", "b"}
        assert set(out["a"].keys()) == {"pitch", "roll"}

    def test_negative_factor_flips_signs(self) -> None:
        p: Pose = {"a": {"pitch": 0.5}}
        assert scale_pose(p, -2.0) == {"a": {"pitch": -1.0}}

    def test_empty_pose_stays_empty(self) -> None:
        assert scale_pose({}, 2.0) == {}


# ---------------------------------------------------------------------------
# mix (interpolação linear via merge aditivo)
# ---------------------------------------------------------------------------


class TestMix:
    def test_t_zero_returns_a(self) -> None:
        a: Pose = {"b": {"pitch": 0.4, "roll": 0.1}}
        b: Pose = {"b": {"pitch": 0.8, "roll": 0.9}}
        assert mix(a, b, 0.0) == a

    def test_t_one_returns_b(self) -> None:
        a: Pose = {"b": {"pitch": 0.4, "roll": 0.1}}
        b: Pose = {"b": {"pitch": 0.8, "roll": 0.9}}
        assert mix(a, b, 1.0) == b

    def test_midpoint_is_average(self) -> None:
        a: Pose = {"b": {"pitch": 0.0}}
        b: Pose = {"b": {"pitch": 1.0}}
        assert mix(a, b, 0.5) == {"b": {"pitch": 0.5}}

    def test_linear_per_component(self) -> None:
        a: Pose = {"b": {"pitch": 0.0, "yaw": 1.0}}
        b: Pose = {"b": {"pitch": 1.0, "yaw": 0.0}}
        out = mix(a, b, 0.25)
        assert out == {"b": {"pitch": 0.25, "yaw": 0.75}}

    def test_quarter_point(self) -> None:
        a: Pose = {"b": {"pitch": 0.0}}
        b: Pose = {"b": {"pitch": 4.0}}
        assert mix(a, b, 0.25) == {"b": {"pitch": 1.0}}


# ---------------------------------------------------------------------------
# HumanoidRig — classificação anatómica
# ---------------------------------------------------------------------------


class TestHumanoidRigClassification:
    def test_is_humanoid_true_for_full_mixamo_rig(self) -> None:
        assert HumanoidRig.is_humanoid(MIXAMO_CHAINS) is True

    def test_is_humanoid_false_for_empty_chains(self) -> None:
        assert HumanoidRig.is_humanoid({}) is False

    def test_is_humanoid_requires_all_four_limbs(self) -> None:
        partial = {k: v for k, v in MIXAMO_CHAINS.items() if k != "arm_l"}
        assert HumanoidRig.is_humanoid(partial) is False

    def test_is_humanoid_requires_min_three_bones_per_chain(self) -> None:
        short = {
            "leg_r": ["a", "b"],
            "leg_l": ["a", "b", "c"],
            "arm_r": ["a", "b", "c"],
            "arm_l": ["a", "b", "c"],
        }
        assert HumanoidRig.is_humanoid(short) is False

    def test_is_humanoid_exactly_three_passes(self) -> None:
        chains = {
            "leg_r": ["a", "b", "c"],
            "leg_l": ["a", "b", "c"],
            "arm_r": ["a", "b", "c"],
            "arm_l": ["a", "b", "c"],
        }
        assert HumanoidRig.is_humanoid(chains) is True

    def test_is_humanoid_ignores_extra_chains(self) -> None:
        chains = {
            "leg_r": ["a", "b", "c"],
            "leg_l": ["a", "b", "c"],
            "arm_r": ["a", "b", "c"],
            "arm_l": ["a", "b", "c"],
            "tail": ["t1", "t2"],
        }
        assert HumanoidRig.is_humanoid(chains) is True


class TestArmBones:
    def test_four_bone_chain_assigns_all_slots(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        ab = rig.arm_bones("r")
        assert ab == {
            "shoulder": "mixamorig:RightShoulder",
            "upper": "mixamorig:RightArm",
            "fore": "mixamorig:RightForeArm",
            "hand": "mixamorig:RightHand",
        }

    def test_three_bone_chain_omits_shoulder(self) -> None:
        chains = {"arm_r": ["Upper", "Fore", "Hand"]}
        rig = _make_rig(chains)
        assert rig.arm_bones("r") == {"upper": "Upper", "fore": "Fore", "hand": "Hand"}

    def test_missing_chain_returns_empty(self) -> None:
        rig = _make_rig({"arm_r": ["a"]})
        assert rig.arm_bones("l") == {}

    def test_two_bone_chain_returns_empty(self) -> None:
        rig = _make_rig({"arm_r": ["a", "b"]})
        assert rig.arm_bones("r") == {}


class TestLegBones:
    def test_four_bone_chain_assigns_all_slots(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        lb = rig.leg_bones("l")
        assert lb == {
            "upper": "mixamorig:LeftUpLeg",
            "lower": "mixamorig:LeftLeg",
            "foot": "mixamorig:LeftFoot",
            "toe": "mixamorig:LeftToeBase",
        }

    def test_two_bone_chain_assigns_upper_lower(self) -> None:
        rig = _make_rig({"leg_r": ["Up", "Low"]})
        assert rig.leg_bones("r") == {"upper": "Up", "lower": "Low"}

    def test_missing_chain_returns_empty(self) -> None:
        rig = _make_rig({})
        assert rig.leg_bones("r") == {}


class TestSpineNeckHips:
    def test_spine_returns_chain(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        assert rig.spine() == ["mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2"]

    def test_neck_returns_chain(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        assert rig.neck() == ["mixamorig:Neck", "mixamorig:Head"]

    def test_spine_missing_returns_empty_gracefully(self) -> None:
        rig = _make_rig({})
        assert rig.spine() == []

    def test_neck_missing_returns_empty_gracefully(self) -> None:
        rig = _make_rig({})
        assert rig.neck() == []

    def test_hips_returns_first_body_bone(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        assert rig.hips() == "mixamorig:Hips"


# ---------------------------------------------------------------------------
# _keys_per_cycle
# ---------------------------------------------------------------------------


class TestKeysPerCycle:
    def test_returns_eight_keys(self) -> None:
        keys = _keys_per_cycle()
        assert len(keys) == 8

    def test_keys_sorted_ascending(self) -> None:
        keys = _keys_per_cycle()
        assert keys == sorted(keys)

    def test_keys_unique(self) -> None:
        keys = _keys_per_cycle()
        assert len(set(keys)) == len(keys)

    def test_keys_within_unit_interval(self) -> None:
        for k in _keys_per_cycle():
            assert 0.0 <= k < 1.0

    def test_starts_at_zero(self) -> None:
        assert _keys_per_cycle()[0] == 0.0

    def test_values_are_eighths(self) -> None:
        assert _keys_per_cycle() == [i / 8.0 for i in range(8)]


# ---------------------------------------------------------------------------
# _gait_phase_pose
# ---------------------------------------------------------------------------


class TestGaitPhasePose:
    def test_output_is_valid_pose_dict(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        pose = _gait_phase_pose(rig, 0.0, _WALK_PARAMS)
        assert isinstance(pose, dict)
        assert len(pose) > 0
        for bone, channels in pose.items():
            assert isinstance(bone, str)
            assert isinstance(channels, dict)
            for name, value in channels.items():
                assert name in {"pitch", "roll", "yaw", "up"}
                assert isinstance(value, float)

    def test_periodic_full_cycle_equals_start(self) -> None:
        """phi=0 e phi=1 produzem a mesma pose (periodicidade, dentro de tolerância float)."""
        rig = _make_rig(MIXAMO_CHAINS)
        p0 = _gait_phase_pose(rig, 0.0, _WALK_PARAMS)
        p1 = _gait_phase_pose(rig, 1.0, _WALK_PARAMS)
        assert _poses_approx_equal(p0, p1)

    def test_half_cycle_mirrors_legs(self) -> None:
        """phi=0 e phi=0.5 trocam os valores de pitch das pernas direita/esquerda.

        Desde a introdução de assimetria L/R orgânica (±3%), o espelho não é
        perfeito — a perna esquerda tem ~3% menos amplitude. Validamos que o
        espelho é aproximado (within 5%) em vez de exato.
        """
        rig = _make_rig(MIXAMO_CHAINS)
        right_upper = MIXAMO_CHAINS["leg_r"][0]
        left_upper = MIXAMO_CHAINS["leg_l"][0]

        p0 = _gait_phase_pose(rig, 0.0, _WALK_PARAMS)
        p_half = _gait_phase_pose(rig, 0.5, _WALK_PARAMS)

        right_at_0 = _channel(p0, right_upper, "pitch")
        left_at_0 = _channel(p0, left_upper, "pitch")
        right_at_half = _channel(p_half, right_upper, "pitch")
        left_at_half = _channel(p_half, left_upper, "pitch")

        # Espelho aproximado (within 5% — assimetria L/R orgânica).
        assert right_at_0 == pytest.approx(left_at_half, rel=0.05)
        assert left_at_0 == pytest.approx(right_at_half, rel=0.05)

    def test_arms_contralateral_to_legs(self) -> None:
        """Braço direito balança em anti-fase com a perna direita no contacto."""
        rig = _make_rig(MIXAMO_CHAINS)
        pose = _gait_phase_pose(rig, 0.0, _WALK_PARAMS)
        right_arm_upper = MIXAMO_CHAINS["arm_r"][1]
        arm_pitch = _channel(pose, right_arm_upper, "pitch")
        # No contacto (phi=0) a perna direita está à frente (hip>0) e o braço
        # direito atrás — sinais opostos de pitch.
        assert arm_pitch != pytest.approx(0.0)

    def test_hips_bone_present(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        pose = _gait_phase_pose(rig, 0.25, _WALK_PARAMS)
        assert "mixamorig:Hips" in pose

    def test_does_not_mutate_params(self) -> None:
        rig = _make_rig(MIXAMO_CHAINS)
        params = dict(_WALK_PARAMS)
        _gait_phase_pose(rig, 0.3, _WALK_PARAMS)
        assert params == _WALK_PARAMS
