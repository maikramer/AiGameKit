"""Operações Blender (bpy) — importação, inspeção, keyframes e exportação."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any


def _bpy():
    import bpy

    return bpy


def clear_scene() -> None:
    """Remove todos os dados da cena (fábrica limpa para importação)."""
    bpy = _bpy()
    bpy.ops.wm.read_factory_settings(use_empty=True)


def _decompress_meshopt_glb(src: Path) -> Path:
    """Se ``src`` é GLB com EXT_meshopt_compression (que bpy não importa),
    descompressa via ``gltf-transform copy`` para um tmpfile e devolve o path
    novo. Caso contrário, devolve ``src``.
    """
    import shutil as _sh
    import subprocess as _sp
    import tempfile as _tf

    if _sh.which("npx") is None:
        return src
    with _tf.NamedTemporaryFile(suffix=".glb", delete=False) as _tmp:
        out = Path(_tmp.name)
    try:
        r = _sp.run(
            ["npx", "--yes", "@gltf-transform/cli", "copy", str(src), str(out)],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (FileNotFoundError, _sp.TimeoutExpired):
        return src
    if r.returncode != 0 or not out.is_file():
        return src
    return out


def import_asset(path: Path) -> list[str]:
    """Importa GLB/GLTF ou FBX. Devolve nomes de objectos de topo criados."""
    bpy = _bpy()
    path = path.expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(path)

    suffix = path.suffix.lower()
    before = {o.name for o in bpy.context.scene.objects}

    if suffix in {".glb", ".gltf"}:
        # bpy GLTF importer não suporta EXT_meshopt_compression; descompressa
        # silenciosamente quando preciso (no-op se já descompresso).
        path = _decompress_meshopt_glb(path)
        bpy.ops.import_scene.gltf(filepath=str(path))
    elif suffix == ".fbx":
        bpy.ops.import_scene.fbx(filepath=str(path))
    else:
        raise ValueError(f"Formato não suportado: {suffix} (usa .glb, .gltf ou .fbx)")

    after = {o.name for o in bpy.context.scene.objects}
    new_names = sorted(after - before)
    return new_names


def list_armatures() -> list[Any]:
    bpy = _bpy()
    return [obj for obj in bpy.context.scene.objects if obj.type == "ARMATURE"]


def inspect_scene() -> dict[str, Any]:
    """Resumo da cena: armatures, meshes, bounds, acções, intervalo de frames."""
    bpy = _bpy()
    from mathutils import Vector

    armatures = list_armatures()
    actions = list(bpy.data.actions)
    out: dict[str, Any] = {
        "blender_version": ".".join(str(x) for x in bpy.app.version),
        "frame_start": int(bpy.context.scene.frame_start),
        "frame_end": int(bpy.context.scene.frame_end),
        "fps": float(bpy.context.scene.render.fps),
        "armatures": [],
        "actions": [],
        "meshes": [],
        "mesh_totals": {"vertex_count": 0, "face_count": 0},
        "world_bounds": None,
    }
    for arm in armatures:
        bone_names = [b.name for b in arm.data.bones]
        ad = arm.animation_data
        nla_n = len(ad.nla_tracks) if ad else 0
        active = ad.action.name if ad and ad.action else None
        out["armatures"].append(
            {
                "name": arm.name,
                "bone_count": len(bone_names),
                "bones": bone_names,
                "bones_sample": bone_names[:32],
                "nla_track_count": nla_n,
                "active_action": active,
            }
        )

    world_corners: list = []
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        data = obj.data
        nv = len(data.vertices)
        nf = len(data.polygons)
        out["mesh_totals"]["vertex_count"] += nv
        out["mesh_totals"]["face_count"] += nf
        out["meshes"].append(
            {
                "name": obj.name,
                "vertex_count": nv,
                "face_count": nf,
                "vertex_groups": len(obj.vertex_groups),
            }
        )
        for c in obj.bound_box:
            world_corners.append(obj.matrix_world @ Vector(c))

    if world_corners:
        xs = [v.x for v in world_corners]
        ys = [v.y for v in world_corners]
        zs = [v.z for v in world_corners]
        mn = [min(xs), min(ys), min(zs)]
        mx = [max(xs), max(ys), max(zs)]
        out["world_bounds"] = {
            "min": mn,
            "max": mx,
            "center": [(mn[i] + mx[i]) / 2 for i in range(3)],
            "size": [mx[i] - mn[i] for i in range(3)],
            "max_extent": max(mx[i] - mn[i] for i in range(3)),
        }

    for act in actions:
        out["actions"].append(
            {
                "name": act.name,
                "frame_range": (int(act.frame_range[0]), int(act.frame_range[1])),
            }
        )
    return out


def ensure_action(armature_name: str, action_name: str) -> Any:
    """Cria ou reutiliza uma acção no armature."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"Armature não encontrado: {armature_name!r}")

    if arm.animation_data is None:
        arm.animation_data_create()
    if arm.animation_data.action is None or arm.animation_data.action.name != action_name:
        act = bpy.data.actions.get(action_name) or bpy.data.actions.new(name=action_name)
        arm.animation_data.action = act
    return arm.animation_data.action


def _is_action_on_nla(arm: Any, act: Any) -> bool:
    if arm.animation_data is None:
        return False
    for track in arm.animation_data.nla_tracks:
        for strip in track.strips:
            if strip.action == act:
                return True
    return False


def normalize_armature_before_animation(armature_name: str) -> None:
    """Se a action activa já está num strip NLA (ex.: reimport GLB), limpa o duplicado activo."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    act = arm.animation_data.action
    if act is None:
        return
    if _is_action_on_nla(arm, act):
        arm.animation_data.action = None


def stash_if_needed_for_action(armature_name: str, next_action_name: str) -> None:
    """Antes de atribuir uma nova Action: empurra a actual para NLA se for substituída."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    act = arm.animation_data.action
    if act is None:
        return
    if act.name == next_action_name:
        return
    if _is_action_on_nla(arm, act):
        arm.animation_data.action = None
        return
    push_active_action_to_nla(armature_name)


def push_active_action_to_nla(armature_name: str) -> None:
    """Empurra a Action activa para um novo track NLA (Blender 5.x: strips.new(name, start, action))."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    act = arm.animation_data.action
    if act is None:
        return
    ad = arm.animation_data
    track = ad.nla_tracks.new()
    track.name = act.name
    f0 = int(act.frame_range[0])
    track.strips.new(act.name, f0, act)


def finalize_current_action_to_nla(armature_name: str) -> None:
    """No fim dos keyframes: garante que o clip actual está em NLA e remove action activa duplicada."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    act = arm.animation_data.action
    if act is None:
        return
    if _is_action_on_nla(arm, act):
        arm.animation_data.action = None
        return
    push_active_action_to_nla(armature_name)
    arm.animation_data.action = None


def count_nla_tracks(armature_name: str) -> int:
    """Número de tracks NLA (cada um exporta como uma animação glTF separada)."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return 0
    return len(arm.animation_data.nla_tracks)


def clear_armature_animations(armature_name: str) -> None:
    """Remove todas as animações do armature e Actions em bpy.data (modo --no-append)."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None:
        return
    if arm.animation_data is not None:
        for t in list(arm.animation_data.nla_tracks):
            arm.animation_data.nla_tracks.remove(t)
        arm.animation_data.action = None
    for act in list(bpy.data.actions):
        bpy.data.actions.remove(act)


def _ensure_pose_mode(armature_name: str) -> None:
    """Garante que o armature está activo e em modo POSE."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"Armature não encontrado: {armature_name!r}")
    bpy.context.view_layer.objects.active = arm
    arm.select_set(True)
    if bpy.context.object is not arm or bpy.context.object.mode != "POSE":
        bpy.ops.object.mode_set(mode="POSE")


def _get_pose_bone(armature_name: str, bone_name: str):
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        raise ValueError(f"Armature não encontrado: {armature_name!r}")
    pb = arm.pose.bones.get(bone_name)
    if pb is None:
        raise ValueError(f"Osso não encontrado: {bone_name!r}")
    return pb


def _rotation_data_path(pb: Any) -> str:
    if pb.rotation_mode == "QUATERNION":
        return "rotation_quaternion"
    if pb.rotation_mode == "AXIS_ANGLE":
        return "rotation_axis_angle"
    return "rotation_euler"


def _set_pose_bone_rotation(pb: Any, euler_xyz: tuple[float, float, float]) -> None:
    from mathutils import Euler

    if pb.rotation_mode == "QUATERNION":
        pb.rotation_quaternion = Euler(euler_xyz, "XYZ").to_quaternion()
        return
    if pb.rotation_mode == "AXIS_ANGLE":
        quat = Euler(euler_xyz, "XYZ").to_quaternion()
        axis, angle = quat.to_axis_angle()
        pb.rotation_axis_angle = (angle, axis.x, axis.y, axis.z)
        return
    pb.rotation_euler = euler_xyz


def insert_pose_keyframe(armature_name: str, bone_name: str, frame: int) -> None:
    """Insere keyframe de rotação euler no osso (modo pose)."""
    _ensure_pose_mode(armature_name)
    pb = _get_pose_bone(armature_name, bone_name)
    pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)


def set_bone_rotation_euler(armature_name: str, bone_name: str, euler: tuple[float, float, float]) -> None:
    pb = _get_pose_bone(armature_name, bone_name)
    _set_pose_bone_rotation(pb, euler)


def pick_demo_bone(armature_name: str) -> str | None:
    """Escolhe um osso para demo (preferência por nomes comuns)."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        return None
    prefs = (
        "spine",
        "Spine",
        "spine_01",
        "mixamorig:Spine",
        "mixamorig_Spine",
        "root",
        "Root",
        "mixamorig:Hips",
        "mixamorig_Hips",
    )
    names = [b.name for b in arm.pose.bones]
    for p in prefs:
        if p in names:
            return p
    return names[0] if names else None


