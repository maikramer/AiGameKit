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
            Quaternion((0.0, 0.0, 1.0), yaw) @ Quaternion((1.0, 0.0, 0.0), pitch) @ Quaternion((0.0, 1.0, 0.0), roll)
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

    def finish_action(self, action: Any, *, cyclic: bool, impact_frames: set[int] | None = None) -> None:
        """Bezier em todas as curvas. Impact keys (strike/launch/land) ficam com
        handles livres (``AUTO``) para permitir overshoot/follow-through natural;
        restantes keys usam ``AUTO_CLAMPED`` (evita oscilação indesejada).
        """
        for fc in self._action_fcurves(action):
            for kp in fc.keyframe_points:
                kp.interpolation = "BEZIER"
                # Unclamp nos frames de impacto → BEZIER pode overshoot.
                if impact_frames and int(round(kp.co.x)) in impact_frames:
                    kp.handle_left_type = "AUTO"
                    kp.handle_right_type = "AUTO"
                else:
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

    Usa ``shaped_cos`` (cosseno que desacelera nos picos) para weight shift
    natural, e quebra a simetria L/R (±3%) para movimento menos robótico.
    """
    from ._motion import shaped_cos

    two_pi = math.tau

    def leg_channels(ph: float, hip_amp_mult: float = 1.0) -> tuple[float, float, float]:
        """(hip_fwd, knee, foot) para uma perna na fase ph do seu ciclo."""
        hip = p["hip"] * hip_amp_mult * shaped_cos(ph)
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

    # Assimetria L/R: perna esquerda com 3% menos amplitude (natural).
    hip_r, knee_r, foot_r = leg_channels(phi % 1.0, hip_amp_mult=1.0)
    hip_l, knee_l, foot_l = leg_channels((phi + 0.5) % 1.0, hip_amp_mult=0.97)

    # Braços: phase lag subtil (+0.03) — seguem as pernas (overlapping action).
    arm_r = -p["arm"] * shaped_cos((phi + 0.03) % 1.0)
    arm_l = -arm_r

    pelvis_yaw = -p["pelvis_yaw"] * shaped_cos(phi)
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
    """Idle com respiração orgânica: sin³ (segura no pico), micro-noise na
    spine/neck (corpo nunca perfeitamente imóvel) e assimetria L/R."""
    from ._motion import fbm, shaped_sin

    total = frame_end - frame_start
    base = base_pose(rig)
    n_keys = max(int(round(cycles)) * 4, 4)
    for i in range(n_keys + 1):
        t = i / n_keys
        frame = frame_start + round(t * total)
        # sin³ em vez de sin puro: respira "segura" no topo da inspiração.
        breath = shaped_sin(t, freq=cycles)
        shift = shaped_sin(t, freq=1.0)  # transferência de peso lenta, 1 ciclo
        # Micro-noise orgânico (±0.004 rad) — o corpo nunca está perfeitamente imóvel.
        spine_noise = 0.004 * fbm(t * cycles, seed=1)
        hip_noise = 0.003 * fbm(t * cycles, seed=2)
        pose = merge(
            base,
            _spine_pose(rig, lean=breath_amp * breath + spine_noise, sway=0.012 * shift),
            _hips_pose(rig, yaw=0.025 * shift + hip_noise, roll=-0.015 * shift, up=-0.004 * max(breath, 0.0)),
        )
        # ombros sobem ligeiramente na inspiração; braços acompanham.
        # Assimetria L/R: ombro esquerdo com micro-noise independente.
        for side, s in (("r", 1.0), ("l", -1.0)):
            ab = rig.arm_bones(side)
            shoulder_noise = 0.003 * fbm(t * cycles, seed=3 if side == "r" else 4)
            if "shoulder" in ab:
                pose = merge(pose, {ab["shoulder"]: {"roll": s * (0.02 * breath + shoulder_noise)}})
            if ab:
                pose = merge(pose, {ab["upper"]: {"pitch": 0.018 * breath}})
        # Idle neutro: braços relaxados simétricos ao longo do corpo, sem pose
        # de guarda de arma. Antes o braço direito era forçado em stance de
        # espada (pitch=0.22, roll=0.14, forearm flexed -0.62), o que ficava
        # errado para um humanoid sem arma. As poses de combate vivem agora
        # só nos clips de attack/sword/chop/axe (que têm a sua própria guard).
        for side, s in (("r", 1.0), ("l", -1.0)):
            ab = rig.arm_bones(side)
            if ab:
                # Rotação subtil para os braços ficarem ligeiramente afastados
                # do corpo (não colados à coxa) — adução natural de repouso.
                pose = merge(pose, {ab["upper"]: {"roll": s * 0.08}})
                if "fore" in ab:
                    pose = merge(pose, {ab["fore"]: {"pitch": 0.05}})
        rig.key_pose(frame, pose)


# ---------------------------------------------------------------------------
# Attack (golpe de braço — espada na mão da cadeia "r")
# ---------------------------------------------------------------------------


def attack_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int, strikes: int) -> None:
    """Golpe amplo à altura do peito/ombro (maos-livres/arma leve).

    Biomecânica: windup ergue o braço para trás/cima até perto do ombro (roll
    externo forte + pitch elevado), strike é um arco largo que atravessa a
    partir dessa altura até à frente do peito (nunca cai para a cintura),
    follow-through onde o braço continua através do alvo e estabiliza.
    Tronco roda (yaw) para gerar potência; pernas acompanham o peso.
    """
    from ._motion import fbm

    total = frame_end - frame_start
    strikes = max(1, strikes)
    seg = total / strikes
    base = base_pose(rig)
    ab = rig.arm_bones("r")
    guard = rig.arm_bones("l")

    def windup(k: int) -> Pose:
        # Micro-variação por strike (±3%) para ataques não-idênticos.
        n = 1.0 + 0.03 * fbm(float(k), seed=1)
        pose = merge(
            base,
            _hips_pose(rig, yaw=-0.14, up=-0.01),
            _spine_pose(rig, lean=-0.05, yaw=-0.35 * n, head_counter=False),
            _leg_pose(rig, "r", hip_fwd=-0.12, knee=0.18),
            _leg_pose(rig, "l", hip_fwd=0.14, knee=0.12),
        )
        if ab:
            # Braço recua para trás/cima até perto do ombro: roll externo forte
            # + pitch elevado (não fica preso à cintura).
            pose = merge(
                pose,
                {ab["upper"]: {"pitch": 0.80, "roll": -0.90 * n}},
                {ab["fore"]: {"pitch": -1.10}},
            )
            if "hand" in ab:
                pose = merge(pose, {ab["hand"]: {"pitch": -0.20}})
        if guard:
            pose = merge(pose, {guard["upper"]: {"pitch": -0.35, "roll": 0.25}})
        return pose

    def strike(k: int) -> Pose:
        n = 1.0 + 0.03 * fbm(float(k), seed=2)
        # Strike em arco largo até à altura do peito: braço estendido para a
        # frente/cima, cotovelo quase esticado — nunca cai para a cintura.
        pose = merge(
            base,
            _hips_pose(rig, yaw=0.20 * n, pitch=0.08, up=-0.02),
            _spine_pose(rig, lean=0.18, yaw=0.40 * n, head_counter=False),
            _leg_pose(rig, "r", hip_fwd=0.22, knee=0.28),
            _leg_pose(rig, "l", hip_fwd=-0.16, knee=0.22),
        )
        if ab:
            pose = merge(
                pose,
                {ab["upper"]: {"pitch": -0.30, "roll": 0.80 * n}},
                {ab["fore"]: {"pitch": -0.15}},
            )
            if "hand" in ab:
                pose = merge(pose, {ab["hand"]: {"pitch": -0.15}})
        if guard:
            pose = merge(pose, {guard["upper"]: {"pitch": 0.40, "roll": -0.30}})
        return pose

    for k in range(strikes):
        f0 = frame_start + round(k * seg)

        def at(t: float) -> int:
            return f0 + round(t * seg)

        rig.key_pose(at(0.0), base)
        rig.key_pose(at(0.30), windup(k))
        rig.key_pose(at(0.46), strike(k))  # snap rápido
        # Overshoot: braço continua ~10% além do strike (follow-through).
        rig.key_pose(at(0.52), mix(strike(k), base, -0.10))
        rig.key_pose(at(0.64), mix(strike(k), base, 0.15))  # settle
        rig.key_pose(frame_start + round((k + 1) * seg) if k < strikes - 1 else frame_end, base)


# ---------------------------------------------------------------------------
# Tool / action clips (mine, chop, spear, axe, sword, gather)
# ---------------------------------------------------------------------------


def _both_arms(rig: HumanoidRig, *, upper: float, fore: float, roll: float = 0.0) -> Pose:
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


def _one_arm(rig: HumanoidRig, side: str, *, upper: float, fore: float, roll: float = 0.0) -> Pose:
    ab = rig.arm_bones(side)
    if not ab:
        return {}
    pose: Pose = {ab["upper"]: {"pitch": upper, "roll": roll}, ab["fore"]: {"pitch": fore}}
    return pose


def mine_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Picaretada: golpe por cima do ombro com as duas mãos.

    Biomecânica: windup ergue a picareta atrás/cima da cabeça (braços para
    cima e para trás, tronco inclina-se ligeiramente para trás, Joelhos
    estendem para carregar); strike é um slam descendente potente (tronco
    inclina-se para a frente, braços estendem para baixo); follow-through
    com joelhos que dobram para amorteceem o impacto.
    """
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Windup: picareta erguida atrás/cima da cabeça, tronco recua, joelhos estendem.
    raise_ = merge(
        base,
        _both_arms(rig, upper=1.85, fore=-0.40),  # braços bem para cima/trás
        _spine_pose(rig, lean=-0.22),
        _hips_pose(rig, pitch=-0.12, up=0.03),
        _leg_pose(rig, "r", knee=0.15),  # pernas quase esticadas (carregar)
        _leg_pose(rig, "l", knee=0.12),
    )
    # Strike: slam descendente — braços estendem para baixo, tronco vai à frente.
    slam = merge(
        base,
        _both_arms(rig, upper=-0.45, fore=-0.05),  # braços estendidos para baixo
        _spine_pose(rig, lean=0.62),  # tronco inclina-se muito para a frente
        _hips_pose(rig, pitch=0.28, up=-0.05),
        _leg_pose(rig, "r", knee=0.45),  # joelhos dobram para amorteceem
        _leg_pose(rig, "l", knee=0.45),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.38), raise_)  # windup longo (carga)
    rig.key_pose(at(0.52), slam)  # snap rápido
    rig.key_pose(at(0.60), mix(slam, base, -0.08))  # overshoot
    rig.key_pose(at(0.72), mix(slam, base, 0.25))  # recover com joelhos
    rig.key_pose(frame_end, base)


