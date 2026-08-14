"""Rigging3D — CLI principal.

Backend: SkinTokens (VAST-AI-Research), autoregressivo unificado
(skeleton+skin numa única passada). Ver
``docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md`` no root do monorepo.
"""

from __future__ import annotations

import json
import os
import re
import struct
import time
from collections.abc import Callable
from pathlib import Path

from aigamekit_shared.profiler.session import ProfilerSession
from aigamekit_shared.progress import STATUS_ERROR, STATUS_OK, TOOL_RIGGING3D, emit_progress, emit_result
from aigamekit_shared.quality import VALID_QUALITIES
from rich.console import Console

from . import __version__
from .cli_rich import click

console = Console()

# ---------------------------------------------------------------------------
# Pós-processo backend-agnóstico (nomeação de bones, validação de origem)
# ---------------------------------------------------------------------------


def _is_humanoid_topology(
    bone_nodes: dict[int, int],
    nodes: list[dict[str, object]],
    parent_map: dict[int, int | None],
) -> bool:
    """Gate topológico: a hierarquia de bones tem forma humanoide?

    Evita impor nomes humanoides (pelvis/upperarm_l/thigh_r...) a criaturas
    articuladas (mosquito com 6 patas + asas, scorpion com 8 patas +
    pinças) onde são semanticamente errados e atrapalham o retarget.
    Critério puramente topológico (forma da árvore parent-child):

      - exatamente **uma raiz** de bone (parent não-ossso ou sem parent);
      - **exatamente 2 ramos laterais** saindo da raiz (pernas);
      - a cadeia central (spine) tem **≥2 bones**;
      - o topo do spine ramifica em **2 ou 3 cadeias** (2 braços, opcionalmente
        pescoço/cabeça) — não 6+ como num inseto.

    Validação empírica sobre os assets do simple-rpg (sobre os rigs gerados
    pelo SkinTokens, que têm mais bones do que os LOD0s correspondentes):
      Passam (humanoid): hero(28), bandit(28), witch_boss(62), npc_merchant(28),
              slime(28), boss_ogre(46), bogling(43), sand_wyrm_boss(46).
      Rejeitados (criatura): mosquito(65, 5 cadeias superiores = asas+patas),
                  scorpion(30, 4 pernas).
    Sem limite superior de joints — humanoides detalhados (witch_boss com 62
    bones) dariam falso-negativo. A forma topológica discrimina melhor.
    """
    if len(bone_nodes) < 8:
        return False
    children_of_bone: dict[int, list[int]] = {}
    root_bi: int | None = None
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is None:
            root_bi = bi
            continue
        parent_name = nodes[parent_ni].get("name", "")
        pm = re.match(r"^bone_(\d+)$", parent_name)
        if pm:
            children_of_bone.setdefault(int(pm.group(1)), []).append(bi)
        else:
            root_bi = bi
    if root_bi is None:
        return False
    root_kids = children_of_bone.get(root_bi, [])
    if len(root_kids) < 2:
        return False
    # Spine = cadeia linear a partir do filho com mais descendentes.
    _descendants = _bone_descendants(children_of_bone)
    spine_start = max(root_kids, key=_descendants)
    spine = [root_bi] + _bone_linear_chain(children_of_bone, spine_start)
    if len(spine) < 2:
        return False
    # Laterais = filhos da raiz que não são o spine. Filtrar artefactos do
    # modelo SkinTokens (bones isolados, desc<2) que não são pernas reais.
    # Uma perna real tem pelo menos 2 segmentos (coxa+joelho).
    leg_candidates = [c for c in root_kids if c != spine_start and _descendants(c) >= 2]
    # Legs: >=2 saindo da raiz OU 1 que se ramifica em 2 (pelvis intermédio).
    if len(leg_candidates) >= 2:
        legs_ok = True
    elif len(leg_candidates) == 1 and len(children_of_bone.get(leg_candidates[0], [])) == 2:
        legs_ok = True
    elif len(leg_candidates) == 0 and len(root_kids) >= 3:
        # Fallback: se não há laterais com desc>=2 mas há >=3 filhos na raiz,
        # pode ser um humanoid com pernas curtas — contar laterais não-spine.
        leg_candidates = [c for c in root_kids if c != spine_start]
        legs_ok = len(leg_candidates) >= 2
    else:
        legs_ok = False
    if not legs_ok:
        return False
    uc_kids = children_of_bone.get(spine[-1], [])
    # 2-4 cadeias superiores: 2 braços + opcional pescoço + extra (asas híbridas).
    return 2 <= len(uc_kids) <= 4


def _bone_linear_chain(children_of_bone: dict[int, list[int]], start_bi: int) -> list[int]:
    """Cadeia linear (1 filho) a partir de ``start_bi`` até ramificação/ponta."""
    chain = [start_bi]
    cur = start_bi
    while len(children_of_bone.get(cur, [])) == 1:
        cur = children_of_bone[cur][0]
        chain.append(cur)
    return chain