def wave_idle_keyframes(
    armature_name: str,
    bone_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 60,
    amplitude_rad: float = 0.35,
    action_name: str = "Animator3D_WaveIdle",
) -> None:
    """Animação simples: oscilação em X da rotação do osso (idle de teste)."""
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end

    bpy = _bpy()
    _ensure_pose_mode(armature_name)
    pb = _get_pose_bone(armature_name, bone_name)

    steps = max(2, frame_end - frame_start + 1)
    for i in range(steps):
        t = i / (steps - 1) if steps > 1 else 0.0
        frame = frame_start + i
        a = amplitude_rad * math.sin(t * math.pi * 2)
        # frame_set avalia a cena e reseta a pose; o euler TEM de ser definido depois.
        bpy.context.scene.frame_set(frame)
        _set_pose_bone_rotation(pb, (a, 0.0, 0.0))
        pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    finalize_current_action_to_nla(armature_name)


def _identify_root_layout(root_kids: list) -> tuple[object | None, list[tuple[object, str]]]:
    """Detect humanoid layout: one central spine + two lateral leg branches.

    Returns (spine_candidate, [(child, 'r'|'l'), ...]) for identified legs.
    Works even when leg branches have small lateral offset (|x| < 0.15),
    common in UniRig and similar generic-named skeletons.
    """
    if len(root_kids) < 3:
        return None, []

    by_abs_x = sorted(root_kids, key=lambda c: abs(c.bone.head_local.x))
    spine_candidate = by_abs_x[0]
    laterals = by_abs_x[1:]

    # Need at least one positive-x and one negative-x child (symmetric pair).
    pos = [c for c in laterals if c.bone.head_local.x > 0.02]
    neg = [c for c in laterals if c.bone.head_local.x < -0.02]
    if not pos or not neg:
        return None, []

    # Verify spine candidate leads to a hub (branching skeleton above).
    cursor = spine_candidate
    has_hub = False
    for _ in range(10):
        if len(cursor.children) >= 2:
            has_hub = True
            break
        if len(cursor.children) == 1:
            cursor = cursor.children[0]
        else:
            break
    if not has_hub:
        return None, []

    pairs: list[tuple[object, str]] = []
    for c in pos:
        pairs.append((c, "r"))
    for c in neg:
        pairs.append((c, "l"))
    return spine_candidate, pairs


def _classify_bone_chains(armature_name: str) -> dict[str, list[str]]:
    """Classifica ossos em cadeias funcionais por posicao/hierarquia.

    Suporta hierarquias profundas (hub nao-raiz, patas na cauda, dedos nas asas).
    Retorna dict com chaves: body, spine, tail, neck, wing_r, wing_l,
    wing_r_fingers, wing_l_fingers, leg_r, leg_l, arm_r, arm_l.
    """
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        return {}

    bones = arm.pose.bones
    if not bones:
        return {}

    root = bones[0]
    chains: dict[str, list[str]] = {"body": [root.name]}

    def _linear_until_branch(start):
        """Segue cadeia linear ate ramificacao (>=2 filhos) ou ponta."""
        chain = [start.name]
        cursor = start
        while len(cursor.children) == 1:
            cursor = cursor.children[0]
            chain.append(cursor.name)
        return chain, cursor

    def _collect_all(start) -> list[str]:
        out = [start.name]
        for c in start.children:
            out.extend(_collect_all(c))
        return out

    def _find_first_hub(start):
        """Encontra o primeiro hub (>=2 filhos) descendo pela cadeia."""
        cursor = start
        path = []
        while cursor:
            path.append(cursor)
            if len(cursor.children) >= 2:
                return path, cursor
            if len(cursor.children) == 1:
                cursor = cursor.children[0]
            else:
                break
        return path, None

    def _bone_name_suggests_leg(pb) -> bool:
        n = pb.name.lower()
        return any(
            s in n
            for s in (
                "leg",
                "foot",
                "thigh",
                "knee",
                "ankle",
                "toe",
                "calf",
                "shin",
                "pata",
                "femur",
                "tibia",
            )
        )

    def _bone_name_suggests_wing(pb) -> bool:
        n = pb.name.lower()
        return any(s in n for s in ("wing", "feather", "alula", "patag"))

    def _lateral_branch_is_leg(sc, hub_z: float) -> bool:
        """Pernas costumam estar abaixo do hub; asas ligam-se ao dorso (z semelhante ou maior)."""
        if _bone_name_suggests_wing(sc):
            return False
        if _bone_name_suggests_leg(sc):
            return True
        hz = sc.bone.head_local.z
        # Margem generosa: pernas penduradas / anca abaixo do tronco no espaco local.
        return hz < hub_z - 0.04

    def _merge_leg_chain(side: str, sc) -> None:
        key = "leg_r" if side == "r" else "leg_l"
        sub = _collect_all(sc)
        if key in chains:
            chains[key].extend(sub)
        else:
            chains[key] = sub

    def _merge_arm_chain(side: str, sc) -> None:
        key = "arm_r" if side == "r" else "arm_l"
        sub = _collect_all(sc)
        if key in chains:
            chains[key].extend(sub)
        else:
            chains[key] = sub

    def _classify_hub_children(hub, hub_path_names: list[str]):
        """Classifica filhos de um hub por posicao espacial.

        When legs were already detected at root level (humanoid layout),
        lateral branches at the upper hub are classified as arms, not wings.
        """
        _has_legs = "leg_r" in chains or "leg_l" in chains
        kids = list(hub.children)
        center_z = hub.bone.head_local.z

        # Sort by |x| so we can pick the most central child as head/neck.
        by_abs_x = sorted(kids, key=lambda c: abs(c.bone.head_local.x))
        head_candidate = by_abs_x[0] if by_abs_x else None

        for sc in kids:
            sx = sc.bone.head_local.x
            sz = sc.bone.head_local.z

            if sc is head_candidate and abs(sx) < 0.12:
                if sz > center_z - 0.05:
                    neck_linear, _neck_tip = _linear_until_branch(sc)
                    chains["neck"] = neck_linear
                    continue
                else:
                    tail_chain, tail_hub = _linear_until_branch(sc)
                    chains["tail"] = tail_chain
                    if tail_hub and len(tail_hub.children) >= 2:
                        _classify_tail_hub(tail_hub)
                    continue

            if abs(sx) < 0.12 and sz <= center_z:
                tail_chain, tail_hub = _linear_until_branch(sc)
                chains.setdefault("tail", tail_chain)
                if tail_hub and len(tail_hub.children) >= 2:
                    _classify_tail_hub(tail_hub)
            elif sx > 0.03:
                if _lateral_branch_is_leg(sc, center_z):
                    _merge_leg_chain("r", sc)
                elif _has_legs:
                    _merge_arm_chain("r", sc)
                else:
                    _classify_wing(sc, "wing_r")
            elif sx < -0.03:
                if _lateral_branch_is_leg(sc, center_z):
                    _merge_leg_chain("l", sc)
                elif _has_legs:
                    _merge_arm_chain("l", sc)
                else:
                    _classify_wing(sc, "wing_l")

    def _classify_wing(start, key: str):
        """Separa asa em tronco principal (3-4 ossos) e dedos."""
        main_chain, tip = _linear_until_branch(start)
        chains[key] = main_chain
        if tip and len(tip.children) >= 2:
            fingers = []
            for fc in tip.children:
                fingers.extend(_collect_all(fc))
            chains[f"{key}_fingers"] = fingers

    def _classify_tail_hub(hub):
        """Classifica filhos do hub da cauda (patas tipicamente)."""
        for sc in hub.children:
            sx = sc.bone.head_local.x
            if abs(sx) > 0.05:
                _merge_leg_chain("r" if sx > 0 else "l", sc)

    # Procurar primeiro hub a partir da raiz
    rz = root.bone.head_local.z
    root_kids = list(root.children)

    # Humanoid heuristic: when root has 3+ children and two form a left/right
    # pair (symmetric about x≈0) with the third being roughly centered, the
    # lateral pair are legs even if |x| < 0.15.
    spine_candidate, lateral_pairs = _identify_root_layout(root_kids)

    for sc, side in lateral_pairs:
        _merge_leg_chain(side, sc)

    for child in root_kids:
        if any(child is sc for sc, _ in lateral_pairs):
            continue

        cx = child.bone.head_local.x
        if abs(cx) > 0.15:
            if _lateral_branch_is_leg(child, rz):
                _merge_leg_chain("r" if cx > 0 else "l", child)
            else:
                _classify_wing(child, "wing_r" if cx > 0 else "wing_l")
            continue

        path_to_hub, hub = _find_first_hub(child)
        spine_names = [b.name for b in path_to_hub]

        if hub and len(hub.children) >= 2:
            chains["spine"] = spine_names
            _classify_hub_children(hub, spine_names)
        else:
            chains.setdefault("spine", spine_names)

    return chains


def rename_bones_from_chains(armature_name: str) -> dict[str, list[str]]:
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.type != "ARMATURE":
        return {}

    chains = _classify_bone_chains(armature_name)
    if not chains:
        return {}

    _CHAIN_NAMES: dict[str, list[str]] = {
        "body": ["Hips"],
        "spine": ["Spine", "Spine1", "Spine2", "Spine3", "Spine4"],
        "neck": ["Neck", "Neck1", "Neck2"],
        "head": ["Head"],
        "leg_r": ["RightUpLeg", "RightLeg", "RightFoot", "RightToeBase"],
        "leg_l": ["LeftUpLeg", "LeftLeg", "LeftFoot", "LeftToeBase"],
        "arm_r": ["RightShoulder", "RightArm", "RightForeArm", "RightHand"],
        "arm_l": ["LeftShoulder", "LeftArm", "LeftForeArm", "LeftHand"],
        "tail": ["Tail", "Tail1", "Tail2", "Tail3", "Tail4", "Tail5", "Tail6", "Tail7"],
        "wing_r": ["RightWing", "RightWing1", "RightWing2", "RightWing3"],
        "wing_l": ["LeftWing", "LeftWing1", "LeftWing2", "LeftWing3"],
        "wing_r_fingers": ["RightWingFinger1", "RightWingFinger2", "RightWingFinger3"],
        "wing_l_fingers": ["LeftWingFinger1", "LeftWingFinger2", "LeftWingFinger3"],
    }

    renamed: dict[str, str] = {}

    for chain_key, bone_names in chains.items():
        name_pool = _CHAIN_NAMES.get(chain_key, [])
        for ci, old_name in enumerate(bone_names):
            new_name = name_pool[ci] if ci < len(name_pool) else f"{name_pool[-1]}_{ci}"
            bone = arm.pose.bones.get(old_name) if old_name else None
            if bone is None:
                continue
            if old_name.startswith("bone_"):
                bone.name = new_name
                renamed[old_name] = new_name

    if renamed:
        updated_chains: dict[str, list[str]] = {}
        for chain_key, bone_names in chains.items():
            updated_chains[chain_key] = [renamed.get(b, b) for b in bone_names]
        return updated_chains

    return chains


