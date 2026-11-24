"""Repara a orientação (tail/eixo local) de bones cuja direção não aponta para
o filho real — defeito observado em rigs gerados por ``rigging3d pipeline``
(SkinTokens): a posição/hierarquia dos bones fica correta para o skinning,
mas a ``tail`` de alguns bones (ex.: ``LeftUpLeg``, ``LeftArm``) fica solta,
sem coincidir com a ``head`` do filho — em vez de poucos milímetros de folga,
foram medidos gaps de ordem de grandeza do próprio osso (ex.: ``LeftUpLeg``
tail a 0.38 m de ``LeftLeg`` head, sendo o osso 0.335 m de comprimento; coxa a
apontar ~72° para o lado em vez de para baixo).

Isto não afecta a malha em bind pose (glTF skinning cancela sempre a rest pose
via inverse bind matrix, seja lá qual for a orientação do bone) mas quebra
qualquer retargeting de animação que dependa da direção do bone em rest (ver
``animator3d retarget``) — a rotação animada acaba a girar em torno de um eixo
que não corresponde à direção real do membro (ex.: joelho a girar para fora).

Fix: para cada bone com exactamente UM filho, redefine ``tail = filho.head``
(mesmo espaço de armature), preservando ``head`` e ``roll``. Bones-folha (sem
filho) ou pontos de ramificação (>1 filho, ex. Hips → Spine + UpLeg×2) ficam
intocados — a ambiguidade de qual filho define a tail não tem resposta única,
e não foram esses os bones com o defeito medido.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


@dataclass
class BoneRepairResult:
    input_path: Path
    output_path: Path
    bones_total: int
    bones_fixed: list[str]


def _repair_edit_bones(arm: Any, min_gap: float) -> list[str]:
    """Alinha tails de cadeias simples com a head do filho."""
    fixed: list[str] = []
    for bone in arm.data.edit_bones:
        if len(bone.children) != 1:
            continue
        child = bone.children[0]
        if (child.head - bone.tail).length < min_gap:
            continue
        bone.tail = child.head.copy()
        fixed.append(bone.name)
    return fixed


def _linear_bone_chain(start: Any) -> list[Any]:
    """Segue uma cadeia linear de bones (1 filho por nível) a partir de ``start``."""
    chain = [start]
    cur = start
    while len(cur.children) == 1:
        cur = cur.children[0]
        chain.append(cur)
    return chain


def _repair_asymmetric_limb_chains(arm: Any, meshes: list[Any]) -> list[str]:
    """Repara membros bilaterais duplicados/em falta em criaturas multi-pata.

    Em artrópodes/aracnídeos (6–10 membros radiais sob a raiz), o modelo
    autoregressivo SkinTokens coloca ocasionalmente duas cadeias sobrepostas no
    mesmo membro e deixa o membro simétrico do lado oposto sem ossos (com pesos
    a vazar para um osso vizinho). Quando a malha é bilateralmente simétrica:

    1. Detecta pares de cadeias sobrepostas no mesmo lado (joelho a <5% da
       extensão do corpo) + cadeia sem par no lado oposto cujo espelho cai
       dentro da malha real.
    2. Transfere os pesos da cadeia duplicada (sobre o membro partilhado) para
       a cadeia sobrevivente, reposiciona a duplicada no espelho exato da
       cadeia oposta (adicionando o segmento final se faltar) e copia os pesos
       limpos por proximidade espelhada (KDTree).
    3. Alinha pontas folha perdidas fora da malha (ex.: ponta de pinça virada
       90° para fora) com o espelho do par bilateral quando o espelho está
       claramente mais próximo da superfície da malha.

    Só actua em rigs multi-membro (≥6 ramos diretos na raiz) — humanoides e
    quadrúpedes comuns não são tocados.
    """
    import bpy
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    if not meshes or not arm.data.bones:
        return []
    root = next((b for b in arm.data.bones if b.parent is None), None)
    if root is None or len(root.children) < 6:
        return []

    main_mesh = max(meshes, key=lambda o: len(o.data.vertices))
    n_verts = len(main_mesh.data.vertices)
    if n_verts < 100:
        return []

    to_arm = arm.matrix_world.inverted() @ main_mesh.matrix_world
    verts_arm = [to_arm @ v.co for v in main_mesh.data.vertices]
    xs = [p.x for p in verts_arm]
    ys = [p.y for p in verts_arm]
    zs = [p.z for p in verts_arm]
    span_x = max(xs) - min(xs)
    span_y = max(ys) - min(ys)
    span_z = max(zs) - min(zs)
    body_extent = max(span_x, span_y, span_z, 1e-4)

    # Eixo lateral = eixo horizontal mais curto (Y se o corpo corre ao longo de X; X se ao longo de Y).
    lat_ax = 1 if span_x > span_y else 0
    long_ax = 1 - lat_ax
    mid_raw = 0.5 * ((min(ys) + max(ys)) if lat_ax == 1 else (min(xs) + max(xs)))
    mid = 0.0 if abs(mid_raw) < 0.02 * body_extent else mid_raw
    max_lat = max(abs(p[lat_ax] - mid) for p in verts_arm)
    if max_lat < 1e-4:
        return []

    def _mirror(v: Vector) -> Vector:
        out = v.copy()
        out[lat_ax] = 2.0 * mid - v[lat_ax]
        return out

    kd = KDTree(n_verts)
    for i, p in enumerate(verts_arm):
        kd.insert(p, i)
    kd.balance()

    # Recolher cadeias laterais multi-osso a partir da raiz.
    chains: list[dict[str, Any]] = []
    for child in root.children:
        chain_bones = _linear_bone_chain(child)
        if len(chain_bones) < 3:
            continue
        heads = [b.head_local.copy() for b in chain_bones]
        tails = [b.tail_local.copy() for b in chain_bones]
        tip_lat = max((abs(h[lat_ax] - mid) for h in heads + [tails[-1]]), default=0.0)
        if tip_lat < 0.25 * max_lat:
            continue
        mid_idx = min(2, len(heads) - 1)
        mid_pt = heads[mid_idx]
        side = 1 if (mid_pt[lat_ax] - mid) >= 0.0 else -1
        chains.append(
            {
                "names": [b.name for b in chain_bones],
                "heads": heads,
                "tails": tails,
                "side": side,
                "mid_pt": mid_pt,
            }
        )

    if len(chains) < 4:
        return []

    # 1) Detetar cadeias duplicadas (mesmo lado, joelho a <5% da extensão).
    overlap_tol = 0.05 * body_extent
    match_tol = 0.08 * body_extent
    mesh_tol = 0.045 * body_extent

    relocations: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = []
    used_dups: set[int] = set()
    used_sources: set[int] = set()

    for i in range(len(chains)):
        if i in used_dups:
            continue
        for j in range(i + 1, len(chains)):
            if j in used_dups or chains[i]["side"] != chains[j]["side"]:
                continue
            if (chains[i]["mid_pt"] - chains[j]["mid_pt"]).length >= overlap_tol:
                continue
            side = chains[i]["side"]
            # Procurar cadeia sem par no lado oposto cujo espelho cai na malha.
            best_src_idx: int | None = None
            for k, cand in enumerate(chains):
                if k in used_sources or cand["side"] == side:
                    continue
                m_mid = _mirror(cand["mid_pt"])
                has_partner = any(
                    other["side"] == side and (other["mid_pt"] - m_mid).length < match_tol for other in chains
                )
                if has_partner:
                    continue
                # Verificar se os joints espelhados caem dentro/junto da malha real.
                sample_pts = [_mirror(h) for h in cand["heads"][1:]]
                if all(kd.find(pt)[2] < mesh_tol for pt in sample_pts):
                    best_src_idx = k
                    break
            if best_src_idx is None:
                continue

            src = chains[best_src_idx]
            src_long = src["heads"][0][long_ax]
            # Mover o duplicado cuja raiz já estava mais próxima do membro em falta.
            di = abs(chains[i]["heads"][0][long_ax] - src_long)
            dj = abs(chains[j]["heads"][0][long_ax] - src_long)
            dup, keep = (chains[i], chains[j]) if di <= dj else (chains[j], chains[i])
            relocations.append((dup, keep, src))
            used_dups.update({i, j})
            used_sources.add(best_src_idx)
            break

    # 2) Detetar pontas folha perdidas fora da malha em pares simétricos.
    leaf_fixes: list[tuple[str, Vector]] = []
    for i, c1 in enumerate(chains):
        for c2 in chains[i + 1 :]:
            if c1["side"] == c2["side"] or len(c1["names"]) != len(c2["names"]):
                continue
            if any(
                (c1["heads"][m] - _mirror(c2["heads"][m])).length > 0.06 * body_extent
                for m in range(1, len(c1["heads"]))
            ):
                continue
            t1, t2 = c1["tails"][-1], c2["tails"][-1]
            mt2, mt1 = _mirror(t2), _mirror(t1)
            if (t1 - mt2).length <= 0.08 * body_extent:
                continue
            d_t1, d_mt2 = kd.find(t1)[2], kd.find(mt2)[2]
            d_t2, d_mt1 = kd.find(t2)[2], kd.find(mt1)[2]
            if d_t1 > d_mt2 + 0.025 * body_extent:
                leaf_fixes.append((c1["names"][-1], mt2))
            elif d_t2 > d_mt1 + 0.025 * body_extent:
                leaf_fixes.append((c2["names"][-1], mt1))

    if not relocations and not leaf_fixes:
        return []

    fixed_names: list[str] = []
    reloc_plan: list[tuple[list[str], list[str], list[str], int]] = []

    # Aplicar alterações de edit_bones em modo EDIT.
    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        ebs = arm.data.edit_bones
        for dup, keep, src in relocations:
            dup_names = list(dup["names"])
            src_names = list(src["names"])
            base_name = dup_names[0]
            # Ajustar número de segmentos ao espelho.
            while len(dup_names) < len(src_names):
                idx = len(dup_names)
                new_name = f"{base_name}_{idx}"
                while new_name in ebs:
                    idx += 1
                    new_name = f"{base_name}_{idx}"
                nb = ebs.new(new_name)
                nb.parent = ebs[dup_names[-1]]
                dup_names.append(new_name)
            for bn, sh, st in zip(dup_names, src["heads"], src["tails"], strict=False):
                eb = ebs[bn]
                eb.head = _mirror(sh)
                eb.tail = _mirror(st)
                fixed_names.append(bn)
            # Remover excedentes se a cadeia duplicada fosse mais longa que o espelho.
            for extra in dup_names[len(src_names) :]:
                if extra in ebs:
                    ebs.remove(ebs[extra])
            dup_names = dup_names[: len(src_names)]
            reloc_plan.append((dup_names, list(keep["names"]), src_names, int(dup["side"])))

        for bn, new_tail in leaf_fixes:
            eb = ebs.get(bn)
            if eb is not None:
                eb.tail = new_tail.copy()
                if bn not in fixed_names:
                    fixed_names.append(bn)
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    # Reparar pesos de skinning apenas nos vértices das zonas afectadas.
    if reloc_plan:
        vgs = main_mesh.vertex_groups
        vg_by_idx = {vg.index: vg.name for vg in vgs}
        root_name = root.name

        # Snapshot dos pesos atuais de cada vértice para consulta espelhada limpa.
        orig_weights: list[dict[str, float]] = [
            {vg_by_idx[g.group]: float(g.weight) for g in v.groups if g.weight > 1e-6} for v in main_mesh.data.vertices
        ]

        for dup_names, keep_names, src_names, side in reloc_plan:
            for bn in dup_names:
                if bn not in vgs:
                    vgs.new(name=bn)
            dup_set = set(dup_names)
            src_set = set(src_names)
            src_to_dup = dict(zip(src_names, dup_names, strict=True))
            dup_to_keep = {dup_names[i]: keep_names[min(i, len(keep_names) - 1)] for i in range(len(dup_names))}

            updated_verts: dict[int, dict[str, float]] = {}
            for vi, p in enumerate(verts_arm):
                w_cur = orig_weights[vi]
                on_side = (p[lat_ax] - mid) * side > 0.04 * body_extent
                # Verificar se o ponto espelhado pertence ao membro fonte limpo.
                mir_idx = -1
                mir_dist = 1e9
                mir_src_w = 0.0
                if on_side:
                    _, mir_idx, mir_dist = kd.find(_mirror(p))
                    if mir_dist < 0.03 * body_extent:
                        mir_w = orig_weights[mir_idx]
                        mir_src_w = sum(w for name, w in mir_w.items() if name in src_set)

                if mir_src_w > 0.05:
                    # Vértice do membro espelhado sem osso: copiar pesos limpos do membro oposto.
                    new_w: dict[str, float] = {}
                    for name, w in orig_weights[mir_idx].items():
                        if name in src_to_dup:
                            tgt = src_to_dup[name]
                        elif name == root_name:
                            tgt = root_name
                        else:
                            # Outros pesos residuais da carapaça caem na raiz.
                            tgt = root_name
                        new_w[tgt] = new_w.get(tgt, 0.0) + w
                    total_w = sum(new_w.values())
                    if total_w > 1e-6:
                        updated_verts[vi] = {k: v / total_w for k, v in new_w.items()}
                elif any(name in dup_set for name in w_cur):
                    # Vértice do membro partilhado (ou fuga antiga): transferir dup → keep.
                    new_w = {}
                    for name, w in w_cur.items():
                        tgt = dup_to_keep.get(name, name)
                        new_w[tgt] = new_w.get(tgt, 0.0) + w
                    total_w = sum(new_w.values())
                    if total_w > 1e-6:
                        updated_verts[vi] = {k: v / total_w for k, v in new_w.items()}

            # Aplicar as novas atribuições de pesos nos vertex groups.
            all_vg_objs = {vg.name: vg for vg in vgs}
            for vi, new_w in updated_verts.items():
                for old_name in orig_weights[vi]:
                    if old_name not in new_w and old_name in all_vg_objs:
                        all_vg_objs[old_name].remove([vi])
                for name, w in new_w.items():
                    all_vg_objs[name].add([vi], w, "REPLACE")

    return fixed_names


_ARM_CHAINS = (("upperarm_l", "lowerarm_l", "hand_l"), ("upperarm_r", "lowerarm_r", "hand_r"))
# Acima disto o braço está mesmo dobrado na malha (não é ruído do SkinTokens).
_MAX_ELBOW_KINK_DEG = 45.0


def _straighten_elbows(arm: Any, *, min_move: float = 1e-3) -> list[str]:
    """Põe o cotovelo na linha ombro→pulso, mantendo só a dobra para trás.

    O SkinTokens às vezes põe o joint do cotovelo 2-3 cm acima/abaixo de um
    braço reto (hero: 3 cm acima, kink 25°). O retarget alinha a direção de
    cada osso com a do source (braço reto do Quaternius), por isso esse kink
    falso aparece invertido na malha: o antebraço dobra para cima/baixo —
    «cotovelo virado». Num A-pose a única dobra natural é para trás; o resto
    do desvio sai.
    """
    import math

    from mathutils import Vector

    back = (arm.matrix_world.to_3x3().inverted() @ Vector((0.0, 1.0, 0.0))).normalized()
    ebs = arm.data.edit_bones
    fixed: list[str] = []
    for upper_n, lower_n, hand_n in _ARM_CHAINS:
        upper, lower, hand = ebs.get(upper_n), ebs.get(lower_n), ebs.get(hand_n)
        if upper is None or lower is None or hand is None:
            continue
        sh, el, wr = upper.head.copy(), lower.head.copy(), hand.head.copy()
        u, v = el - sh, wr - el
        if u.length < 1e-6 or v.length < 1e-6 or math.degrees(u.angle(v)) > _MAX_ELBOW_KINK_DEG:
            continue
        line = (wr - sh).normalized()
        on_line = sh + line * u.dot(line)
        off = el - on_line
        # Componente "para trás" perpendicular à linha do braço.
        back_perp = back - line * back.dot(line)
        keep = Vector((0.0, 0.0, 0.0))
        if back_perp.length > 1e-6:
            back_perp.normalize()
            keep = back_perp * max(0.0, off.dot(back_perp))
        new_el = on_line + keep
        if (new_el - el).length < min_move:
            continue
        upper.tail = new_el
        lower.head = new_el
        fixed.append(lower_n)
    return fixed


def _import_glb(path: Path) -> tuple[Any, Any]:
    import bpy

    try:
        from aigamekit_shared.bpy_mesh import import_gltf

        import_gltf(path)
    except Exception:
        bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not arms:
        raise ValueError(f"GLB sem armature: {path}")
    return meshes, arms[0]


def fix_bone_orientation(
    input_glb: str | Path,
    output_glb: str | Path,
    *,
    min_gap: float = 0.01,
) -> BoneRepairResult:
    """Corrige a ``tail`` de bones de cadeia simples (1 filho) para apontar
    exactamente para a ``head`` do filho.

    Args:
        input_glb: GLB rigged de entrada (ex.: ``hero_rigged.glb``).
        output_glb: Caminho de saída (pode ser igual ao input para overwrite).
        min_gap: gap mínimo (metros, espaço de armature) entre ``tail`` e
            ``head`` do filho para considerar o bone como "a precisar de
            correcção" — evita reescrever bones já correctos (ruído de
            ponto flutuante) e mantém o relatório focado nos casos reais.

    Returns:
        BoneRepairResult com a lista de bones corrigidos.
    """
    import bpy
    from aigamekit_shared.bpy_mesh import clear_scene, smooth_shade_scene

    input_glb = Path(input_glb)
    output_glb = Path(output_glb)

    clear_scene()
    meshes, arm = _import_glb(input_glb)

    sym_fixed = _repair_asymmetric_limb_chains(arm, meshes)

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        fixed = list(sym_fixed)
        fixed += [b for b in _straighten_elbows(arm) if b not in fixed]
        fixed += [b for b in _repair_edit_bones(arm, min_gap) if b not in fixed]
    finally:
        bpy.ops.object.mode_set(mode="OBJECT")

    bones_total = len(arm.data.bones)
    log.info("fix-bone-orientation: %d/%d bones corrigidos em %s", len(fixed), bones_total, input_glb.name)

    # Anti V/Tri=3: o export do SkinTokens (vendored, sem kwargs) sai flat e
    # este é o último passe do pipeline sobre o ficheiro — smooth-by-angle
    # aqui faz o exporter deduplicar loops (rigged do ogre: 2.8M → ~470k verts).
    smooth_shade_scene(meshes)

    output_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(
        filepath=str(output_glb),
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
        # Data maps PBR (metallicRoughness/normal) saem com banding no q75.
        export_jpeg_quality=92,
    )

    return BoneRepairResult(input_path=input_glb, output_path=output_glb, bones_total=bones_total, bones_fixed=fixed)
