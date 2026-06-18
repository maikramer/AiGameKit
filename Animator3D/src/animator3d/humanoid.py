"""Clips humanoides baseados em key poses.

Substitui o baking denso de senoides por poucas poses-chave com easing bezier
do Blender. Rotações são definidas em EIXOS DO MUNDO (pitch/roll/yaw) e
convertidas exactamente para o espaço local do osso via quaternions
(``q_local = rest⁻¹ · q_world · rest``) — a aproximação antiga de "um índice
euler local por eixo do mundo" degenerava em ossos diagonais (braços em
A-pose torciam em vez de balançar).

Convenções (glTF importado no Blender: up=+Z, forward=−Y):
  pitch + : rotação sobre +X mundial — spine inclina à frente; perna/braço
            (a apontar para baixo) balançam para TRÁS; logo frente = pitch −.
  roll  + : rotação sobre +Y mundial. O sinal de adução depende do sinal de X
            da cadeia "r" em rest: rigs com Right em −X (caso do hero, facing
            −Z — ver ``_name_side`` em bpy_ops) aduzem com roll −, pelo que
            base_pose usa ``-s * mag``; rigs com Right em +X aduziriam com
            roll + (``+s * mag``).
  yaw   + : rotação sobre +Z mundial.
  up      : translação vertical (unidades do mundo) — só para a anca (bob).

Todos os valores em radianos. Poses são absolutas a partir do rest pose
(sem acumulação), por isso cada key descreve a pose completa do osso.
"""

from __future__ import annotations

import math
from typing import Any

Pose = dict[str, dict[str, float]]


def _bpy():
    import bpy

    return bpy


