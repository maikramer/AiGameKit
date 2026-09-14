"""Clips de quadrúpede (galope, attack v2, death, hit, idle v2) com bpy real.

Rig sintético: pelvis + spine + neck + Head + 4 patas (2 traseiras na pelvis,
2 dianteiras no spine_02). Valida: keys em todas as patas, galope/fases,
death terminal (pelve desce e segura), hit one-shot, lunge do attack e —
crítico — que TODOS os clips gerados passam o passe IK/limites com ZERO
correções (geradores limits-clean por construção).
"""

from __future__ import annotations

from importlib.util import find_spec

import pytest

pytestmark = pytest.mark.skipif(find_spec("bpy") is None, reason="requer bpy (venv do Animator3D)")

THIGHS = ("thigh_r", "thigh_l", "thigh_r2", "thigh_l2")
CALVES = ("calf_r", "calf_l", "calf_r2", "calf_l2")


def _build_quad() -> None:
    import bpy
    from mathutils import Vector

    bpy.ops.wm.read_factory_settings(use_empty=True)
    arm_data = bpy.data.armatures.new("Arm")
    obj = bpy.data.objects.new("Quad", arm_data)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")

    def bone(name: str, head, tail, parent: str | None):
        eb = arm_data.edit_bones.new(name)
        eb.head = Vector(head)
        eb.tail = Vector(tail)
        if parent:
            eb.parent = arm_data.edit_bones[parent]

    # Tronco: pelvis -> spine_01 -> spine_02 (hub) -> neck -> Head.
    bone("pelvis", (0.0, 0.0, 1.0), (0.0, 0.05, 1.1), None)
    bone("spine_01", (0.0, 0.05, 1.1), (0.0, 0.25, 1.15), "pelvis")
    bone("spine_02", (0.0, 0.25, 1.15), (0.0, 0.45, 1.18), "spine_01")
    bone("neck_01", (0.0, 0.45, 1.18), (0.0, 0.55, 1.25), "spine_02")
    bone("neck_02", (0.0, 0.55, 1.25), (0.0, 0.62, 1.30), "neck_01")
    bone("Head", (0.0, 0.62, 1.30), (0.0, 0.72, 1.32), "neck_02")
    # Patas traseiras (na pelvis) e dianteiras (no spine_02). Sem use_connect:
    # ligar puxaria o head para o tail do pai e a lateralidade perdia-se.
    for sx, sfx in ((0.2, "r"), (-0.2, "l")):
        bone(f"thigh_{sfx}", (sx, 0.05, 1.05), (sx, 0.05, 0.6), "pelvis")
        bone(f"calf_{sfx}", (sx, 0.05, 0.6), (sx, 0.05, 0.15), f"thigh_{sfx}")
        bone(f"foot_{sfx}", (sx, 0.05, 0.15), (sx, 0.17, 0.12), f"calf_{sfx}")
        bone(f"thigh_{sfx}2", (sx, 0.45, 1.15), (sx, 0.45, 0.7), "spine_02")
        bone(f"calf_{sfx}2", (sx, 0.45, 0.7), (sx, 0.45, 0.25), f"thigh_{sfx}2")
        bone(f"foot_{sfx}2", (sx, 0.45, 0.25), (sx, 0.57, 0.22), f"calf_{sfx}2")
    bpy.ops.object.mode_set(mode="OBJECT")
    for pb in obj.pose.bones:
        pb.rotation_mode = "QUATERNION"


@pytest.fixture()
def quad():
    _build_quad()
    yield "Quad"


def _rot_paths(clip_name: str, bone_sub: str) -> int:
    """Nº de fcurves de rotação de um osso numa action por nome de clip."""
    import bpy
    from animator3d import ik

    ad = bpy.data.objects["Quad"].animation_data
    act = next(a for t in ad.nla_tracks for s in t.strips if (a := s.action) and a.name == clip_name)
    return sum(1 for fc in ik._action_fcurves(act) if bone_sub in fc.data_path and "rotation" in fc.data_path)