# ---------------------------------------------------------------------------
# Generic rig-aware rotation axes
#
# Procedural clips used to write rotations on hardcoded local euler indices
# (X=hip/body, Z=knee, Y=wings). That only works if every bone happens to share
# the same local orientation — which auto-rigs (UniRig) do not. The result was
# knees/arms bending on the wrong axis ("strange" joints).
#
# Instead we derive, per bone, which *local* euler component rotates the bone
# about the desired *world* axis, plus the sign. The "swing" axis (forward/back
# pendulum, and joint flexion) is `bonedir × forward`: for a leg pointing down
# this is the mediolateral axis, but for an arm pointing sideways it is the
# vertical axis — exactly what natural swing requires. "yaw" is rotation about
# world up (used for turning / spine twist).
# ---------------------------------------------------------------------------

_WORLD_UP = (0.0, 0.0, 1.0)


def _detect_forward(arm_obj: Any, chains: dict[str, list[str]]):
    """Horizontal forward direction of the rig (toes point forward). Falls back
    to -Y (Blender front), which is also the glTF-import convention."""
    from mathutils import Vector

    fwd = Vector((0.0, 0.0, 0.0))
    for key in ("leg_r", "leg_l"):
        names = chains.get(key, [])
        if len(names) >= 3:
            foot = arm_obj.data.bones.get(names[2])
            if foot is not None:
                d = foot.tail_local - foot.head_local
                d.z = 0.0
                if d.length > 1e-4:
                    fwd += d.normalized()
    if fwd.length < 1e-4:
        return Vector((0.0, -1.0, 0.0))
    return fwd.normalized()


def _resolve_bone_axes(arm_obj: Any, bone_name: str, forward: Any) -> dict | None:
    """Map world swing/yaw rotations to this bone's local euler index + sign."""
    from mathutils import Vector

    b = arm_obj.data.bones.get(bone_name)
    if b is None:
        return None
    rot = b.matrix_local.to_3x3()
    cols = [rot.col[0], rot.col[1], rot.col[2]]

    bonedir = b.tail_local - b.head_local
    if bonedir.length < 1e-6:
        bonedir = cols[1].copy()
    bonedir = bonedir.normalized()

    swing = bonedir.cross(forward)
    if swing.length < 1e-4:
        swing = Vector((1.0, 0.0, 0.0))
    swing = swing.normalized()

    def best(target) -> tuple[int, float]:
        dots = [cols[i].dot(target) for i in range(3)]
        k = max(range(3), key=lambda i: abs(dots[i]))
        return k, (1.0 if dots[k] >= 0.0 else -1.0)

    # swing: pendulum/flexion (bonedir × forward). medio: rotation about the
    # world mediolateral (X) axis — the real forward/back swing for a limb that
    # has been brought down to the side. lift: rotation about the world forward
    # axis — raises/lowers a limb in the frontal plane (arm adduction). yaw:
    # rotation about world up (turn/twist).
    return {
        "swing": best(swing),
        "medio": best(Vector((1.0, 0.0, 0.0))),
        "lift": best(Vector((0.0, 1.0, 0.0))),
        "yaw": best(Vector(_WORLD_UP)),
    }


def _build_axes_map(
    arm_obj: Any, chains: dict[str, list[str]], forward: Any, keys: tuple[str, ...]
) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for key in keys:
        for bn in chains.get(key, []):
            ax = _resolve_bone_axes(arm_obj, bn, forward)
            if ax is not None:
                out[bn] = ax
    return out


def _key_humanoid_bone(
    pb: Any,
    axes: dict,
    frame: int,
    *,
    swing: float = 0.0,
    bend: float = 0.0,
    yaw: float = 0.0,
    medio: float = 0.0,
    lift: float = 0.0,
) -> None:
    """Set a bone's pose for one frame from a zero baseline (deterministic, no
    residual cross-talk). `swing` = forward(+)/back(-) about the bone's natural
    pendulum axis; `bend` = flexion(+, tail back); `medio` = forward/back about
    the world mediolateral axis (arm pendulum once lowered); `lift` = raise(+)/
    lower(-) in the frontal plane (arm adduction); `yaw` = turn about world up."""
    e = [0.0, 0.0, 0.0]
    si, ss = axes["swing"]
    e[si] += ss * (swing - bend)
    if medio:
        mi, ms = axes["medio"]
        e[mi] += ms * medio
    if lift:
        li, ls = axes["lift"]
        e[li] += ls * lift
    yi, ys = axes["yaw"]
    e[yi] += ys * yaw
    _set_pose_bone_rotation(pb, (e[0], e[1], e[2]))
    pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)


def _locomotion_cycle(
    arm_obj: Any,
    chains: dict[str, list[str]],
    forward: Any,
    *,
    frame_start: int,
    total: int,
    cycles: float,
    hip_amp: float,
    knee_swing: float,
    knee_stance: float,
    ankle_amp: float,
    arm_amp: float,
    arm_decay: float,
    arm_adduct: float,
    body_lean: float,
    spine_twist: float,
    body_bob: float = 0.0,
) -> None:
    """Shared walk/run engine: anatomically-correct hip swing + knee flexion +
    contralateral arm swing, all on rig-resolved axes."""
    import math

    bpy = _bpy()
    axes = _build_axes_map(
        arm_obj, chains, forward,
        ("body", "spine", "neck", "leg_r", "leg_l", "arm_r", "arm_l"),
    )
    two_pi = math.pi * 2.0

    def hip_swing(phi: float) -> float:
        # +forward at heel-strike (phi=0), -back at toe-off (phi=0.5).
        return hip_amp * math.cos(phi * two_pi)

    def knee_flex(phi: float) -> float:
        # Slight cushion in stance, large flexion mid-swing to clear the ground.
        if phi < 0.5:
            return knee_stance * (0.5 - 0.5 * math.cos(phi / 0.5 * two_pi))
        w = (phi - 0.5) / 0.5
        return knee_swing * math.sin(w * math.pi)

    def anim_leg(names: list[str], phase: float) -> None:
        for ci, bn in enumerate(names):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb is None or ax is None:
                continue
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                phi = (t * cycles + phase) % 1.0
                bpy.context.scene.frame_set(frame)
                if ci == 0:  # hip
                    _key_humanoid_bone(pb, ax, frame, swing=hip_swing(phi))
                elif ci == 1:  # knee
                    _key_humanoid_bone(
                        pb, ax, frame,
                        bend=knee_flex(phi), swing=hip_swing(phi) * 0.12,
                    )
                elif ci == 2:  # ankle/foot: counter-rotate to stay roughly flat
                    _key_humanoid_bone(
                        pb, ax, frame, swing=-ankle_amp * math.cos(phi * two_pi)
                    )

    def anim_arm(names: list[str], phase: float, side: float) -> None:
        # Bring the arm down from the A-pose to the side (constant adduction via
        # the frontal axis), then swing it forward/back about the world
        # mediolateral axis — an arm left pointing sideways can't pendulum.
        for ci, bn in enumerate(names[:3]):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb is None or ax is None:
                continue
            scale = max(0.3, 1.0 - ci * arm_decay)
            adduct = arm_adduct if ci < 2 else arm_adduct * 0.4
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                phi = (t * cycles + phase) % 1.0
                bpy.context.scene.frame_set(frame)
                _key_humanoid_bone(
                    pb, ax, frame,
                    medio=arm_amp * scale * math.cos(phi * two_pi),
                    lift=side * adduct,
                )

    def anim_spine() -> None:
        for ci, bn in enumerate(chains.get("spine", [])):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb is None or ax is None:
                continue
            scale = max(0.3, 1.0 - ci * 0.2)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                phi = (t * cycles) % 1.0
                bpy.context.scene.frame_set(frame)
                _key_humanoid_bone(
                    pb, ax, frame,
                    swing=body_lean * scale,
                    yaw=spine_twist * scale * math.sin(phi * two_pi),
                )

    # Hips: gentle forward lean + a real vertical bob (translate the root bone
    # along world up). Two dips per stride, lowest at foot contact.
    for bn in chains.get("body", []):
        pb = arm_obj.pose.bones.get(bn)
        ax = axes.get(bn)
        if pb is None or ax is None:
            continue
        ui, us = ax["yaw"]  # local axis aligned with world up
        for fi in range(total):
            t = fi / max(total - 1, 1)
            frame = frame_start + fi
            phi = (t * cycles) % 1.0
            bpy.context.scene.frame_set(frame)
            _key_humanoid_bone(
                pb, ax, frame,
                swing=body_lean * 0.5 + body_lean * 0.2 * math.cos(phi * two_pi * 2),
            )
            if body_bob > 0.0:
                loc = [0.0, 0.0, 0.0]
                loc[ui] = us * (-body_bob * math.cos(phi * two_pi * 2.0))
                pb.location = (loc[0], loc[1], loc[2])
                pb.keyframe_insert(data_path="location", frame=frame)

    anim_spine()
    # Legs half a cycle out of phase.
    anim_leg(chains.get("leg_r", []), phase=0.0)
    anim_leg(chains.get("leg_l", []), phase=0.5)
    # Arms swing opposite the same-side leg (contralateral). `side` lowers each
    # arm to its own side: arm_r points +X (adduct about +Y), arm_l points -X.
    anim_arm(chains.get("arm_r", []), phase=0.5, side=1.0)
    anim_arm(chains.get("arm_l", []), phase=0.0, side=-1.0)