def _smoothstep01(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def merge(*poses: Pose) -> Pose:
    """Soma canais de várias poses (composição aditiva)."""
    out: Pose = {}
    for p in poses:
        for bone, ch in p.items():
            slot = out.setdefault(bone, {})
            for k, v in ch.items():
                slot[k] = slot.get(k, 0.0) + v
    return out


def scale_pose(pose: Pose, factor: float) -> Pose:
    return {b: {k: v * factor for k, v in ch.items()} for b, ch in pose.items()}


def mix(a: Pose, b: Pose, t: float) -> Pose:
    """Interpolação linear entre duas poses (para keys intermédias)."""
    return merge(scale_pose(a, 1.0 - t), scale_pose(b, t))


class HumanoidRig:
    """Resolve cadeias + escreve keys de pose exactas em eixos do mundo."""

    def __init__(self, armature_name: str, chains: dict[str, list[str]]):
        from mathutils import Quaternion

        bpy = _bpy()
        self.arm = bpy.data.objects[armature_name]
        self.chains = chains
        self._rest: dict[str, Any] = {}
        self._rest_inv: dict[str, Any] = {}
        self._last_q: dict[str, Any] = {}
        for b in self.arm.data.bones:
            q = b.matrix_local.to_quaternion()
            self._rest[b.name] = q
            self._rest_inv[b.name] = q.inverted()
        for pb in self.arm.pose.bones:
            pb.rotation_mode = "QUATERNION"
        self._quat_identity = Quaternion()

    # -- anatomia -----------------------------------------------------------

    @staticmethod
    def is_humanoid(chains: dict[str, list[str]]) -> bool:
        return all(len(chains.get(k, [])) >= 3 for k in ("leg_r", "leg_l", "arm_r", "arm_l"))

    def hips(self) -> str:
        return self.chains["body"][0]

    def spine(self) -> list[str]:
        return self.chains.get("spine", [])

    def neck(self) -> list[str]:
        return self.chains.get("neck", [])

    def arm_bones(self, side: str) -> dict[str, str]:
        """{'shoulder'?, 'upper', 'fore', 'hand'?} para a cadeia arm_<side>."""
        names = self.chains.get(f"arm_{side}", [])
        out: dict[str, str] = {}
        if len(names) >= 4:
            out["shoulder"], out["upper"], out["fore"] = names[0], names[1], names[2]
            out["hand"] = names[3]
        elif len(names) == 3:
            out["upper"], out["fore"], out["hand"] = names[0], names[1], names[2]
        return out

    def leg_bones(self, side: str) -> dict[str, str]:
        names = self.chains.get(f"leg_{side}", [])
        out: dict[str, str] = {}
        if len(names) >= 2:
            out["upper"], out["lower"] = names[0], names[1]
        if len(names) >= 3:
            out["foot"] = names[2]
        if len(names) >= 4:
            out["toe"] = names[3]
        return out

    # -- escrita de keys ----------------------------------------------------

    def key_bone(
        self,
        bone: str,
        frame: int,
        *,
        pitch: float = 0.0,
        roll: float = 0.0,
        yaw: float = 0.0,
        up: float = 0.0,
    ) -> None:
        from mathutils import Quaternion, Vector

        pb = self.arm.pose.bones.get(bone)
        rest_inv = self._rest_inv.get(bone)
        if pb is None or rest_inv is None:
            return
        q_world = (
            Quaternion((0.0, 0.0, 1.0), yaw)
            @ Quaternion((1.0, 0.0, 0.0), pitch)
            @ Quaternion((0.0, 1.0, 0.0), roll)
        )
        q_local = rest_inv @ q_world @ self._rest[bone]
        prev = self._last_q.get(bone)
        if prev is not None and prev.dot(q_local) < 0.0:
            q_local.negate()
        self._last_q[bone] = q_local.copy()
        pb.rotation_quaternion = q_local
        pb.keyframe_insert(data_path="rotation_quaternion", frame=frame)
        if bone == self.hips():
            loc = rest_inv @ Vector((0.0, 0.0, up))
            pb.location = loc
            pb.keyframe_insert(data_path="location", frame=frame)

    def key_pose(self, frame: int, pose: Pose) -> None:
        for bone, ch in pose.items():
            self.key_bone(bone, frame, **ch)

    def all_pose_bones(self, pose: Pose) -> list[str]:
        return list(pose.keys())

    @staticmethod
    def _action_fcurves(action: Any) -> list[Any]:
        """Curvas da action — compatível com Blender 4 (flat) e 5 (layered)."""
        fcurves = getattr(action, "fcurves", None)
        if fcurves is not None:
            return list(fcurves)
        out: list[Any] = []
        for layer in action.layers:
            for strip in layer.strips:
                for bag in strip.channelbags:
                    out.extend(bag.fcurves)
        return out

    def finish_action(self, action: Any, *, cyclic: bool) -> None:
        """Bezier auto-clamped em todas as curvas (easing natural)."""
        for fc in self._action_fcurves(action):
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"
                kp.handle_left_type = "AUTO_CLAMPED"
                kp.handle_right_type = "AUTO_CLAMPED"
            fc.update()


# ---------------------------------------------------------------------------
# Poses base
# ---------------------------------------------------------------------------


def base_pose(rig: HumanoidRig) -> Pose:
    """Postura relaxada partilhada por todos os clips: braços descidos da
    A-pose para junto do corpo, cotovelos ligeiramente flectidos. Evita o
    "pop" entre clips e o herói a respirar de braços abertos."""
    pose: Pose = {}
    for side, s in (("r", 1.0), ("l", -1.0)):
        ab = rig.arm_bones(side)
        if not ab:
            continue
        if "shoulder" in ab:
            pose[ab["shoulder"]] = {"roll": -s * 0.06}
        pose[ab["upper"]] = {"roll": -s * 0.66, "pitch": 0.06}
        pose[ab["fore"]] = {"roll": -s * 0.10, "pitch": -0.32}
        if "hand" in ab:
            pose[ab["hand"]] = {"pitch": -0.10}
    return pose


def _arm_swing(rig: HumanoidRig, side: str, fwd: float, *, elbow_extra: float = 0.0) -> Pose:
    """Balanço do braço: fwd>0 = à frente. Cotovelo dobra mais à frente."""
    ab = rig.arm_bones(side)
    if not ab:
        return {}
    pose: Pose = {ab["upper"]: {"pitch": -fwd}}
    bend = -0.12 * max(fwd, 0.0) - elbow_extra
    pose[ab["fore"]] = {"pitch": bend}
    return pose


def _leg_pose(
    rig: HumanoidRig,
    side: str,
    *,
    hip_fwd: float = 0.0,
    knee: float = 0.0,
    foot: float = 0.0,
) -> Pose:
    """hip_fwd>0 = coxa à frente; knee>0 = flexão; foot>0 = ponta para baixo."""
    lb = rig.leg_bones(side)
    if not lb:
        return {}
    pose: Pose = {lb["upper"]: {"pitch": -hip_fwd}}
    if "lower" in lb:
        pose[lb["lower"]] = {"pitch": knee}
    if "foot" in lb:
        pose[lb["foot"]] = {"pitch": foot}
    return pose


def _spine_pose(
    rig: HumanoidRig,
    *,
    lean: float = 0.0,
    yaw: float = 0.0,
    sway: float = 0.0,
    head_counter: bool = True,
) -> Pose:
    """lean>0 = tronco à frente; yaw distribui torção; cabeça compensa."""
    pose: Pose = {}
    spine = rig.spine()
    n = max(len(spine), 1)
    for i, bone in enumerate(spine):
        w = (i + 1) / n
        pose[bone] = {"pitch": lean * (0.5 + 0.5 * w) / n * 2.0, "yaw": yaw * w / n * 2.0, "roll": sway * w}
    for bone in rig.neck():
        pose[bone] = {
            "pitch": -lean * 0.35 if head_counter else lean * 0.2,
            "yaw": -yaw * 0.4 if head_counter else yaw * 0.3,
        }
    return pose


def _hips_pose(rig: HumanoidRig, *, pitch: float = 0.0, yaw: float = 0.0, roll: float = 0.0, up: float = 0.0) -> Pose:
    return {rig.hips(): {"pitch": pitch, "yaw": yaw, "roll": roll, "up": up}}


# ---------------------------------------------------------------------------
# Gait (walk / run)
# ---------------------------------------------------------------------------


def _gait_phase_pose(rig: HumanoidRig, phi: float, p: dict[str, float]) -> Pose:
    """Pose do ciclo de passada na fase ``phi`` ∈ [0,1).

    phi=0: contacto do calcanhar da perna "r". Pernas em anti-fase; braços
    contralaterais. Construída a partir das 4 poses-chave clássicas
    (contact / down / passing / up) com perfis contínuos.
    """
    two_pi = math.tau

    def leg_channels(ph: float) -> tuple[float, float, float]:
        """(hip_fwd, knee, foot) para uma perna na fase ph do seu ciclo."""
        hip = p["hip"] * math.cos(ph * two_pi)
        if ph < 0.1:  # contacto: calcanhar, joelho quase esticado
            w = ph / 0.1
            knee = p["knee_stance"] * w
            foot = -p["heel"] * (1.0 - w)
        elif ph < 0.3:  # apoio: amortece e o corpo passa por cima
            w = (ph - 0.1) / 0.2
            knee = p["knee_stance"] * (1.0 - 0.6 * w)
            foot = 0.0
        elif ph < 0.5:  # impulso: calcanhar levanta, ponta empurra
            w = (ph - 0.3) / 0.2
            knee = p["knee_stance"] * 0.4 + p["knee_push"] * w
            foot = p["push"] * w
        elif ph < 0.8:  # balanço: joelho dobra alto para passar o chão
            w = (ph - 0.5) / 0.3
            knee = p["knee_push"] + (p["knee_swing"] - p["knee_push"]) * math.sin(w * math.pi)
            foot = p["push"] * (1.0 - w) - p["clear"] * math.sin(w * math.pi)
        else:  # extensão para novo contacto
            w = (ph - 0.8) / 0.2
            knee = p["knee_swing"] * 0.35 * (1.0 - w) + p["knee_stance"] * 0.2 * w
            foot = -p["heel"] * w
        return hip, knee, foot

    hip_r, knee_r, foot_r = leg_channels(phi % 1.0)
    hip_l, knee_l, foot_l = leg_channels((phi + 0.5) % 1.0)

    arm_r = -p["arm"] * math.cos(phi * two_pi)  # contralateral à perna r
    arm_l = -arm_r

    pelvis_yaw = -p["pelvis_yaw"] * math.cos(phi * two_pi)
    pelvis_roll = p["pelvis_roll"] * math.sin(phi * two_pi)
    bob = -p["bob"] * math.cos(phi * 2.0 * two_pi)

    return merge(
        base_pose(rig),
        _leg_pose(rig, "r", hip_fwd=hip_r, knee=knee_r, foot=foot_r),
        _leg_pose(rig, "l", hip_fwd=hip_l, knee=knee_l, foot=foot_l),
        _arm_swing(rig, "r", arm_r, elbow_extra=p["elbow"]),
        _arm_swing(rig, "l", arm_l, elbow_extra=p["elbow"]),
        _hips_pose(rig, pitch=p["lean"] * 0.4, yaw=pelvis_yaw, roll=pelvis_roll, up=bob),
        _spine_pose(rig, lean=p["lean"], yaw=-pelvis_yaw * 1.4),
    )


_WALK_PARAMS: dict[str, float] = {
    "hip": 0.55,
    "knee_stance": 0.22,
    "knee_push": 0.40,
    "knee_swing": 1.20,
    "heel": 0.28,
    "push": 0.45,
    "clear": 0.14,
    "arm": 0.40,
    "elbow": 0.12,
    "pelvis_yaw": 0.11,
    "pelvis_roll": 0.06,
    "bob": 0.028,
    "lean": 0.06,
}

_RUN_PARAMS: dict[str, float] = {
    "hip": 0.75,
    "knee_stance": 0.40,
    "knee_push": 0.55,
    "knee_swing": 1.55,
    "heel": 0.10,
    "push": 0.55,
    "clear": 0.12,
    "arm": 0.70,
    "elbow": 0.85,
    "pelvis_yaw": 0.13,
    "pelvis_roll": 0.06,
    "bob": 0.045,
    "lean": 0.22,
}


def _keys_per_cycle() -> list[float]:
    return [i / 8.0 for i in range(8)]


def gait_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int, cycles: float, params: dict[str, float]) -> None:
    total = frame_end - frame_start
    n_cycles = max(int(round(cycles)), 1)
    cycle_frames = total / n_cycles
    for c in range(n_cycles):
        for phi in _keys_per_cycle():
            frame = frame_start + round((c + phi) * cycle_frames)
            rig.key_pose(frame, _gait_phase_pose(rig, phi, params))
    # fecha o loop com a pose do início
    rig.key_pose(frame_end, _gait_phase_pose(rig, 0.0, params))