def chop_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Lenhador: golpe diagonal overhead com machado de duas mãos.

    Biomecânica: windup ergue o machado sobre o ombro direito (braços para
    cima/cima-direita, tronco roda para a direita); strike corta diagonalmente
    para baixo-esquerda (tronco roda potente para a esquerda, braços estendem).
    """
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Windup: machado sobre ombro direito — braços para cima/direita, roda direita.
    raise_ = merge(
        base,
        _both_arms(rig, upper=1.45, fore=-0.55, roll=0.55),  # cima + afasta
        _spine_pose(rig, lean=-0.08, yaw=-0.50),  # roda direita
        _hips_pose(rig, yaw=-0.18),
        _leg_pose(rig, "r", hip_fwd=0.10, knee=0.15),
    )
    # Strike: corta diagonal para baixo-esquerda — roda potente esquerda.
    strike = merge(
        base,
        _both_arms(rig, upper=-0.35, fore=-0.10, roll=-0.35),  # baixo + cruzado
        _spine_pose(rig, lean=0.42, yaw=0.52),  # roda esquerda forte
        _hips_pose(rig, yaw=0.22, pitch=0.15),
        _leg_pose(rig, "l", hip_fwd=-0.14, knee=0.30),
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.36), raise_)
    rig.key_pose(at(0.52), strike)  # snap
    rig.key_pose(at(0.60), mix(strike, base, -0.08))  # overshoot
    rig.key_pose(at(0.70), mix(strike, base, 0.20))  # recover
    rig.key_pose(frame_end, base)


def spear_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Lança: thrust reto com as duas mãos.

    Biomecânica: guarda com a lança segura ao peito (ambas as mãos à frente);
    recuar (cock) recolhe a lança junto ao corpo, tronco roda para trás, peso
    na perna de trás; thrust estende ambos os braços em linha recta para a
    frente, perna de trás empurra, tronco avança; retrair rápido.
    """
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Guarda: lança ao peito, ambas as mãos à frente, postura de combate.
    guard = merge(
        base,
        _both_arms(rig, upper=0.15, fore=-0.85),  # mãos ao peito
        _spine_pose(rig, lean=0.05, yaw=-0.10),
        _leg_pose(rig, "r", hip_fwd=0.08, knee=0.20),  # guarda
        _leg_pose(rig, "l", hip_fwd=-0.08, knee=0.18),
    )
    # Cock: recuar lança — braços encolhem, tronco roda para trás.
    cock = merge(
        base,
        _both_arms(rig, upper=0.55, fore=-1.55),  # lança recolhida
        _spine_pose(rig, lean=-0.05, yaw=-0.25),
        _hips_pose(rig, yaw=-0.12),
        _leg_pose(rig, "l", hip_fwd=0.14, knee=0.12),  # peso trás
        _leg_pose(rig, "r", hip_fwd=-0.10, knee=0.22),
    )
    # Thrust: estende braços em linha recta, perna de trás empurra.
    thrust = merge(
        base,
        _both_arms(rig, upper=-1.10, fore=-0.05),  # braços estendidos frente
        _spine_pose(rig, lean=0.25, yaw=0.15),
        _hips_pose(rig, yaw=0.18, pitch=0.10),
        _leg_pose(rig, "r", hip_fwd=0.30, knee=0.10),  # perna frente estica
        _leg_pose(rig, "l", hip_fwd=-0.18, knee=0.35),  # perna trás empurra
    )
    rig.key_pose(at(0.0), guard)
    rig.key_pose(at(0.30), cock)  # recuar
    rig.key_pose(at(0.46), thrust)  # thrust rápido
    rig.key_pose(at(0.54), mix(thrust, base, -0.06))  # overshoot subtil
    rig.key_pose(at(0.66), mix(thrust, base, 0.20))  # retrair
    rig.key_pose(frame_end, guard)


