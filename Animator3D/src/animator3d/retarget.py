"""Retarget de animações entre rigs: aplica animações de um rig *source* (ex.: pack
CC0 do Quaternius) sobre um rig *target* (ex.: humanoides do simple-rpg).

Método: o target adopta a pose global *absoluta* do source por osso. A correção
fixa por osso é ``inverse(rest_source) @ inverse(swing) @ rest_target``, onde
``swing`` é a rotação de arco mínimo entre a direção de rest do osso source e a
do osso target (em espaço de armature). Remover o swing é essencial quando os
rests diferem (source T-pose vs target A-pose): sem isso, o delta "baixar
braços" do idle Quaternius é aplicado por cima de braços já baixados no A-pose
e enfia-os dentro do tronco. Com o swing removido, a direção mundial do osso
target iguala sempre a do source — o idle fica com os braços ao lado das coxas
como no Quaternius.

A pose global do pai é propagada analiticamente (dict por frame, pais primeiro),
nunca lida de ``pose.bone.matrix`` a meio do frame — essa leitura seria stale
(o depsgraph só re-avalia em ``view_layer.update()``). A continuidade de sinal
dos quaternions é forçada frame a frame (``make_compatible``) para evitar
interpolação pelo caminho longo (trambolhão no viewer).

Não é retargeting universal (ex.: não resolve rigs com topologias muito diferentes),
mas é robusto e simples para humanoides. Alternativas mais complexas (constraints
Copy Transforms + bake visual, retarget por chains) foram prototipadas e produziram
resultados piores (twists, "bola de carne") por causa de propagação dupla de
rotações parent ao vivo — a correcção de eixo aqui é fixa (calculada do rest pose,
uma vez por osso), não recalculada por frame a partir da pose corrente, o que evita
essa duplicação.

Fontes: docs/quaternius_inventory.md, protótipos validados visualmente no browser.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data" / "retarget"


def _bpy():
    import bpy

    return bpy


@dataclass
class RetargetProfile:
    """Perfil de retarget: mapeamento de bones e de clips source→target."""

    name: str
    # source_bone -> candidatos target (primeiro que existir no rig ganha).
    bone_map: dict[str, list[str]]
    clip_map: dict[str, str]  # clean_name -> source_track_name
    source_path: Path | None = None  # opcional: override do ficheiro source
    extra: dict[str, Any] = field(default_factory=dict)


def load_profile(name_or_path: str | Path) -> RetargetProfile:
    """Carrega um perfil de retarget de um YAML em data/retarget/ ou de um path.

    Args:
        name_or_path: nome do perfil (ex.: ``"quaternius"``) ou path absoluto
            para um YAML custom.

    Raises:
        FileNotFoundError: perfil não encontrado.
    """
    import yaml

    p = Path(name_or_path)
    if not p.is_absolute() and not p.is_file():
        candidate = _DATA_DIR / f"{name_or_path}.yaml"
        if not candidate.is_file():
            raise FileNotFoundError(f"Perfil de retarget não encontrado: {name_or_path} (procurei em {candidate})")
        p = candidate
    if not p.is_file():
        raise FileNotFoundError(p)

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    bone_map = {
        src: [cand] if isinstance(cand, str) else [str(c) for c in cand]
        for src, cand in dict(raw.get("bone_map", {})).items()
    }
    return RetargetProfile(
        name=raw.get("profile", p.stem),
        bone_map=bone_map,
        clip_map=dict(raw.get("clip_map", {})),
        source_path=Path(raw["source_path"]) if raw.get("source_path") else None,
    )


def _available_profiles() -> list[str]:
    """Lista nomes de perfis YAML disponíveis em data/retarget/."""
    if not _DATA_DIR.is_dir():
        return []
    return sorted(p.stem for p in _DATA_DIR.glob("*.yaml"))


def _topo_sort_target_bones(arm: Any, names: list[str]) -> list[str]:
    """Ordena bones target por profundidade na hierarquia (pais primeiro)."""
    depth = {}
    for bn in names:
        b = arm.data.bones.get(bn)
        d = 0
        while b and b.parent:
            b = b.parent
            d += 1
        depth[bn] = d
    return sorted(names, key=lambda n: depth[n])


def _armature_topo_order(arm: Any) -> list[str]:
    """Todos os bones do armature, pais antes de filhos (DFS a partir das roots)."""
    order: list[str] = []

    def walk(bone: Any) -> None:
        order.append(bone.name)
        for child in bone.children:
            walk(child)

    for bone in arm.data.bones:
        if bone.parent is None:
            walk(bone)
    return order


def _axis_correction(src_rest: Any, tgt_rest: Any, src_dir: Any, tgt_dir: Any) -> Any:
    """Correção fixa por osso: converte pose global source em pose global target.

    ``desired_target = source_pose @ correction`` faz a direção mundial do osso
    target seguir exactamente a do source. O ``swing`` (arco mínimo entre as
    direções de rest) é removido para que diferenças T-pose/A-pose entre rigs
    não sejam re-aplicadas como delta por cima do rest do target.

    Args:
        src_rest: quaternion do rest do osso source (armature space).
        tgt_rest: quaternion do rest do osso target (armature space).
        src_dir: direção de rest do osso source (``tail - head`` normalizado).
        tgt_dir: direção de rest do osso target.
    """
    swing = src_dir.rotation_difference(tgt_dir)
    return src_rest.inverted() @ swing.inverted() @ tgt_rest


def _solve_target_basis(
    source_pose: Any,
    rest_alignment: Any,
    target_rest: Any,
    *,
    target_parent_pose: Any | None = None,
    target_parent_rest: Any | None = None,
) -> Any:
    """Converte uma pose global source para o basis local target."""
    desired_pose = source_pose @ rest_alignment
    if target_parent_pose is None or target_parent_rest is None:
        rest_chain = target_rest
    else:
        rest_chain = target_parent_pose @ target_parent_rest.inverted() @ target_rest
    return rest_chain.inverted() @ desired_pose


def resolve_bone_pairs(
    target_bones: Any,
    source_bones: Any,
    bone_map: dict[str, str | list[str]],
) -> tuple[dict[str, str], list[str]]:
    """Resolve o ``bone_map`` (com candidatos) em pares efetivos target→source.

    Args:
        target_bones: coleção de bones do target (suporta ``in``).
        source_bones: coleção de bones do source (suporta ``in``).
        bone_map: ``{source_bone: target_bone | [candidatos]}``.

    Returns:
        ``(tgt_to_src, skipped)`` — pares resolvidos e mapeamentos falhados
        (fora dedos/leafs, para não inundar o log).
    """
    tgt_to_src: dict[str, str] = {}
    skipped: list[str] = []
    for src_bn, candidates in bone_map.items():
        cand_list = [candidates] if isinstance(candidates, str) else list(candidates)
        tgt_bn = next((c for c in cand_list if c in target_bones), None)
        if tgt_bn is not None and src_bn in source_bones:
            tgt_to_src[tgt_bn] = src_bn
        elif not any(k in src_bn for k in ("_leaf", "index_", "middle_", "pinky_", "ring_", "thumb_")):
            skipped.append(f"{src_bn}->{'|'.join(cand_list)}")
    return tgt_to_src, skipped


def retarget_animation(
    target_arm_name: str,
    source_arm_name: str,
    bone_map: dict[str, str | list[str]],
    source_action_name: str,
    output_clip_name: str,
) -> dict[str, Any]:
    """Retarget de uma action do source para o target (matrix_basis copy).

    Pré-condições: ambos os armatures já importados na cena; a action existe em
    ``bpy.data.actions`` e pertence ao source.

    Args:
        target_arm_name: nome do armature alvo (mantém-se no output).
        source_arm_name: nome do armature source (descartado no fim).
        bone_map: ``{source_bone: target_bone | [candidatos target]}``.
        source_action_name: nome da action no source a retargetizar.
        output_clip_name: nome limpo do clip de saída (ex.: ``"idle"``).

    Returns:
        Dict com ``clip``, ``frames``, ``bones_mapped``, ``skipped_bones``.
    """
    bpy = _bpy()

    target = bpy.data.objects.get(target_arm_name)
    source = bpy.data.objects.get(source_arm_name)
    if target is None or source is None:
        raise ValueError(f"Armature não encontrado: target={target_arm_name!r}, source={source_arm_name!r}")
    if target.type != "ARMATURE" or source.type != "ARMATURE":
        raise ValueError("Tanto target como source têm de ser armatures.")

    src_action = bpy.data.actions.get(source_action_name)
    if src_action is None:
        raise ValueError(f"Action source não encontrada: {source_action_name!r}")

    # Pares mapeados que existem em ambos os rigs (candidatos: primeiro ganha).
    tgt_to_src, skipped = resolve_bone_pairs(target.data.bones, source.data.bones, bone_map)

    order = _topo_sort_target_bones(target, list(tgt_to_src))

    # Correção fixa por osso (ver _axis_correction): pose global target =
    # pose global source @ correction. O swing entre direções de rest é
    # removido para que T-pose vs A-pose não vire delta duplicado.
    axis_correction: dict[str, Any] = {}
    for tgt_bn, src_bn in tgt_to_src.items():
        src_bone = source.data.bones[src_bn]
        tgt_bone = target.data.bones[tgt_bn]
        src_dir = (src_bone.tail_local - src_bone.head_local).normalized()
        tgt_dir = (tgt_bone.tail_local - tgt_bone.head_local).normalized()
        axis_correction[tgt_bn] = _axis_correction(
            src_bone.matrix_local.to_quaternion(),
            tgt_bone.matrix_local.to_quaternion(),
            src_dir,
            tgt_dir,
        )

    # Rests do target pré-calculados para a propagação analítica por frame.
    full_order = _armature_topo_order(target)
    tgt_rest_quat = {b.name: b.matrix_local.to_quaternion() for b in target.data.bones}
    tgt_parent = {b.name: (b.parent.name if b.parent else None) for b in target.data.bones}

    # Reset do estado de animação do target. O reset do basis de TODOS os pose
    # bones garante que ossos não mapeados ficam mesmo no rest (a propagação
    # analítica assume basis identidade para eles).
    if target.animation_data is None:
        target.animation_data_create()
    target.animation_data.action = None
    for pb in target.pose.bones:
        pb.matrix_basis.identity()
    source.animation_data.action = src_action

    scene = bpy.context.scene
    f0 = int(src_action.frame_range[0])
    f1 = int(src_action.frame_range[1])

    new_act = bpy.data.actions.new(name=output_clip_name)
    target.animation_data.action = new_act

    bpy.context.view_layer.objects.active = target
    bpy.ops.object.mode_set(mode="POSE")
    for tgt_bn in order:
        pb = target.pose.bones[tgt_bn]
        pb.rotation_mode = "QUATERNION"

    prev_basis: dict[str, Any] = {}
    for frame in range(f0, f1 + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        # Pose global (armature space) propagada analiticamente, pais primeiro.
        # Ossos não mapeados ficam no rest (basis identidade).
        pose_global: dict[str, Any] = {}
        for bn in full_order:
            parent = tgt_parent[bn]
            if parent is None:
                rest_chain = tgt_rest_quat[bn]
            else:
                rest_chain = pose_global[parent] @ tgt_rest_quat[parent].inverted() @ tgt_rest_quat[bn]
            if bn not in tgt_to_src:
                pose_global[bn] = rest_chain
                continue
            source_pose = source.pose.bones[tgt_to_src[bn]].matrix.to_quaternion()
            if parent is None:
                target_basis = _solve_target_basis(source_pose, axis_correction[bn], tgt_rest_quat[bn])
            else:
                target_basis = _solve_target_basis(
                    source_pose,
                    axis_correction[bn],
                    tgt_rest_quat[bn],
                    target_parent_pose=pose_global[parent],
                    target_parent_rest=tgt_rest_quat[parent],
                )
            target_basis.normalize()
            prev = prev_basis.get(bn)
            if prev is not None:
                target_basis.make_compatible(prev)
            prev_basis[bn] = target_basis.copy()
            pose_global[bn] = rest_chain @ target_basis
            tpb = target.pose.bones[bn]
            tpb.rotation_quaternion = target_basis
            tpb.keyframe_insert(data_path="rotation_quaternion", frame=frame)

    bpy.ops.object.mode_set(mode="OBJECT")

    # Empurrar a nova action para NLA e limpar action activa.
    new_act.name = output_clip_name
    track = target.animation_data.nla_tracks.new()
    track.name = output_clip_name
    track.strips.new(output_clip_name, f0, new_act)
    target.animation_data.action = None

    return {
        "clip": output_clip_name,
        "source_track": source_action_name,
        "frames": (f0, f1),
        "bones_mapped": len(order),
        "skipped_bones": skipped,
    }


def retarget_batch(
    target_arm_name: str,
    source_arm_name: str,
    profile: RetargetProfile,
    *,
    only_clips: list[str] | None = None,
    replace: bool = False,
) -> list[dict[str, Any]]:
    """Retarget de todos os clips de um perfil (ou subconjunto via ``only_clips``).

    Args:
        target_arm_name: nome do armature alvo.
        source_arm_name: nome do armature source.
        profile: perfil com ``bone_map`` e ``clip_map``.
        only_clips: se dado, retargetiza apenas estes nomes limpos.
        replace: se True, limpa as NLA tracks existentes no target antes de começar.

    Returns:
        Lista de resultados por clip (ver :func:`retarget_animation`).
    """
    bpy = _bpy()
    target = bpy.data.objects.get(target_arm_name)
    if target is None:
        raise ValueError(f"Target armature não encontrado: {target_arm_name!r}")
    if replace:
        _clear_nla_tracks(target_arm_name)

    clips = profile.clip_map
    if only_clips:
        clips = {k: v for k, v in clips.items() if k in set(only_clips)}

    results = []
    for clean_name, src_track in clips.items():
        try:
            res = retarget_animation(target_arm_name, source_arm_name, profile.bone_map, src_track, clean_name)
            results.append(res)
        except ValueError as e:
            # Clip source em falta — reportar mas continuar.
            results.append({"clip": clean_name, "source_track": src_track, "error": str(e)})
    return results


def rename_existing_clips(
    armature_name: str,
    rename_map: dict[str, str],
) -> list[dict[str, str]]:
    """Renomeia clips (NLA tracks + actions) já existentes num armature.

    Útil para alinhar nomes sem retarget (ex.: criaturas não-humanoides como
    scorpion/mosquito que mantêm animação procedural mas precisam de nomes limpos).

    Args:
        armature_name: nome do armature.
        rename_map: ``{old_name: new_name}`` (ex.: ``{"Animator3D_BreatheIdle": "idle"}``).

    Returns:
        Lista de ``{old, new}`` dos renames efetuados.
    """
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return []
    done = []
    for track in arm.animation_data.nla_tracks:
        old = track.name
        new = rename_map.get(old)
        if new is None:
            continue
        track.name = new
        for strip in track.strips:
            if strip.action:
                strip.action.name = new
        done.append({"old": old, "new": new})
    return done


def _clear_nla_tracks(armature_name: str) -> None:
    """Remove as NLA tracks do armature e as actions que lhes pertenciam.

    Só remove actions referenciadas pelas tracks deste armature — apagar
    ``bpy.data.actions`` inteiro destruiria as actions do pack source já
    importado (bug latente quando o target chega com clips existentes).
    """
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    own_actions = {s.action for t in arm.animation_data.nla_tracks for s in t.strips if s.action}
    for t in list(arm.animation_data.nla_tracks):
        arm.animation_data.nla_tracks.remove(t)
    arm.animation_data.action = None
    for act in own_actions:
        bpy.data.actions.remove(act)
