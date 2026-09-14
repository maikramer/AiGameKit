"""IK de 2 ossos com limites de juntas — repara joelhos/cotovelos invertidos e
impõe o curso anatómico (``curso das juntas``) a clips já gerados.

Porque existe: o retarget (:mod:`animator3d.retarget`) adopta quaternions
absolutos do pack source sem verificação de limites — mocap/packs trazem poses
anatomicamente impossíveis (joelho a dobrar para a frente, pointe, cotovelo
invertido) que chegam intactas ao engine (nem VibeGame nem Viber fazem IK em
runtime). O Motion3D clampe dobradiças a montante, mas só no esqueleto HML22 de
22 juntas e por ângulo interior — não sabe de *direção*.

Método (passe pós-animção, por action, por frame com keyframes):

1. Cadeias de 2 ossos + end-effector (pernas/braços humanoid, patas de
   quadrúpede — topologia já resolvida por ``bpy_ops._classify_bone_chains``).
2. Ângulo interior na junta (prox←junta→distal, 180° = esticado) fora da banda
   → nova distância root→effector pela lei dos cossenos (clamp de curso).
3. Junta do lado errado do *pole vector* (joelho atrás, cotovelo fora+trás;
   auto-derivado do lado de flexão do rest pose — funciona em quadrúpedes sem
   configuração) → re-solução analítica da cadeia com o pé/mão NO SÍTIO.
4. Ball joints (anca/ombro/pé): clamp swing-from-rest + twist.

O núcleo é puro (numpy, quaternions ``[w, x, y, z]``, testável sem bpy); a
camada bpy lê/reescreve keyframes existentes (nunca acrescenta — densidade de
keys do clip preservada) e força continuidade de sinal por osso.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

_DATA_DIR = Path(__file__).resolve().parent / "data" / "ik"
DEFAULT_LIMITS_PATH = _DATA_DIR / "limits.yaml"

# Tolerâncias numéricas.
_EPS = 1e-8
_DEG_TOL = 0.5  # só corrige se sair da banda por mais que isto (evita churn de keys)

_BONE_ROT_RE = re.compile(r'pose\.bones\["(.+?)"\]\.rotation_quaternion')

# ---------------------------------------------------------------------------
# Matemática pura (quaternions numpy [w, x, y, z]; vetores numpy (3,))
# ---------------------------------------------------------------------------


def q_normalize(q: np.ndarray) -> np.ndarray:
    """Normaliza um quaternion; quaternion nulo volta como identidade."""
    n = float(np.linalg.norm(q))
    if n < _EPS:
        return np.array([1.0, 0.0, 0.0, 0.0])
    return q / n


def q_mul(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Produto de Hamilton ``a @ b`` (aplica ``b`` primeiro)."""
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return np.array(
        [
            aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
        ]
    )


def q_conj(q: np.ndarray) -> np.ndarray:
    """Conjugado (inverso para unitários)."""
    return np.array([q[0], -q[1], -q[2], -q[3]])


def q_from_axis_angle(axis: np.ndarray, angle: float) -> np.ndarray:
    """Quaternion de rotação de ``angle`` rad em torno de ``axis`` (unitizado)."""
    n = float(np.linalg.norm(axis))
    if n < _EPS:
        return np.array([1.0, 0.0, 0.0, 0.0])
    axis = axis / n
    half = 0.5 * angle
    return np.array([math.cos(half), *(axis * math.sin(half))])


