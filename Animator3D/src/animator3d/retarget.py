"""Retarget de animações entre rigs: aplica animações de um rig *source* (ex.: pack
CC0 do Quaternius) sobre um rig *target* (ex.: humanoides do simple-rpg).

Método: **cópia do matrix_basis** (rotação local pura — delta relativamente ao rest
pose de cada osso). Para cada frame e cada osso mapeado, copia-se o quaternion de
rotação local do source para o target. Isto funciona para rigs humanoides com naming
1:1 porque o ``matrix_basis`` é relativo ao rest de cada osso, absorvendo diferenças
de T-pose vs A-pose e de bone-roll entre rigs.

Não é retargeting universal (ex.: não resolve rigs com topologias muito diferentes),
mas é robusto e simples para humanoides. Alternativas mais complexas (constraints
Copy Transforms + bake visual, retarget por chains) foram prototipadas e produziram
resultados piores (twists, "bola de carne") por causa de propagação dupla de
rotações parent. A cópia directa do matrix_basis evita esse problema.

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
    bone_map: dict[str, str]  # source_bone -> target_bone
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
    return RetargetProfile(
        name=raw.get("profile", p.stem),
        bone_map=dict(raw.get("bone_map", {})),
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


def retarget_animation(
    target_arm_name: str,
    source_arm_name: str,
    bone_map: dict[str, str],
    source_action_name: str,
    output_clip_name: str,
) -> dict[str, Any]:
    """Retarget de uma action do source para o target (matrix_basis copy).

    Pré-condições: ambos os armatures já importados na cena; a action existe em
    ``bpy.data.actions`` e pertence ao source.

    Args:
        target_arm_name: nome do armature alvo (mantém-se no output).
        source_arm_name: nome do armature source (descartado no fim).
        bone_map: ``{source_bone: target_bone}``.
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

    # Pares mapeados que existem em ambos os rigs.
    tgt_to_src: dict[str, str] = {}
    skipped: list[str] = []
    for src_bn, tgt_bn in bone_map.items():
        if tgt_bn in target.data.bones and src_bn in source.data.bones:
            tgt_to_src[tgt_bn] = src_bn
        else:
            # reportar só os principais (não dedos/leafs) para não inundar o log
            if not any(k in src_bn for k in ("_leaf", "index_", "middle_", "pinky_", "ring_", "thumb_")):
                skipped.append(f"{src_bn}->{tgt_bn}")

    order = _topo_sort_target_bones(target, list(tgt_to_src))

    # Reset do estado de animação do target.
    if target.animation_data is None:
        target.animation_data_create()
    target.animation_data.action = None
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

    for frame in range(f0, f1 + 1):
        scene.frame_set(frame)
        bpy.context.view_layer.update()
        for tgt_bn in order:
            src_bn = tgt_to_src[tgt_bn]
            src_basis = source.pose.bones[src_bn].matrix_basis.to_quaternion()
            tpb = target.pose.bones[tgt_bn]
            tpb.rotation_quaternion = src_basis
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
    """Remove todas as NLA tracks e actions de um armature (modo --replace)."""
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return
    for t in list(arm.animation_data.nla_tracks):
        arm.animation_data.nla_tracks.remove(t)
    arm.animation_data.action = None
    for act in list(bpy.data.actions):
        bpy.data.actions.remove(act)