def _legacy_secondary_motion(
    arm_obj: Any,
    chains: dict[str, list[str]],
    *,
    frame_start: int,
    total: int,
    cycles: float,
    wing_amp: float,
    tail_amp: float,
) -> None:
    """Sinusoidal flap/sway for non-humanoid chains (wings, tail). Humanoid hero
    rigs have none of these, so this is a no-op for the player."""
    import math

    bpy = _bpy()

    def _sway(bone_names: list[str], axis: int, amp: float, *, phase_step: float, decay: float):
        for ci, bname in enumerate(bone_names):
            pb = arm_obj.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.25, 1.0 - ci * decay) if decay > 0 else 1.0
            phase = ci * phase_step
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                angle = amp * scale * math.sin(t * math.pi * 2 * cycles + phase)
                bpy.context.scene.frame_set(frame)
                euler = list(pb.rotation_euler)
                euler[axis] = angle
                _set_pose_bone_rotation(pb, tuple(euler))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    if "wing_r" in chains:
        _sway(chains["wing_r"], 1, wing_amp, phase_step=0.25, decay=0.15)
    if "wing_l" in chains:
        _sway(chains["wing_l"], 1, -wing_amp, phase_step=0.25, decay=0.15)
    if "tail" in chains:
        _sway(chains["tail"], 2, tail_amp, phase_step=0.4, decay=0.1)


def turn_in_place_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 32,
    direction: float = 1.0,
    turn_amp: float = 0.45,
    step_amp: float = 0.06,
    action_name: str = "Animator3D_Turn",
) -> dict[str, list[str]]:
    """Pivot/lean toward `direction` (+1 left / -1 right): mostly torso + head
    twist with a light foot shuffle. Loops back to neutral, so it reads as a
    turn-in-place on its own and as a curve-lean when blended additively over a
    walk. The engine applies the actual heading change."""
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = _detect_forward(arm_obj, chains)
    axes = _build_axes_map(
        arm_obj, chains, forward,
        ("body", "spine", "neck", "leg_r", "leg_l", "arm_r", "arm_l"),
    )
    two_pi = math.pi * 2.0
    d = 1.0 if direction >= 0 else -1.0

    def twist(t: float) -> float:
        return d * turn_amp * math.sin(t * two_pi)

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)
        body_yaw = twist(t)

        for bn in chains.get("body", []):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb and ax:
                _key_humanoid_bone(pb, ax, frame, yaw=body_yaw * 0.5)

        for ci, bn in enumerate(chains.get("spine", [])):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb and ax:
                scale = max(0.3, 1.0 - ci * 0.18)
                _key_humanoid_bone(pb, ax, frame, yaw=body_yaw * scale)

        for ci, bn in enumerate(chains.get("neck", [])):
            pb = arm_obj.pose.bones.get(bn)
            ax = axes.get(bn)
            if pb and ax:
                _key_humanoid_bone(pb, ax, frame, yaw=body_yaw * 0.4)

        # Feet shuffle: the leg on the turn side steps, the other pivots.
        step = step_amp * math.sin(t * two_pi)
        for leg_key, sgn in (("leg_r", 1.0), ("leg_l", -1.0)):
            names = chains.get(leg_key, [])
            for ci, bn in enumerate(names):
                pb = arm_obj.pose.bones.get(bn)
                ax = axes.get(bn)
                if pb is None or ax is None:
                    continue
                if ci == 0:
                    _key_humanoid_bone(pb, ax, frame, swing=step * sgn * d)
                elif ci == 1:
                    lift = max(0.0, step * sgn * d)
                    _key_humanoid_bone(pb, ax, frame, bend=lift * 0.8)

        # Arms counter-swing slightly for balance.
        for arm_key, sgn in (("arm_r", -1.0), ("arm_l", 1.0)):
            for ci, bn in enumerate(chains.get(arm_key, [])):
                pb = arm_obj.pose.bones.get(bn)
                ax = axes.get(bn)
                if pb is None or ax is None:
                    continue
                scale = max(0.3, 1.0 - ci * 0.1)
                _key_humanoid_bone(
                    pb, ax, frame, swing=0.12 * scale * math.sin(t * two_pi) * sgn
                )

    finalize_current_action_to_nla(armature_name)
    return chains


def breathe_idle_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 120,
    breath_amp: float = 0.04,
    wing_amp: float = 0.18,
    tail_amp: float = 0.12,
    neck_amp: float = 0.08,
    cycles: float = 2.0,
    action_name: str = "Animator3D_BreatheIdle",
) -> dict[str, list[str]]:
    """Animacao idle multi-osso: respiracao, asas, cauda, pescoco.

    Amplitudes moderadas; decaimento por profundidade para evitar cascata.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _anim_chain(
        bone_names: list[str],
        axis: int,
        amp: float,
        freq: float,
        phase_step: float = 0.4,
        secondary_axis: int | None = None,
        secondary_amp: float = 0.0,
        max_bones: int | None = None,
        decay: float = 0.0,
    ):
        names = bone_names[:max_bones] if max_bones else bone_names
        for ci, bname in enumerate(names):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.2, 1.0 - ci * decay) if decay > 0 else 1.0
            phase = ci * phase_step
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                angle = amp * scale * math.sin(t * math.pi * 2 * freq + phase)
                bpy.context.scene.frame_set(frame)
                euler = list(pb.rotation_euler)
                euler[axis] = angle
                if secondary_axis is not None:
                    euler[secondary_axis] = secondary_amp * scale * math.sin(t * math.pi * 2 * freq * 0.5 + phase + 0.7)
                _set_pose_bone_rotation(pb, tuple(euler))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    if "body" in chains:
        _anim_chain(chains["body"], axis=0, amp=breath_amp, freq=cycles)

    if "spine" in chains:
        _anim_chain(
            chains["spine"],
            axis=0,
            amp=breath_amp * 0.7,
            freq=cycles,
            phase_step=0.2,
            secondary_axis=2,
            secondary_amp=breath_amp * 0.3,
        )

    if "tail" in chains:
        _anim_chain(
            chains["tail"],
            axis=2,
            amp=tail_amp,
            freq=cycles,
            phase_step=0.5,
            secondary_axis=0,
            secondary_amp=tail_amp * 0.2,
            decay=0.1,
        )

    if "neck" in chains:
        _anim_chain(
            chains["neck"],
            axis=0,
            amp=neck_amp,
            freq=cycles * 0.8,
            phase_step=0.25,
            secondary_axis=2,
            secondary_amp=neck_amp * 0.3,
            decay=0.15,
        )

    if "arm_r" in chains:
        _anim_chain(chains["arm_r"], axis=0, amp=breath_amp * 0.5, freq=cycles, phase_step=0.2, decay=0.15)
    if "arm_l" in chains:
        _anim_chain(chains["arm_l"], axis=0, amp=breath_amp * 0.5, freq=cycles, phase_step=0.2, decay=0.15)

    if "wing_r" in chains:
        _anim_chain(
            chains["wing_r"],
            axis=1,
            amp=wing_amp,
            freq=cycles,
            phase_step=0.3,
            decay=0.2,
        )
    if "wing_l" in chains:
        _anim_chain(
            chains["wing_l"],
            axis=1,
            amp=-wing_amp,
            freq=cycles,
            phase_step=0.3,
            decay=0.2,
        )

    # Dedos das asas: movimento muito subtil
    for fk in ("wing_r_fingers", "wing_l_fingers"):
        if fk in chains:
            sign = 1.0 if "r" in fk else -1.0
            _anim_chain(
                chains[fk],
                axis=1,
                amp=sign * wing_amp * 0.08,
                freq=cycles * 1.5,
                phase_step=0.15,
                decay=0.3,
                max_bones=6,
            )

    # Patas ficam paradas no chao (idle = pousado).

    finalize_current_action_to_nla(armature_name)
    return chains


def _smoothstep01(x: float) -> float:
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def _attack_strike_profile(t: float) -> float:
    """Um golpe: recuar ligeiro -> investida -> pico -> recuperacao. t em [0,1]."""
    if t < 0.15:
        return -0.1 * _smoothstep01(t / 0.15)
    if t < 0.42:
        w = (t - 0.15) / 0.27
        return -0.1 + _smoothstep01(w) * 1.1
    if t < 0.58:
        return 1.0
    w = (t - 0.58) / 0.42
    return 1.0 - _smoothstep01(w)


def attack_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 72,
    strikes: int = 1,
    body_amp: float = 0.22,
    spine_amp: float = 0.38,
    neck_amp: float = 0.55,
    wing_amp: float = 0.62,
    tail_amp: float = 0.42,
    finger_amp: float = 0.28,
    arm_amp: float = 1.45,
    action_name: str = "Animator3D_Attack",
) -> dict[str, list[str]]:
    """Animacao de ataque. Humanoides: golpe de braco (investida do tronco +
    balanco do braco direito, braco esquerdo a contrabalancar). Criaturas:
    mordida/investida com pescoco, asas e cauda.

    Patas sem keyframes. `strikes` repetem o perfil no intervalo de frames.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    strikes = max(1, int(strikes))
    forward = _detect_forward(arm, chains)
    axes = _build_axes_map(arm, chains, forward, ("arm_r", "arm_l"))

    def _strike_swing(chain_key: str, amp: float, sign: float, decay: float) -> None:
        """Forward arm swing through the strike profile on rig-resolved axes."""
        for ci, bname in enumerate(chains.get(chain_key, [])):
            pb = arm.pose.bones.get(bname)
            ax = axes.get(bname)
            if pb is None or ax is None:
                continue
            scale = max(0.25, 1.0 - ci * decay) if decay > 0 else 1.0
            for fi in range(total):
                td = max(0.0, min(1.0, _strike_t(fi)))
                prof = _attack_strike_profile(td)
                frame = frame_start + fi
                bpy.context.scene.frame_set(frame)
                _key_humanoid_bone(pb, ax, frame, swing=sign * amp * scale * prof)

    def _strike_t(fi: int) -> float:
        u = fi / max(total - 1, 1)
        return (u * strikes) % 1.0

    def _key_bone_rot(
        bone_name: str,
        frame: int,
        euler: tuple[float, float, float],
    ) -> None:
        pb = arm.pose.bones.get(bone_name)
        if pb is None:
            return
        bpy.context.scene.frame_set(frame)
        _set_pose_bone_rotation(pb, euler)
        pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    def _anim_chain_strike(
        bone_names: list[str],
        axis: int,
        amp: float,
        *,
        sign: float = 1.0,
        decay: float = 0.12,
        phase_delay: float = 0.0,
        secondary_axis: int | None = None,
        secondary_scale: float = 0.0,
        max_bones: int | None = None,
    ):
        names = bone_names[:max_bones] if max_bones else bone_names
        for ci, bname in enumerate(names):
            pb0 = arm.pose.bones.get(bname)
            if pb0 is None:
                continue
            bpy.context.scene.frame_set(frame_start)
            base = list(pb0.rotation_euler)
            scale = max(0.25, 1.0 - ci * decay) if decay > 0 else 1.0
            for fi in range(total):
                t_raw = _strike_t(fi)
                td = max(0.0, min(1.0, t_raw - phase_delay))
                prof = _attack_strike_profile(td)
                frame = frame_start + fi
                euler = list(base)
                euler[axis] = base[axis] + sign * amp * scale * prof
                if secondary_axis is not None and secondary_scale != 0.0:
                    euler[secondary_axis] = base[secondary_axis] + secondary_scale * scale * prof * math.sin(
                        max(prof, 0.0) * math.pi
                    )
                _key_bone_rot(bname, frame, tuple(euler))

    if "body" in chains:
        _anim_chain_strike(chains["body"], axis=0, amp=body_amp, decay=0.0)

    if "spine" in chains:
        _anim_chain_strike(
            chains["spine"],
            axis=0,
            amp=spine_amp,
            decay=0.08,
            secondary_axis=2,
            secondary_scale=spine_amp * 0.15,
        )

    if "neck" in chains:
        _anim_chain_strike(
            chains["neck"],
            axis=0,
            amp=neck_amp,
            decay=0.1,
            secondary_axis=2,
            secondary_scale=neck_amp * 0.2,
        )

    if "tail" in chains:
        _anim_chain_strike(
            chains["tail"],
            axis=2,
            amp=-tail_amp,
            decay=0.08,
            phase_delay=0.06,
        )

    # Humanoid melee: the lead arm whips forward through the strike (rig-resolved
    # swing axis), the other arm gives a milder counter for balance. Decay along
    # the chain so the shoulder leads and the hand follows through. (No-op on
    # rigs without arms, e.g. dragons.)
    if "arm_r" in chains:
        _strike_swing("arm_r", amp=arm_amp, sign=1.0, decay=0.05)
    if "arm_l" in chains:
        _strike_swing("arm_l", amp=arm_amp * 0.35, sign=0.5, decay=0.1)

    if "wing_r" in chains:
        _anim_chain_strike(chains["wing_r"], axis=1, amp=wing_amp, decay=0.15)
    if "wing_l" in chains:
        _anim_chain_strike(chains["wing_l"], axis=1, amp=-wing_amp, decay=0.15)

    for fk in ("wing_r_fingers", "wing_l_fingers"):
        if fk in chains:
            sgn = 1.0 if "r" in fk else -1.0
            _anim_chain_strike(
                chains[fk],
                axis=1,
                amp=sgn * finger_amp,
                decay=0.2,
                max_bones=8,
            )

    finalize_current_action_to_nla(armature_name)
    return chains