def q_rot_vec(q: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Roda o vetor ``v`` por ``q``."""
    t = 2.0 * np.cross(q[1:], v)
    return v + q[0] * t + np.cross(q[1:], t)


def rotation_between(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Arco mínimo que roda o vetor (não-necessariamente unitário) ``a`` em ``b``."""
    na = float(np.linalg.norm(a))
    nb = float(np.linalg.norm(b))
    if na < _EPS or nb < _EPS:
        return np.array([1.0, 0.0, 0.0, 0.0])
    a = a / na
    b = b / nb
    c = float(np.dot(a, b))
    if c < -1.0 + 1e-6:
        # Opostos: 180° em torno de um eixo perpendicular qualquer.
        axis = np.cross(a, np.array([1.0, 0.0, 0.0]))
        if float(np.linalg.norm(axis)) < 1e-4:
            axis = np.cross(a, np.array([0.0, 1.0, 0.0]))
        return q_from_axis_angle(axis, math.pi)
    return q_normalize(np.concatenate(([1.0 + c], np.cross(a, b))))


def swing_twist_split(q: np.ndarray, axis: np.ndarray) -> tuple[np.ndarray, float]:
    """Decompõe ``q`` em (swing, twist) — twist é a componente em torno de ``axis``.

    Returns:
        ``(swing_q, twist_angle_rad)`` com ``q == swing_q @ twist_q``.

    Nota: canonicamos o sinal (``q`` e ``-q`` são a mesma rotação) para o
    ``twist_angle = 2·atan2`` viver em (-π, π] — quaternions vindos de
    produtos de Hamilton chegam com frequência com ``w < 0`` e sem isto o
    twist "medido" explode para ~±2π e dispara clamps em pose neutra.
    """
    q = q_normalize(q)
    if q[0] < 0.0:
        q = -q
    axis = axis / max(float(np.linalg.norm(axis)), _EPS)
    d = float(np.dot(q[1:], axis))
    twist = q_normalize(np.array([q[0], *(axis * d)]))
    swing = q_mul(q, q_conj(twist))
    if swing[0] < 0.0:
        swing = -swing
    twist_angle = 2.0 * math.atan2(d, q[0])
    return swing, twist_angle


def swing_twist_clamp(
    q: np.ndarray,
    axis: np.ndarray,
    *,
    max_swing_deg: float,
    max_twist_deg: float,
) -> tuple[np.ndarray, bool]:
    """Clampa o swing (desvio do rest) e o twist de ``q`` em torno de ``axis``.

    Returns:
        ``(q_clamped, changed)``.
    """
    swing, twist_angle = swing_twist_split(q, axis)
    swing_angle = 2.0 * math.atan2(float(np.linalg.norm(swing[1:])), float(swing[0]))
    changed = False
    if swing_angle > math.radians(max_swing_deg + _DEG_TOL):
        swing_new = q_from_axis_angle(swing[1:], math.radians(max_swing_deg))
        changed = True
    else:
        swing_new = swing
    twist_max = math.radians(max_twist_deg)
    if abs(twist_angle) > twist_max + math.radians(_DEG_TOL):
        twist_new = q_from_axis_angle(axis, math.copysign(twist_max, twist_angle))
        changed = True
    else:
        twist_new = q_from_axis_angle(axis, twist_angle)
    if not changed:
        return q_normalize(q), False
    return q_normalize(q_mul(swing_new, twist_new)), True


def interior_angle_deg(prox: np.ndarray, joint: np.ndarray, dist: np.ndarray) -> float:
    """Ângulo interior em ``joint`` entre prox←joint e joint←dist (graus)."""
    v1 = prox - joint
    v2 = dist - joint
    n1 = float(np.linalg.norm(v1))
    n2 = float(np.linalg.norm(v2))
    if n1 < _EPS or n2 < _EPS:
        return 180.0
    cos = float(np.clip(np.dot(v1, v2) / (n1 * n2), -1.0, 1.0))
    return math.degrees(math.acos(cos))


def distance_for_interior_angle(l1: float, l2: float, angle_deg: float) -> float:
    """Distância root→effector que produz ``angle_deg`` na junta (lei dos cossenos).

    Monótona decrescente no ângulo: 180° (esticado) → ``l1 + l2``; dobrado →
    ``|l1 - l2|``.
    """
    cos = math.cos(math.radians(angle_deg))
    d2 = l1 * l1 + l2 * l2 - 2.0 * l1 * l2 * cos
    return math.sqrt(max(d2, 0.0))


def two_bone_knee_position(
    root: np.ndarray,
    target: np.ndarray,
    l1: float,
    l2: float,
    pole: np.ndarray,
) -> np.ndarray:
    """Posição da junta média (joelho/cotovelo) por lei dos cossenos.

    O lado é decidido por ``pole`` (projetado perpendicular ao eixo
    root→target): pole para trás → joelho atrás. Target inalcançável estica a
    cadeia; target colapsado dobra na direção do pole.

    Args:
        root: posição do head do osso proximal.
        target: posição desejada do end-effector.
        l1: comprimento root→junta.
        l2: comprimento junta→effector.
        pole: direção (espaço armature) para onde a junta deve dobrar.

    Returns:
        Posição da junta.
    """
    d_vec = target - root
    d = float(np.linalg.norm(d_vec))
    pole_n = float(np.linalg.norm(pole))
    if d < _EPS:
        # Cadeia colapsada no root: dobra na direção do pole.
        p = pole / pole_n if pole_n > _EPS else np.array([0.0, -1.0, 0.0])
        return root + p * (l1 * 0.5)
    u = d_vec / d
    cos_a = 1.0
    sin_a = 0.0
    if l1 > _EPS and l2 > _EPS:
        d_clamped = min(max(d, abs(l1 - l2) * 1.001), (l1 + l2) * 0.999)
        d_use = max(d_clamped, _EPS)
        cos_a = min(max((l1 * l1 + d_use * d_use - l2 * l2) / (2.0 * l1 * d_use), -1.0), 1.0)
        sin_a = math.sqrt(max(1.0 - cos_a * cos_a, 0.0))
    pole_perp = pole - float(np.dot(pole, u)) * u
    pn = float(np.linalg.norm(pole_perp))
    if pn < 1e-6:
        # Pole paralelo ao eixo: perpendicular arbitrária (com cadeia de
        # comprimento real, sin_a ~ 0 quando esticada — lado indiferente).
        helper = np.array([1.0, 0.0, 0.0]) if abs(u[0]) < 0.9 else np.array([0.0, 1.0, 0.0])
        pole_perp = np.cross(u, helper)
        pn = float(np.linalg.norm(pole_perp))
    pole_perp = pole_perp / pn
    return root + u * (l1 * cos_a) + pole_perp * (l1 * sin_a)


def hinge_axis_alignment(root: np.ndarray, knee: np.ndarray, eff: np.ndarray, pole: np.ndarray) -> float:
    """Alinhamento do eixo de dobradiça atual com o eixo esperado pelo pole.

    ``dot(normalize(T×S), normalize(T×pole))`` com T = knee−root e
    S = eff−knee. ≈ +1 = dobra do lado certo; ≈ −1 = joelho/cotovelo
    INVERTIDO (eixo de flexão ao contrário); ≈ 0 = membro esticado ou pole
    degenerado (sem sinal fiável).

    Nota: usa o EIXO de flexão, não a posição do joelho vs a corda
    root→eff — poses de perna em balanço profundo têm o joelho
    legitimamente "à frente" da corda sem estarem invertidas.
    """
    t = knee - root
    s = eff - knee
    n_bend = np.cross(t, s)
    n_pole = np.cross(t, pole)
    nb = float(np.linalg.norm(n_bend))
    npn = float(np.linalg.norm(n_pole))
    if nb < 1e-6 * float(np.linalg.norm(t)) * float(np.linalg.norm(s)) + 1e-12 or npn < 1e-9:
        return 0.0
    return float(np.dot(n_bend / nb, n_pole / npn))


# ---------------------------------------------------------------------------
# Limites (YAML)
# ---------------------------------------------------------------------------


@dataclass
class ChainLimit:
    """Limites anatómicos de um papel de cadeia (``leg``/``arm``).

    Ângulos em graus. ``hinge_min/hinge_max`` = banda do ângulo interior na
    junta média (180° = esticado). ``one_way`` repara a direção da dobradiça
    (joelho nunca dobra para a frente) via pole. ``upper_*`` = clamps ball da
    anca/ombro; ``effector_*`` = clamps ball do pé/mão.
    """

    hinge_min: float = 45.0
    hinge_max: float = 180.0
    one_way: bool = True
    pole: str | list[float] = "auto"  # auto | back | front | out-back | [x, y, z]
    pole_fallback: str = "back"
    upper_swing_max: float = 165.0
    upper_twist_max: float = 75.0
    effector_swing_max: float = 75.0
    effector_twist_max: float = 45.0


@dataclass
class JointLimitSet:
    """Conjunto de limites por papel + estado geral (``enabled``)."""

    roles: dict[str, ChainLimit] = field(default_factory=dict)
    enabled: bool = True


_ROLE_KEYS = (
    "hinge_min",
    "hinge_max",
    "one_way",
    "pole",
    "pole_fallback",
    "upper_swing_max",
    "upper_twist_max",
    "effector_swing_max",
    "effector_twist_max",
)


def load_limits(
    path: str | Path | None = None,
    override: dict[str, Any] | None = None,
) -> JointLimitSet:
    """Carrega limites do YAML (default: empacotado) com merge de um override.

    Args:
        path: YAML de limites; ``None`` = ``data/ik/limits.yaml`` do pacote.
        override: bloco ``ik_limits:`` de um perfil de retarget — merge raso
            por papel sobre o default (``enabled: false`` desliga o passe).

    Raises:
        ValueError: chaves/papéis desconhecidos ou bandas inválidas.
    """
    import yaml

    p = Path(path) if path else DEFAULT_LIMITS_PATH
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = JointLimitSet(enabled=bool(raw.get("enabled", True)))
    for role, cfg in dict(raw.get("roles", {})).items():
        out.roles[str(role)] = _chain_limit_from(dict(cfg or {}), f"ik limits[{role}]")
    for key, val in dict(override or {}).items():
        if key == "enabled":
            out.enabled = bool(val)
            continue
        if key not in out.roles:
            raise ValueError(f"ik limits override: papel desconhecido {key!r} (conhecidos: {sorted(out.roles)})")
        if not isinstance(val, dict):
            raise ValueError(f"ik limits override[{key!r}] tem de ser um mapa")
        unknown = [k for k in val if k not in _ROLE_KEYS]
        if unknown:
            raise ValueError(f"ik limits override[{key!r}]: chaves desconhecidas {unknown}")
        base = vars(out.roles[key])
        merged = {k: val.get(k, base[k]) for k in _ROLE_KEYS}
        out.roles[key] = _chain_limit_from(merged, f"ik limits override[{key}]")
    return out


def _chain_limit_from(cfg: dict[str, Any], ctx: str) -> ChainLimit:
    """Constrói um :class:`ChainLimit` de um dict YAML validado."""
    unknown = [k for k in cfg if k not in _ROLE_KEYS]
    if unknown:
        raise ValueError(f"{ctx}: chaves desconhecidas {unknown}")
    base = ChainLimit()
    lim = ChainLimit(
        hinge_min=float(cfg.get("hinge_min", base.hinge_min)),
        hinge_max=float(cfg.get("hinge_max", base.hinge_max)),
        one_way=bool(cfg.get("one_way", base.one_way)),
        pole=cfg.get("pole", base.pole),
        pole_fallback=str(cfg.get("pole_fallback", base.pole_fallback)),
        upper_swing_max=float(cfg.get("upper_swing_max", base.upper_swing_max)),
        upper_twist_max=float(cfg.get("upper_twist_max", base.upper_twist_max)),
        effector_swing_max=float(cfg.get("effector_swing_max", base.effector_swing_max)),
        effector_twist_max=float(cfg.get("effector_twist_max", base.effector_twist_max)),
    )
    if lim.hinge_min >= lim.hinge_max:
        raise ValueError(f"{ctx}: hinge_min ({lim.hinge_min}) >= hinge_max ({lim.hinge_max})")
    if lim.hinge_min < 0.0 or lim.hinge_max > 180.0:
        raise ValueError(f"{ctx}: banda de ângulo interior tem de viver em [0, 180]")
    if isinstance(lim.pole, str) and lim.pole not in ("auto", "back", "front", "out-back"):
        raise ValueError(f"{ctx}: pole {lim.pole!r} inválido (auto|back|front|out-back|[x, y, z])")
    return lim


# ---------------------------------------------------------------------------
# Camada bpy — cadeias, poles, passe por frame
# ---------------------------------------------------------------------------


def _bpy():
    import bpy

    return bpy


@dataclass
class TwoBoneChain:
    """Cadeia de 2 ossos + end-effector (bone names do rig alvo)."""

    role: str  # "leg" | "arm"
    upper: str
    lower: str
    effector: str | None  # foot/hand; None → tail do lower


def resolve_ik_chains(arm_name: str, chains: dict[str, Any] | None = None) -> list[TwoBoneChain]:
    """Extrai cadeias 2-ossos das chains classificadas (humanoid + multi-pata).

    Args:
        arm_name: nome do armature (para ``_classify_bone_chains`` quando
            ``chains`` não é dado).
        chains: dict de chains pré-classificado (opcional).

    Returns:
        Lista de :class:`TwoBoneChain` (pernas → papel ``leg``, braços → ``arm``).
    """
    if chains is None:
        from . import bpy_ops

        chains = bpy_ops._classify_bone_chains(arm_name)

    def _chain(names: list[Any], role: str) -> TwoBoneChain | None:
        if len(names) < 2:
            return None
        if role == "arm" and len(names) >= 4:
            # [shoulder, upper, fore, hand] — salta o shoulder (HumanoidRig.arm_bones).
            return TwoBoneChain(role, str(names[1]), str(names[2]), str(names[3]))
        if len(names) >= 3:
            # [thigh, calf, foot(, toe)] — knee no 2.º osso, foot é o effector.
            return TwoBoneChain(role, str(names[0]), str(names[1]), str(names[2]))
        return TwoBoneChain(role, str(names[0]), str(names[1]), None)

    out: list[TwoBoneChain] = []
    for key in ("leg_r", "leg_l", "arm_r", "arm_l", "legs_r", "legs_l"):
        val = chains.get(key) or []
        role = "leg" if key.startswith("leg") else "arm"
        if val and isinstance(val[0], list):
            # Multi-pata (legs_r/legs_l): uma sub-lista por pata individual.
            for sub in val:
                c = _chain(list(sub), role)
                if c is not None:
                    out.append(c)
        else:
            c = _chain(list(val), role)
            if c is not None:
                out.append(c)
    # Dedup: pernas de multi-pata podem repetir-se entre leg_r/legs_r.
    seen: set[tuple[str, str, str | None]] = set()
    unique: list[TwoBoneChain] = []
    for c in out:
        key = (c.upper, c.lower, c.effector)
        if key not in seen:
            seen.add(key)
            unique.append(c)
    return unique


def _fallback_pole(mode: str, forward: Any, h1: Any) -> Any:
    """Pole anatómico por papel, relativo à frente detetada do rig."""
    from mathutils import Vector

    side = 1.0 if h1.x >= 0.0 else -1.0
    outward = Vector((side, 0.0, 0.0))
    fwd = Vector(forward).normalized()
    if mode == "front":
        return fwd
    if mode == "out-back":
        return (outward - fwd).normalized()
    # default / "back": a dobradiça dobra para trás em relação à frente.
    return (-fwd).normalized()


def _resolve_pole(arm: Any, chain: TwoBoneChain, lim: ChainLimit, forward: Any) -> Any:
    """Pole vector (unitário, espaço armature) da dobradiça da cadeia.

    ``auto`` usa o lado de flexão do rest pose (lado do joelho no rest) — é a
    convenção anatómica do próprio rig e funciona em quadrúpedes. Sem flexão
    no rest (perna esticada), ou se a flexão de rest aponta CONTRA o prior
    anatómico do papel (rótula anterior em pernas quase esticadas), cai no
    fallback (``back``/``front``/``out-back``) usando a frente detetada do
    rig. YAML aceita vetor explícito ``[x, y, z]``.
    """
    from mathutils import Vector

    bones = arm.data.bones
    b_up = bones.get(chain.upper)
    b_lo = bones.get(chain.lower)
    if b_up is None or b_lo is None:
        return Vector((0.0, -1.0, 0.0))
    h1 = Vector(b_up.head_local)
    h2 = Vector(b_lo.head_local)
    b_eff = bones.get(chain.effector) if chain.effector else None
    h3 = Vector(b_eff.head_local) if b_eff is not None else Vector(b_lo.tail_local)

    mode = lim.pole if isinstance(lim.pole, str) else None
    if mode in (None, "", "auto"):
        axis = h3 - h1
        if axis.length > _EPS:
            axis_u = axis.normalized()
            bend = h2 - (h1 + h3) * 0.5
            pole = bend - axis_u * bend.dot(axis_u)
            if pole.length > 1e-4:
                pole_u = pole.normalized()
                # Validar contra o prior anatómico do papel: rigs com joelho
                # ligeiramente À FRENTE no rest (rótula anterior) produzem um
                # auto-pole invertido que marca TODOS os clips como joelho
                # virado. Se apontar contra o fallback, vence o fallback.
                fb = _fallback_pole(lim.pole_fallback, forward, h1)
                if pole_u.dot(fb) >= 0.0:
                    return pole_u
        mode = lim.pole_fallback

    if isinstance(lim.pole, (list, tuple)):
        v = Vector((float(lim.pole[0]), float(lim.pole[1]), float(lim.pole[2])))
        return v.normalized() if v.length > _EPS else Vector((0.0, -1.0, 0.0))
    return _fallback_pole(mode or "back", forward, h1)


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


def _chain_key_frames(action: Any, bone_names: set[str]) -> list[float]:
    """Frames (floats exatos) com keys de rotação nos ossos das cadeias."""
    frames: set[float] = set()
    for fc in _action_fcurves(action):
        m = _BONE_ROT_RE.match(fc.data_path or "")
        if m and m.group(1) in bone_names:
            frames.update(float(kp.co.x) for kp in fc.keyframe_points)
    return sorted(frames)


def enforce_joint_limits(
    arm_name: str,
    action: Any | None = None,
    *,
    limits: JointLimitSet | None = None,
    chains: list[TwoBoneChain] | None = None,
) -> dict[str, Any] | None:
    """Aplica limites de juntas + reparações IK a UMA action do armature.

    Corrige apenas frames com keys nos ossos das cadeias (densidade de keys
    preservada) e só escreve onde a pose viola limites. Muta as NLA tracks
    durante a passagem (a ação tem de ser avaliada sozinha) e repõe o estado.

    Args:
        arm_name: nome do armature alvo.
        action: bpy Action a corrigir; ``None`` = action activa.
        limits: limites carregados (:func:`load_limits`); None = default.
        chains: cadeias pré-resolvidas; None = auto a partir do rig.

    Returns:
        Stats ``{clip, frames, chains, hinge_fixes, flips, ball_clamps}`` ou
        ``None`` quando desligado/sem cadeias/sem keys.
    """
    bpy = _bpy()
    from mathutils import Quaternion

    from . import bpy_ops

    lim_set = limits if limits is not None else load_limits()
    if not lim_set.enabled:
        return None

    arm_obj = bpy.data.objects.get(arm_name)
    if arm_obj is None or arm_obj.type != "ARMATURE":
        raise ValueError(f"Armature não encontrado: {arm_name!r}")

    if chains is None:
        chains = resolve_ik_chains(arm_name)
    if not chains:
        return None

    # Ossos em falta → cadeia ignorada (rigs parciais não rebentam o passe).
    valid: list[TwoBoneChain] = []
    for c in chains:
        names = [c.upper, c.lower] + ([c.effector] if c.effector else [])
        if all(name in arm_obj.pose.bones and name in arm_obj.data.bones for name in names):
            valid.append(c)
    chains = valid
    if not chains:
        return None

    ad = arm_obj.animation_data or arm_obj.animation_data_create()
    target_action = action if action is not None else ad.action
    if target_action is None:
        return None

    chains_dict = bpy_ops._classify_bone_chains(arm_name)
    forward = bpy_ops._detect_forward(arm_obj, chains_dict)

    pb_map = {
        name: arm_obj.pose.bones[name]
        for c in chains
        for name in [c.upper, c.lower, *([c.effector] if c.effector else [])]
    }
    for pb in pb_map.values():
        pb.rotation_mode = "QUATERNION"
    rest_quats = {name: arm_obj.data.bones[name].matrix_local.to_quaternion() for name in pb_map}

    bone_names = set(pb_map)
    frames = _chain_key_frames(target_action, bone_names)
    if not frames:
        return None

    # Muta NLA para a avaliação por frame ficar SÓ da action alvo.
    saved_action = ad.action
    track_state = [(t, t.mute) for t in ad.nla_tracks]
    for t, _ in track_state:
        t.mute = True
    ad.action = target_action

    scene = bpy.context.scene
    stats = {
        "clip": target_action.name,
        "frames": len(frames),
        "chains": len(chains),
        "hinge_fixes": 0,
        "flips": 0,
        "ball_clamps": 0,
    }

    def _n(v: Any) -> np.ndarray:
        return np.array([v.x, v.y, v.z], dtype=np.float64)

    def _mq(q: np.ndarray) -> Any:
        return Quaternion((float(q[0]), float(q[1]), float(q[2]), float(q[3])))

    def _nm(quat: Any) -> np.ndarray:
        """mathutils Quaternion → numpy [w, x, y, z]."""
        return np.array([quat.w, quat.x, quat.y, quat.z], dtype=np.float64)

    # Eixo local da dobradiça/ball no espaço do próprio osso (Blender: o osso
    # aponta +Y local). Os clamps ball são RELATIVOS AO PAI (matrix_basis) —
    # medir contra o rest em espaço armature disparava caps quando o corpo
    # inteiro roda (death/roll deitados) sem a junta ter rodado.
    _BONE_Y = np.array([0.0, 1.0, 0.0])
    _IDENTITY_Q = np.array([1.0, 0.0, 0.0, 0.0])

    try:
        for chain in chains:
            lim = lim_set.roles.get(chain.role, ChainLimit())
            pb_u = pb_map[chain.upper]
            pb_l = pb_map[chain.lower]
            pb_e = pb_map.get(chain.effector) if chain.effector else None
            r1_arr = _nm(rest_quats[chain.upper])
            r2_arr = _nm(rest_quats[chain.lower])
            r_e_arr = _nm(rest_quats[chain.effector]) if chain.effector else None
            # Identidade de cadeia (validada empiricamente): W = Wp·Rp⁻¹·R·B,
            # logo o basis (rotação da JUNTA, relativa ao pai) é
            # B = (Wp·Rp⁻¹·R)⁻¹·W — o termo Rp é essencial (rigs com rest
            # rodado, ex. Quaternius pelvis/thigh ~±90°, sem ele tudo explode).
            # Wp anima (pelve roda em death/roll), logo lê-se POR FRAME.
            if pb_u.parent is not None:
                r_parent_arr = _nm(arm_obj.data.bones[pb_u.parent.name].matrix_local.to_quaternion())
            else:
                r_parent_arr = np.array([1.0, 0.0, 0.0, 0.0])
            # Pole no frame de REST do OSSO UPPER (constante) → rodado por
            # W1(t) por frame: segue o swing da perna (deteção de eixo com
            # sinal estável mesmo com a coxa a cruzar a vertical, e reparação
            # espelhada consistente com a deteção) e o corpo (deitar/rolhar
            # roda o pole com o rig).
            pole_rest = _n(_resolve_pole(arm_obj, chain, lim, forward))
            pole_upper_local = q_rot_vec(q_conj(r1_arr), pole_rest)
            l1 = (arm_obj.data.bones[chain.upper].tail_local - arm_obj.data.bones[chain.upper].head_local).length
            l2 = (arm_obj.data.bones[chain.lower].tail_local - arm_obj.data.bones[chain.lower].head_local).length
            l1 = l1 if l1 > _EPS else 1e-4
            l2 = l2 if l2 > _EPS else 1e-4

            def _read() -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
                root = _n(pb_u.matrix.translation)
                knee = _n(pb_l.matrix.translation)
                eff = _n(pb_e.matrix.translation) if pb_e is not None else _n(pb_l.tail)
                w1 = _nm(pb_u.matrix.to_quaternion())
                wp = _nm(pb_u.parent.matrix.to_quaternion()) if pb_u.parent else _IDENTITY_Q
                return root, knee, eff, w1, wp

            def _eval_at(fr: float) -> None:
                """Avalia o depsgraph no tempo EXATO da key (bpy 5.x: frame_set
                é int-only → inteiro + ``frame_subframe``). Avaliar no
                ``round`` avaliava a pose interpolada no tempo errado e
                escrevia a correção na key do frame vizinho (clips com keys a
                30fps em cena 24fps têm keys fracionárias)."""
                fi = math.floor(fr)
                scene.frame_set(fi)
                scene.frame_subframe = fr - fi
                bpy.context.view_layer.update()

            # Pré-passo: alinhamento do eixo de dobradiça em todos os frames →
            # maioria-voto do one_way. Clips em que a maioria dos frames
            # dobra do lado "errado" do pole (pole mal configurado, rigs
            # exóticos) NÃO têm flips — rede de segurança.
            sides: list[float] = []
            for frame in frames:
                _eval_at(frame)
                n_root, n_knee, n_eff, w1_t, _ = _read()
                n_pole_t = q_rot_vec(w1_t, pole_upper_local)
                sides.append(hinge_axis_alignment(n_root, n_knee, n_eff, n_pole_t))
            n_positive = sum(1 for s in sides if s > 0.5)
            one_way_active = lim.one_way and (len(sides) > 0 and n_positive / len(sides) >= 0.7)

            prev: dict[str, Any] = {}
            for frame in frames:
                _eval_at(frame)
                n_root, n_knee, n_eff, q1n, wp_t = _read()
                n_pole_t = q_rot_vec(q1n, pole_upper_local)

                # -- 1. Clamp ball da anca/ombro (swing/twist da JUNTA = basis) --
                upper_ref_t = q_mul(q_mul(wp_t, q_conj(r_parent_arr)), r1_arr)  # Wp(t)·Rp⁻¹·R1
                q_rel = q_mul(q_conj(upper_ref_t), q1n)  # = matrix_basis do upper
                q_rel_c, changed = swing_twist_clamp(
                    q_rel, _BONE_Y, max_swing_deg=lim.upper_swing_max, max_twist_deg=lim.upper_twist_max
                )
                if changed:
                    basis = _mq(q_rel_c)
                    pq = prev.get(chain.upper)
                    if pq is not None and basis.dot(pq) < 0.0:
                        basis.negate()
                    prev[chain.upper] = basis.copy()
                    pb_u.rotation_quaternion = basis
                    pb_u.keyframe_insert(data_path="rotation_quaternion", frame=frame)
                    stats["ball_clamps"] += 1
                    bpy.context.view_layer.update()
                    n_root, n_knee, n_eff, q1n, wp_t = _read()

                # -- 2. Hinge: banda de ângulo interior + eixo da dobradiça ---
                angle = interior_angle_deg(n_root, n_knee, n_eff)
                side = hinge_axis_alignment(n_root, n_knee, n_eff, n_pole_t)
                angle_bad = angle < lim.hinge_min - _DEG_TOL or angle > lim.hinge_max + _DEG_TOL
                # Inversão: eixo de flexão invertido (side < -0.5 ≈ >30° de
                # desvio) com dobra suficiente para o sinal ser fiável
                # (membros quase esticados não têm sinal fiável).
                side_bad = one_way_active and side < -0.5 and angle < lim.hinge_max - 5.0
                if not angle_bad and not side_bad:
                    continue

                d = float(np.linalg.norm(n_eff - n_root))
                if d < 1e-6:
                    continue  # cadeia colapsada no root — nada sensato a fazer
                if angle_bad:
                    angle_t = min(max(angle, lim.hinge_min), lim.hinge_max)
                    d_target = distance_for_interior_angle(l1, l2, angle_t)
                    u_eff = (n_eff - n_root) / d
                    n_target = n_root + u_eff * d_target
                else:
                    n_target = n_eff  # espelho do joelho com o pé NO SÍTIO
                if side_bad:
                    stats["flips"] += 1
                else:
                    stats["hinge_fixes"] += 1
                # Pole do REPARO: alinhar o lado da solução com o lado
                # DESEJADO da junta. No flip é o lado OPOSTO ao joelho atual
                # (espelho); no clamp de curso é o MESMO lado (não inverter a
                # dobradiça). O pole do frame roda com a coxa e, com ela
                # além de ±90°, pode apontar para o lado contrário ao
                # desejado — o sinal aqui garante a geometria.
                u_chk = (n_target - n_root) / max(float(np.linalg.norm(n_target - n_root)), 1e-9)
                knee_off = n_knee - n_root
                knee_perp = knee_off - float(np.dot(knee_off, u_chk)) * u_chk
                # Sinal desejado de dot(knee_perp, pole): flip = joelho no lado
                # OPOSTO ao atual; clamp = MESMO lado (não inverter a dobra).
                want_sign = -1.0 if side_bad else 1.0
                if float(np.dot(knee_perp, n_pole_t)) * want_sign < 0.0:
                    n_pole_repair = -n_pole_t
                else:
                    n_pole_repair = n_pole_t
                n_knee_new = two_bone_knee_position(n_root, n_target, l1, l2, n_pole_repair)

                # Rotações world de arco mínimo: upper leva a junta ao sítio
                # novo; lower alinha a canela com o novo joelho→effector.
                q1 = rotation_between(n_knee - n_root, n_knee_new - n_root)
                q2 = rotation_between(n_eff - n_knee, n_target - n_knee_new)
                w1_new = q_mul(q1, q1n)
                w2_new = q_mul(q2, _nm(pb_l.matrix.to_quaternion()))
                # B1 = R1⁻¹·Rp·Wp(t)⁻¹·W1n ; B2 = R2⁻¹·R1·W1n⁻¹·W2n (identidade acima)
                basis1 = _mq(q_mul(q_mul(q_conj(r1_arr), r_parent_arr), q_mul(q_conj(wp_t), w1_new)))
                basis2 = _mq(q_mul(q_mul(q_conj(r2_arr), r1_arr), q_mul(q_conj(w1_new), w2_new)))
                for bone, basis in ((chain.upper, basis1), (chain.lower, basis2)):
                    pq = prev.get(bone)
                    if pq is not None and basis.dot(pq) < 0.0:
                        basis.negate()
                    prev[bone] = basis.copy()
                    pb_map[bone].rotation_quaternion = basis
                    pb_map[bone].keyframe_insert(data_path="rotation_quaternion", frame=frame)

                # -- 3. Clamp ball do pé/mão (junta relativa ao lower) ---------
                if pb_e is not None:
                    bpy.context.view_layer.update()
                    n_we = _nm(pb_e.matrix.to_quaternion())
                    # O pai do effector (lower) pode ter sido reescrito neste
                    # frame — ler a rotação world ATUAL dele do depsgraph.
                    w_lower = _nm(pb_e.parent.matrix.to_quaternion()) if pb_e.parent else n_we
                    # B_e = (Wl·R2⁻¹·Re)⁻¹·We
                    eff_ref = q_mul(q_mul(w_lower, q_conj(r2_arr)), r_e_arr)
                    q_rel_e = q_mul(q_conj(eff_ref), n_we)
                    q_ec, e_changed = swing_twist_clamp(
                        q_rel_e,
                        _BONE_Y,
                        max_swing_deg=lim.effector_swing_max,
                        max_twist_deg=lim.effector_twist_max,
                    )
                    if e_changed:
                        basis_e = _mq(q_ec)
                        pq = prev.get(chain.effector)
                        if pq is not None and basis_e.dot(pq) < 0.0:
                            basis_e.negate()
                        prev[chain.effector] = basis_e.copy()
                        pb_e.rotation_quaternion = basis_e
                        pb_e.keyframe_insert(data_path="rotation_quaternion", frame=frame)
                        stats["ball_clamps"] += 1
    finally:
        if saved_action is not None:
            ad.action = saved_action
        else:
            ad.action = None
        for t, m in track_state:
            t.mute = m
        scene.frame_set(int(frames[0]))
        scene.frame_subframe = 0.0

    return stats


def enforce_all_actions(
    arm_name: str,
    *,
    limits: JointLimitSet | None = None,
) -> list[dict[str, Any]]:
    """Aplica :func:`enforce_joint_limits` a todas as actions do armature.

    Cobre tanto a action activa como as actions empurradas para NLA (o formato
    do ``game-pack``/glTF import) — actions duplicadas entre tracks correm uma
    única vez.
    """
    bpy = _bpy()
    arm = bpy.data.objects.get(arm_name)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"Armature não encontrado: {arm_name!r}")
    ad = arm.animation_data
    actions: list[Any] = []
    if ad is not None:
        if ad.action is not None:
            actions.append(ad.action)
        for track in ad.nla_tracks:
            for strip in track.strips:
                if strip.action is not None and not any(a.name == strip.action.name for a in actions):
                    actions.append(strip.action)
    out = []
    for act in actions:
        res = enforce_joint_limits(arm_name, act, limits=limits)
        if res is not None:
            out.append(res)
    return out


__all__ = [
    "ChainLimit",
    "DEFAULT_LIMITS_PATH",
    "JointLimitSet",
    "TwoBoneChain",
    "distance_for_interior_angle",
    "enforce_all_actions",
    "enforce_joint_limits",
    "hinge_axis_alignment",
    "interior_angle_deg",
    "load_limits",
    "resolve_ik_chains",
    "rotation_between",
    "swing_twist_clamp",
    "swing_twist_split",
    "two_bone_knee_position",
]
