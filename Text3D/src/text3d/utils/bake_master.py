"""Stage 4 — bake-master.

Produz o LOD0 final (master) a partir de ``id_painted.glb`` (high-poly
texturizado) e opcionalmente ``id_clean.glb`` (high-poly limpo, fonte do
normal map).

Pipeline:

1. Decimação UV-aware via ``remesh_textured_glb`` (existente).
2. Sessão bpy unificada (Fase 6.2): import decimated + (opcional) bake
   normal map high→low + ``calc_tangents()`` + export.
3. ``gltf_transform_finish`` (dedup + prune + uastc + meshopt).

Antes da Round 2 esta lib reabria 3x o bpy para tarefas separadas; agora
faz tudo numa sessão e delega a finalização à lib comum.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class BakeMasterResult:
    output_path: Path
    decimated_faces: int
    tangents_added: bool
    normal_map_path: Path | None
    ktx2_applied: bool
    meshopt_applied: bool
    dedup_applied: bool
    prune_applied: bool
    validation_ok: bool | None = None  # None = não corrida


def _bake_master_bpy_session(
    decimated_glb: Path,
    output_glb: Path,
    *,
    high_poly_clean: Path | None,
    bake_normals: bool,
    normal_map_path: Path | None,
    normal_map_resolution: int,
) -> tuple[bool, Path | None]:
    """Sessão bpy unificada: import + (opcional) bake-normal + tangents + export.

    Devolve (tangents_added, normal_map_path_or_None). Em ausência de bpy ou
    erro fatal, copia ``decimated_glb`` para ``output_glb`` e devolve
    (False, None).
    """
    try:
        import bpy
    except ImportError:
        log.warning("bake-master: bpy indisponível — output sem tangents/bake-normal")
        if decimated_glb != output_glb:
            shutil.copy2(decimated_glb, output_glb)
        return False, None

    try:
        from gamedev_shared.bpy_mesh import clear_scene
    except ImportError:
        if decimated_glb != output_glb:
            shutil.copy2(decimated_glb, output_glb)
        return False, None

    clear_scene()
    try:
        bpy.ops.import_scene.gltf(filepath=str(decimated_glb))
    except Exception as exc:
        log.warning("bake-master: import GLB falhou: %s", exc)
        if decimated_glb != output_glb:
            shutil.copy2(decimated_glb, output_glb)
        return False, None

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not mesh_objs:
        if decimated_glb != output_glb:
            shutil.copy2(decimated_glb, output_glb)
        return False, None

    low = max(mesh_objs, key=lambda o: len(o.data.polygons))

    nm_path: Path | None = None
    if bake_normals and high_poly_clean is not None and Path(high_poly_clean).is_file():
        try:
            bpy.ops.import_scene.gltf(filepath=str(Path(high_poly_clean).resolve()))
            all_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            high_candidates = [o for o in all_meshes if o is not low]
            if high_candidates and low.data.uv_layers:
                high = max(high_candidates, key=lambda o: len(o.data.polygons))
                target_nm = (
                    Path(normal_map_path).resolve()
                    if normal_map_path
                    else output_glb.with_name(f"{output_glb.stem}_normal_map.png")
                )

                img_name = f"normal_bake_{output_glb.stem}"
                if img_name in bpy.data.images:
                    bpy.data.images.remove(bpy.data.images[img_name])
                img = bpy.data.images.new(
                    img_name,
                    width=normal_map_resolution,
                    height=normal_map_resolution,
                    alpha=False,
                    float_buffer=False,
                )
                img.colorspace_settings.name = "Non-Color"

                if not low.data.materials:
                    mat = bpy.data.materials.new(name=f"{low.name}_mat")
                    mat.use_nodes = True
                    low.data.materials.append(mat)
                mat = low.data.materials[0]
                if not mat.use_nodes:
                    mat.use_nodes = True
                nodes = mat.node_tree.nodes
                tex_node = nodes.new("ShaderNodeTexImage")
                tex_node.image = img
                tex_node.select = True
                nodes.active = tex_node

                bpy.ops.object.select_all(action="DESELECT")
                high.select_set(True)
                low.select_set(True)
                bpy.context.view_layer.objects.active = low

                scene = bpy.context.scene
                scene.render.engine = "CYCLES"
                scene.cycles.bake_type = "NORMAL"
                scene.render.bake.use_selected_to_active = True
                scene.render.bake.use_cage = False
                scene.render.bake.cage_extrusion = 0.05
                scene.render.bake.normal_space = "TANGENT"
                scene.cycles.samples = 4

                bpy.ops.object.bake(type="NORMAL")
                target_nm.parent.mkdir(parents=True, exist_ok=True)
                img.filepath_raw = str(target_nm)
                img.file_format = "PNG"
                img.save()
                nm_path = target_nm

                # Remover high-poly antes do export para não inflar o GLB
                bpy.data.objects.remove(high, do_unlink=True)
        except Exception as exc:
            log.warning("bake-master: bake-normal falhou: %s", exc)
            nm_path = None

    tangents_added = False
    if low.data.uv_layers:
        try:
            low.data.calc_tangents()
            tangents_added = True
        except Exception as exc:
            log.debug("calc_tangents falhou: %s", exc)

    bpy.ops.object.select_all(action="DESELECT")
    for o in [*mesh_objs, *arm_objs]:
        if o.name in bpy.context.scene.objects:
            o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else low

    output_glb.parent.mkdir(parents=True, exist_ok=True)
    try:
        bpy.ops.export_scene.gltf(
            filepath=str(output_glb),
            export_format="GLB",
            use_selection=True,
            export_apply=True,
            export_normals=True,
            export_tangents=True,
            export_texcoords=True,
            export_materials="EXPORT",
            export_image_format="AUTO",
            export_animations=bool(arm_objs),
            export_skins=bool(arm_objs),
        )
    except Exception as exc:
        log.warning("bake-master: export bpy falhou: %s", exc)
        if decimated_glb != output_glb:
            shutil.copy2(decimated_glb, output_glb)
        return False, None

    return tangents_added, nm_path


def bake_master(
    painted_glb: Path,
    output_lod0_glb: Path,
    *,
    target_faces: int,
    high_poly_clean: Path | None = None,
    bake_normals: bool = False,
    normal_map_resolution: int = 1024,
    normal_map_path: Path | None = None,
    apply_ktx2: bool = True,
    apply_meshopt: bool = False,
    apply_dedup: bool = True,
    apply_prune: bool = True,
    texture_size: int = 2048,
) -> BakeMasterResult:
    """Stage 4 — produz LOD0 master a partir do painted high-poly.

    Implementação refatorada (Round 2): unifica bpy session e delega o
    pós-processamento (ktx2 + meshopt + dedup + prune) à lib partilhada
    ``gltf_transform_finish``.
    """
    from .gltf_finish import gltf_transform_finish
    from .mesh_remesh_textured import remesh_textured_glb

    painted_glb = Path(painted_glb).resolve()
    output_lod0_glb = Path(output_lod0_glb).resolve()
    output_lod0_glb.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bake_master_") as tdir:
        tmp = Path(tdir)
        decimated = tmp / "decimated.glb"
        with_tangents = tmp / "with_tangents.glb"

        log.info("bake-master: decimando %s para ~%d faces", painted_glb.name, target_faces)
        remesh_textured_glb(
            painted_glb,
            decimated,
            target_faces=target_faces,
            texture_size=texture_size,
        )

        tangents_ok, nm_path = _bake_master_bpy_session(
            decimated,
            with_tangents,
            high_poly_clean=high_poly_clean,
            bake_normals=bake_normals,
            normal_map_path=normal_map_path,
            normal_map_resolution=normal_map_resolution,
        )

        # Finalização — tangents já adicionados acima, pulamos o passo de
        # tangents do gltf_finish para não duplicar.
        finish = gltf_transform_finish(
            with_tangents,
            output_lod0_glb,
            apply_tangents=False,
            apply_dedup=apply_dedup,
            apply_prune=apply_prune,
            apply_uastc=apply_ktx2,
            apply_meshopt=apply_meshopt,
        )

    # Recontagem de faces (para report)
    try:
        import json as _json
        import struct

        with open(output_lod0_glb, "rb") as f:
            data = f.read(4096 * 64)
        if data[:4] == b"glTF":
            json_len = struct.unpack_from("<I", data, 12)[0]
            chunk = _json.loads(data[20 : 20 + json_len])
            faces = 0
            for m in chunk.get("meshes", []):
                for p in m.get("primitives", []):
                    idx = p.get("indices")
                    if idx is not None:
                        accs = chunk.get("accessors", [])
                        if idx < len(accs):
                            faces += accs[idx].get("count", 0) // 3
        else:
            faces = 0
    except Exception:
        faces = 0

    return BakeMasterResult(
        output_path=output_lod0_glb,
        decimated_faces=faces,
        tangents_added=tangents_ok,
        normal_map_path=nm_path,
        ktx2_applied=finish.ktx2_applied,
        meshopt_applied=finish.meshopt_applied,
        dedup_applied=finish.dedup_applied,
        prune_applied=finish.prune_applied,
    )
