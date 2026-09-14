"""Passe IK/limites com bpy real sobre armature sintético (skip fora do venv com bpy).

Cobre: reparação de joelho invertido preservando o pé, clamp de curso
(hiperextensão), clamp swing/twist da anca, frames corretos intactos, cobertura
NLA e opt-out global.
"""

from __future__ import annotations

import math
from importlib.util import find_spec

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(find_spec("bpy") is None, reason="requer bpy (venv do Animator3D)")

L1 = 0.5  # thigh_l: (0.1, 0, 1.0) -> (0.1, 0, 0.5)
L2 = 0.45  # calf_l: (0.1, 0, 0.5) -> (0.1, 0, 0.05)


def _arm_quat(bone_name: str):
    import bpy

    return bpy.data.objects["Rig"].data.bones[bone_name].matrix_local.to_quaternion()


def _set_world_rot(pb, q_world) -> None:
    """Escreve a rotação no mundo (fórmula do humanoid.key_bone: rest⁻¹·q·rest)."""
    rest = _arm_quat(pb.name)
    pb.rotation_quaternion = rest.inverted() @ q_world @ rest


def _key_world_rot(pb, frame: int, q_world) -> None:
    _set_world_rot(pb, q_world)
    pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)


def _measure() -> tuple[float, float, np.ndarray]:
    """(ângulo interior, side vs pole auto, pos do pé) da perna esquerda."""
    import bpy
    from animator3d import bpy_ops, ik

    arm = bpy.data.objects["Rig"]
    chains = ik.resolve_ik_chains("Rig")
    chain = next(c for c in chains if c.upper == "thigh_l")
    lim = ik.load_limits().roles["leg"]
    pole = ik._resolve_pole(arm, chain, lim, bpy_ops._detect_forward(arm, bpy_ops._classify_bone_chains("Rig")))
    pb_u, pb_l = arm.pose.bones["thigh_l"], arm.pose.bones["calf_l"]
    root = np.array(pb_u.matrix.translation)
    knee = np.array(pb_l.matrix.translation)
    eff = np.array(arm.pose.bones[chain.effector].matrix.translation)
    # Pole na convenção do passe: rest do osso upper, rodado por W1(t).
    r1 = _arm_quat("thigh_l")
    r1_arr = np.array([r1.w, r1.x, r1.y, r1.z])
    pul = ik.q_rot_vec(ik.q_conj(r1_arr), np.array([pole.x, pole.y, pole.z]))
    w1 = pb_u.matrix.to_quaternion()
    w1_arr = np.array([w1.w, w1.x, w1.y, w1.z])
    pole_arr = ik.q_rot_vec(w1_arr, pul)
    return ik.interior_angle_deg(root, knee, eff), ik.hinge_axis_alignment(root, knee, eff, pole_arr), eff


def _build_rig() -> None:
    import bpy
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_data = bpy.data.armatures.new("Arm")
    obj = bpy.data.objects.new("Rig", arm_data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name: str, head: tuple[float, float, float], tail: tuple[float, float, float], parent: str | None):
        eb = arm_data.edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        # Sem use_connect: ligar à pelvis puxaria o head da coxa para o tail
        # dela (x=0) e o classificador deixaria de a ver como lateral.
        if parent:
            eb.parent = arm_data.edit_bones[parent]

    bone("pelvis", (0.0, 0.0, 1.0), (0.0, 0.0, 1.1), None)
    for side, x in (("l", 0.25), ("r", -0.25)):
        bone(f"thigh_{side}", (x, 0.0, 1.0), (x, 0.0, 1.0 - L1), "pelvis")
        bone(f"calf_{side}", (x, 0.0, 1.0 - L1), (x, 0.0, 1.0 - L1 - L2), f"thigh_{side}")
        bone(f"foot_{side}", (x, 0.0, 1.0 - L1 - L2), (x, 0.15, 0.03), f"calf_{side}")
    bpy.ops.object.mode_set(mode="OBJECT")

    for pb in obj.pose.bones:
        pb.rotation_mode = "QUATERNION"


