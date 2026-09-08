"""Carregamento e exportação de meshes 3D (GLB/GLTF via bpy canónico)."""

from __future__ import annotations

import contextlib
from pathlib import Path

from aigamekit_shared.bpy_mesh import load_glb
from aigamekit_shared.bpy_mesh import save_glb as _bpy_save_glb

_MERGE_THRESHOLD = 2e-4


def load_mesh_bpy(path: str | Path) -> list:
    """Carrega GLB/GLTF via bpy e devolve lista de mesh objects."""
    return load_glb(path)


def _merge_duplicates_bmesh(obj, threshold: float = _MERGE_THRESHOLD) -> None:
    """Merge duplicate vertices (delegado em ``aigamekit_shared.mesh_repair``)."""
    import logging

    from aigamekit_shared.mesh_repair import remove_doubles

    before = len(obj.data.vertices)
    removed = remove_doubles(obj, threshold=threshold)
    logging.getLogger("paint3d.save_glb").info("bmesh merge: %d → %d verts", before, before - removed)


def save_glb(objects, output_path: str | Path, *, verify_stage: str = "painted") -> Path:
    """Exporta mesh objects via ``aigamekit_shared.bpy_mesh.save_glb``.

    Mesmo contrato que Text3D/Rigging/Animator: shade-smooth + NORMAL+TANGENT,
    com ``export_image_format="AUTO"`` para preservar o PBR do paint (albedo
    JPEG + metallicRoughness/normal PNG). Merge de duplicados só quando há UVs
    (costuras de atlas).

    Args:
        objects: Objecto(s) bpy a exportar.
        output_path: GLB de saída.
        verify_stage: Estágio para o ``glb_verify``. A mesma função escreve o
            **input** do paint (ainda sem UVs — o unwrap é feito dentro do
            pipeline), e verificá-lo como ``painted`` dava um ERROR ``NO_UV``
            que não é erro nenhum. Nesse caso passar ``"to_paint"``.
    """
    if not isinstance(objects, (list, tuple)):
        objects = [objects]

    mesh_objs = [obj for obj in objects if getattr(obj, "type", None) == "MESH"]
    for obj in mesh_objs:
        if obj.data.uv_layers:
            _merge_duplicates_bmesh(obj)

    from aigamekit_shared.bpy_mesh import smooth_shade_scene

    # 180°: painted cartoon — sem creases duros que o exporter parta em seams.
    smooth_shade_scene(mesh_objs, degrees=180.0)

    # Escrita atómica: o caminho final é gravado em vários passes ao longo do
    # pipeline (save → fit AABB → postprocess). Um crash entre passes deixava
    # um GLB intermédio no caminho final, que o resume aceitava como concluído
    # ("remendo" entre runs). temp + os.replace garante: ou o ficheiro final
    # completo, ou o anterior intacto.
    import os

    out = Path(output_path)
    # Manter o sufixo .glb no tmp: o exporter bpy acrescenta a extensão
    # quando o filepath não termina na esperada (x.tmp virava x.tmp.glb).
    tmp = out.with_name(out.stem + ".tmp" + out.suffix)
    try:
        _bpy_save_glb(
            objects,
            tmp,
            export_normals=True,
            export_tangents=True,
            # AUTO preserva o formato de cada imagem embutida: albedo JPEG +
            # metallicRoughness/normal PNG (dados). JPEG global reencodava os
            # data maps com perda (banding na roughness, artefactos na normal).
            export_image_format="AUTO",
            export_jpeg_quality=95,
            verify_stage=verify_stage,
        )
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp.unlink()
        raise
    return out
    # Idempotente: repõe occlusionTexture se um re-export bpy a deixou cair
    # (a heurística do helper só toca em materiais ORM+normal sem slot).
    from aigamekit_shared.gltf_occlusion import ensure_occlusion_texture

    ensure_occlusion_texture(output_path)
    return Path(output_path)


load_mesh_trimesh = load_mesh_bpy