def _bone_descendants(children_of_bone: dict[int, list[int]]) -> Callable[[int], int]:
    """Devolve ``children_count(bi)`` sobre o mapa de filhos dado."""

    def _count(bi: int) -> int:
        n = 0
        for c in children_of_bone.get(bi, []):
            n += 1 + _count(c)
        return n

    return _count


def _assign_fingers(
    hand_bi: int,
    side: str,
    children_of_bone: dict[int, list[int]],
    assignments: dict[int, str],
) -> None:
    """Nomeia cadeias de dedos ramificadas sob um hand bone.

    O SkinTokens gera tipicamente 5 cadeias de 3 falanges por mão. Cada cadeia
    linear (a partir de cada filho do hand) recebe ``{side}HandFingerN`` na
    base e ``{side}HandFingerN_1/_2`` nas falanges seguintes. Bones já
    atribuídos (e.g. o hand em si) são preservados.

    Args:
        hand_bi: Bone index do LeftHand/RightHand.
        side: "Left" | "Right".
        children_of_bone: mapa bone_index → filhos.
        assignments: mapa bone_index → nome (mutado in-place).
    """
    kids = children_of_bone.get(hand_bi, [])
    if len(kids) < 2:
        return  # Sem dedos ramificados (mão linear já tratada pelo template).
    # Ordena por “lateralidade” para nomeação estável (não é essencial, mas dá
    # resultados reproduzíveis entre runs). Sem geometria, usa a ordem do índice.
    for fi, finger_root in enumerate(kids):
        chain = _bone_linear_chain(children_of_bone, finger_root)
        base = f"{side}HandFinger{fi + 1}"
        for ci, bi in enumerate(chain):
            if bi in assignments:
                continue
            assignments[bi] = base if ci == 0 else f"{base}_{ci}"


def _assign_head_accessories(
    head_bi: int,
    children_of_bone: dict[int, list[int]],
    assignments: dict[int, str],
) -> None:
    """Nomeia cadeias filhas de Head (chapéu, cabelo, orelhas, antenas).

    Cada cadeia linear (a partir de cada filho de Head não já atribuído)
    recebe ``HeadAccessoryN`` na base e ``HeadAccessoryN_1/_2`` nos
    segmentos seguintes. Isto cobre acessórios comuns do SkinTokens como o
    chapéu pontiagudo da bruxa (2 cadeias de 4 bones) ou orelhas élficas.

    Args:
        head_bi: Bone index do Head.
        children_of_bone: mapa bone_index → filhos.
        assignments: mapa bone_index → nome (mutado in-place).
    """
    kids = children_of_bone.get(head_bi, [])
    if not kids:
        return
    ai = 0
    for acc_root in kids:
        if acc_root in assignments:
            continue  # Já classificado (e.g. pescoço invertido).
        chain = _bone_linear_chain(children_of_bone, acc_root)
        base = f"HeadAccessory{ai + 1}"
        for ci, bi in enumerate(chain):
            if bi in assignments:
                continue
            assignments[bi] = base if ci == 0 else f"{base}_{ci}"
        ai += 1


def _write_glb_json_chunk(glb_path: Path, glb_json: dict, remaining: bytes) -> None:
    """Reescreve o chunk JSON de um GLB in-place, mantendo o BIN chunk.

    Args:
        glb_path: Caminho do GLB a reescrever.
        glb_json: Documento JSON (nodes já modificados) a serializar.
        remaining: Bytes do BIN chunk (e chunks seguintes) já lidos.
    """
    new_json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(new_json_bytes) % 4) % 4
    new_json_bytes += b" " * pad
    new_chunk0_len = len(new_json_bytes)
    new_total = 12 + 8 + new_chunk0_len + len(remaining)
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, new_total))
        f.write(struct.pack("<II", new_chunk0_len, 0x4E4F534A))
        f.write(new_json_bytes)
        f.write(remaining)


