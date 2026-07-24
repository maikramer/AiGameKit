"""Transfer skin weights + skeleton + animations onto a new mesh topology.

Canonical home for LOD / bake-master rebinding. Rigging3D ``transfer-weights``
CLI wraps this module.

Why Shared (not Rigging3D-only):
* ``text3d lod`` (textured) rebuilds mesh from painted → must rebind skin here.
* ``save_glb`` / ``simplify_glb`` must not ``export_apply`` armatures (that
  freezes deform and drops ``skins[]``).
* Animations live on the armature; weight source is often ``rigged_hi``
  (no clips) while ``animation_source`` holds Quaternius / game-pack actions.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from gamedev_shared.gltf_decode import bpy_readable_glb, run_gltf_transform

log = logging.getLogger(__name__)


def _decompress_glb(src: Path, dst: Path) -> bool:
    """Decompress GLB via ``gltf-transform copy`` (strip EXT_meshopt)."""
    ok, err = run_gltf_transform("copy", src, dst)
    if not ok:
        log.warning("skin_transfer decompress failed: %s", err)
    return ok


@dataclass
class SkinTransferResult:
    target_in: Path
    target_out: Path
    bones: int
    vertex_groups: int
    animations: int = 0
    weights_assigned: int = 0


def _import_glb(path: Path) -> tuple[Any, list[Any]]:
    import bpy

    try:
        from gamedev_shared.bpy_mesh import import_gltf

        import_gltf(path)
    except Exception:
        bpy.ops.import_scene.gltf(filepath=str(path), bone_heuristic="TEMPERANCE")
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    arms = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    if not meshes:
        raise ValueError(f"GLB without mesh: {path}")
    main = max(meshes, key=lambda o: len(o.data.polygons))
    return main, arms


def transfer_vgroup_weights_kdtree(src_mesh: Any, tgt_mesh: Any) -> int:
    """Copy vertex-group weights src→tgt via world-space KDTree nearest.

    ``bpy.ops.object.data_transfer`` (VGROUP_WEIGHTS) on Blender 5.x often
    leaves empty groups; GLTF exporter then omits ``skins[]``.
    """
    from mathutils.kdtree import KDTree

    src_me = src_mesh.data
    n_src = len(src_me.vertices)
    if n_src == 0:
        raise ValueError("Source mesh has no vertices")

    kd = KDTree(n_src)
    for i, v in enumerate(src_me.vertices):
        kd.insert(src_mesh.matrix_world @ v.co, i)
    kd.balance()

    for g in src_mesh.vertex_groups:
        if g.name not in tgt_mesh.vertex_groups:
            tgt_mesh.vertex_groups.new(name=g.name)

    src_w: list[list[tuple[int, float]]] = [[(g.group, g.weight) for g in v.groups] for v in src_me.vertices]

    assigned = 0
    for i, v in enumerate(tgt_mesh.data.vertices):
        _co, idx, _dist = kd.find(tgt_mesh.matrix_world @ v.co)
        pairs = src_w[idx]
        if not pairs:
            continue
        total = sum(w for _gi, w in pairs) or 1.0
        for gi, w in pairs:
            gname = src_mesh.vertex_groups[gi].name
            tgt_mesh.vertex_groups[gname].add([i], w / total, "REPLACE")
        assigned += 1
    return assigned


def bind_armature(tgt: Any, src_arm: Any) -> None:
    """Attach ``tgt`` to armature (modifier + parent) so exporter emits skins[]."""
    for m in list(tgt.modifiers):
        if m.type == "ARMATURE":
            tgt.modifiers.remove(m)
    amod = tgt.modifiers.new("Armature", "ARMATURE")
    amod.object = src_arm
    amod.use_vertex_groups = True
    tgt.parent = src_arm
    tgt.matrix_parent_inverse = src_arm.matrix_world.inverted()


def count_armature_animations(arm: Any) -> int:
    ad = getattr(arm, "animation_data", None)
    if ad is None:
        return 0
    n = 1 if ad.action else 0
    n += sum(len(t.strips) for t in ad.nla_tracks)
    return n


def copy_armature_animations(src_arm: Any, dst_arm: Any) -> int:
    """Copy action + NLA strips from ``src_arm`` onto ``dst_arm``.

    Returns number of action references copied (active action + NLA strips).
    Blender 5.x: ``NlaTracks.new()`` takes no ``name`` kwarg — set after.
    """
    import bpy

    src_ad = getattr(src_arm, "animation_data", None)
    if src_ad is None:
        return 0

    dst_arm.animation_data_create()
    dst_ad = dst_arm.animation_data
    copied = 0
    referenced: set[str] = set()

    if src_ad.action is not None:
        dst_ad.action = src_ad.action
        referenced.add(src_ad.action.name)
        copied += 1

    for track in src_ad.nla_tracks:
        new_track = dst_ad.nla_tracks.new()
        new_track.name = track.name
        new_track.mute = track.mute
        for strip in track.strips:
            if strip.action is None:
                continue
            new_strip = new_track.strips.new(strip.name, int(strip.frame_start), strip.action)
            referenced.add(strip.action.name)
            with contextlib.suppress(Exception):
                new_strip.frame_end = strip.frame_end
            with contextlib.suppress(Exception):
                new_strip.blend_type = strip.blend_type
            copied += 1

    if copied == 0:
        # glTF import may leave actions in bpy.data.actions without NLA yet.
        # Dedup por nome: o import pode criar cópias ``.001`` da mesma clip e
        # empurrar todas para NLA gerava entregáveis com animações duplicadas
        # (bandit/boss_ogre com 14-18 clips em vez de 7-9).
        seen: set[str] = set()
        for act in bpy.data.actions:
            clip_name = act.name.split(".")[0]
            if act.name in referenced or clip_name in seen:
                continue
            seen.add(clip_name)
            track = dst_ad.nla_tracks.new()
            track.name = clip_name
            track.strips.new(clip_name, 1, act)
            copied += 1

    return copied


def _load_animations_from_glb(anim_glb: Path, dst_arm: Any, keep_objects: set[Any]) -> int:
    """Import ``anim_glb`` and copy its armature animations onto ``dst_arm``."""
    import bpy

    before = {o.as_pointer() for o in bpy.context.scene.objects}
    with bpy_readable_glb(anim_glb) as anim_path:
        from gamedev_shared.bpy_mesh import import_gltf

        import_gltf(anim_path)

    new_arms = [
        o for o in bpy.context.scene.objects if o.type == "ARMATURE" and o.as_pointer() not in before and o != dst_arm
    ]
    anim_arms = [a for a in new_arms if count_armature_animations(a) > 0]
    src_arm = (anim_arms or new_arms or [None])[0]
    if src_arm is None:
        log.warning("skin_transfer: animation_source has no armature: %s", anim_glb)
        return 0

    n = copy_armature_animations(src_arm, dst_arm)

    for o in list(bpy.context.scene.objects):
        if o.as_pointer() in before or o in keep_objects or o == dst_arm:
            continue
        with contextlib.suppress(Exception):
            bpy.data.objects.remove(o, do_unlink=True)
    return n


def export_skinned_glb(
    output_glb: Path,
    mesh_obj: Any,
    arm_obj: Any,
    *,
    export_tangents: bool = True,
) -> None:
    """Export mesh+armature with skins+animations. Never ``export_apply``."""
    import bpy

    from gamedev_shared.bpy_mesh import smooth_shade_scene

    output_glb = Path(output_glb)
    output_glb.parent.mkdir(parents=True, exist_ok=True)

    # Anti V/Tri=3: GLBs sem NORMAL importam flat; sem este passe o exporter
    # escreve normais per-face e quadruplica os vértices do entregável.
    smooth_shade_scene([mesh_obj])

    bpy.ops.object.select_all(action="DESELECT")
    arm_obj.select_set(True)
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = arm_obj

    export_kwargs: dict[str, Any] = {
        "filepath": str(output_glb),
        "export_format": "GLB",
        "use_selection": True,
        # CRITICAL: apply=True freezes Armature modifier and drops skins[].
        "export_apply": False,
        "export_skins": True,
        "export_animations": True,
        "export_normals": True,
        "export_tangents": export_tangents,
        "export_texcoords": True,
        "export_materials": "EXPORT",
        "export_image_format": "AUTO",
        "export_all_influences": False,
    }
    with contextlib.suppress(Exception):
        props = bpy.ops.export_scene.gltf.get_rna_type().properties
        if "export_optimize_disable_viewport" in props:
            export_kwargs["export_optimize_disable_viewport"] = True
    bpy.ops.export_scene.gltf(**export_kwargs)


def transfer_skin_to_mesh(
    source_glb: Path,
    target_glb: Path,
    output_glb: Path,
    *,
    animation_source: Path | None = None,
) -> SkinTransferResult:
    """Bind ``target_glb`` mesh to skeleton from ``source_glb``.

    * Weights: KDTree nearest from source mesh vertex groups.
    * Skeleton: source armature (parent + Armature modifier).
    * Animations: from ``source_glb`` if present; else from ``animation_source``.
    """
    import bpy

    from gamedev_shared.bpy_mesh import clear_scene

    source_glb = Path(source_glb)
    target_glb = Path(target_glb)
    output_glb = Path(output_glb)

    clear_scene()
    with bpy_readable_glb(source_glb) as src_path:
        src_mesh, src_arms = _import_glb(src_path)
    if not src_arms:
        raise ValueError(f"Source GLB sem armature: {source_glb}")
    src_arm = src_arms[0]

    with bpy_readable_glb(target_glb) as tgt_path:
        from gamedev_shared.bpy_mesh import import_gltf

        import_gltf(tgt_path)
    all_meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    tgt_candidates = [o for o in all_meshes if o is not src_mesh]
    if not tgt_candidates:
        raise ValueError(f"Target GLB has no mesh: {target_glb}")
    tgt = max(tgt_candidates, key=lambda o: len(o.data.polygons))

    assigned = transfer_vgroup_weights_kdtree(src_mesh, tgt)
    if assigned == 0:
        raise RuntimeError(f"skin_transfer: no weights assigned ({source_glb.name} → {target_glb.name})")
    log.info("skin_transfer KDTree: %d/%d verts weighted", assigned, len(tgt.data.vertices))

    bind_armature(tgt, src_arm)
    bpy.data.objects.remove(src_mesh, do_unlink=True)

    anim_count = count_armature_animations(src_arm)
    anim_src = Path(animation_source) if animation_source else None
    if anim_count == 0 and anim_src is not None and anim_src.is_file():
        if anim_src.resolve() != source_glb.resolve():
            anim_count = _load_animations_from_glb(anim_src, src_arm, keep_objects={src_arm, tgt})
            log.info("skin_transfer: copied %d anim refs from %s", anim_count, anim_src.name)
        else:
            anim_count = count_armature_animations(src_arm)
    else:
        anim_count = count_armature_animations(src_arm)

    export_skinned_glb(output_glb, tgt, src_arm)

    return SkinTransferResult(
        target_in=target_glb,
        target_out=output_glb,
        bones=len(src_arm.data.bones) if src_arm.data else 0,
        vertex_groups=len(tgt.vertex_groups),
        animations=anim_count,
        weights_assigned=assigned,
    )


def transfer_skin_to_targets(
    source_glb: Path,
    targets: list[Path],
    *,
    output_dir: Path | None = None,
    output_suffix: str = "_rigged",
    targets_out: list[Path] | None = None,
    animation_source: Path | None = None,
    apply_finish: bool = False,
) -> list[SkinTransferResult]:
    """Transfer skin (+ anims) from ``source_glb`` onto each target mesh."""
    source_glb = Path(source_glb)
    if targets_out and len(targets_out) != len(targets):
        raise ValueError("targets_out must match targets length")

    anim_src = Path(animation_source) if animation_source else None
    results: list[SkinTransferResult] = []
    for i, t in enumerate(targets):
        t_path = Path(t)
        if targets_out is not None:
            out = Path(targets_out[i])
        else:
            base = output_dir if output_dir is not None else t_path.parent
            out = Path(base) / f"{t_path.stem}{output_suffix}{t_path.suffix}"
        log.info("skin_transfer: %s → %s", t_path.name, out.name)
        results.append(
            transfer_skin_to_mesh(
                source_glb,
                t_path,
                out,
                animation_source=anim_src,
            )
        )

    if apply_finish:
        try:
            from text3d.utils.gltf_finish import gltf_transform_finish
        except ImportError:
            log.warning("skin_transfer: gltf_finish unavailable — skip finish")
        else:
            for r in results:
                with contextlib.suppress(Exception):
                    gltf_transform_finish(r.target_out, r.target_out)
    return results
