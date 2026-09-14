"""Testes do núcleo IK/limites de juntas (animator3d.ik) — matemática pura, sem bpy."""

from __future__ import annotations

import math

import numpy as np
import pytest
from animator3d import ik

IDENTITY = np.array([1.0, 0.0, 0.0, 0.0])
BACK = np.array([0.0, 0.0, -1.0])  # -Z = "trás" num rig virado para +Z


def _uq(*args: float) -> np.ndarray:
    q = np.array(args, dtype=np.float64)
    return q / np.linalg.norm(q)


# ---------------------------------------------------------------------------
# q_* primitivas
# ---------------------------------------------------------------------------


def test_q_mul_identity() -> None:
    q = _uq(0.7071, 0.7071, 0.0, 0.0)
    assert np.allclose(ik.q_mul(q, IDENTITY), q)
    assert np.allclose(ik.q_mul(IDENTITY, q), q)


def test_q_mul_composes_rotations() -> None:
    q90z = ik.q_from_axis_angle(np.array([0.0, 0.0, 1.0]), math.pi / 2)
    v = ik.q_rot_vec(q90z, ik.q_rot_vec(q90z, np.array([1.0, 0.0, 0.0])))
    assert np.allclose(v, [-1.0, 0.0, 0.0], atol=1e-9)


def test_q_normalize_zero_returns_identity() -> None:
    assert np.allclose(ik.q_normalize(np.zeros(4)), IDENTITY)


def test_rotation_between_perpendicular() -> None:
    q = ik.rotation_between(np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0]))
    assert np.allclose(ik.q_rot_vec(q, [1.0, 0.0, 0.0]), [0.0, 1.0, 0.0], atol=1e-9)


def test_rotation_between_opposite_vectors_is_180() -> None:
    q = ik.rotation_between(np.array([0.0, 0.0, 1.0]), np.array([0.0, 0.0, -1.0]))
    out = ik.q_rot_vec(q, [0.0, 0.0, 1.0])
    assert np.allclose(out, [0.0, 0.0, -1.0], atol=1e-6)


def test_rotation_between_degenerate_returns_identity() -> None:
    assert np.allclose(ik.rotation_between(np.zeros(3), np.array([1.0, 0.0, 0.0])), IDENTITY)


@pytest.mark.parametrize("angle", [0.0, 0.5, 1.2, math.pi])
def test_swing_twist_round_trip(angle: float) -> None:
    q = ik.q_mul(
        ik.q_from_axis_angle(np.array([0.3, 0.8, 0.2]), angle),  # swing
        ik.q_from_axis_angle(np.array([0.0, 1.0, 0.0]), 0.7),  # twist em +Y
    )
    swing, twist = ik.swing_twist_split(q, np.array([0.0, 1.0, 0.0]))
    rebuilt = ik.q_mul(swing, ik.q_from_axis_angle(np.array([0.0, 1.0, 0.0]), twist))
    assert np.allclose(rebuilt, q, atol=1e-9) or np.allclose(rebuilt, -q, atol=1e-9)


def test_swing_twist_identity_is_zero() -> None:
    swing, twist = ik.swing_twist_split(IDENTITY, np.array([0.0, 1.0, 0.0]))
    assert abs(twist) < 1e-9
    assert np.allclose(swing, IDENTITY, atol=1e-9)


def test_swing_twist_negative_w_canonicalized() -> None:
    # -q ≡ q: o twist medido não pode "explodir" com w negativo.
    swing, twist = ik.swing_twist_split(-IDENTITY, np.array([0.0, 1.0, 0.0]))
    assert abs(twist) < 1e-9
    assert swing[0] >= 0.0


def test_swing_twist_clamp_noop_within_caps() -> None:
    q = ik.q_from_axis_angle(np.array([1.0, 0.0, 0.0]), math.radians(30))
    out, changed = ik.swing_twist_clamp(q, np.array([0.0, 1.0, 0.0]), max_swing_deg=60, max_twist_deg=45)
    assert not changed
    assert np.allclose(out, q, atol=1e-9)


def test_swing_twist_clamp_limits_twist() -> None:
    q = ik.q_from_axis_angle(np.array([0.0, 1.0, 0.0]), math.radians(90))  # twist puro em +Y
    out, changed = ik.swing_twist_clamp(q, np.array([0.0, 1.0, 0.0]), max_swing_deg=165, max_twist_deg=45)
    assert changed
    swing, twist = ik.swing_twist_split(out, np.array([0.0, 1.0, 0.0]))
    assert abs(math.degrees(twist)) == pytest.approx(45.0, abs=0.5)


def test_swing_twist_clamp_limits_swing() -> None:
    q = ik.q_from_axis_angle(np.array([1.0, 0.0, 0.0]), math.radians(120))  # swing puro
    out, changed = ik.swing_twist_clamp(q, np.array([0.0, 1.0, 0.0]), max_swing_deg=60, max_twist_deg=90)
    assert changed
    swing, _ = ik.swing_twist_split(out, np.array([0.0, 1.0, 0.0]))
    swing_deg = math.degrees(2 * math.atan2(float(np.linalg.norm(swing[1:])), float(swing[0])))
    assert swing_deg == pytest.approx(60.0, abs=0.5)