def _rename_creature_bones(
    bone_nodes: dict[int, int],
    nodes: list[dict[str, object]],
    parent_map: dict[int, int | None],
) -> dict[int, str]:
    """Classifica bones de criaturas (quando o gate humanoid falha).

    Para criaturas articuladas (mosquito, scorpion, dragões) onde não faz
    sentido impor nomes humanoides, atribui nomes genéricos por papel:
    - ``Spine``/``Spine1``...: cadeia central descendente do maior ramo.
    - ``LeftWing``/``RightWing``: ramos laterais do tronco com sub-ramificação
      (asas têm tipicamente dedos/penas — detectamos ramos que se ramificam).
    - ``Tail``/``Tail1``...: cadeia central descendente longa (>=4 bones).
    - ``LegL1..N``/``LegR1..N``: ramos laterais sem sub-ramificação (patas).

    Isto alinha com os ``_CHAIN_NAMES`` do animator3d (wing/tail/leg) para
    que o retarget funcione por nome, não só por geometria.

    Returns:
        Mapa bone_index → nome (vazio se nada a classificar).
    """
    if not bone_nodes:
        return {}
    children_of_bone: dict[int, list[int]] = {}
    root_bi: int | None = None
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is None:
            root_bi = bi
            continue
        parent_name = nodes[parent_ni].get("name", "")
        pm = re.match(r"^bone_(\d+)$", parent_name)
        if pm:
            children_of_bone.setdefault(int(pm.group(1)), []).append(bi)
        else:
            root_bi = bi
    if root_bi is None:
        return {}
    _descendants = _bone_descendants(children_of_bone)
    root_kids = children_of_bone.get(root_bi, [])
    if not root_kids:
        return {}
    assignments: dict[int, str] = {root_bi: "Root"}

    # Spine = maior ramo (tronco principal). Seguir até ao primeiro hub (≥2 kids).
    spine_start = max(root_kids, key=_descendants)
    spine: list[int] = []
    cur = spine_start
    while cur is not None:
        spine.append(cur)
        kids = children_of_bone.get(cur, [])
        cur = kids[0] if len(kids) == 1 else None
    for i, bi in enumerate(spine):
        assignments[bi] = f"Spine{i + 1}" if i > 0 else "Spine"

    # Recolher hubs ao longo do spine: cada hub é um bone do spine com ≥2 filhos.
    # Os hubs classificam patas dianteiras (quadrúpedes) e asas.
    hubs: list[int] = []
    for bi in spine:
        if len(children_of_bone.get(bi, [])) >= 2:
            hubs.append(bi)

    # Função para classificar um ramo lateral como pata ou asa.
    def _classify_lateral(start: int, idx: int) -> None:
        chain = _bone_linear_chain(children_of_bone, start)
        kids = children_of_bone.get(start, [])
        n_desc = _descendants(start)
        # Wing: ramificado (≥2 kids diretos) com muitos descendentes (asas têm
        # dedos/penas). Leg: cadeia linear (pata de inseto/quadrúpede) ou com
        # poucos descendentes.
        is_wing = len(kids) >= 2 and n_desc >= 8
        if is_wing:
            side = "Left" if idx % 2 == 0 else "Right"
            for i, bi in enumerate(chain):
                if bi in assignments:
                    continue
                assignments[bi] = f"{side}Wing" if i == 0 else f"{side}Wing{i}"
        else:
            side = "Left" if idx % 2 == 0 else "Right"
            leg_n = idx // 2 + 1
            for i, bi in enumerate(chain):
                if bi in assignments:
                    continue
                assignments[bi] = f"Leg{side}{leg_n}" if i == 0 else f"Leg{side}{leg_n}_{i}"

    # 1. Patas/asas que saem diretamente da raiz (traseiras em quadrúpedes).
    lateral_idx = 0
    for c in root_kids:
        if c == spine_start:
            continue
        _classify_lateral(c, lateral_idx)
        lateral_idx += 1

    # 2. Patas/asas que saem de hubs do spine (dianteiras em quadrúpedes).
    # Cada hub pode ter um ramo "central" (continuação do spine/pescoço) que
    # não deve ser classificado como pata — identificamo-lo como o filho mais
    # alinhado com o spine (menor |dx| ou mais acima).
    for hub in hubs:
        hub_kids = list(children_of_bone.get(hub, []))
        if len(hub_kids) < 2:
            continue
        # O filho central é o que tem mais descendentes (continuação do tronco).
        central = max(hub_kids, key=_descendants)
        for c in hub_kids:
            if c == central or c in spine:
                continue
            _classify_lateral(c, lateral_idx)
            lateral_idx += 1

    return assignments