def walk_cycle_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 48,
    cycles: float = 2.0,
    body_amp: float = 0.05,
    leg_amp: float = 0.14,
    wing_amp: float = 0.06,
    tail_amp: float = 0.1,
    action_name: str = "Animator3D_Walk",
) -> dict[str, list[str]]:
    """Ciclo de caminhada: hip swing + flexao de joelho anatomica em eixos
    resolvidos do rig, com balanco contralateral de bracos."""
    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = _detect_forward(arm, chains)

    _locomotion_cycle(
        arm, chains, forward,
        frame_start=frame_start, total=total, cycles=cycles,
        hip_amp=leg_amp * 3.2,      # ~26° hip swing (was ~8°, looked shuffly)
        knee_swing=leg_amp * 6.0,   # ~48° flexion clearing the ground in swing
        knee_stance=leg_amp * 0.7,
        ankle_amp=leg_amp * 1.2,
        arm_amp=leg_amp * 3.5,      # ~28° forward/back arm pendulum
        arm_decay=0.35,
        arm_adduct=0.6,             # bring arms down ~34° from the A-pose
        body_lean=body_amp * 1.5,
        spine_twist=body_amp * 1.5,
        body_bob=0.035,
    )

    # Non-humanoid chains keep the legacy sinusoidal flap/sway.
    _legacy_secondary_motion(
        arm, chains, frame_start=frame_start, total=total, cycles=cycles,
        wing_amp=wing_amp, tail_amp=tail_amp,
    )

    finalize_current_action_to_nla(armature_name)
    return chains


def hover_flap_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 60,
    cycles: float = 3.5,
    wing_amp: float = 0.38,
    body_amp: float = 0.07,
    tail_amp: float = 0.08,
    finger_amp: float = 0.12,
    action_name: str = "Animator3D_Hover",
) -> dict[str, list[str]]:
    """Pairar / batimento de asas: frequência alta, tronco estável, cauda leve."""
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _anim_chain(
        bone_names: list[str],
        axis: int,
        amp: float,
        freq: float,
        *,
        phase_step: float = 0.3,
        secondary_axis: int | None = None,
        secondary_amp: float = 0.0,
        decay: float = 0.0,
        max_bones: int | None = None,
    ):
        names = bone_names[:max_bones] if max_bones else bone_names
        for ci, bname in enumerate(names):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.2, 1.0 - ci * decay) if decay > 0 else 1.0
            phase = ci * phase_step
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                angle = amp * scale * math.sin(t * math.pi * 2 * freq + phase)
                bpy.context.scene.frame_set(frame)
                euler = list(pb.rotation_euler)
                euler[axis] = angle
                if secondary_axis is not None:
                    euler[secondary_axis] = secondary_amp * scale * math.sin(t * math.pi * 2 * freq * 0.5 + phase)
                _set_pose_bone_rotation(pb, tuple(euler))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    if "body" in chains:
        _anim_chain(chains["body"], axis=0, amp=body_amp, freq=cycles * 0.35)

    if "spine" in chains:
        _anim_chain(
            chains["spine"],
            axis=0,
            amp=body_amp * 0.6,
            freq=cycles * 0.35,
            phase_step=0.15,
            secondary_axis=2,
            secondary_amp=body_amp * 0.2,
        )

    if "neck" in chains:
        _anim_chain(
            chains["neck"],
            axis=0,
            amp=body_amp * 0.4,
            freq=cycles * 0.3,
            phase_step=0.2,
            decay=0.12,
        )

    if "tail" in chains:
        _anim_chain(
            chains["tail"],
            axis=2,
            amp=tail_amp,
            freq=cycles * 0.45,
            phase_step=0.45,
            decay=0.08,
        )

    if "wing_r" in chains:
        _anim_chain(chains["wing_r"], axis=1, amp=wing_amp, freq=cycles, phase_step=0.22, decay=0.12)
    if "wing_l" in chains:
        _anim_chain(
            chains["wing_l"],
            axis=1,
            amp=-wing_amp,
            freq=cycles,
            phase_step=0.22,
            decay=0.12,
        )

    for fk in ("wing_r_fingers", "wing_l_fingers"):
        if fk in chains:
            sgn = 1.0 if "r" in fk else -1.0
            _anim_chain(
                chains[fk],
                axis=1,
                amp=sgn * finger_amp,
                freq=cycles * 1.1,
                phase_step=0.12,
                decay=0.25,
                max_bones=8,
            )

    finalize_current_action_to_nla(armature_name)
    return chains