# ---------------------------------------------------------------------------
# Geometria de cadeia 2-ossos
# ---------------------------------------------------------------------------


def test_interior_angle_straight_is_180() -> None:
    root = np.array([0.0, 1.0, 0.0])
    joint = np.zeros(3)
    dist = np.array([0.0, -1.0, 0.0])
    assert ik.interior_angle_deg(root, joint, dist) == pytest.approx(180.0)


def test_interior_angle_right_angle() -> None:
    ang = ik.interior_angle_deg(np.array([1.0, 0.0, 0.0]), np.zeros(3), np.array([0.0, 1.0, 0.0]))
    assert ang == pytest.approx(90.0)


def test_distance_for_interior_angle_known_values() -> None:
    assert ik.distance_for_interior_angle(1.0, 1.0, 180.0) == pytest.approx(2.0)
    assert ik.distance_for_interior_angle(1.0, 1.0, 90.0) == pytest.approx(math.sqrt(2.0))
    assert ik.distance_for_interior_angle(1.0, 1.0, 0.0) == pytest.approx(0.0)


@pytest.mark.parametrize("angle", [30.0, 70.0, 90.0, 140.0, 170.0])
def test_two_bone_knee_reproduces_interior_angle(angle: float) -> None:
    root = np.zeros(3)
    d = ik.distance_for_interior_angle(1.0, 1.0, angle)
    target = np.array([0.0, d, 0.0])
    knee = ik.two_bone_knee_position(root, target, 1.0, 1.0, BACK)
    assert ik.interior_angle_deg(root, knee, target) == pytest.approx(angle, abs=0.1)


def test_two_bone_knee_respects_pole_side() -> None:
    root = np.zeros(3)
    target = np.array([0.0, 1.2, 0.0])
    knee_back = ik.two_bone_knee_position(root, target, 1.0, 1.0, BACK)
    knee_front = ik.two_bone_knee_position(root, target, 1.0, 1.0, -BACK)
    assert knee_back[2] < 0.0 < knee_front[2]
    # Espelho: mesmo ângulo interior nos dois lados.
    assert ik.interior_angle_deg(root, knee_back, target) == pytest.approx(
        ik.interior_angle_deg(root, knee_front, target), abs=0.1
    )


def test_two_bone_knee_unreachable_target_stretches() -> None:
    root = np.zeros(3)
    knee = ik.two_bone_knee_position(root, np.array([0.0, 5.0, 0.0]), 1.0, 1.0, BACK)
    assert float(np.linalg.norm(knee - root)) == pytest.approx(1.0, abs=0.01)
    assert abs(knee[2]) < 0.05  # praticamente esticada


def test_two_bone_knee_collapsed_chain_folds_toward_pole() -> None:
    root = np.zeros(3)
    knee = ik.two_bone_knee_position(root, np.zeros(3), 1.0, 1.0, BACK)
    # Cadeia colapsada: dobra na direção do pole.
    assert np.dot(knee, BACK) > 0.0


def test_hinge_axis_alignment_signs() -> None:
    """Eixo de dobradiça: dobra para trás > 0; dobradiça invertida < 0."""
    root = np.zeros(3)
    l1 = l2 = 1.0

    def pose(thigh_deg: float, shin_world_deg: float) -> tuple[np.ndarray, np.ndarray]:
        knee = root + ik.q_rot_vec(
            ik.q_from_axis_angle(np.array([1.0, 0.0, 0.0]), math.radians(thigh_deg)), [0, 0, -l1]
        )
        eff = knee + ik.q_rot_vec(
            ik.q_from_axis_angle(np.array([1.0, 0.0, 0.0]), math.radians(shin_world_deg)), [0, 0, -l2]
        )
        return knee, eff

    pole = np.array([0.0, -1.0, 0.0])  # trás
    # Dobra para trás (shin 90° atrás do thigh vertical): eixo alinhado.
    knee, eff = pose(0.0, -90.0)
    assert ik.hinge_axis_alignment(root, knee, eff, pole) > 0.5
    # Dobra para a FRENTE (inversão): eixo ao contrário.
    knee, eff = pose(0.0, 90.0)
    assert ik.hinge_axis_alignment(root, knee, eff, pole) < -0.5
    # Perna em balanço para trás com dobra para trás: segue positivo (o eixo
    # acompanha o swing — é a vantagem sobre o teste de posição vs corda).
    knee, eff = pose(-45.0, -135.0)
    assert ik.hinge_axis_alignment(root, knee, eff, pole) > 0.5
    # Membro esticado: sem sinal fiável (~0).
    knee, eff = pose(0.0, 0.0)
    assert abs(ik.hinge_axis_alignment(root, knee, eff, pole)) < 0.1


def test_hinge_axis_alignment_degenerate() -> None:
    z = np.zeros(3)
    assert ik.hinge_axis_alignment(z, z, z, np.array([0.0, -1.0, 0.0])) == 0.0


# ---------------------------------------------------------------------------
# Limites: defaults, override, validação
# ---------------------------------------------------------------------------