def _make_action(name: str, frames: dict[int, tuple[float, float]]) -> None:
    """Action com rotações X-mundo (thigh, calf) por frame."""
    import bpy
    from mathutils import Quaternion

    obj = bpy.data.objects["Rig"]
    ad = obj.animation_data or obj.animation_data_create()
    act = bpy.data.actions.new(name)
    ad.action = act
    for pb in obj.pose.bones:
        pb.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    for frame, (thigh_x, calf_x) in frames.items():
        _key_world_rot(obj.pose.bones["thigh_l"], frame, Quaternion((1.0, 0.0, 0.0), math.radians(thigh_x)))
        _key_world_rot(obj.pose.bones["calf_l"], frame, Quaternion((1.0, 0.0, 0.0), math.radians(calf_x)))


@pytest.fixture()
def rig():
    _build_rig()
    yield "Rig"


def test_flip_repairs_inverted_knee_preserving_foot(rig) -> None:
    """Joelho virado para a frente (lado errado do pole) é espelhado com o pé NO SÍTIO."""
    import bpy
    from animator3d import ik

    # f2-f7 corretos (dobra para trás); f8 com dobradiça INVERTIDA (calf
    # +40 relativo → dobra para a frente). A maioria (>70%) tem de estar
    # correta para o one_way ativar.
    frames = {i: (-20.0, -70.0) for i in range(1, 8)}
    frames[8] = (50.0, 90.0)
    _make_action("walk", frames)
    scene = bpy.context.scene
    scene.frame_set(8)
    bpy.context.view_layer.update()
    ang_bad, side_bad, foot_before = _measure()
    assert side_bad < 0.0, "fixture devia gerar joelho do lado errado"

    stats = ik.enforce_joint_limits(rig)
    assert stats is not None and stats["flips"] >= 1

    scene.frame_set(8)
    bpy.context.view_layer.update()
    ang_fixed, side_fixed, foot_after = _measure()
    assert side_fixed > 0.0, "joelho devia ter passado para o lado do pole"
    assert ang_fixed == pytest.approx(ang_bad, abs=1.0), "flip preserva o ângulo interior"
    assert np.linalg.norm(foot_after - foot_before) < 1e-3, "pé preservado"


def test_hinge_clamp_folds_hyperextension_into_band(rig) -> None:
    """Ângulo interior abaixo da banda (colapso) é clampeado para hinge_min."""
    import bpy
    from animator3d import ik

    # f2: tuck extremo para trás (interior 30° < hinge_min 40), dobra do
    # lado certo do pole para exercitar só o ramo de clamp de curso.
    _make_action("walk", {1: (0.0, 0.0), 2: (-20.0, -150.0)})
    scene = bpy.context.scene
    scene.frame_set(2)
    bpy.context.view_layer.update()
    ang_bad, _, _ = _measure()
    assert ang_bad < ik.load_limits().roles["leg"].hinge_min

    stats = ik.enforce_joint_limits(rig)
    assert stats is not None and stats["hinge_fixes"] >= 1

    scene.frame_set(2)
    bpy.context.view_layer.update()
    ang_fixed, side_fixed, _ = _measure()
    lim = ik.load_limits().roles["leg"]
    assert lim.hinge_min - 1.0 <= ang_fixed <= lim.hinge_max + 1.0
    assert side_fixed > 0.0, "clamp não pode inverter a direção da dobradiça"