def _rename_generic_bones(glb_path: Path, root: Path | None = None) -> int:  # noqa: ARG001
    """Rename ``bone_0..bone_N`` nodes to semantic humanoid names.

    O TokenRig (SkinTokens) prediz nomes genéricos ``bone_N`` para qualquer
    asset cujo ``cls`` não esteja mapeado em ``configs/skeleton/*.yaml``
    (ex.: ``cls="articulation"``, o caso comum aqui — ver
    ``docs/RIGGING3D_SKINTOKENS_MIGRATION_PLAN.md`` §0, risco #5). Este
    pós-processo analisa a **hierarquia de bones** no GLB e atribui nomes
    Quaternius/UE5-style (pelvis, spine_01, upperarm_l, thigh_r, …) por papel
    estrutural — o naming canónico do pipeline, compatível 1:1 com o pack de
    animações Quaternius (``animator3d game-pack`` retargetiza sem remapear).

    Classificação é puramente topológica (forma da árvore parent-child), não
    posicional — funciona independentemente de transforms dos bones. Bones
    que não podem ser classificados com confiança mantêm o nome ``bone_*``.

    **Gate de humanoide** (ver ``_is_humanoid_topology``): só renomeia se a
    hierarquia tiver forma humanoide (2 pernas + spine + 2-3 cadeias
    superiores). Criaturas articuladas (mosquito, scorpion) mantêm ``bone_*``
    — o ``animator3d`` faz retarget geométrico e nomes humanoides mal-postos
    eram piores do que genéricos.

    Args:
        glb_path: Ficheiro GLB de saída, reescrito in-place.
        root: não usado (mantido por compatibilidade de assinatura).

    Returns:
        Número de bones renomeados (0 se nada a fazer ou se a topologia
        não for humanoid).
    """
    _SPINE_NAMES = ["pelvis", "spine_01", "spine_02", "spine_03", "spine_04", "spine_05"]
    _NECK_NAMES = ["neck_01", "Head", "neck_02", "neck_03"]
    _ARM_L = ["clavicle_l", "upperarm_l", "lowerarm_l", "hand_l"]
    _ARM_R = ["clavicle_r", "upperarm_r", "lowerarm_r", "hand_r"]
    _LEG_L = ["thigh_l", "calf_l", "foot_l", "ball_l"]
    _LEG_R = ["thigh_r", "calf_r", "foot_r", "ball_r"]

    try:
        if glb_path.stat().st_size < 20:
            return 0

        with open(glb_path, "rb") as f:
            header = f.read(12)
            if len(header) < 12:
                return 0
            _magic, _ver, _total = struct.unpack("<III", header)
            if _magic != 0x46546C67:
                return 0
            chunk0_header = f.read(8)
            if len(chunk0_header) < 8:
                return 0
            chunk0_len, _ct = struct.unpack("<II", chunk0_header)
            json_bytes = f.read(chunk0_len)
            remaining = f.read()

        glb_json = json.loads(json_bytes)
    except (struct.error, json.JSONDecodeError, OSError):
        return 0
    nodes = glb_json.get("nodes", [])

    bone_re = re.compile(r"^bone_(\d+)$")
    bone_nodes: dict[int, int] = {}  # bone_index → node_index
    for ni, node in enumerate(nodes):
        name = node.get("name", "")
        m = bone_re.match(name)
        if m:
            bone_nodes[int(m.group(1))] = ni

    if not bone_nodes:
        return 0

    parent_map: dict[int, int | None] = {}  # node_index → parent_node_index
    for ni, node in enumerate(nodes):
        for c in node.get("children", []):
            parent_map[c] = ni

    # Gate topológico: só impor nomes humanoides (Quaternius) se o esqueleto
    # for humanoid-like.
    # Criaturas articuladas (mosquito, scorpion): o gate humanoid falha, mas
    # ainda classificamos por papel estrutural (Spine/Wings/Legs/Tail) para
    # alinhar com o retarget do animator3d. Humanoides seguem o camário abaixo.
    if not _is_humanoid_topology(bone_nodes, nodes, parent_map):
        creature_assignments = _rename_creature_bones(bone_nodes, nodes, parent_map)
        if not creature_assignments:
            return 0
        renames: dict[int, str] = {}
        for bi, new_name in creature_assignments.items():
            ni = bone_nodes.get(bi)
            if ni is not None:
                renames[ni] = new_name
        if not renames:
            return 0
        for ni, new_name in renames.items():
            nodes[ni]["name"] = new_name
        _write_glb_json_chunk(glb_path, glb_json, remaining)
        return len(renames)

    children_of_bone: dict[int, list[int]] = {}
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is not None:
            parent_name = nodes[parent_ni].get("name", "")
            pm = bone_re.match(parent_name)
            if pm:
                parent_bi = int(pm.group(1))
                children_of_bone.setdefault(parent_bi, []).append(bi)

    root_bi: int | None = None
    for bi, ni in bone_nodes.items():
        parent_ni = parent_map.get(ni)
        if parent_ni is None:
            root_bi = bi
            break
        parent_name = nodes[parent_ni].get("name", "")
        if not bone_re.match(parent_name):
            root_bi = bi
            break
    if root_bi is None:
        return 0

    root_kids = children_of_bone.get(root_bi, [])
    if not root_kids:
        return 0
    _descendants = _bone_descendants(children_of_bone)
    spine_start = max(root_kids, key=_descendants)
    spine = [root_bi] + _bone_linear_chain(children_of_bone, spine_start)

    upper_chest = spine[-1]
    uc_kids = children_of_bone.get(upper_chest, [])

    leg_starts = [c for c in root_kids if c != spine_start]
    assignments: dict[int, str] = {}
    # Padrão "pelvis intermédio": 1 leg_start que ramifica em 2 pernas
    # (alguns modelos SkinTokens inserem um bone pelvis entre raiz e pernas).
    if len(leg_starts) == 1 and len(children_of_bone.get(leg_starts[0], [])) == 2:
        pelvis = leg_starts[0]
        assignments[pelvis] = "pelvis_helper"
        leg_starts = children_of_bone[pelvis]
    elif len(leg_starts) > 2:
        # Filtrar artefactos do SkinTokens (bones isolados/curtos na raiz).
        # Manter só os 2 laterais com mais descendentes = pernas reais.
        leg_starts = sorted(leg_starts, key=_descendants, reverse=True)[:2]

    uc_chains = sorted(
        [_bone_linear_chain(children_of_bone, c) for c in uc_kids],
        key=len,
    )

    for i, bi in enumerate(spine):
        assignments[bi] = _SPINE_NAMES[i] if i < len(_SPINE_NAMES) else f"Spine{i}"

    neck_done = False
    arm_idx = 0
    arm_templates = [_ARM_L, _ARM_R]
    hand_bones: list[tuple[str, int]] = []  # (side, bone_index) para classificar dedos depois
    for chain in uc_chains:
        if not neck_done and len(chain) <= 3:
            for i, bi in enumerate(chain):
                assignments[bi] = _NECK_NAMES[i] if i < len(_NECK_NAMES) else f"NeckExtra{i}"
            neck_done = True
            # Acessórios de cabeça: filhos de Head (último bone do pescoço) que
            # não foram classificados — chapéu, cabelo, orelhas, antenas.
            head_bi = chain[-1] if chain else None
            if head_bi is not None:
                _assign_head_accessories(head_bi, children_of_bone, assignments)
        else:
            tpl = arm_templates[arm_idx] if arm_idx < len(arm_templates) else None
            side = "Left" if arm_idx == 0 else "Right"
            tpl_len = len(tpl) if tpl else 0
            for i, bi in enumerate(chain):
                if tpl and i < tpl_len:
                    assignments[bi] = tpl[i]
                else:
                    finger_idx = i - tpl_len
                    assignments[bi] = f"{side}HandFinger{finger_idx + 1}"
            # Marca o hand bone (último do template) para classificar dedos ramificados.
            if tpl and len(chain) >= tpl_len:
                hand_bones.append((side, chain[tpl_len - 1]))
            arm_idx += 1

    # Dedos das mãos: cada cadeia linear ramificada sob o hand bone.
    # O SkinTokens gera tipicamente 5 cadeias de 3 falanges por mão.
    for side, hand_bi in hand_bones:
        _assign_fingers(hand_bi, side, children_of_bone, assignments)

    leg_templates = [_LEG_L, _LEG_R]
    for idx, start in enumerate(leg_starts):
        chain = _bone_linear_chain(children_of_bone, start)
        tpl = leg_templates[idx] if idx < len(leg_templates) else None
        for i, bi in enumerate(chain):
            if tpl and i < len(tpl):
                assignments[bi] = tpl[i]

    renames: dict[int, str] = {}  # node_index → new_name
    for bi, new_name in assignments.items():
        ni = bone_nodes.get(bi)
        if ni is not None:
            renames[ni] = new_name

    if not renames:
        return 0

    for ni, new_name in renames.items():
        nodes[ni]["name"] = new_name

    _write_glb_json_chunk(glb_path, glb_json, remaining)
    return len(renames)