def soar_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 90,
    cycles: float = 1.5,
    action_name: str = "Animator3D_Soar",
) -> dict[str, list[str]]:
    """Dragão planar em altitude com asas estendidas, batidas largas e lentas.

    As asas movem-se em ondas longas e desiguais, cauda como leme, corpo suave.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _anim_chain(
        bone_names: list[str],
        axis: int,
        amp: float,
        freq: float,
        *,
        phase_step: float = 0.3,
        phase_global: float = 0.0,
        secondary_axis: int | None = None,
        secondary_amp: float = 0.0,
        decay: float = 0.0,
        max_bones: int | None = None,
    ):
        names = bone_names[:max_bones] if max_bones else bone_names
        for ci, bname in enumerate(names):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.3, 1.0 - ci * decay) if decay > 0 else 1.0
            phase = ci * phase_step + phase_global
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                # Batida larga com pausa no topo (usando seno^2 para simular)
                sine_val = math.sin(t * math.pi * 2 * freq + phase)
                angle = amp * scale * (sine_val * sine_val) * math.copysign(1, sine_val)
                bpy.context.scene.frame_set(frame)
                euler = list(pb.rotation_euler)
                euler[axis] = angle
                if secondary_axis is not None and secondary_amp != 0.0:
                    euler[secondary_axis] = secondary_amp * scale * math.sin(t * math.pi * 2 * freq * 0.7 + phase + 1.0)
                _set_pose_bone_rotation(pb, tuple(euler))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Asas com batidas desiguais (assimétricas)
    if "wing_r" in chains:
        _anim_chain(chains["wing_r"], axis=1, amp=0.45, freq=cycles * 0.6, phase_step=0.15, phase_global=0.0, decay=0.1)
    if "wing_l" in chains:
        _anim_chain(
            chains["wing_l"], axis=1, amp=-0.38, freq=cycles * 0.55, phase_step=0.18, phase_global=0.4, decay=0.1
        )

    # Cauda como leme - movimentos largos e lentos
    if "tail" in chains:
        _anim_chain(
            chains["tail"],
            axis=2,
            amp=0.25,
            freq=cycles * 0.4,
            phase_step=0.5,
            decay=0.08,
            secondary_axis=0,
            secondary_amp=0.15,
        )

    # Corpo com leve rolagem e arfagem
    if "body" in chains:
        _anim_chain(chains["body"], axis=0, amp=0.06, freq=cycles * 0.5, phase_global=0.2)

    # Pescoço estabilizando
    if "neck" in chains:
        _anim_chain(chains["neck"], axis=0, amp=0.04, freq=cycles * 0.6, phase_step=0.2, decay=0.1)

    finalize_current_action_to_nla(armature_name)
    return chains


def dive_attack_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 48,
    action_name: str = "Animator3D_DiveAttack",
) -> dict[str, list[str]]:
    """Ataque em picada: dragão mergulha com asas recolhidas, depois abre para impacto.

    Fases: preparação (asas recolhidas) → picada (corpo inclinado) → impacto (asas abertas bruscamente).
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _profile(t: float) -> tuple[float, float, float]:
        """Retorna (inclinação_corpo, abertura_asas, extensão_cauda) em função do tempo [0,1]."""
        if t < 0.2:
            # Preparação: recolhe asas, inclina para frente
            return (
                _smoothstep01(t / 0.2) * 0.8,  # corpo inclinado
                -0.6 * _smoothstep01(t / 0.2),  # asas recolhidas (negativo)
                0.3 * _smoothstep01(t / 0.2),  # cauda estendida para cima
            )
        elif t < 0.7:
            # Picada: corpo em 45 graus, asas coladas
            return (0.8, -0.6, 0.3)
        else:
            # Impacto: abre asas bruscamente para frear, cauda abaixa
            w = (t - 0.7) / 0.3
            return (
                0.8 - _smoothstep01(w) * 0.5,  # corpo volta
                -0.6 + _smoothstep01(w) * 1.2,  # asas abrem de -0.6 para 0.6
                0.3 - _smoothstep01(w) * 0.5,  # cauda abaixa
            )

    # Anima asas assimétricas (direita lidera)
    wing_names = [("wing_r", "wing_r_fingers", 1.0), ("wing_l", "wing_l_fingers", -1.0)]

    for base_name, fingers_name, side_sign in wing_names:
        if base_name not in chains:
            continue

        base_bones = chains[base_name]
        phase_offset = 0.15 if side_sign > 0 else 0.0

        for ci, bname in enumerate(base_bones[:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            decay = 0.15
            scale = max(0.4, 1.0 - ci * decay)

            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                incl, wing_open, _ = _profile(t)

                # Asas recolhidas ou abertas com pequena oscilação
                phase = ci * 0.1 + phase_offset
                flutter = 0.05 * math.sin(t * math.pi * 6 + phase) if wing_open > 0 else 0.0

                bpy.context.scene.frame_set(frame)
                angle = side_sign * scale * (wing_open + flutter)
                _set_pose_bone_rotation(pb, (0.0, angle, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Corpo - inclinação em X (mergulho)
    if "body" in chains:
        for bname in chains["body"][:1]:
            pb = arm.pose.bones.get(bname)
            if pb:
                for fi in range(total):
                    t = fi / max(total - 1, 1)
                    frame = frame_start + fi
                    incl, _, _ = _profile(t)
                    bpy.context.scene.frame_set(frame)
                    _set_pose_bone_rotation(pb, (incl, 0.0, 0.0))
                    pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Cauda - estabilizador
    if "tail" in chains:
        for ci, bname in enumerate(chains["tail"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.5, 1.0 - ci * 0.15)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, tail_ext = _profile(t)
                bpy.context.scene.frame_set(frame)
                # Cauda se move lateralmente para equilibrar
                z_angle = tail_ext * scale + 0.1 * math.sin(t * math.pi * 4)
                _set_pose_bone_rotation(pb, (0.0, 0.0, z_angle))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    finalize_current_action_to_nla(armature_name)
    return chains


def fire_breath_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 64,
    bursts: int = 2,
    action_name: str = "Animator3D_FireBreath",
) -> dict[str, list[str]]:
    """Dragão ergue peito, inclina pescoço e solta rajadas de fogo.

    Peito expande, pescoço recua e avança em sincronia com "expiração".
    Asas abrem para estabilizar, cauda contrabalança.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _burst_profile(t: float) -> tuple[float, float, float]:
        """Retorna (peito_X, pescoço_X, asas_Y) para cada rajada."""
        u = (t * bursts) % 1.0
        if u < 0.3:
            # Inspiração: peito abaixa, pescoço recua, asas recolhem
            w = u / 0.3
            return (
                -0.15 * _smoothstep01(w),  # peito abaixa (inspira)
                -0.25 * _smoothstep01(w),  # pescoço recua
                -0.2 * _smoothstep01(w),  # asas recolhem
            )
        elif u < 0.5:
            # Expiração violenta: peito sobe, pescoço avança, asas abrem
            w = (u - 0.3) / 0.2
            return (
                0.35 * _smoothstep01(w),  # peito sobe
                0.55 * _smoothstep01(w),  # pescoço avança
                0.4 * _smoothstep01(w),  # asas abrem
            )
        else:
            # Recuperação suave
            w = (u - 0.5) / 0.5
            return (
                0.35 * (1.0 - _smoothstep01(w) * 0.5),
                0.55 * (1.0 - _smoothstep01(w) * 0.7),
                0.4 * (1.0 - _smoothstep01(w) * 0.5),
            )

    # Peito/Spine - ondas de inspiração/expiração
    if "spine" in chains:
        for ci, bname in enumerate(chains["spine"][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.6, 1.0 - ci * 0.2)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                chest_x, _, _ = _burst_profile(t)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (chest_x * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Pescoço - avanço e vibração no fogo
    if "neck" in chains:
        for ci, bname in enumerate(chains["neck"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.5, 1.0 - ci * 0.15)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, neck_x, _ = _burst_profile(t)
                # Vibração no momento do fogo
                vib = 0.08 * math.sin(t * math.pi * bursts * 8) if neck_x > 0.3 else 0.0
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, ((neck_x + vib) * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Asas - abrem para estabilizar contra o recuo do fogo
    for wing_name, side_sign in [("wing_r", 1.0), ("wing_l", -1.0)]:
        if wing_name not in chains:
            continue
        for ci, bname in enumerate(chains[wing_name][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.7, 1.0 - ci * 0.15)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, wing_y = _burst_profile(t)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, side_sign * wing_y * scale, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Cauda - contrabalanço suave lateral
    if "tail" in chains:
        for ci, bname in enumerate(chains["tail"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.6, 1.0 - ci * 0.12)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, wing_y = _burst_profile(t)
                # Cauda balança lateralmente em contrário
                z_sway = 0.15 * scale * math.sin(t * math.pi * bursts * 2 + ci * 0.5)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, 0.0, z_sway))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    finalize_current_action_to_nla(armature_name)
    return chains


def land_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 80,
    action_name: str = "Animator3D_Land",
) -> dict[str, list[str]]:
    """Dragão pousando: descida controlada, asas em "freio aerodinâmico", impacto suave.

    Fases: planeio → freio com asas → extensão de patas → amortecimento.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _land_phase(t: float) -> tuple[float, float, float, float]:
        """Retorna (inclinação_corpo, asas_Y, pernas_X, cauda_Z)."""
        if t < 0.4:
            # Planeio descendente
            w = t / 0.4
            return (
                0.4 + w * 0.3,  # inclinação aumenta
                0.2 + w * 0.15,  # asas semi-abertas
                0.0,  # pernas retraídas
                -0.1,  # cauda baixa
            )
        elif t < 0.65:
            # Freio aerodinâmico - asas abrem ao máximo para frear
            w = (t - 0.4) / 0.25
            return (
                0.7 - w * 0.2,  # corpo nivela
                0.35 + w * 0.45,  # asas abrem de 0.35 para 0.8
                w * 0.25,  # pernas começam a estender
                -0.1 + w * 0.3,  # cauda sobe para equilibrar
            )
        elif t < 0.85:
            # Preparação para impacto
            w = (t - 0.65) / 0.2
            return (
                0.5 - w * 0.3,  # corpo volta
                0.8 - w * 0.2,  # asas fecham um pouco
                0.25 + w * 0.15,  # pernas estendidas
                0.2 - w * 0.1,  # cauda nivela
            )
        else:
            # Amortecimento
            w = (t - 0.85) / 0.15
            return (
                0.2 * (1.0 - w * 0.5),  # corpo estabiliza
                0.6 * (1.0 - w * 0.3),  # asas abrem para equilibrar
                0.4 * (1.0 - w * 0.2),  # pernas flexionam
                0.1 + w * 0.05,  # cauda estabiliza
            )

    # Corpo/Spine - inclinação durante descida
    if "spine" in chains:
        for ci, bname in enumerate(chains["spine"][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.7, 1.0 - ci * 0.15)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                incl, _, _, _ = _land_phase(t)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (incl * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Asas - movimento de freio aerodinâmico
    for wing_name, side_sign in [("wing_r", 1.0), ("wing_l", -1.0)]:
        if wing_name not in chains:
            continue
        for ci, bname in enumerate(chains[wing_name][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.8, 1.0 - ci * 0.1)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, wing_y, _, _ = _land_phase(t)
                # Tremulação nas asas durante o freio
                flutter = 0.03 * math.sin(t * math.pi * 12) if wing_y > 0.6 else 0.0
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, side_sign * (wing_y + flutter) * scale, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Pernas - extensão para pouso
    for leg_name, side_sign in [("leg_r", 1.0), ("leg_l", -1.0)]:
        if leg_name not in chains:
            continue
        for ci, bname in enumerate(chains[leg_name][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.7, 1.0 - ci * 0.2)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, leg_x, _ = _land_phase(t)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (leg_x * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Cauda - equilíbrio durante descida
    if "tail" in chains:
        for ci, bname in enumerate(chains["tail"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.8, 1.0 - ci * 0.12)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, _, tail_z = _land_phase(t)
                # Sway lateral para estabilidade
                sway = 0.08 * math.sin(t * math.pi * 3 + ci * 0.3)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, sway, tail_z * scale))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    finalize_current_action_to_nla(armature_name)
    return chains


def victory_roar_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 96,
    action_name: str = "Animator3D_VictoryRoar",
) -> dict[str, list[str]]:
    """Dragão em pose de vitória: peito inflado, cabeça erguida, rugido poderoso.

    Fases: elevação dramática → pose de poder → rugido com vibração → estabilização majestosa.
    """
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    def _roar_profile(t: float) -> tuple[float, float, float, float]:
        """Retorna (peito_X, pescoço_X, asas_Y, cauda_Z)."""
        if t < 0.25:
            # Elevação dramática - peito sobe, pescoço ergue
            w = t / 0.25
            return (
                -0.4 * _smoothstep01(w),  # peito ergue (negativo = cima)
                -0.6 * _smoothstep01(w),  # pescoço para trás/erguido
                w * 0.3,  # asas começam a abrir
                w * 0.2,  # cauda sobe
            )
        elif t < 0.4:
            # Pose de poder - sustenta
            return (-0.4, -0.6, 0.3, 0.2)
        elif t < 0.7:
            # RUGIDO - vibração violenta
            w = (t - 0.4) / 0.3
            roar_intensity = _smoothstep01(w) * (1.0 - _smoothstep01((w - 0.7) / 0.3))
            return (
                -0.4 + roar_intensity * 0.15,  # peito vibra
                -0.6 + roar_intensity * 0.4,  # pescoço avança no rugido
                0.3 + roar_intensity * 0.5,  # asas abrem no máximo
                0.2 + roar_intensity * 0.3,  # cauda ergue
            )
        else:
            # Estabilização majestosa
            w = (t - 0.7) / 0.3
            return (
                -0.25 * (1.0 - _smoothstep01(w) * 0.3),  # peito mantém altura
                -0.2 * (1.0 - _smoothstep01(w) * 0.5),  # pescoço relaxa um pouco
                0.8 * (1.0 - _smoothstep01(w) * 0.25),  # asas mantêm abertas
                0.5 * (1.0 - _smoothstep01(w) * 0.2),  # cauda mantém posição
            )

    # Peito/Spine - elevação majestosa
    if "spine" in chains:
        for ci, bname in enumerate(chains["spine"][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.8, 1.0 - ci * 0.15)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                chest_x, _, _, _ = _roar_profile(t)
                # Vibração no rugido
                vib = 0.05 * math.sin(t * math.pi * 20) if 0.4 < t < 0.7 else 0.0
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, ((chest_x + vib) * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Pescoço - erguido com vibração no rugido
    if "neck" in chains:
        for ci, bname in enumerate(chains["neck"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.9, 1.0 - ci * 0.1)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, neck_x, _, _ = _roar_profile(t)
                # Vibração violenta no rugido
                vib = 0.12 * math.sin(t * math.pi * 25) * (1.0 if 0.4 < t < 0.65 else 0.3)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, ((neck_x + vib) * scale, 0.0, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Asas - abertura majestosa e simétrica
    for wing_name, side_sign in [("wing_r", 1.0), ("wing_l", -1.0)]:
        if wing_name not in chains:
            continue
        for ci, bname in enumerate(chains[wing_name][:3]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.9, 1.0 - ci * 0.08)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, wing_y, _ = _roar_profile(t)
                # Tremor nas asas no momento do rugido
                tremor = 0.06 * math.sin(t * math.pi * 18) if 0.42 < t < 0.68 else 0.0
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, side_sign * (wing_y + tremor) * scale, 0.0))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    # Cauda - erguida em pose de poder
    if "tail" in chains:
        for ci, bname in enumerate(chains["tail"][:4]):
            pb = arm.pose.bones.get(bname)
            if pb is None:
                continue
            scale = max(0.9, 1.0 - ci * 0.1)
            for fi in range(total):
                t = fi / max(total - 1, 1)
                frame = frame_start + fi
                _, _, _, tail_z = _roar_profile(t)
                # Ondulação majestosa
                wave = 0.1 * math.sin(t * math.pi * 4 + ci * 0.6)
                bpy.context.scene.frame_set(frame)
                _set_pose_bone_rotation(pb, (0.0, wave, tail_z * scale))
                pb.keyframe_insert(data_path=_rotation_data_path(pb), frame=frame)

    finalize_current_action_to_nla(armature_name)
    return chains


def run_cycle_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 36,
    cycles: float = 2.0,
    body_amp: float = 0.08,
    leg_amp: float = 0.22,
    arm_amp: float = 0.18,
    wing_amp: float = 0.08,
    tail_amp: float = 0.12,
    action_name: str = "Animator3D_Run",
) -> dict[str, list[str]]:
    """Running cycle: maior amplitude de hip swing e flexao de joelho, em eixos
    resolvidos do rig."""
    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = _detect_forward(arm_obj, chains)

    _locomotion_cycle(
        arm_obj, chains, forward,
        frame_start=frame_start, total=total, cycles=cycles,
        hip_amp=leg_amp * 3.0,      # ~38° hip swing for a run
        knee_swing=leg_amp * 5.5,   # deep ~70° flexion in swing
        knee_stance=leg_amp * 1.0,
        ankle_amp=leg_amp * 1.4,
        arm_amp=arm_amp * 3.0,      # strong forward/back drive
        arm_decay=0.3,
        arm_adduct=0.7,             # arms tucked closer for a run
        body_lean=body_amp * 2.2,   # pronounced forward lean
        spine_twist=body_amp * 2.0,
        body_bob=0.06,
    )

    _legacy_secondary_motion(
        arm_obj, chains, frame_start=frame_start, total=total, cycles=cycles,
        wing_amp=wing_amp, tail_amp=tail_amp,
    )

    finalize_current_action_to_nla(armature_name)
    return chains


def jump_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 36,
    leg_amp: float = 0.25,
    arm_amp: float = 0.2,
    body_amp: float = 0.1,
    action_name: str = "Animator3D_Jump",
) -> dict[str, list[str]]:
    """Jump: anticipation -> crouch -> extend -> airborne -> descend -> land. Non-looping."""
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1
    forward = _detect_forward(arm_obj, chains)
    axes = _build_axes_map(
        arm_obj, chains, forward,
        ("body", "spine", "neck", "leg_r", "leg_l", "arm_r", "arm_l"),
    )

    def _swing_chain(chain_key: str, value: float, decay: float = 0.1):
        """Forward/back swing on a chain (rig-resolved axis), with decay."""
        for ci, bname in enumerate(chains.get(chain_key, [])[:3]):
            pb = arm_obj.pose.bones.get(bname)
            ax = axes.get(bname)
            if pb is None or ax is None:
                continue
            scale = max(0.3, 1.0 - ci * decay)
            _key_humanoid_bone(pb, ax, bpy.context.scene.frame_current, swing=value * scale)

    def _key_leg(value_hip: float, value_knee_bend: float):
        for leg_key in ("leg_r", "leg_l"):
            leg = chains.get(leg_key, [])
            if leg:
                pb = arm_obj.pose.bones.get(leg[0])
                ax = axes.get(leg[0])
                if pb and ax:
                    _key_humanoid_bone(pb, ax, bpy.context.scene.frame_current, swing=value_hip)
            if len(leg) > 1:
                pb = arm_obj.pose.bones.get(leg[1])
                ax = axes.get(leg[1])
                if pb and ax:
                    _key_humanoid_bone(
                        pb, ax, bpy.context.scene.frame_current,
                        bend=value_knee_bend, swing=value_hip * 0.1,
                    )
            if len(leg) > 2:
                pb = arm_obj.pose.bones.get(leg[2])
                ax = axes.get(leg[2])
                if pb and ax:
                    _key_humanoid_bone(
                        pb, ax, bpy.context.scene.frame_current, bend=-value_knee_bend * 0.25
                    )

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        # --- Phase weights (smooth transitions via smoothstep) ---
        # ANTICIPATION: 0.00 - 0.18
        # CROUCH:       0.18 - 0.35
        # EXTEND:       0.35 - 0.50
        # AIRBORNE:     0.50 - 0.70
        # DESCEND:      0.70 - 0.85
        # LAND:         0.85 - 1.00

        if t < 0.18:
            # ANTICIPATION: small counter-movement
            p = _smoothstep01(t / 0.18)
            knee_bend = leg_amp * 0.15 * p
            hip_fwd = -body_amp * 0.2 * p
            arm_swing = -arm_amp * 0.25 * p
            body_lean = body_amp * 0.15 * p
            neck_lean = -body_amp * 0.1 * p
        elif t < 0.35:
            # CROUCH: deep knee bend, body forward, arms back
            p = _smoothstep01((t - 0.18) / 0.17)
            knee_bend = leg_amp * (0.15 + 0.45 * p)
            hip_fwd = -body_amp * 0.2 + body_amp * 0.5 * p
            arm_swing = -arm_amp * 0.25 - arm_amp * 0.55 * p
            body_lean = body_amp * 0.15 + body_amp * 0.55 * p
            neck_lean = -body_amp * 0.1 - body_amp * 0.25 * p
        elif t < 0.50:
            # EXTEND/LAUNCH: rapid knee extension, body straightens, arms swing up
            p = _smoothstep01((t - 0.35) / 0.15)
            knee_bend = leg_amp * 0.6 * (1.0 - p)
            hip_fwd = body_amp * 0.3 * (1.0 - p)
            arm_swing = -arm_amp * 0.8 + arm_amp * 1.6 * p
            body_lean = body_amp * 0.7 * (1.0 - p) - body_amp * 0.2 * p
            neck_lean = -body_amp * 0.35 * (1.0 - p)
        elif t < 0.70:
            # AIRBORNE: legs slightly tucked, body slight forward, arms raised
            p = _smoothstep01((t - 0.50) / 0.20)
            knee_bend = leg_amp * 0.2 * (0.5 + 0.5 * math.sin(p * math.pi))
            hip_fwd = 0.0
            arm_swing = arm_amp * 0.8 * (1.0 - p * 0.2)
            body_lean = -body_amp * 0.2 * (1.0 - p * 0.5)
            neck_lean = body_amp * 0.05 * p
        elif t < 0.85:
            # DESCEND: legs extend to prepare for landing, arms come down
            p = _smoothstep01((t - 0.70) / 0.15)
            knee_bend = leg_amp * 0.2 * (1.0 - p * 0.6)
            hip_fwd = 0.0
            arm_swing = arm_amp * 0.64 * (1.0 - p * 0.6)
            body_lean = -body_amp * 0.1 * (1.0 - p)
            neck_lean = body_amp * 0.05 * (1.0 - p)
        else:
            # LAND: knees bend to absorb impact, body forward, arms settle
            p = _smoothstep01((t - 0.85) / 0.15)
            # Impact absorption: ramp up quickly then ease off
            absorb = math.sin(p * math.pi) if p < 0.6 else 0.4 * (1.0 - (p - 0.6) / 0.4)
            knee_bend = leg_amp * (0.08 + 0.35 * absorb)
            hip_fwd = body_amp * 0.3 * absorb
            arm_swing = arm_amp * 0.25 * (1.0 - p)
            body_lean = body_amp * 0.3 * absorb
            neck_lean = -body_amp * 0.15 * absorb

        _swing_chain("body", body_lean, decay=0.08)
        _swing_chain("spine", body_lean * 0.6, decay=0.15)
        # Hip swings forward/back; knee flexes (rig-resolved); ankle follows.
        _key_leg(hip_fwd, knee_bend)
        _swing_chain("arm_r", arm_swing, decay=0.1)
        _swing_chain("arm_l", arm_swing, decay=0.1)
        _swing_chain("neck", neck_lean, decay=0.15)

    finalize_current_action_to_nla(armature_name)
    return chains


def fall_keyframes(
    armature_name: str,
    *,
    frame_start: int = 1,
    frame_end: int = 24,
    leg_spread: float = 0.08,
    arm_spread: float = 0.15,
    body_lean: float = 0.06,
    action_name: str = "Animator3D_Fall",
) -> dict[str, list[str]]:
    """Falling: splay -> mid-fall sway -> terminal velocity -> impact prep. Non-looping."""
    import math

    bpy = _bpy()
    normalize_armature_before_animation(armature_name)
    stash_if_needed_for_action(armature_name, action_name)
    ensure_action(armature_name, action_name)
    bpy.context.scene.frame_start = frame_start
    bpy.context.scene.frame_end = frame_end
    _ensure_pose_mode(armature_name)

    chains = _classify_bone_chains(armature_name)
    arm_obj = bpy.data.objects[armature_name]
    total = frame_end - frame_start + 1

    forward = _detect_forward(arm_obj, chains)
    axes = _build_axes_map(
        arm_obj, chains, forward,
        ("body", "spine", "neck", "leg_r", "leg_l", "arm_r", "arm_l"),
    )

    def _swing_chain(chain_key: str, val: float, decay: float = 0.1, *, bend: float = 0.0):
        for ci, bname in enumerate(chains.get(chain_key, [])):
            pb = arm_obj.pose.bones.get(bname)
            ax = axes.get(bname)
            if pb is None or ax is None:
                continue
            s = max(0.3, 1.0 - ci * decay)
            _key_humanoid_bone(
                pb, ax, bpy.context.scene.frame_current, swing=val * s, bend=bend * s
            )

    for fi in range(total):
        t = fi / max(total - 1, 1)
        frame = frame_start + fi
        bpy.context.scene.frame_set(frame)

        # INITIAL_FALL: 0.00-0.25 — limbs come up/out smoothly
        # MID_FALL:     0.25-0.50 — wind sway begins
        # TERMINAL:     0.50-0.85 — stabilize with subtle sway
        # IMPACT_PREP:  0.85-1.00 — slight limb tuck
        settle = _smoothstep01(min(t / 0.25, 1.0))
        wind = 0.05 * math.sin(t * 2 * math.pi * 2.0)
        wind_secondary = 0.025 * math.sin(t * 2 * math.pi * 3.0 + 0.5)

        if t < 0.25:
            spread_blend = settle
            tuck = 0.0
        elif t < 0.50:
            p = _smoothstep01((t - 0.25) / 0.25)
            spread_blend = 1.0
            tuck = 0.0
            wind *= p
            wind_secondary *= p
        elif t < 0.85:
            spread_blend = 1.0
            tuck = 0.0
        else:
            p = _smoothstep01((t - 0.85) / 0.15)
            spread_blend = 1.0 - 0.15 * p
            tuck = p * 0.3

        leg_val = leg_spread * spread_blend
        arm_sway = arm_spread * spread_blend

        # Body leans back slightly; arms raise (swing back); legs tuck (knee bend).
        _swing_chain("body", -body_lean * spread_blend + wind, decay=0.08)
        _swing_chain("spine", -body_lean * 0.5 * spread_blend + wind_secondary, decay=0.15)
        _swing_chain("neck", body_lean * 0.4 * spread_blend + wind_secondary, decay=0.15)
        # Arms swing up/back (negative = back), with a little wind.
        _swing_chain("arm_r", -arm_sway * 4.0 + wind, decay=0.08)
        _swing_chain("arm_l", -arm_sway * 4.0 - wind, decay=0.08)
        # Legs hang slightly forward with knee tuck (more at impact prep).
        _swing_chain("leg_r", leg_val * 0.5 + wind_secondary, decay=0.1, bend=leg_val + tuck)
        _swing_chain("leg_l", leg_val * 0.5 - wind_secondary, decay=0.1, bend=leg_val + tuck)

    finalize_current_action_to_nla(armature_name)
    return chains


def _weld_scene_meshes() -> None:
    bpy = _bpy()

    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        nv = len(obj.data.vertices)
        if nv > 150_000:
            dist = 0.003
        elif nv > 100_000:
            dist = 0.005
        elif nv > 50_000:
            dist = 0.008
        else:
            dist = 0.01

        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        obj.data.calc_normals_split()
        bpy.ops.mesh.remove_doubles(threshold=dist, use_sharp_edge_from_normals=True)
        bpy.ops.mesh.customdata_custom_splitnormals_clear()
        bpy.ops.mesh.faces_shade_smooth()
        bpy.ops.object.mode_set(mode="OBJECT")


def export_glb(path: Path, *, draco: bool = False) -> None:
    bpy = _bpy()
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(Exception):
        _weld_scene_meshes()
    # ACTIONS: no Blender 5.1 preserva melhor clips múltiplos do que NLA_TRACKS
    # quando as animações são reimportadas/empilhadas via NLA.
    bpy.ops.export_scene.gltf(
        filepath=str(path),
        export_format="GLB",
        use_selection=False,
        export_animations=True,
        export_animation_mode="ACTIONS",
        export_draco_mesh_compression_enable=draco,
        export_draco_mesh_compression_level=6 if draco else 0,
        export_all_influences=False,
        export_normals=True,
        # Tangents keep the hero's normal map seam-free, including while the
        # skinned mesh deforms during animation.
        export_tangents=True,
        export_image_format="JPEG",
        export_keep_originals=False,
    )


def export_fbx(path: Path) -> None:
    bpy = _bpy()
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.fbx(
        filepath=str(path),
        use_selection=False,
        bake_anim=True,
    )


def export_auto(path: Path, *, draco: bool = False) -> None:
    """Exporta conforme extensão (.glb/.gltf → GLB/GLTF; .fbx → FBX)."""
    suf = path.suffix.lower()
    if suf == ".fbx":
        export_fbx(path)
    elif suf in {".glb", ".gltf"}:
        export_glb(path, draco=draco)
    else:
        raise ValueError(f"Extensão de saída não suportada: {suf}")


def project_texture_to_parts(
    original_glb: Path,
    parts_glb: Path,
    output_path: Path,
    resolution: int = 1024,
    margin: int = 16,
    *,
    draco: bool = False,
) -> None:
    """Project textures from an original textured mesh onto Part3D part meshes via Cycles bake."""
    import math

    bpy = _bpy()
    clear_scene()

    bpy.ops.import_scene.gltf(filepath=str(_decompress_meshopt_glb(original_glb.resolve())))
    source_objs = [o for o in bpy.context.selected_objects if o.type == "MESH"]

    bpy.ops.import_scene.gltf(filepath=str(_decompress_meshopt_glb(parts_glb.resolve())))
    all_objs = list(bpy.context.selected_objects)
    part_objs = [o for o in all_objs if o not in source_objs and o.type == "MESH"]

    bpy.context.scene.render.engine = "CYCLES"

    for part_obj in part_objs:
        if part_obj.type != "MESH":
            continue

        if not part_obj.data.uv_layers:
            bpy.ops.object.select_all(action="DESELECT")
            part_obj.select_set(True)
            bpy.context.view_layer.objects.active = part_obj
            bpy.ops.object.mode_set(mode="EDIT")
            bpy.ops.mesh.select_all(action="SELECT")
            try:
                area = next((a for a in bpy.context.screen.areas if a.type == "VIEW_3D"), None)
                if area:
                    with bpy.context.temp_override(area=area):
                        bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
                else:
                    bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
            except Exception:
                bpy.ops.uv.smart_project(angle_limit=math.radians(66), island_margin=0.01)
            bpy.ops.mesh.select_all(action="DESELECT")
            bpy.ops.object.mode_set(mode="OBJECT")

        img_name = f"{part_obj.name}_baked"
        bake_img = bpy.data.images.new(img_name, width=resolution, height=resolution, alpha=True)
        bake_img.colorspace_settings.name = "sRGB"

        if not part_obj.data.materials:
            mat = bpy.data.materials.new(f"{part_obj.name}_mat")
            mat.use_nodes = True
            part_obj.data.materials.append(mat)
        mat = part_obj.data.materials[0]
        if not mat.use_nodes:
            mat.use_nodes = True

        nodes = mat.node_tree.nodes
        # Clean up any leftover __bake_target__ nodes from previous iterations
        for old in [n for n in nodes if n.name == "__bake_target__"]:
            nodes.remove(old)
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.name = "__bake_target__"
        tex_node.image = bake_img
        tex_node.select = True
        nodes.active = tex_node  # CRITICAL: Cycles bakes to the active node

        bpy.ops.object.select_all(action="DESELECT")
        for src in source_objs:
            src.select_set(True)
        part_obj.select_set(True)
        bpy.context.view_layer.objects.active = part_obj  # part = bake target

        scene = bpy.context.scene
        scene.cycles.bake_type = "DIFFUSE"
        scene.cycles.samples = 1
        scene.cycles.use_denoising = False
        bake = scene.render.bake
        bake.use_pass_direct = False
        bake.use_pass_indirect = False
        bake.use_pass_color = True  # only base color
        bake.use_selected_to_active = True
        bake.margin = margin
        bake.margin_type = "EXTEND"
        bake.cage_extrusion = 0.1
        bake.target = "IMAGE_TEXTURES"
        bake.use_clear = True

        bpy.ops.object.bake(
            type="DIFFUSE",
            use_selected_to_active=True,
            use_clear=True,
            margin=margin,
            cage_extrusion=0.1,
        )
        bake_img.pack()

        bsdf = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
        if bsdf and "Base Color" in bsdf.inputs:
            mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        nodes.active = bsdf if bsdf else nodes[0]

    bpy.ops.object.select_all(action="DESELECT")
    for src in source_objs:
        src.select_set(True)
    bpy.ops.object.delete()

    bpy.ops.object.select_all(action="SELECT")
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=True,
        export_draco_mesh_compression_enable=draco,
        export_draco_mesh_compression_level=6 if draco else 0,
        export_all_influences=False,
        export_normals=True,
        export_tangents=True,
        export_image_format="JPEG",
        export_keep_originals=False,
    )