def test_load_limits_builtin_defaults() -> None:
    lim = ik.load_limits()
    assert lim.enabled
    assert set(lim.roles) >= {"leg", "arm"}
    leg = lim.roles["leg"]
    assert leg.hinge_min < leg.hinge_max <= 180.0
    assert leg.one_way
    assert leg.pole == "auto"


def test_load_limits_override_merges_shallow() -> None:
    lim = ik.load_limits(override={"leg": {"hinge_min": 60.0}})
    assert lim.roles["leg"].hinge_min == 60.0
    assert lim.roles["leg"].hinge_max == ik.load_limits().roles["leg"].hinge_max  # resto mantido
    assert lim.roles["arm"].hinge_min == ik.load_limits().roles["arm"].hinge_min  # outro papel intacto


def test_load_limits_override_can_disable() -> None:
    lim = ik.load_limits(override={"enabled": False})
    assert not lim.enabled


def test_load_limits_unknown_role_raises() -> None:
    with pytest.raises(ValueError, match="papel desconhecido"):
        ik.load_limits(override={"tail": {"hinge_min": 10.0}})


def test_load_limits_unknown_key_raises() -> None:
    with pytest.raises(ValueError, match="desconhecidas"):
        ik.load_limits(override={"leg": {"hinge_min_deg": 10.0}})


def test_load_limits_inverted_band_raises() -> None:
    with pytest.raises(ValueError, match="hinge_min"):
        ik.load_limits(override={"leg": {"hinge_min": 170.0, "hinge_max": 90.0}})


def test_load_limits_out_of_range_band_raises() -> None:
    with pytest.raises(ValueError, match="0, 180"):
        ik.load_limits(override={"leg": {"hinge_min": -10.0, "hinge_max": 90.0}})


def test_load_limits_bad_pole_raises() -> None:
    with pytest.raises(ValueError, match="pole"):
        ik.load_limits(override={"leg": {"pole": "diagonal"}})


def test_load_limits_missing_file_raises(tmp_path) -> None:
    from pathlib import Path as P

    with pytest.raises(FileNotFoundError):
        ik.load_limits(path=P(tmp_path) / "missing.yaml")


# ---------------------------------------------------------------------------
# resolve_ik_chains (com chains injetadas — sem bpy)
# ---------------------------------------------------------------------------


def test_resolve_chains_humanoid_layout() -> None:
    chains = {
        "leg_r": ["thigh_r", "calf_r", "foot_r"],
        "leg_l": ["thigh_l", "calf_l", "foot_l", "toe_l"],
        "arm_r": ["clavicle_r", "upperarm_r", "forearm_r", "hand_r"],
        "arm_l": ["upperarm_l", "forearm_l", "hand_l"],
    }
    out = ik.resolve_ik_chains("x", chains)
    roles = {(c.role, c.upper, c.lower, c.effector) for c in out}
    assert ("leg", "thigh_r", "calf_r", "foot_r") in roles
    # Perna com toe: effector é o foot (não o toe).
    assert ("leg", "thigh_l", "calf_l", "foot_l") in roles
    # Braço com clavicle: shoulder saltado.
    assert ("arm", "upperarm_r", "forearm_r", "hand_r") in roles
    assert ("arm", "upperarm_l", "forearm_l", "hand_l") in roles


def test_resolve_chains_multi_leg() -> None:
    chains = {
        "legs_r": [["coxa_r1", "tibia_r1"], ["coxa_r2", "tibia_r2", "tarso_r2"]],
        "legs_l": [["coxa_l1", "tibia_l1"]],
    }
    out = ik.resolve_ik_chains("x", chains)
    assert len(out) == 3
    assert all(c.role == "leg" for c in out)
    assert all(c.effector is None or c.effector == "tarso_r2" for c in out)


def test_resolve_chains_dedup_leg_and_legs() -> None:
    chains = {
        "leg_r": ["thigh_r", "calf_r", "foot_r"],
        "legs_r": [["thigh_r", "calf_r", "foot_r"]],
    }
    out = ik.resolve_ik_chains("x", chains)
    assert len(out) == 1


def test_resolve_chains_short_chains_skipped() -> None:
    assert ik.resolve_ik_chains("x", {"leg_r": ["thigh_r"]}) == []


# ---------------------------------------------------------------------------
# CLI: opções presentes (sem bpy — só --help)
# ---------------------------------------------------------------------------


def _opts(command: str) -> set[str]:
    from animator3d.cli import main

    cmd = main.commands[command]
    # Flags booleanas do click guardam o --no-X em secondary_opts.
    return {opt for param in cmd.params for opt in (*param.opts, *param.secondary_opts)}


@pytest.mark.parametrize("command", ["game-pack", "retarget", "retarget-batch"])
def test_ik_limits_flag_on_commands(command: str) -> None:
    opts = _opts(command)
    assert "--ik-limits" in opts
    assert "--no-ik-limits" in opts


def test_ik_limits_command_exists() -> None:
    from animator3d.cli import main

    assert "ik-limits" in main.commands
    opts = _opts("ik-limits")
    assert "--profile" in opts
    assert "--disable" in opts