def _validate_and_fix_origin(glb_path: Path, tolerance: float = 0.1) -> bool:
    """Valida se a base do modelo está em Y≈0 (convenção feet do Text3D).

    Não aplica correção: reexportar com trimesh removeria armature/skin do GLB rigado.

    Args:
        glb_path: GLB final após geração.
        tolerance: Aceita |min_y| até este valor.

    Returns:
        True se min_y está fora da tolerância (foi emitido aviso); False se OK ou em erro de leitura.
    """
    try:
        import trimesh

        scene = trimesh.load(str(glb_path))
        if isinstance(scene, trimesh.Scene):
            mesh = trimesh.util.concatenate(scene.dump())
        else:
            mesh = scene
        min_y = float(mesh.bounds[0][1])
        if abs(min_y) <= tolerance:
            return False
        click.echo(
            f"  ⚠ Origem: min Y = {min_y:.3f} (esperado ≈0); "
            "GLB rigado não pode ser corrigido aqui — regenerar com origin=feet (Text3D)."
        )
        return True
    except Exception:
        return False


def _fix_bone_orientation_or_warn(glb_path: Path) -> None:
    """Corrige a tail dos bones (defeito sistemático do SkinTokens — ver
    ``bone_repair.py``) direto no output do pipeline, em-lugar.

    Passo automático por defeito: sem isto, GLBs saídos de ``pipeline`` não
    são directamente compatíveis com retargeting de animação externa (ex.:
    ``animator3d retarget``) — a tail de bones como coxa/braço fica solta da
    posição do filho, o que corrompe o eixo de rotação usado para reproduzir
    animações de outro rig. Não falha o pipeline se der erro (best-effort,
    tal como ``_validate_and_fix_origin``); a malha em bind pose nunca é
    afectada (inverse bind matrix cancela sempre a rest pose).

    O re-export do bone_repair já aplica smooth-by-angle (anti V/Tri=3); se
    ele falhar, fazemos aqui um passe só de smooth para não entregar o
    ficheiro flat do export vendored do SkinTokens.
    """
    try:
        from .bone_repair import fix_bone_orientation

        result = fix_bone_orientation(glb_path, glb_path)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ fix-bone-orientation falhou (não fatal): {exc}[/yellow]")
        _smooth_shade_glb_or_warn(glb_path)
        return
    if result.bones_fixed:
        console.print(
            f"[green]Bone orientation:[/green] {len(result.bones_fixed)} bone(s) corrigidos "
            f"({', '.join(result.bones_fixed)}) — pronto para retarget."
        )