def test_swing_twist_clamp_limits_hip_twist(rig) -> None:
    """Twist da anca acima do cap é reduzido; swing de joelho correto intacto."""
    import bpy
    from animator3d import ik
    from mathutils import Quaternion

    _make_action("walk", {1: (0.0, 0.0), 2: (-20.0, -70.0), 3: (-20.0, -70.0)})
    obj = bpy.data.objects["Rig"]
    # f3: twist puro da perna (rotação sobre o eixo do osso, Z mundo).
    _key_world_rot(obj.pose.bones["thigh_l"], 3, Quaternion((0.0, 0.0, 1.0), math.radians(120)))

    stats = ik.enforce_joint_limits(rig)
    assert stats is not None and stats["ball_clamps"] >= 1

    bpy.context.scene.frame_set(3)
    bpy.context.view_layer.update()

    arm = bpy.data.objects["Rig"]
    pb_u = arm.pose.bones["thigh_l"]
    w1 = pb_u.matrix.to_quaternion()
    wp = pb_u.parent.matrix.to_quaternion()
    rp = arm.data.bones["pelvis"].matrix_local.to_quaternion()
    r1 = _arm_quat("thigh_l")
    # decomposição direta: basis = (Wp·Rp⁻¹·R1)⁻¹·W1
    ref = wp @ rp.inverted() @ r1
    q_rel = np.array([v for v in (ref.inverted() @ w1)])
    _, twist = ik.swing_twist_split(q_rel, np.array([0.0, 1.0, 0.0]))
    assert abs(math.degrees(twist)) <= ik.load_limits().roles["leg"].upper_twist_max + 1.0


def test_correct_frames_untouched(rig) -> None:
    """Frames anatomicamente corretos ficam bit-idênticos."""
    import bpy
    from animator3d import ik

    frames = {1: (0.0, 0.0), 2: (-20.0, -70.0), 3: (50.0, 90.0), 4: (-35.0, -90.0)}
    _make_action("walk", frames)
    obj = bpy.data.objects["Rig"]
    ad = obj.animation_data

    def calf_keys() -> dict[float, float]:
        act = ad.action
        for fc in ik._action_fcurves(act):
            if 'calf_l"' in fc.data_path and fc.array_index == 0:
                return {round(kp.co.x, 3): round(kp.co.y, 6) for kp in fc.keyframe_points}
        raise AssertionError("fcurve de calf_l não encontrada")

    before = calf_keys()
    ik.enforce_joint_limits(rig)
    after = calf_keys()
    assert set(before) == set(after), "densidade de keys preservada"
    changed = [f for f in before if before[f] != after[f]]
    # 3 é o frame invertido (corrigido); 1/2/4 são válidos e ficam intactos.
    assert set(changed) <= {3.0}, f"frames corretos alterados: {changed}"


def test_enforce_all_actions_covers_nla(rig) -> None:
    """Actions empurradas para NLA são processadas por enforce_all_actions."""
    import bpy
    from animator3d import ik

    frames = {i: (-20.0, -70.0) for i in range(1, 7)}
    frames[7] = (50.0, 90.0)
    _make_action("walk", frames)
    obj = bpy.data.objects["Rig"]
    ad = obj.animation_data
    act = ad.action
    track = ad.nla_tracks.new()
    track.strips.new("walk", 1, act)
    ad.action = None

    results = ik.enforce_all_actions(rig)
    clips = [s["clip"] for s in results]
    assert "walk" in clips
    walk = next(s for s in results if s["clip"] == "walk")
    assert walk["flips"] + walk["hinge_fixes"] >= 1


def test_disabled_limits_noop(rig) -> None:
    """``enabled: false`` desliga o passe (mesmo com joelho invertido)."""
    from animator3d import ik

    _make_action("walk", {1: (0.0, 0.0), 2: (50.0, -10.0)})
    limits = ik.load_limits(override={"enabled": False})
    assert ik.enforce_joint_limits(rig, limits=limits) is None
    assert ik.enforce_all_actions(rig, limits=limits) == []


def test_enforce_requires_armature(rig) -> None:
    """Nome de armature inexistente -> ValueError."""
    from animator3d import ik

    _make_action("walk", {1: (0.0, 0.0)})
    with pytest.raises(ValueError, match="não encontrado"):
        ik.enforce_joint_limits("Fantasma")