# ---------------------------------------------------------------------------
# Idle
# ---------------------------------------------------------------------------


def idle_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int, cycles: float, breath_amp: float) -> None:
    total = frame_end - frame_start
    base = base_pose(rig)
    n_keys = max(int(round(cycles)) * 4, 4)
    for i in range(n_keys + 1):
        t = i / n_keys
        frame = frame_start + round(t * total)
        breath = math.sin(t * math.tau * cycles)
        shift = math.sin(t * math.tau)  # transferência de peso lenta, 1 ciclo
        pose = merge(
            base,
            _spine_pose(rig, lean=breath_amp * breath, sway=0.012 * shift),
            _hips_pose(rig, yaw=0.025 * shift, roll=-0.015 * shift, up=-0.004 * max(breath, 0.0)),
        )
        # ombros sobem ligeiramente na inspiração; braços acompanham
        for side, s in (("r", 1.0), ("l", -1.0)):
            ab = rig.arm_bones(side)
            if "shoulder" in ab:
                pose = merge(pose, {ab["shoulder"]: {"roll": s * 0.02 * breath}})
            if ab:
                pose = merge(pose, {ab["upper"]: {"pitch": 0.018 * breath}})
        # Weapon (right) arm holds a ready stance: forearm flexed up and hand
        # drawn slightly inward so a held sword rests in front of the belt
        # instead of hanging stiff against the thigh.
        abr = rig.arm_bones("r")
        if abr:
            pose = merge(pose, {
                abr["upper"]: {"pitch": 0.22, "roll": 0.14},
                abr["fore"]: {"pitch": -0.62, "yaw": 0.10},
            })
            if "hand" in abr:
                pose = merge(pose, {abr["hand"]: {"pitch": -0.12}})
        # Off (left) hand hangs relaxed and low against the side.
        abl = rig.arm_bones("l")
        if abl:
            pose = merge(pose, {
                abl["upper"]: {"roll": -0.20},
                abl["fore"]: {"pitch": 0.10},
            })
        rig.key_pose(frame, pose)