def _smooth_shade_glb_or_warn(glb_path: Path) -> None:
    """Passe só de smooth-by-angle + re-export (anti V/Tri=3), best-effort."""
    try:
        import bpy
        from aigamekit_shared.bpy_mesh import clear_scene, smooth_shade_scene

        clear_scene()
        bpy.ops.import_scene.gltf(filepath=str(glb_path))
        meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
        smooth_shade_scene(meshes)
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.export_scene.gltf(
            filepath=str(glb_path),
            export_format="GLB",
            use_selection=True,
            export_apply=False,
            export_skins=True,
            export_animations=True,
            export_normals=True,
            export_tangents=True,
            export_texcoords=True,
            export_materials="EXPORT",
            export_image_format="JPEG",
        )
    except Exception as exc:  # noqa: BLE001
        console.print(f"[yellow]⚠ smooth-shade pós-rig falhou (não fatal): {exc}[/yellow]")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


@click.group()
@click.version_option(version=__version__, prog_name="rigging3d")
@click.option(
    "--profiler",
    "profiler_flag",
    is_flag=True,
    help="Gravar métricas de performance (perf DB).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help='ID de GPU visível ao processo (ex: "0"). Propaga CUDA_VISIBLE_DEVICES.',
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: em rigs multi-GPU pina o modelo na placa com "
        "mais VRAM livre; avisa em GPUs muito pequenas. --gpu-ids explícito ganha. "
        "Env: RIGGING3D_HW_AUTO=0."
    ),
)
@click.pass_context
def cli(
    ctx: click.Context,
    profiler_flag: bool,
    gpu_ids_str: str | None,
    hw_auto: bool,
) -> None:
    """Rigging3D — auto-rigging 3D (skeleton + skin numa única passada, SkinTokens)."""
    ctx.ensure_object(dict)
    ctx.obj["PROFILER"] = profiler_flag
    gpu_ids: list[int] | None = None
    if gpu_ids_str:
        gpu_ids = [int(x) for x in gpu_ids_str.split(",") if x.strip()]

    if hw_auto:
        from .hardware import detect_hardware_profile, hw_auto_enabled

        if hw_auto_enabled():
            hwp = detect_hardware_profile()
            if gpu_ids is None and hwp.gpu_ids is not None:
                gpu_ids = hwp.gpu_ids
                click.echo(f"Hardware (auto): {hwp.summary()}", err=True)
            elif hwp.low_memory_warning and hwp.device == "cuda":
                click.echo(f"Hardware (auto): {hwp.summary()}", err=True)
    ctx.obj["GPU_IDS"] = gpu_ids
    if profiler_flag:
        os.environ["AIGAMEKIT_PROFILE"] = "1"


def _ctx_profiler(ctx: click.Context) -> bool:
    parent = ctx.parent
    if parent is None:
        return False
    return bool(parent.obj.get("PROFILER"))


def _ctx_gpu_ids(ctx: click.Context) -> list[int] | None:
    parent = ctx.parent
    if parent is None:
        return None
    return parent.obj.get("GPU_IDS")


# --- pipeline ---