def _action(clip_name: str):
    import bpy

    ad = bpy.data.objects["Quad"].animation_data
    return next(s.action for t in ad.nla_tracks for s in t.strips if s.action and s.action.name == clip_name)


# ---------------------------------------------------------------------------
# Puros (sem bpy)
# ---------------------------------------------------------------------------


def test_split_front_hind_orders_by_forward() -> None:
    """Dianteiras = maior projeção no forward (perto do pescoço)."""
    from animator3d import bpy_ops

    chains = {
        "legs_r": [["thigh_r", "calf_r", "foot_r"], ["thigh_r2", "calf_r2", "foot_r2"]],
        "legs_l": [["thigh_l", "calf_l", "foot_l"], ["thigh_l2", "calf_l2", "foot_l2"]],
    }

    class FakeBone:
        def __init__(self, x: float, y: float) -> None:
            self.head_local = type("H", (), {"x": x, "y": y, "z": 1.0})()

    class FakeArm:
        data = type(
            "D",
            (),
            {
                "bones": {
                    "thigh_r": FakeBone(0.2, 0.05),
                    "thigh_r2": FakeBone(0.2, 0.45),
                    "thigh_l": FakeBone(-0.2, 0.05),
                    "thigh_l2": FakeBone(-0.2, 0.45),
                }
            },
        )()

    forward = type("F", (), {"x": 0.0, "y": 1.0})()
    front, hind = bpy_ops._split_front_hind_legs(FakeArm(), chains, forward)
    assert front["r"] == [["thigh_r2", "calf_r2", "foot_r2"]]
    assert hind["r"] == [["thigh_r", "calf_r", "foot_r"]]
    assert front["l"] == [["thigh_l2", "calf_l2", "foot_l2"]]


def test_gallop_phases_quadruped_and_fallbacks() -> None:
    from animator3d import bpy_ops

    front = {"r": [["fr"]], "l": [["fl"]]}
    hind = {"r": [["hr"]], "l": [["hl"]]}
    phases = bpy_ops._gallop_phases(front, hind)
    # Traseiras arrancam primeiro; dianteiras a meio ciclo; Δ esquerda 0.1.
    assert phases == {("hr",): 0.0, ("hl",): 0.10, ("fr",): 0.50, ("fl",): 0.60}
    # Bípede (1 pata/lado sem par traseiro): None → gait alternado.
    assert bpy_ops._gallop_phases({"r": [], "l": []}, {"r": [["hr"]], "l": [["hl"]]}) is None
    # Aracnídeo (2 patas/lado): None → gait alternado.
    assert bpy_ops._gallop_phases({"r": [["f1"], ["f2"]], "l": []}, {"r": [], "l": [["h1"], ["h2"]]}) is None


def test_gait_phases_trot_preserved() -> None:
    """O walk mantém o trot diagonal (2 patas/lado = [0, 0.5])."""
    from animator3d import bpy_ops

    assert bpy_ops._gait_phases(2) == [0.0, 0.5]


def test_creature_preset_has_real_hit_death() -> None:
    """Hit/Death do preset creature são geradores reais (não aliases mine/chop)."""
    from animator3d.cli import _PRESETS

    creature = _PRESETS["creature"]
    hit = [kw for fn, kw in creature if fn == "hit_keyframes"]
    death = [kw for fn, kw in creature if fn == "death_keyframes"]
    assert hit and hit[0]["action_name"] == "Animator3D_Hit"
    assert death and death[0]["action_name"] == "Animator3D_Death"
    # Nenhum alias histórico restante (Hit=mine, Death=chop).
    assert not [kw for fn, kw in creature if fn == "mine_keyframes" and kw["action_name"] == "Animator3D_Hit"]
    assert not [kw for fn, kw in creature if fn == "chop_keyframes" and kw["action_name"] == "Animator3D_Death"]