# ---------------------------------------------------------------------------
# Attack (golpe de braço — espada na mão da cadeia "r")
# ---------------------------------------------------------------------------


def attack_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int, strikes: int) -> None:
    total = frame_end - frame_start
    strikes = max(1, strikes)
    seg = total / strikes
    base = base_pose(rig)
    ab = rig.arm_bones("r")
    guard = rig.arm_bones("l")

    def windup() -> Pose:
        pose = merge(
            base,
            _hips_pose(rig, yaw=-0.12, up=-0.01),
            _spine_pose(rig, lean=-0.06, yaw=-0.30, head_counter=False),
            _leg_pose(rig, "r", hip_fwd=-0.10, knee=0.15),
            _leg_pose(rig, "l", hip_fwd=0.12, knee=0.10),
        )
        if ab:
            pose = merge(
                pose,
                {ab["upper"]: {"pitch": 0.85, "roll": -0.35}},
                {ab["fore"]: {"pitch": -1.25}},
            )
            if "hand" in ab:
                pose = merge(pose, {ab["hand"]: {"pitch": -0.25}})
        if guard:
            pose = merge(pose, {guard["upper"]: {"pitch": -0.35}})
        return pose

    def strike() -> Pose:
        pose = merge(
            base,
            _hips_pose(rig, yaw=0.18, pitch=0.10, up=-0.025),
            _spine_pose(rig, lean=0.32, yaw=0.35, head_counter=False),
            _leg_pose(rig, "r", hip_fwd=0.18, knee=0.30),
            _leg_pose(rig, "l", hip_fwd=-0.15, knee=0.20),
        )
        if ab:
            pose = merge(
                pose,
                {ab["upper"]: {"pitch": -1.15, "roll": -0.10}},
                {ab["fore"]: {"pitch": -0.20}},
            )
            if "hand" in ab:
                pose = merge(pose, {ab["hand"]: {"pitch": -0.35}})
        if guard:
            pose = merge(pose, {guard["upper"]: {"pitch": 0.40}})
        return pose

    for k in range(strikes):
        f0 = frame_start + round(k * seg)

        def at(t: float) -> int:
            return f0 + round(t * seg)

        rig.key_pose(at(0.0), base)
        rig.key_pose(at(0.30), windup())            # antecipação lenta
        rig.key_pose(at(0.48), strike())            # golpe rápido (snap)
        rig.key_pose(at(0.58), mix(strike(), base, 0.12))  # follow-through segura
        rig.key_pose(frame_start + round((k + 1) * seg) if k < strikes - 1 else frame_end, base)