def axe_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Machadada lateral à altura do peito (uma mão) — não overhead.

    Biomecânica: golpe de machado de uma mão é um swing LATERAL (arco
    horizontal), não um corte diagonal overhead — isso fica para o
    ``chop_clip`` de duas mãos. Windup recua o machado para trás/lateral e
    ergue-o até à altura do peito (roll externo forte + pitch elevado — não
    fica preso à cintura), tronco roda para trás para gerar torque; strike é
    um swing horizontal largo que atravessa a essa mesma altura de trás para
    a frente (cotovelo estende), tronco roda potente para a frente.
    """
    total = frame_end - frame_start
    base = base_pose(rig)
    guard = rig.arm_bones("l")

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Windup: machado recuado para trás/lateral, cotovelo alto (cocked).
    windup = merge(
        base,
        _one_arm(rig, "r", upper=0.45, fore=-0.60, roll=-1.15),  # recua lateral + eleva cotovelo
        _spine_pose(rig, lean=-0.05, yaw=-0.48),  # roda para trás
        _hips_pose(rig, yaw=-0.20),
        _leg_pose(rig, "r", hip_fwd=-0.10, knee=0.20),
    )
    # Strike: swing horizontal largo à altura do peito — braço sobe e estende
    # para a frente (pitch negativo é o que levanta o braço; roll dá o alcance
    # lateral), nunca cai de volta à cintura como no golpe antigo.
    strike = merge(
        base,
        _one_arm(rig, "r", upper=-0.55, fore=-0.20, roll=1.05),  # sobe + estende frente
        _spine_pose(rig, lean=0.22, yaw=0.52),  # roda potente para a frente
        _hips_pose(rig, yaw=0.26, pitch=0.08),
        _leg_pose(rig, "r", hip_fwd=0.20, knee=0.30),
        _leg_pose(rig, "l", hip_fwd=-0.15, knee=0.22),
    )
    if guard:
        windup = merge(windup, {guard["upper"]: {"pitch": -0.32, "roll": 0.22}})
        strike = merge(strike, {guard["upper"]: {"pitch": 0.38, "roll": -0.26}})
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.40), windup)  # windup longo (machado é pesado)
    rig.key_pose(at(0.56), strike)  # swing
    rig.key_pose(at(0.64), mix(strike, base, -0.12))  # overshoot (inércia)
    rig.key_pose(at(0.74), mix(strike, base, 0.18))  # recover
    rig.key_pose(frame_end, base)


def sword_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Espada: slash diagonal cortante (uma mão).

    Biomecânica: windup recua a espada sobre o ombro (cima/trás), tronco roda
    para trás; strike é um slash descendente-diagonal rápido (corte cortante,
    não impacto — a lâmina atravessa), tronco roda para a frente; follow-through
    onde a espada continua através do alvo até à cintura oposta.
    """
    total = frame_end - frame_start
    base = base_pose(rig)
    guard = rig.arm_bones("l")

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Windup: espada bem alta sobre o ombro — cima/trás, roda direita. (pitch
    # positivo grande é o que ergue o braço sobre o ombro; roll fica perto do
    # neutro para não cancelar a elevação — mesma lógica do ``chop_clip``.)
    windup = merge(
        base,
        _one_arm(rig, "r", upper=1.45, fore=-0.55, roll=0.55),  # bem alto sobre o ombro
        _spine_pose(rig, lean=-0.08, yaw=-0.55),  # roda direita
        _hips_pose(rig, yaw=-0.18),
        _leg_pose(rig, "r", hip_fwd=-0.12, knee=0.18),
    )
    # Strike: slash descendente-diagonal amplo — pitch negativo levanta e projecta
    # o braço para a frente enquanto desce/cruza, lâmina atravessa até à anca
    # esquerda sem o braço cair de volta à cintura.
    strike = merge(
        base,
        _one_arm(rig, "r", upper=-0.45, fore=-0.10, roll=-0.35),  # desce/cruza, mantém altura
        _spine_pose(rig, lean=0.28, yaw=0.48),  # roda esquerda forte
        _hips_pose(rig, yaw=0.22, pitch=0.12),
        _leg_pose(rig, "r", hip_fwd=0.24, knee=0.26),
        _leg_pose(rig, "l", hip_fwd=-0.16, knee=0.24),
    )
    if guard:
        windup = merge(windup, {guard["upper"]: {"pitch": -0.30, "roll": 0.20}})
        strike = merge(strike, {guard["upper"]: {"pitch": 0.32, "roll": -0.28}})
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.28), windup)
    rig.key_pose(at(0.42), strike)  # slash rápido (snap)
    rig.key_pose(at(0.50), mix(strike, base, -0.12))  # overshoot (inércia do corte)
    rig.key_pose(at(0.62), mix(strike, base, 0.18))  # recover
    rig.key_pose(frame_end, base)