# ---------------------------------------------------------------------------
# bpy real (rig sintético)
# ---------------------------------------------------------------------------


def test_split_front_hind_on_real_quad(quad: str) -> None:
    import bpy
    from animator3d import bpy_ops

    arm = bpy.data.objects[quad]
    chains = bpy_ops._classify_bone_chains(quad)
    forward = bpy_ops._detect_forward(arm, chains)
    front, hind = bpy_ops._split_front_hind_legs(arm, chains, forward)
    # 2+2: dianteiras (y=0.45) vs traseiras (y=0.05).
    assert len(front["r"]) == len(front["l"]) == len(hind["r"]) == len(hind["l"]) == 1
    assert front["r"][0][0] == "thigh_r2"
    assert hind["r"][0][0] == "thigh_r"
    assert bpy_ops._gallop_phases(front, hind) is not None


@pytest.mark.parametrize(
    "gen,clip",
    [
        ("walk_cycle_keyframes", "Animator3D_Walk"),
        ("run_cycle_keyframes", "Animator3D_Run"),
        ("attack_keyframes", "Animator3D_Attack"),
        ("death_keyframes", "Animator3D_Death"),
        ("hit_keyframes", "Animator3D_Hit"),
        ("breathe_idle_keyframes", "Animator3D_BreatheIdle"),
    ],
)
def test_clip_writes_keys_on_all_legs(quad: str, gen: str, clip: str) -> None:
    """Cada clip novo anima as 4 patas (hips + knees) — nada de patas congeladas."""
    from animator3d import bpy_ops

    fn = getattr(bpy_ops, gen)
    fn(quad, frame_end=32, action_name=clip)
    for bn in THIGHS + CALVES:
        assert _rot_paths(clip, f'"{bn}"') >= 1, f"{clip} não anima {bn}"


def test_death_drops_and_holds(quad: str) -> None:
    """Death: pelve desce (colapso) e a pose terminal é estável (hold)."""

    from animator3d import bpy_ops

    bpy_ops.death_keyframes(quad, frame_end=48)
    act = _action("Animator3D_Death")
    loc_fcs = [fc for fc in _iter_fcurves(act) if "pelvis" in fc.data_path and "location" in fc.data_path]
    assert loc_fcs, "death devia keyar a location da pelvis"
    drop_fc = max(loc_fcs, key=lambda fc: max(abs(kp.co.y) for kp in fc.keyframe_points))
    vals = [kp.co.y for kp in drop_fc.keyframe_points]
    assert len(vals) == 48
    assert min(vals) < -0.2, f"pelve devia descer no colapso (vals min={min(vals):.3f})"
    # Hold: os últimos valores convergem (pose terminal estável).
    assert abs(vals[-1] - vals[-2]) < 1e-3


def test_hit_is_short_one_shot(quad: str) -> None:
    from animator3d import bpy_ops

    bpy_ops.hit_keyframes(quad)
    act = _action("Animator3D_Hit")
    assert int(act.frame_range[1]) - int(act.frame_range[0]) <= 16


def test_attack_lunge_moves_body(quad: str) -> None:
    """Attack v2: o corpo avança (location no eixo forward) no pico do strike."""

    from animator3d import bpy_ops

    bpy_ops.attack_keyframes(quad, frame_end=48)
    act = _action("Animator3D_Attack")
    loc_fcs = [fc for fc in _iter_fcurves(act) if "pelvis" in fc.data_path and "location" in fc.data_path]
    assert loc_fcs, "attack devia ter location do lunge na pelvis"
    assert any(abs(kp.co.y) > 0.02 for fc in loc_fcs for kp in fc.keyframe_points)


def test_idle_has_weight_shift(quad: str) -> None:
    from animator3d import bpy_ops

    bpy_ops.breathe_idle_keyframes(quad, frame_end=72)
    act = _action("Animator3D_BreatheIdle")
    loc_fcs = [fc for fc in _iter_fcurves(act) if "pelvis" in fc.data_path and "location" in fc.data_path]
    assert loc_fcs, "idle v2 devia ter shift de peso (location da pelvis)"