# ---------------------------------------------------------------------------
# Tool / action clips (mine, chop, spear, axe, sword, gather)
# ---------------------------------------------------------------------------


def _both_arms(
    rig: HumanoidRig, *, upper: float, fore: float, roll: float = 0.0
) -> Pose:
    """Symmetric two-handed arm pose (e.g. gripping a pick/axe/spear haft).
    `roll` spreads the arms outward (mirrored). pitch- swings forward/down."""
    pose: Pose = {}
    for side, s in (("r", 1.0), ("l", -1.0)):
        ab = rig.arm_bones(side)
        if not ab:
            continue
        pose[ab["upper"]] = {"pitch": upper, "roll": s * roll}
        pose[ab["fore"]] = {"pitch": fore}
    return pose


def _one_arm(
    rig: HumanoidRig, side: str, *, upper: float, fore: float, roll: float = 0.0
) -> Pose:
    ab = rig.arm_bones(side)
    if not ab:
        return {}
    pose: Pose = {ab["upper"]: {"pitch": upper, "roll": roll}, ab["fore"]: {"pitch": fore}}
    return pose


def mine_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Pickaxe: two-handed overhead raise then a hard slam down into the ground."""
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    raise_ = merge(
        base,
        _both_arms(rig, upper=1.5, fore=-0.55),
        _spine_pose(rig, lean=-0.18),
        _hips_pose(rig, up=0.02),
    )
    slam = merge(
        base,
        _both_arms(rig, upper=-1.35, fore=-0.15),
        _spine_pose(rig, lean=0.55),
        _hips_pose(rig, pitch=0.22, up=-0.04),
        _leg_pose(rig, "r", knee=0.35),
        _leg_pose(rig, "l", knee=0.35),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.35), raise_)
    rig.key_pose(at(0.52), slam)
    rig.key_pose(at(0.66), mix(slam, base, 0.2))
    rig.key_pose(frame_end, base)


def chop_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Axe felling a tree: two-handed diagonal overhead chop across the body."""
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    raise_ = merge(
        base,
        _both_arms(rig, upper=1.25, fore=-0.5, roll=0.4),
        _spine_pose(rig, lean=-0.1, yaw=-0.42),
    )
    strike = merge(
        base,
        _both_arms(rig, upper=-1.0, fore=-0.2, roll=-0.2),
        _spine_pose(rig, lean=0.35, yaw=0.45),
        _hips_pose(rig, yaw=0.15),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.35), raise_)
    rig.key_pose(at(0.52), strike)
    rig.key_pose(at(0.64), mix(strike, base, 0.2))
    rig.key_pose(frame_end, base)