def gather_clip(rig: HumanoidRig, *, frame_start: int, frame_end: int) -> None:
    """Apanhar do chão: agachar, alcançar com ambas as mãos, levantar.

    Biomecânica: agachar com as duas pernas, tronco inclina-se para a frente;
    braços estendem para baixo para apanhar; recuperar com as pernas a estender
    e o tronco a endireitar, mãos sobem ao peito.
    """
    total = frame_end - frame_start
    base = base_pose(rig)

    def at(t: float) -> int:
        return frame_start + round(t * total)

    # Crouch + reach: agachar, inclinar à frente, alcançar com ambas as mãos.
    reach = merge(
        base,
        _hips_pose(rig, pitch=0.50, up=-0.18),
        _spine_pose(rig, lean=0.60),
        _leg_pose(rig, "r", knee=0.60, hip_fwd=0.12),
        _leg_pose(rig, "l", knee=0.60, hip_fwd=0.12),
        _both_arms(rig, upper=-0.85, fore=-0.30),  # ambas as mãos para baixo
    )
    # Lift: levantar — pernas estendem, tronco endireita, mãos ao peito.
    lift = merge(
        base,
        _hips_pose(rig, up=0.01),
        _spine_pose(rig, lean=0.05),
        _leg_pose(rig, "r", knee=0.15),
        _leg_pose(rig, "l", knee=0.15),
        _both_arms(rig, upper=0.25, fore=-0.90),  # mãos sobem ao peito
    )
    rig.key_pose(at(0.0), base)
    rig.key_pose(at(0.35), reach)
    rig.key_pose(at(0.55), reach)  # segurar a apanhar
    rig.key_pose(at(0.75), lift)  # levantar
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
    rig.key_pose(at(0.36), launch())  # extensão rápida
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
    "idle",
    "walk",
    "run",
    "attack",
    "jump",
    "fall",
    "turn",
    "mine",
    "chop",
    "spear",
    "axe",
    "sword",
    "gather",
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

    # Frames de impacto (strike/launch/land) para unclamp → overshoot natural.
    # Conforme o tipo de clip, calcular os frames onde o movimento atinge o pico.
    impact_frames: set[int] = set()
    total = frame_end - frame_start
    if kind in {"attack", "mine", "chop", "spear", "axe", "sword"}:
        # Strike frame: ~meio do clip (pico do golpe).
        impact_frames.add(frame_start + round(total * 0.50))
    elif kind == "jump":
        # Launch (crouch → vôo) e land (impacto do pouso).
        impact_frames.add(frame_start + round(total * 0.25))  # launch
        impact_frames.add(frame_start + round(total * 0.75))  # peak
        impact_frames.add(frame_start + round(total * 0.85))  # land
    elif kind == "fall":
        # Impacto no final (preparação de aterragem).
        impact_frames.add(frame_start + round(total * 0.85))

    rig.finish_action(action, cyclic=cyclic, impact_frames=impact_frames)
    bpy_ops.finalize_current_action_to_nla(armature_name)
    return True