def _iter_fcurves(act):
    from animator3d import ik

    return ik._action_fcurves(act)


def test_generated_clips_are_ik_clean(quad: str) -> None:
    """CRÍTICO: todos os clips novos passam o passe IK/limites sem correções."""
    from animator3d import bpy_ops, ik

    gens = [
        (bpy_ops.walk_cycle_keyframes, 48, "Animator3D_Walk"),
        (bpy_ops.run_cycle_keyframes, 36, "Animator3D_Run"),
        (bpy_ops.attack_keyframes, 48, "Animator3D_Attack"),
        (bpy_ops.death_keyframes, 48, "Animator3D_Death"),
        (bpy_ops.hit_keyframes, 16, "Animator3D_Hit"),
        (bpy_ops.breathe_idle_keyframes, 72, "Animator3D_BreatheIdle"),
    ]
    for fn, fe, name in gens:
        fn(quad, frame_end=fe, action_name=name)

    for act_name in (
        "Animator3D_Walk",
        "Animator3D_Run",
        "Animator3D_Attack",
        "Animator3D_Death",
        "Animator3D_Hit",
        "Animator3D_BreatheIdle",
    ):
        stats = ik.enforce_joint_limits(quad, _action(act_name))
        assert stats is not None, f"{act_name}: passe não correu"
        total = stats["hinge_fixes"] + stats["flips"] + stats["ball_clamps"]
        assert total == 0, f"{act_name} violou limites: {stats}"


def test_gallop_run_asymmetric_feet(quad: str) -> None:
    """Galope: traseiras arrancam quase juntas, dianteiras a ~meio ciclo.

    Fase medida pelo PICO DE ELEVAÇÃO do pé (matriz avaliada) — o frame em que
    a pata está mais alta é o meio do swing, robusto a convenções de eixo.
    """
    import bpy
    from animator3d import bpy_ops

    bpy_ops.run_cycle_keyframes(quad, frame_end=37)  # 37 frames = 36 intervalos
    act = _action("Animator3D_Run")
    arm = bpy.data.objects[quad]
    ad = arm.animation_data
    saved = ad.action
    for t in ad.nla_tracks:
        t.mute = True
    ad.action = act
    scene = bpy.context.scene

    def lift_peak_phase(foot: str) -> float:
        """Fase φ do pico de elevação — dobrada por `cycles` (2): o mesmo φ
        repete em t e t+0.5, por isso mede-se em φ e não em t."""
        best_i, best_z = 0, -9.0
        for fi in range(37):
            scene.frame_set(fi + 1)
            bpy.context.view_layer.update()
            z = float(arm.pose.bones[foot].matrix.translation.z)
            if z > best_z:
                best_z, best_i = z, fi
        return ((best_i / 36.0) * 2.0) % 1.0  # cycles=2

    phases = {f: lift_peak_phase(f) for f in ("foot_r", "foot_l", "foot_r2", "foot_l2")}
    ad.action = saved
    for t in ad.nla_tracks:
        t.mute = False

    def cyc_delta(a: float, b: float) -> float:
        d = abs(a - b) % 1.0
        return min(d, 1.0 - d)

    # Par traseiro em sequência rápida (Δφ pequeno), par dianteiro idem.
    assert cyc_delta(phases["foot_r"], phases["foot_l"]) < 0.3
    assert cyc_delta(phases["foot_r2"], phases["foot_l2"]) < 0.3
    # Dianteiras ~meio ciclo depois das traseiras (suspensão entre os pares).
    assert cyc_delta(phases["foot_r2"], phases["foot_r"]) == pytest.approx(0.5, abs=0.25)
    # Pares distintos (não é trot diagonal, onde seriam anti-fase exata).
    assert cyc_delta(phases["foot_r"], phases["foot_l"]) > 0.0
