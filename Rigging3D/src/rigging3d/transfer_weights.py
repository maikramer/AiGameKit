"""Stage 8 — transferência de skin weights de um GLB rigged high-poly
para alvos LOD0/LOD1/LOD2.

Usa ``bpy.ops.object.data_transfer`` com ``data_type='VGROUP_WEIGHTS'`` e
``vert_mapping='POLYINTERP_NEAREST'``. A armature do source é parented a
cada target (com ``ARMATURE`` modifier), reaproveitando os bones; o source
mesh em si é descartado da cena de saída.

Saída: para cada target, um GLB ``<target_stem>_rigged.glb`` (defeito) ou
caminho explícito via ``targets_out``.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


def _decompress_glb(src: Path, dst: Path) -> bool:
    """Descompressa GLB via ``gltf-transform copy`` (remove EXT_meshopt_compression)."""
    if shutil.which("npx") is None:
        return False
    try:
        r = subprocess.run(
            ["npx", "--yes", "@gltf-transform/cli", "copy", str(src), str(dst)],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        log.warning("transfer-weights decompress falhou: %s", exc)
        return False
    if r.returncode != 0:
        log.warning("transfer-weights decompress retornou %d: %s", r.returncode, r.stderr[-300:])
        return False
    return dst.is_file()


@contextmanager
def _bpy_readable_glb(path: Path):
    """Yields a path to ``path`` that bpy's GLTF importer can read.

    If ``path`` uses ``EXT_meshopt_compression`` (output de
    ``gltf_transform_finish``), bpy falha o import. Descompressamos para
    um tmpfile via gltf-transform copy. Para GLBs não-comprimidos, o
    copy é idempotente (apenas reserializa).
    """
    src = Path(path).resolve()
    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        if _decompress_glb(src, tmp_path):
            yield tmp_path
        else:
            yield src
    finally:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass


@dataclass
class TransferResult:
    target_in: Path
    target_out: Path
    bones: int
    vertex_groups: int


def _import_glb(path: Path) -> tuple[object, list[object]]:
    """Importa GLB e devolve (mesh principal, lista de armatures)."""
    import bpy

    bpy.ops.import_scene.gltf(filepath=str(path))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not meshes:
        raise ValueError(f"GLB sem mesh: {path}")
    main = max(meshes, key=lambda o: len(o.data.polygons))
    return main, arms


def _transfer_one(
    source_glb: Path,
    target_glb: Path,
    output_glb: Path,
) -> TransferResult:
    """Transfere skin do source rigged para o target, exporta novo GLB."""
    import bpy
    from gamedev_shared.bpy_mesh import clear_scene

    clear_scene()
    with _bpy_readable_glb(source_glb) as src_path:
        src_mesh, src_arms = _import_glb(src_path)
    if not src_arms:
        raise ValueError(f"Source GLB sem armature: {source_glb}")
    src_arm = src_arms[0]

    with _bpy_readable_glb(target_glb) as tgt_path:
        bpy.ops.import_scene.gltf(filepath=str(tgt_path))
    all_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    tgt_candidates = [o for o in all_meshes if o is not src_mesh]
    if not tgt_candidates:
        raise ValueError(f"Target GLB sem mesh: {target_glb}")
    tgt = max(tgt_candidates, key=lambda o: len(o.data.polygons))

    # Marca source/target para data_transfer
    bpy.ops.object.select_all(action="DESELECT")
    src_mesh.select_set(True)
    tgt.select_set(True)
    bpy.context.view_layer.objects.active = tgt

    # Cria vertex groups que faltarem em target (com mesmos nomes do source)
    src_vg_names = [g.name for g in src_mesh.vertex_groups]
    for name in src_vg_names:
        if name not in tgt.vertex_groups:
            tgt.vertex_groups.new(name=name)

    # data_transfer: weights por POLYINTERP_NEAREST
    try:
        bpy.ops.object.data_transfer(
            use_reverse_transfer=False,
            data_type="VGROUP_WEIGHTS",
            use_create=True,
            vert_mapping="POLYINTERP_NEAREST",
            layers_select_src="ALL",
            layers_select_dst="NAME",
            mix_mode="REPLACE",
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("data_transfer (POLYINTERP_NEAREST) falhou: %s — tentando NEAREST", exc)
        bpy.ops.object.data_transfer(
            use_reverse_transfer=False,
            data_type="VGROUP_WEIGHTS",
            use_create=True,
            vert_mapping="NEAREST",
            layers_select_src="ALL",
            layers_select_dst="NAME",
            mix_mode="REPLACE",
        )

    # Parent target ao armature usando o operator idiomático do Blender.
    # ``object.parent_set(type='ARMATURE_NAME')`` configura tanto o parenting
    # como o Armature modifier — necessário para o exportador GLTF do Blender
    # detectar o mesh como "skinned" e emitir uma `skin` real (sem isso, o
    # GLTF ainda contém JOINTS_0/WEIGHTS_0 mas não tem ``skin`` no node, e
    # importadores reconstruem os bones como Empties em vez de Armature).
    bpy.ops.object.select_all(action="DESELECT")
    tgt.select_set(True)
    src_arm.select_set(True)
    bpy.context.view_layer.objects.active = src_arm
    try:
        bpy.ops.object.parent_set(type="ARMATURE_NAME")
    except Exception as exc:  # noqa: BLE001
        log.warning("transfer-weights: parent_set ARMATURE_NAME falhou: %s — fallback manual", exc)
        if not any(m.type == "ARMATURE" for m in tgt.modifiers):
            amod = tgt.modifiers.new("Armature", "ARMATURE")
            amod.object = src_arm
            amod.use_vertex_groups = True
        tgt.parent = src_arm
        tgt.matrix_parent_inverse = src_arm.matrix_world.inverted()

    # Exportar só (target + armature). Remover source mesh da cena para não
    # ficar duplicado no output.
    bpy.data.objects.remove(src_mesh, do_unlink=True)

    bpy.ops.object.select_all(action="DESELECT")
    src_arm.select_set(True)
    tgt.select_set(True)
    bpy.context.view_layer.objects.active = src_arm

    output_glb.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output_glb),
        export_format="GLB",
        use_selection=True,
        export_apply=False,  # NÃO aplicar mods (mantém Armature ativo)
        export_skins=True,
        export_animations=True,
        export_normals=True,
        export_tangents=True,
        export_texcoords=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )

    bones = len(src_arm.data.bones) if src_arm and src_arm.data else 0
    vgroups = len(tgt.vertex_groups)
    return TransferResult(target_in=target_glb, target_out=output_glb, bones=bones, vertex_groups=vgroups)


def transfer_weights(
    source_glb: Path,
    targets: list[Path],
    *,
    output_dir: Path | None = None,
    output_suffix: str = "_rigged",
    targets_out: list[Path] | None = None,
    apply_finish: bool = True,
) -> list[TransferResult]:
    """Transfere skin do source para cada target.

    Args:
        source_glb: GLB rigged high-poly (output do ``rigging3d pipeline``).
        targets: Lista de GLBs target (LOD0, LOD1, LOD2 ou outros).
        output_dir: Pasta para os outputs (defeito = pasta do target).
        output_suffix: Sufixo aplicado ao stem do target (defeito ``_rigged``).
        targets_out: Lista explícita de paths de output (1:1 com targets).
            Quando definida, ignora ``output_dir`` e ``output_suffix``.

    Returns:
        Lista de TransferResult (um por target).
    """
    source_glb = Path(source_glb)
    if targets_out and len(targets_out) != len(targets):
        raise ValueError("targets_out deve ter o mesmo tamanho de targets")

    results: list[TransferResult] = []
    for i, t in enumerate(targets):
        t_path = Path(t)
        if targets_out is not None:
            out = Path(targets_out[i])
        else:
            base = output_dir if output_dir is not None else t_path.parent
            out = Path(base) / f"{t_path.stem}{output_suffix}{t_path.suffix}"
        log.info("transfer-weights: %s → %s", t_path.name, out.name)
        try:
            r = _transfer_one(source_glb, t_path, out)
            results.append(r)
        except Exception as exc:  # noqa: BLE001
            log.error("transfer-weights falhou para %s: %s", t_path, exc)
            raise

    if apply_finish:
        # Round 2: finalizar cada output rigged (KTX2+meshopt+tangents+dedup+prune).
        # gltf_transform_finish vive em Text3D — importação tardia para não criar
        # dependência circular.
        try:
            from text3d.utils.gltf_finish import gltf_transform_finish
        except ImportError:
            log.warning("transfer-weights: gltf_finish indisponível — outputs sem KTX2/meshopt")
        else:
            for r in results:
                try:
                    gltf_transform_finish(r.target_out, r.target_out)
                except Exception as exc:  # noqa: BLE001
                    log.warning("transfer-weights finish falhou em %s: %s", r.target_out, exc)
    return results