def spear_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Spear: cock both hands back, then a fast straight thrust + retract."""
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    cock = merge(
        base,
        _both_arms(rig, upper=0.4, fore=-1.3),
        _spine_pose(rig, lean=-0.08, yaw=-0.1),
        _leg_pose(rig, "l", hip_fwd=0.12, knee=0.1),
        _leg_pose(rig, "r", hip_fwd=-0.1, knee=0.15),
    )
    thrust = merge(
        base,
        _both_arms(rig, upper=-0.95, fore=-0.1),
        _spine_pose(rig, lean=0.28),
        _leg_pose(rig, "r", hip_fwd=0.25, knee=0.3),
        _leg_pose(rig, "l", hip_fwd=-0.12),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.32), cock)
    rig.key_pose(at(0.46), thrust)
    rig.key_pose(at(0.58), mix(thrust, base, 0.15))
    rig.key_pose(frame_end, base)


def axe_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """One-handed axe: a heavy, wide horizontal swing (slower wind-up)."""
    total = frame_end - frame_start
    base = base_pose(rig)
    guard = rig.arm_bones("l")

    def at(t: float) -> int:
        return frame_start + round(t * total)

    windup = merge(
        base,
        _one_arm(rig, "r", upper=0.7, fore=-0.6, roll=-0.7),
        _spine_pose(rig, yaw=-0.35),
    )
    strike = merge(
        base,
        _one_arm(rig, "r", upper=-0.9, fore=-0.15, roll=0.25),
        _spine_pose(rig, lean=0.3, yaw=0.42),
    )
    if guard:
        windup = merge(windup, {guard["upper"]: {"pitch": -0.3}})
        strike = merge(strike, {guard["upper"]: {"pitch": 0.4}})
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.40), windup)
    rig.key_pose(at(0.56), strike)
    rig.key_pose(at(0.68), mix(strike, base, 0.15))
    rig.key_pose(frame_end, base)


def sword_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """One-handed sword: a crisp overhead diagonal slash."""
    total = frame_end - frame_start
    base = base_pose(rig)
    guard = rig.arm_bones("l")

    def at(t: float) -> int:
        return frame_start + round(t * total)

    windup = merge(
        base,
        _one_arm(rig, "r", upper=0.95, fore=-0.9, roll=-0.6),
        _spine_pose(rig, lean=-0.05, yaw=-0.5),
    )
    strike = merge(
        base,
        _one_arm(rig, "r", upper=-1.2, fore=-0.2, roll=0.55),
        _spine_pose(rig, lean=0.3, yaw=0.5),
    )
    if guard:
        strike = merge(strike, {guard["upper"]: {"pitch": 0.3}})
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.30), windup)
    rig.key_pose(at(0.46), strike)
    rig.key_pose(at(0.58), mix(strike, base, 0.12))
    rig.key_pose(frame_end, base)


def gather_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Bare-hand gather: crouch and reach down to pick something off the ground."""
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    reach = merge(
        base,
        _hips_pose(rig, pitch=0.45, up=-0.16),
        _spine_pose(rig, lean=0.55),
        _leg_pose(rig, "r", knee=0.55, hip_fwd=0.1),
        _leg_pose(rig, "l", knee=0.55, hip_fwd=0.1),
        _one_arm(rig, "r", upper=-1.0, fore=-0.35),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.40), reach)
    rig.key_pose(at(0.62), reach)
    rig.key_pose(frame_end, base)