@cli.command("pipeline")
@click.option("--input", "-i", "mesh", type=click.Path(exists=True, path_type=Path), required=True)
@click.option("--output", "-o", "out", type=click.Path(path_type=Path), required=True)
@click.option("--seed", type=int, default=123, show_default=True, help="Seed reprodutível.")
@click.option(
    "--use-existing-skeleton",
    is_flag=True,
    help="--input já tem skeleton (ex.: de uma corrida anterior); gera só o skin.",
)
@click.option(
    "--transfer/--no-transfer",
    "use_transfer",
    default=True,
    show_default=True,
    help="Reanexa o rig à mesh/textura/escala originais (equivalente ao antigo merge).",
)
@click.option(
    "--postprocess",
    "use_postprocess",
    is_flag=True,
    help="Suaviza skin via voxel (requer `pip install open3d`, não é dependência do pacote).",
)
@click.option("--groups-per-vertex", type=int, default=4, show_default=True, help="Influências de osso por vértice.")
@click.option("--top-k", type=int, default=5, show_default=True)
@click.option("--top-p", type=float, default=0.95, show_default=True)
@click.option("--temperature", type=float, default=1.0, show_default=True)
@click.option("--repetition-penalty", type=float, default=2.0, show_default=True)
@click.option(
    "--num-beams", type=int, default=10, show_default=True, help="Beam search — mais alto = mais qualidade/tempo."
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.pass_context
def pipeline_cmd(
    ctx: click.Context,
    mesh: Path,
    out: Path,
    seed: int,
    use_existing_skeleton: bool,
    use_transfer: bool,
    use_postprocess: bool,
    groups_per_vertex: int,
    top_k: int,
    top_p: float,
    temperature: float,
    repetition_penalty: float,
    num_beams: int,
    quality: str,
) -> None:
    """Gera um GLB rigado (skeleton + skin, um único passo autoregressivo)."""
    from aigamekit_shared.gpu import warn_if_vram_occupied
    from aigamekit_shared.quality import QualityEngine

    from .skintokens_runner import run_rig_inprocess

    gpu_ids = _ctx_gpu_ids(ctx)
    do_profile = _ctx_profiler(ctx)

    warn_if_vram_occupied()

    _src = click.core.ParameterSource
    _user_set_groups = ctx.get_parameter_source("groups_per_vertex") not in (_src.DEFAULT,)

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("rigging3d", quality=quality)
    if not _user_set_groups and "groups_per_vertex" in _qresolved.params:
        groups_per_vertex = _qresolved.params["groups_per_vertex"]

    item_id = mesh.stem
    t0 = time.monotonic()

    _old_cuda = os.environ.get("CUDA_VISIBLE_DEVICES")
    try:
        if gpu_ids:
            os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(str(g) for g in gpu_ids)
        # Sem --gpu-ids: NÃO tocar na variável. O pop antigo apagava a pinagem
        # do operador (ex.: CUDA_VISIBLE_DEVICES=1) e o torch via TODAS as GPUs
        # — o oposto do pretendido.

        with ProfilerSession(
            "rigging3d",
            cli_profile=do_profile,
            params={"seed": seed, "groups_per_vertex": groups_per_vertex, "num_beams": num_beams},
        ):
            emit_progress(item_id, TOOL_RIGGING3D, phase="rig", percent=0)
            try:
                run_rig_inprocess(
                    str(mesh),
                    str(out),
                    use_skeleton=use_existing_skeleton,
                    use_transfer=use_transfer,
                    use_postprocess=use_postprocess,
                    top_k=top_k,
                    top_p=top_p,
                    temperature=temperature,
                    repetition_penalty=repetition_penalty,
                    num_beams=num_beams,
                    group_per_vertex=groups_per_vertex,
                    seed=seed,
                )
            except Exception as exc:
                emit_result(
                    item_id,
                    TOOL_RIGGING3D,
                    STATUS_ERROR,
                    phase="rig",
                    error=str(exc),
                    seconds=time.monotonic() - t0,
                )
                raise click.ClickException(f"pipeline falhou: {exc}") from exc
            if not out.is_file() or out.stat().st_size == 0:
                emit_result(
                    item_id,
                    TOOL_RIGGING3D,
                    STATUS_ERROR,
                    phase="rig",
                    error="pipeline não produziu GLB",
                    seconds=time.monotonic() - t0,
                )
                raise click.ClickException("pipeline não produziu GLB.")
            emit_progress(item_id, TOOL_RIGGING3D, phase="rig", percent=100)

            renamed = _rename_generic_bones(out)
            if renamed:
                console.print(f"[green]Renomeados {renamed} ossos para nomes semânticos (humanoid).[/green]")
            _validate_and_fix_origin(out)
            _fix_bone_orientation_or_warn(out)
    finally:
        if _old_cuda is not None:
            os.environ["CUDA_VISIBLE_DEVICES"] = _old_cuda
        else:
            os.environ.pop("CUDA_VISIBLE_DEVICES", None)

    console.print(f"[green]Pipeline concluído:[/green] {out}")
    emit_result(item_id, TOOL_RIGGING3D, STATUS_OK, output=str(out), seconds=time.monotonic() - t0)


# --- transfer-weights (stage 8, independente de backend) ---


@cli.command("transfer-weights")
@click.option(
    "--source",
    "-s",
    "source_glb",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="GLB rigged high-poly (saída de ``rigging3d pipeline`` sobre _clean.glb).",
)
@click.option(
    "--target",
    "-t",
    "targets",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    multiple=True,
    required=True,
    help="GLB(s) target(s) — use múltiplas vezes para LOD0/1/2.",
)
@click.option(
    "--output",
    "-o",
    "outputs",
    type=click.Path(dir_okay=False, path_type=Path),
    multiple=True,
    default=None,
    help=(
        "Caminhos explícitos de output (1:1 com --target). Se omitido, escreve "
        "ao lado de cada target com sufixo ``_rigged``."
    ),
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Pasta de saída comum quando --output não é especificado.",
)
@click.option(
    "--output-suffix",
    type=str,
    default="_rigged",
    show_default=True,
    help="Sufixo aplicado ao stem do target quando --output não é especificado.",
)
@click.option(
    "--finish/--no-finish",
    default=True,
    show_default=True,
    help="Round 2: aplica gltf_transform_finish (dedup+prune+uastc+meshopt+tangents) aos outputs.",
)
@click.option(
    "--animation-source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "GLB com clips (NLA/actions) quando --source não tem animações "
        "(ex.: rigged_hi + id_lod0.glb já animado). Usa aigamekit_shared.skin_transfer."
    ),
)
def transfer_weights_cmd(
    source_glb: Path,
    targets: tuple[Path, ...],
    outputs: tuple[Path, ...],
    output_dir: Path | None,
    output_suffix: str,
    finish: bool,
    animation_source: Path | None,
) -> None:
    """Stage 8 — transfere weights + skeleton + animações para LOD0/1/2.

    Implementação: ``aigamekit_shared.skin_transfer`` (KDTree weights, bind
    armature, copia actions/NLA). Ideal para reaproveitar ``rigged_hi`` em
    meshes decimadas sem re-correr o modelo de skinning.
    """
    from .transfer_weights import transfer_weights

    out_list: list[Path] | None = list(outputs) if outputs else None
    if out_list is not None and len(out_list) != len(targets):
        raise click.UsageError("Número de --output deve coincidir com o de --target.")

    try:
        results = transfer_weights(
            source_glb,
            list(targets),
            output_dir=output_dir,
            output_suffix=output_suffix,
            targets_out=out_list,
            apply_finish=finish,
            animation_source=animation_source,
        )
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    for r in results:
        try:
            sz = r.target_out.stat().st_size
            sz_str = f"{sz / 1024:.0f} KB" if sz < 1024 * 1024 else f"{sz / (1024 * 1024):.2f} MB"
        except OSError:
            sz_str = "?"
        anims = getattr(r, "animations", 0)
        console.print(
            f"[bold green]✓[/bold green] transfer-weights → "
            f"[cyan]{r.target_out}[/cyan] [dim]({sz_str}, "
            f"{r.bones} bones, {r.vertex_groups} vgroups, {anims} anims)[/dim]"
        )


