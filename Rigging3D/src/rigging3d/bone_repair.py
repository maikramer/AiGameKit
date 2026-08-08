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


def _import_glb(path: Path) -> tuple[Any, Any]:
    import bpy

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

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        fixed = _repair_edit_bones(arm, min_gap)
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
    )

    return BoneRepairResult(input_path=input_glb, output_path=output_glb, bones_total=bones_total, bones_fixed=fixed)