# ---------------------------------------------------------------------------
# Jump / Fall
# ---------------------------------------------------------------------------


def jump_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    def crouch() -> Pose:
        return merge(
            base,
            _hips_pose(rig, pitch=0.15, up=-0.10),
            _spine_pose(rig, lean=0.30),
            _leg_pose(rig, "r", hip_fwd=0.55, knee=1.05, foot=-0.25),
            _leg_pose(rig, "l", hip_fwd=0.55, knee=1.05, foot=-0.25),
            _arm_swing(rig, "r", -0.55),
            _arm_swing(rig, "l", -0.55),
        )

    def launch() -> Pose:
        return merge(
            base,
            _hips_pose(rig, pitch=-0.05, up=0.05),
            _spine_pose(rig, lean=-0.06),
            _leg_pose(rig, "r", hip_fwd=0.05, knee=0.08, foot=0.55),
            _leg_pose(rig, "l", hip_fwd=0.05, knee=0.08, foot=0.55),
            _arm_swing(rig, "r", 1.05),
            _arm_swing(rig, "l", 1.05),
        )

    def tuck() -> Pose:
        return merge(
            base,
            _hips_pose(rig, pitch=0.08, up=0.02),
            _spine_pose(rig, lean=0.10),
            _leg_pose(rig, "r", hip_fwd=0.50, knee=0.70),
            _leg_pose(rig, "l", hip_fwd=0.42, knee=0.62),
            _arm_swing(rig, "r", 0.45),
            _arm_swing(rig, "l", 0.45),
        )

    def descend() -> Pose:
        pose = merge(
            base,
            _hips_pose(rig, pitch=0.04),
            _spine_pose(rig, lean=0.06),
            _leg_pose(rig, "r", hip_fwd=0.22, knee=0.28, foot=0.15),
            _leg_pose(rig, "l", hip_fwd=0.18, knee=0.24, foot=0.15),
        )
        for side, s in (("r", 1.0), ("l", -1.0)):
            a = rig.arm_bones(side)
            if a:
                pose = merge(pose, {a["upper"]: {"roll": s * 0.35, "pitch": -0.15}})
        return pose

    def land() -> Pose:
        return merge(
            base,
            _hips_pose(rig, pitch=0.12, up=-0.07),
            _spine_pose(rig, lean=0.26),
            _leg_pose(rig, "r", hip_fwd=0.45, knee=0.85, foot=-0.10),
            _leg_pose(rig, "l", hip_fwd=0.45, knee=0.85, foot=-0.10),
            _arm_swing(rig, "r", 0.30),
            _arm_swing(rig, "l", 0.30),
        )

    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.10), mix(base, crouch(), 0.3))  # antecipação
    rig.key_pose(at(0.24), crouch())
    rig.key_pose(at(0.36), launch())                   # extensão rápida
    rig.key_pose(at(0.52), tuck())
    rig.key_pose(at(0.72), descend())
    rig.key_pose(at(0.86), land())
    rig.key_pose(frame_end, base)


def fall_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    total = frame_end - frame_start
    base = base_pose(rig)

    def fall_pose(sway: float) -> Pose:
        pose = merge(
            base,
            _hips_pose(rig, pitch=-0.08, roll=sway * 0.4),
            _spine_pose(rig, lean=-0.10, sway=sway),
            _leg_pose(rig, "r", hip_fwd=0.18 + sway * 0.3, knee=0.40),
            _leg_pose(rig, "l", hip_fwd=-0.10 - sway * 0.3, knee=0.55),
        )
        for side, s in (("r", 1.0), ("l", -1.0)):
            a = rig.arm_bones(side)
            if a:
                pose = merge(
                    pose,
                    {a["upper"]: {"roll": s * 0.55, "pitch": -0.25 + s * sway * 0.2}},
                    {a["fore"]: {"pitch": -0.20}},
                )
        return pose

    n = 4
    for i in range(n + 1):
        t = i / n
        rig.key_pose(frame_start + round(t * total), fall_pose(0.10 * math.sin(t * math.tau)))