@cli.command("fix-bone-orientation")
@click.argument("input_glb", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("output_glb", type=click.Path(dir_okay=False, path_type=Path), required=False)
@click.option(
    "--min-gap",
    type=float,
    default=0.01,
    show_default=True,
    help="Gap mínimo (m, espaço de armature) entre tail e head do filho para considerar defeituoso.",
)
def fix_bone_orientation_cmd(input_glb: Path, output_glb: Path | None, min_gap: float) -> None:
    """Corrige bones cuja ``tail`` não aponta para o filho real (defeito do
    SkinTokens observado em coxas/braços — ex.: coxa a apontar para o lado em
    vez de para baixo). Não mexe na malha em bind pose (inverse bind matrix
    cancela sempre a rest pose) nem nos pesos — só corrige a direção usada
    por retargeting de animação. Sem OUTPUT_GLB, sobrescreve o input.

    Ex.: rigging3d fix-bone-orientation hero_rigged.glb
    """
    from .bone_repair import fix_bone_orientation

    out = output_glb if output_glb is not None else input_glb
    try:
        result = fix_bone_orientation(input_glb, out, min_gap=min_gap)
    except Exception as exc:
        raise click.ClickException(str(exc)) from exc

    if result.bones_fixed:
        console.print(
            f"[bold green]✓[/bold green] fix-bone-orientation → [cyan]{result.output_path}[/cyan] "
            f"[dim]({len(result.bones_fixed)}/{result.bones_total} bones corrigidos: "
            f"{', '.join(result.bones_fixed)})[/dim]"
        )
    else:
        console.print(
            f"[dim]fix-bone-orientation → {result.output_path} (0/{result.bones_total} bones — já correcto)[/dim]"
        )


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------


def main() -> None:
    cli(obj={})


if __name__ == "__main__":
    main()
