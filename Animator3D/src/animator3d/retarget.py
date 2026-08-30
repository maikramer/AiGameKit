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

Location / root: Quaternius tem ``root`` + ``pelvis.location`` (bob). O target
ganha um ``root`` estático nos pés (nunca animado — retargetar a rotação do
root Quaternius injecta ±90° Y↔Z e a origem salta para a cintura no play).
A location do gait vai para o osso do target que é o hips, a partir do source
a que estiver mapeado — resolvido POR PAPEL (``pelvis``, ``B-hips`` do KevDev,
``Hips`` do Mixamo), nunca por nome fixo de source (ver _resolve_location_pair).

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

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).resolve().parent / "data" / "retarget"

# Só o osso mapeado para o hips do target recebe location — resolvido por PAPEL
# (_resolve_location_pair), não por nome: o source pode chamar-se ``pelvis``
# (Quaternius), ``B-hips`` (KevDev) ou ``Hips`` (Mixamo). O root fica sempre
# estático (ver ensure_feet_root_bone).
_HIPS_CANDIDATES = ("pelvis", "Hips")


def _resolve_location_pair(tgt_to_src: dict[str, str]) -> tuple[str | None, str | None]:
    """Par ``(tgt_hips, src_hips)`` que recebe location (gait/bob); ``(None, None)`` sem hips.

    Procura o osso do target que é o hips (``pelvis``/``Hips``) e devolve o
    source a que está mapeado. Transferir por nome de source fixo partia rigs
    com naming diferente (KevDev ``B-hips``): sem a location do source, o
    tronco fica preso à altura de rest enquanto as pernas dobram — "hang from
    waist", pés a levantar do chão em clips agachados (mining, farming...).
    """
    tgt_hips = next((tgt for tgt in tgt_to_src if tgt in _HIPS_CANDIDATES), None)
    if tgt_hips is None:
        return None, None
    return tgt_hips, tgt_to_src[tgt_hips]


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
    # Packs POR-FICHEIRO (ex.: KevDev, um FBX por clip): subpasta do pack onde
    # vivem os ficheiros de clip; os valores do clip_map passam a caminhos
    # relativos a esta raiz (sem extensão). None = pack de ficheiro único
    # (Quaternius: um GLB com todas as actions).
    source_files_root: str | None = None
    # Combinação de packs (multi-pass): keys deste perfil autorizadas a
    # SUBSTITUIR clips de packs anteriores. None = substitui tudo (comportamento
    # UAL1→UAL2). Lista vazia = só acrescenta, nunca substitui (pack "add-on").
    # Ver plan_pack_passes.
    replace_keys: list[str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def _resolve_profile_path(name_or_path: str | Path) -> Path:
    """Resolve um perfil de retarget para o path do YAML.

    Args:
        name_or_path: nome do perfil (ex.: ``"quaternius"``) ou path absoluto
            para um YAML custom.

    Raises:
        FileNotFoundError: perfil não encontrado.
    """
    p = Path(name_or_path)
    if not p.is_absolute() and not p.is_file():
        candidate = _DATA_DIR / f"{name_or_path}.yaml"
        if not candidate.is_file():
            raise FileNotFoundError(f"Perfil de retarget não encontrado: {name_or_path} (procurei em {candidate})")
        return candidate
    if not p.is_file():
        raise FileNotFoundError(p)
    return p


def load_profile(name_or_path: str | Path) -> RetargetProfile:
    """Carrega um perfil de retarget de um YAML em data/retarget/ ou de um path.

    Args:
        name_or_path: nome do perfil (ex.: ``"quaternius"``) ou path absoluto
            para um YAML custom.

    Raises:
        FileNotFoundError: perfil não encontrado.
    """
    import yaml

    p = _resolve_profile_path(name_or_path)

    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    bone_map = {
        src: [cand] if isinstance(cand, str) else [str(c) for c in cand]
        for src, cand in dict(raw.get("bone_map", {})).items()
    }
    extra: dict[str, Any] = {}
    if raw.get("source_pack"):
        extra["source_pack"] = str(raw["source_pack"])
    clip_map_raw = dict(raw.get("clip_map", {}))
    # Guarda YAML 1.1: ``yes``/``no``/``on``/``off`` sem aspas viram bool e
    # rebentam o retarget a meio (actions.get(True)). Falhar cedo com mensagem clara.
    bad = [k for k, v in clip_map_raw.items() if not isinstance(k, str) or not isinstance(v, str)]
    if bad:
        raise ValueError(
            f'clip_map do perfil {p.name!r} tem keys/valores não-string ({bad!r}) — usa aspas (ex.: "yes": "Yes")'
        )
    replace_keys_raw = raw.get("replace_keys")
    replace_keys: list[str] | None
    if replace_keys_raw is None:
        replace_keys = None
    else:
        if not isinstance(replace_keys_raw, list) or not all(isinstance(k, str) for k in replace_keys_raw):
            raise ValueError(f"replace_keys do perfil {p.name!r} tem de ser uma lista de nomes limpos")
        replace_keys = list(replace_keys_raw)
    return RetargetProfile(
        name=raw.get("profile", p.stem),
        bone_map=bone_map,
        clip_map=clip_map_raw,
        source_path=Path(raw["source_path"]) if raw.get("source_path") else None,
        source_files_root=(str(raw["source_files"]["root"]) if raw.get("source_files", {}).get("root") else None),
        replace_keys=replace_keys,
        extra=extra,
    )


def plan_pack_passes(profiles: list[RetargetProfile]) -> list[tuple[RetargetProfile, dict[str, str]]]:
    """Plano de passes para uma combinação ordenada de packs (multi-pack).

    Cada pack corre por inteiro sobre o rig; a questão é que keys SUBSTITUEM
    clips de packs anteriores e quais só se acrescentam:

    - ``replace_keys is None`` (UAL): substitui todas as colisões — mantém o
      comportamento histórico do ``--anim-pack both`` (UAL2 dedicado > UAL1);
    - ``replace_keys`` definida (villager): substitui SÓ essas keys; restantes
      colisões são ignoradas (o ``idle`` do villager NÃO pisaria o da UAL) e
      as demais keys acrescentam-se.

    Sem este plano, duas tracks NLA com o mesmo nome chegariam ao glTF
    (duplicado no engine).

    Returns:
        ``[(profile, clip_map_efetivo)]`` — o 2.º elemento é o clip_map
        filtrado que esse pass deve retargetizar.
    """
    plan: list[tuple[RetargetProfile, dict[str, str]]] = []
    already: set[str] = set()
    for prof in profiles:
        if prof.replace_keys is None:
            replacing: set[str] = set(prof.clip_map)
        else:
            replacing = {k for k in prof.replace_keys if k in prof.clip_map}
        eff = {k: v for k, v in prof.clip_map.items() if k not in already or k in replacing}
        plan.append((prof, eff))
        already.update(eff)
    return plan


def available_profiles() -> list[str]:
    """Nomes de perfis YAML disponíveis em data/retarget/."""
    if not _DATA_DIR.is_dir():
        return []
    return sorted(p.stem for p in _DATA_DIR.glob("*.yaml"))


# Cabeçalho de secção dentro do bloco ``clip_map:`` (ex.: ``# --- Locomoção ---``).
_SECTION_HEADER_RE = re.compile(r"^#\s*-{2,}\s*(.+?)\s*-{2,}\s*$")
# Entrada ``clean_name:`` (com ou sem aspas) — só a key, para agrupar por texto.
_CLIP_KEY_RE = re.compile(r'^\s+("([^"]+)"|\'([^\']+)\'|[^:#\s][^:]*):')


def profile_clip_groups(name_or_path: str | Path) -> dict[str, str]:
    """Grupos (secções) dos clips de um perfil, lidos do texto do YAML.

    As secções vêm dos comentários ``# --- Nome ---`` dentro do bloco
    ``clip_map:`` — é documentação do catálogo, não schema: se os comentários
    faltarem, os clips ficam com grupo ``""`` e o catálogo continua válido.

    Args:
        name_or_path: nome do perfil (ex.: ``"quaternius2"``) ou path YAML.

    Returns:
        ``{clean_name: grupo}`` na ordem do YAML; grupo ``""`` fora de secções.
    """
    path = _resolve_profile_path(name_or_path)
    groups: dict[str, str] = {}
    in_clips = False
    section = ""
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        if not line[0].isspace():
            in_clips = line.strip() == "clip_map:"
            continue
        if not in_clips:
            continue
        header = _SECTION_HEADER_RE.match(line.strip())
        if header:
            section = header.group(1).strip()
            continue
        entry = _CLIP_KEY_RE.match(line)
        if entry:
            key = entry.group(2) or entry.group(3) or entry.group(1)
            groups[key] = section
    return groups


def _bone_rest_height(arm: Any, names: tuple[str, ...] = _HIPS_CANDIDATES) -> float:
    """Altura rest do hips (máx |y|,|z| do head) — escala location source→target."""
    for name in names:
        bone = arm.data.bones.get(name)
        if bone is None:
            continue
        h = bone.head_local
        return max(abs(float(h.y)), abs(float(h.z)), 1e-3)
    return 1.0


def ensure_feet_root_bone(arm_obj: Any) -> bool:
    """Garante osso ``root`` na origem (pés) como pai de ``pelvis``/``Hips``.

    Rigs SkinTokens chegam muitas vezes com ``pelvis`` como única raiz — o
    ponto fixo da animação fica a meio do corpo. Quaternius usa ``root``→``pelvis``.

    Returns:
        ``True`` se o bone foi criado; ``False`` se já existia ou não aplicável.
    """
    bpy = _bpy()
    if arm_obj is None or getattr(arm_obj, "type", None) != "ARMATURE":
        raise ValueError("ensure_feet_root_bone: objecto ARMATURE obrigatório")
    if "root" in arm_obj.data.bones:
        return False

    hips_name: str | None = None
    for cand in _HIPS_CANDIDATES:
        bone = arm_obj.data.bones.get(cand)
        if bone is not None and bone.parent is None:
            hips_name = cand
            break
    if hips_name is None:
        return False

    bpy.context.view_layer.objects.active = arm_obj
    arm_obj.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ebones = arm_obj.data.edit_bones
        hips = ebones[hips_name]
        root = ebones.new("root")
        root.head = (0.0, 0.0, 0.0)
        hx, hy, hz = float(hips.head.x), float(hips.head.y), float(hips.head.z)
        span = max((hx * hx + hy * hy + hz * hz) ** 0.5 * 0.12, 0.05)
        # Tail ao longo do eixo vertical dominante (Z após import glTF no Blender).
        if abs(hz) >= abs(hy):
            root.tail = (0.0, 0.0, span)
        else:
            root.tail = (0.0, span, 0.0)
        root.use_deform = False
        hips.parent = root
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")
    return True


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


_TRUNK_KEYWORDS = ("spine", "chest", "neck", "head")


def _bone_rest_dir(arm: Any, bone_name: str, mapped: set[str]) -> Any:
    """Direção de rest do osso derivada da GEOMETRIA do esqueleto (armature space).

    O tail sintetizado pelo importador glTF (heurística TEMPERANCE/BLENDER) é
    ruído por rig — no pelvis pode sair horizontal ou para baixo, e o swing
    correction roda a pose do pelvis ~90-180°, empurrando os offsets dos filhos
    (barriga para a frente, "bunda empinada"). A direção semântica de um joint
    é a linha para o próximo joint da cadeia:

    1. filho mapeado de tronco (spine/chest/neck/head) — continua a cadeia;
    2. senão, média dos filhos mapeados (mão→dedos, pé→ball);
    3. senão, direção desde o pai (leaf continua a cadeia);
    4. último recurso: tail do importador.
    """
    bone = arm.data.bones[bone_name]
    kids = [c for c in bone.children if c.name in mapped]
    trunk = [c for c in kids if any(k in c.name.lower() for k in _TRUNK_KEYWORDS)]
    pick = trunk or kids
    if pick:
        acc = None
        for c in pick:
            v = c.head_local - bone.head_local
            acc = v if acc is None else acc + v
        if acc is not None and acc.length > 1e-6:
            return acc.normalized()
    if bone.parent is not None:
        v = bone.head_local - bone.parent.head_local
        if v.length > 1e-6:
            return v.normalized()
    return (bone.tail_local - bone.head_local).normalized()


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
    src_mapped = set(tgt_to_src.values())
    tgt_mapped = set(tgt_to_src)
    for tgt_bn, src_bn in tgt_to_src.items():
        src_bone = source.data.bones[src_bn]
        tgt_bone = target.data.bones[tgt_bn]
        src_dir = _bone_rest_dir(source, src_bn, src_mapped)
        tgt_dir = _bone_rest_dir(target, tgt_bn, tgt_mapped)
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
    # Location: role-based (ver _resolve_location_pair). A escala usa as
    # ALTURAS DOS BONES MAPEADOS — o naming do source não é fixo e packs como
    # o KevDev vêm em cm no FBX: com candidatos fixos (pelvis/Hips ausentes),
    # _bone_rest_height devolvia 1.0 e os ~38 cm do clip entravam como metros.
    tgt_hips, src_hips = _resolve_location_pair(tgt_to_src)
    if tgt_hips is not None and src_hips is not None:
        loc_scale = _bone_rest_height(target, (tgt_hips,)) / _bone_rest_height(source, (src_hips,))
        loc_targets = {tgt_hips}
    else:
        loc_scale = 1.0
        loc_targets: set[str] = set()
    # ``pose.bone.location`` vive no frame de REST do próprio osso, e esses
    # frames diferem entre rigs (Quaternius pelvis ≈ +104° X vs target
    # identidade). Copiar componentes cruas troca vertical↔horizontal: o
    # agachamento (location.y do source) vira deslocamento para trás no
    # target — corpo não desce e os pés "flutuam". Converter via espaço de
    # armature: ``tgt_rest⁻¹ @ src_rest @ location``.
    loc_conv = {
        tgt_bn: (
            target.data.bones[tgt_bn].matrix_local.to_3x3().inverted()
            @ source.data.bones[tgt_to_src[tgt_bn]].matrix_local.to_3x3()
        )
        for tgt_bn in loc_targets
    }

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
            # Hip bob / agachamento: location convertida do frame de rest do
            # osso source para o do target (ver loc_conv acima) + escala.
            if bn in loc_targets:
                sl = loc_conv[bn] @ source.pose.bones[tgt_to_src[bn]].location
                tpb.location = (float(sl.x) * loc_scale, float(sl.y) * loc_scale, float(sl.z) * loc_scale)
                tpb.keyframe_insert(data_path="location", frame=frame)

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
    # Antes dos clips: root nos pés (SkinTokens → pelvis-as-root), estático.
    ensure_feet_root_bone(target)
    if "root" in target.pose.bones:
        pb = target.pose.bones["root"]
        pb.matrix_basis.identity()
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


def retarget_batch_files(
    target_arm_name: str,
    profile: RetargetProfile,
    pack_root: Path,
    *,
    only_clips: list[str] | None = None,
    replace: bool = False,
) -> list[dict[str, Any]]:
    """Retarget de packs POR-FICHEIRO (um FBX por clip, ex.: KevDev villager).

    Diferença de :func:`retarget_batch`: aqui não há um ficheiro source único
    com todas as actions — cada valor do ``clip_map`` é um caminho relativo a
    ``pack_root/profile.source_files_root`` (sem extensão) para um FBX com o
    rig do pack + UMA action. Cada ficheiro é importado, retargetizado e
    descartado antes do clip seguinte (a cena fica só com o target).

    Args:
        target_arm_name: nome do armature alvo.
        profile: perfil com ``bone_map``, ``clip_map`` e ``source_files_root``.
        pack_root: diretório do pack extraído (``ItchPack.root``).
        only_clips: se dado, retargetiza apenas estes nomes limpos.
        replace: se True, limpa as NLA tracks existentes no target antes de começar.

    Returns:
        Lista de resultados por clip (ver :func:`retarget_animation`).
    """
    bpy = _bpy()
    target = bpy.data.objects.get(target_arm_name)
    if target is None:
        raise ValueError(f"Target armature não encontrado: {target_arm_name!r}")
    ensure_feet_root_bone(target)
    if "root" in target.pose.bones:
        pb = target.pose.bones["root"]
        pb.matrix_basis.identity()
    if replace:
        _clear_nla_tracks(target_arm_name)

    clips = profile.clip_map
    if only_clips:
        clips = {k: v for k, v in clips.items() if k in set(only_clips)}

    root = pack_root / (profile.source_files_root or "")
    results = []
    for clean_name, rel in clips.items():
        fbx_path = root / f"{rel}.fbx"
        if not fbx_path.is_file():
            results.append(
                {"clip": clean_name, "source_track": rel, "error": f"Ficheiro do pack não encontrado: {fbx_path}"}
            )
            continue
        try:
            results.append(_retarget_from_file(target_arm_name, fbx_path, profile.bone_map, clean_name))
        except ValueError as e:
            results.append({"clip": clean_name, "source_track": rel, "error": str(e)})
    return results


def _retarget_from_file(
    target_arm_name: str,
    fbx_path: Path,
    bone_map: dict[str, str | list[str]],
    clean_name: str,
) -> dict[str, Any]:
    """Importa um FBX de animação, retargetiza a sua action única e limpa a cena.

    O nome da action nos FBX do pack varia com o nome do objecto importado
    (``Armature[.NNN]|<Take>|Base Layer``) — descobre-se por diferença aos
    actions/objects presentes antes do import.
    """
    from . import bpy_ops

    bpy = _bpy()
    before_objects = set(bpy.data.objects.keys())
    before_actions = {a.name for a in bpy.data.actions}

    bpy_ops.import_asset(fbx_path)
    source = next((o for o in bpy.data.objects if o.type == "ARMATURE" and o.name not in before_objects), None)
    take_actions = [a for a in bpy.data.actions if a.name not in before_actions]
    if source is None:
        raise ValueError(f"FBX sem armature novo: {fbx_path.name}")
    if not take_actions:
        raise ValueError(f"FBX sem action de animação: {fbx_path.name}")

    try:
        return retarget_animation(target_arm_name, source.name, bone_map, take_actions[0].name, clean_name)
    finally:
        # Descartar o source e os actions do take (a action nova do target
        # chama-se ``clean_name`` e não está em take_actions).
        for obj in [o for o in bpy.data.objects if o.name not in before_objects]:
            bpy.data.objects.remove(obj, do_unlink=True)
        for act in take_actions:
            bpy.data.actions.remove(act)


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


def remove_clips(armature_name: str, names: Any) -> list[str]:
    """Remove NLA tracks (e as suas actions) cujo nome está em ``names``.

    Usado no retarget multi-pack (``--anim-pack both``): o segundo pack corre
    depois do primeiro e as keys que colidem (ex.: ``chop``) devem SUBSTITUIR a
    versão anterior, não acumular tracks duplicadas. Só remove actions que
    ficam órfãs (a mesma action pode estar em uso noutra track).
    """
    bpy = _bpy()
    arm = bpy.data.objects.get(armature_name)
    if arm is None or arm.animation_data is None:
        return []
    wanted = {str(n) for n in names}
    removed: list[str] = []
    doomed: list[Any] = []
    for t in list(arm.animation_data.nla_tracks):
        # Nome/acções lidos ANTES do remove: referências RNA de tracks removidas
        # ficam inválidas (StructRNA has been removed).
        track_name = t.name
        if track_name in wanted:
            doomed.extend(s.action for s in t.strips if s.action)
            arm.animation_data.nla_tracks.remove(t)
            removed.append(track_name)
    for act in doomed:
        used = any(
            s.action == act
            for a in bpy.data.objects
            if a.type == "ARMATURE" and a.animation_data
            for tr in a.animation_data.nla_tracks
            for s in tr.strips
        )
        if not used:
            bpy.data.actions.remove(act)
    return removed