# ---------------------------------------------------------------------------
# Turn in place
# ---------------------------------------------------------------------------


def turn_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int, direction: float, turn_amp: float) -> None:
    total = frame_end - frame_start
    base = base_pose(rig)
    d = 1.0 if direction >= 0 else -1.0
    # direction +1 = "esquerda" (+yaw roda para +X)
    lead = "r" if d > 0 else "l"
    trail = "l" if d > 0 else "r"

    def turn_pose(t: float) -> Pose:
        tw = d * turn_amp * math.sin(t * math.tau)
        step = max(0.0, math.sin(t * math.tau))
        return merge(
            base,
            _hips_pose(rig, yaw=tw * 0.55, up=-0.012 * step),
            _spine_pose(rig, lean=0.03, yaw=tw * 0.8, head_counter=False),
            {bone: {"yaw": tw * 0.35} for bone in rig.neck()},
            _leg_pose(rig, lead, hip_fwd=0.18 * step, knee=0.45 * step),
            _leg_pose(rig, trail, hip_fwd=-0.06 * step, knee=0.08 * step),
            _arm_swing(rig, "r", -d * 0.14 * math.sin(t * math.tau)),
            _arm_swing(rig, "l", d * 0.14 * math.sin(t * math.tau)),
        )

    for i in range(7):
        t = i / 6.0
        rig.key_pose(frame_start + round(t * total), turn_pose(t))


# ---------------------------------------------------------------------------
# Entrada única chamada por bpy_ops
# ---------------------------------------------------------------------------

_CLIPS = {
  "idle", "walk", "run", "attack", "jump", "fall", "turn",
  "mine", "chop", "spear", "axe", "sword", "gather",
}


def try_humanoid_clip(
    kind: str,
    armature_name: str,
    chains: dict[str, list[str]],
    *,
    frame_start: int,
    frame_end: int,
    action_name: str,
    **params: float,
) -> bool:
    """Gera o clip ``kind`` com o motor de key poses se o rig for humanoide.

    Devolve False (sem tocar na cena) para rigs não-humanoides — o chamador
    mantém o caminho legado (dragões, criaturas, etc.).
    """
    if kind not in _CLIPS or not HumanoidRig.is_humanoid(chains):
        return False

    from . import bpy_ops

    bpy = _bpy()
    bpy_ops.normalize_armature_before_animation(armature_name)
    bpy_ops.stash_if_needed_for_action(armature_name, action_name)
    action = bpy_ops.ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end

    rig = HumanoidRig(armature_name, chains)
    cyclic = kind in {"idle", "walk", "run", "fall", "turn"}

    if kind == "idle":
        idle_clip(
            rig,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=float(params.get("cycles", 2.0)),
            breath_amp=float(params.get("breath_amp", 0.035)),
        )
    elif kind == "walk":
        gait_clip(
            rig,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=float(params.get("cycles", 2.0)),
            params=_WALK_PARAMS,
        )
    elif kind == "run":
        gait_clip(
            rig,
            frame_start=frame_start,
            frame_end=frame_end,
            cycles=float(params.get("cycles", 2.0)),
            params=_RUN_PARAMS,
        )
    elif kind == "attack":
        attack_clip(
            rig,
            frame_start=frame_start,
            frame_end=frame_end,
            strikes=int(params.get("strikes", 1)),
        )
    elif kind == "jump":
        jump_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "fall":
        fall_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "turn":
        turn_clip(
            rig,
            frame_start=frame_start,
            frame_end=frame_end,
            direction=float(params.get("direction", 1.0)),
            turn_amp=float(params.get("turn_amp", 0.45)),
        )
    elif kind == "mine":
        mine_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "chop":
        chop_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "spear":
        spear_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "axe":
        axe_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "sword":
        sword_clip(rig, frame_start=frame_start, frame_end=frame_end)
    elif kind == "gather":
        gather_clip(rig, frame_start=frame_start, frame_end=frame_end)

    rig.finish_action(action, cyclic=cyclic)
    bpy_ops.finalize_current_action_to_nla(armature_name)
    return True
