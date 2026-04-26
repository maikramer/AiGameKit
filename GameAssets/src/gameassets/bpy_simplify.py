"""Mesh simplification via bpy (Blender Python): decimate, merge-by-distance, smooth.

Requires the Animator3D venv (bpy 5.1 / Python 3.13).
Run from: ``cd Animator3D && source .venv/bin/activate``

Usage from subprocess (GameAssets pipeline)::

    # Decimate to target face count (default 16 000)
    python -m gameassets.bpy_simplify \\
        goblin_rigged_animated.glb -o goblin_final.glb \\
        --target-faces 16000 --merge-dist 0.0001 --smooth-factor 0.5

    # Decimate by ratio (0.0-1.0)
    python -m gameassets.bpy_simplify \\
        goblin_rigged_animated.glb -o goblin_lod1.glb \\
        --ratio 0.5

    # Merge+smooth only (no decimation)
    python -m gameassets.bpy_simplify \\
        goblin_rigged_animated.glb -o goblin_clean.glb \\
        --clean-only

Or programmatically::

    from gameassets.bpy_simplify import simplify_glb, simplify_lod, clean_glb
    simplify_glb("input.glb", "output.glb", target_faces=16000)
    simplify_lod("input.glb", "lod1.glb", ratio=0.5)
    clean_glb("input.glb", "clean.glb")
"""

from __future__ import annotations

from pathlib import Path

MERGE_DIST = 0.0001
SMOOTH_FACTOR = 0.2
SMOOTH_REPEAT = 1


def _clean_scene() -> None:
    import bpy

    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in (bpy.data.meshes, bpy.data.armatures, bpy.data.actions, bpy.data.cameras, bpy.data.lights):
        for item in block:
            block.remove(item)


def _load_glb(input_path: Path) -> tuple:
    import bpy

    _clean_scene()
    bpy.ops.import_scene.gltf(filepath=str(input_path))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    mesh_obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    return mesh_obj, arm_objs


def _export_glb(output_path: Path, mesh_obj, arm_objs: list) -> None:
    import bpy

    export_objects = [mesh_obj, *arm_objs]
    for o in export_objects:
        o.select_set(True)
    bpy.context.view_layer.objects.active = arm_objs[0] if arm_objs else mesh_obj
    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        export_apply=False,
        export_animations=True,
        export_skins=True,
        export_all_influences=False,
        export_image_format="AUTO",
    )


def merge_by_distance(obj, threshold: float = MERGE_DIST) -> int:
    """Remove duplicated vertices closer than *threshold*. Returns vertices removed."""
    import bpy

    before = len(obj.data.vertices)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=threshold)
    bpy.ops.object.mode_set(mode="OBJECT")
    return before - len(obj.data.vertices)


def decimate_collapse(obj, target_faces: int) -> int:
    """Quadric edge-collapse decimation to ~*target_faces*. Returns actual face count."""
    import bpy

    current = len(obj.data.polygons)
    if current <= target_faces:
        return current
    ratio = target_faces / current
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=mod.name)
    return len(obj.data.polygons)


def smooth_mesh(obj, factor: float = SMOOTH_FACTOR, repeat: int = SMOOTH_REPEAT) -> None:
    """Regular vertex smooth (Blender default) on all vertices."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    for _ in range(repeat):
        bpy.ops.mesh.vertices_smooth(factor=factor, repeat=1)
    bpy.ops.object.mode_set(mode="OBJECT")


def post_simplify_clean(obj, merge_dist: float = MERGE_DIST, smooth_factor: float = SMOOTH_FACTOR) -> None:
    """Merge-by-distance + laplacian smooth. Call after ANY simplification step."""
    removed = merge_by_distance(obj, threshold=merge_dist)
    if smooth_factor > 0:
        smooth_mesh(obj, factor=smooth_factor, repeat=SMOOTH_REPEAT)
    return removed


def simplify_glb(
    input_path: str | Path,
    output_path: str | Path,
    target_faces: int = 16000,
    merge_dist: float = MERGE_DIST,
    smooth_factor: float = SMOOTH_FACTOR,
) -> dict:
    """Full pipeline: load GLB → merge → decimate → merge+smooth → export.

    Returns dict with stats (src_faces, final_faces, etc.).
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    mesh_obj, arm_objs = _load_glb(input_path)

    src_faces = len(mesh_obj.data.polygons)
    src_verts = len(mesh_obj.data.vertices)

    removed_pre = merge_by_distance(mesh_obj, threshold=merge_dist)
    after_merge = len(mesh_obj.data.polygons)

    final_faces = decimate_collapse(mesh_obj, target_faces)

    removed_post = post_simplify_clean(mesh_obj, merge_dist=merge_dist, smooth_factor=smooth_factor)
    final_verts = len(mesh_obj.data.vertices)

    _export_glb(output_path, mesh_obj, arm_objs)

    stats = {
        "src_faces": src_faces,
        "src_verts": src_verts,
        "pre_merge_removed": removed_pre,
        "after_merge": after_merge,
        "final_faces": final_faces,
        "final_verts": final_verts,
        "post_merge_removed": removed_post,
        "has_armature": bool(arm_objs),
        "n_bones": len(arm_objs[0].data.bones) if arm_objs else 0,
        "output_size_kb": output_path.stat().st_size / 1024,
    }
    _clean_scene()
    return stats


def simplify_lod(
    input_path: str | Path,
    output_path: str | Path,
    ratio: float,
    merge_dist: float = MERGE_DIST,
    smooth_factor: float = SMOOTH_FACTOR,
) -> dict:
    """Simplify a single LOD level: decimate by *ratio* → merge+smooth → export.

    Preserves armature/skin/animations from the input GLB.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    mesh_obj, arm_objs = _load_glb(input_path)

    src_faces = len(mesh_obj.data.polygons)

    merge_by_distance(mesh_obj, threshold=merge_dist)
    target = max(int(src_faces * ratio), 100)
    final_faces = decimate_collapse(mesh_obj, target)
    post_simplify_clean(mesh_obj, merge_dist=merge_dist, smooth_factor=smooth_factor)

    _export_glb(output_path, mesh_obj, arm_objs)

    stats = {
        "src_faces": src_faces,
        "final_faces": final_faces,
        "output_size_kb": output_path.stat().st_size / 1024,
    }
    _clean_scene()
    return stats


def clean_glb(
    input_path: str | Path,
    output_path: str | Path,
    merge_dist: float = MERGE_DIST,
    smooth_factor: float = SMOOTH_FACTOR,
) -> dict:
    """Load GLB, run merge+smooth only (no decimation), export.

    Returns dict with src_faces, src_verts, final_faces, final_verts,
    merge_removed, output_size_kb.
    """
    input_path = Path(input_path)
    output_path = Path(output_path)
    mesh_obj, arm_objs = _load_glb(input_path)

    src_faces = len(mesh_obj.data.polygons)
    src_verts = len(mesh_obj.data.vertices)

    merge_removed = post_simplify_clean(mesh_obj, merge_dist=merge_dist, smooth_factor=smooth_factor)

    final_faces = len(mesh_obj.data.polygons)
    final_verts = len(mesh_obj.data.vertices)

    _export_glb(output_path, mesh_obj, arm_objs)

    stats = {
        "src_faces": src_faces,
        "src_verts": src_verts,
        "final_faces": final_faces,
        "final_verts": final_verts,
        "merge_removed": merge_removed,
        "output_size_kb": output_path.stat().st_size / 1024,
    }
    _clean_scene()
    return stats


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="Simplify or clean a rigged+animated GLB via bpy")
    parser.add_argument("input", type=Path, help="Input GLB path")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output GLB path")
    parser.add_argument("--merge-dist", type=float, default=MERGE_DIST)
    parser.add_argument("--smooth-factor", type=float, default=SMOOTH_FACTOR)

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--target-faces", type=int, default=None, help="Target face count (default: 16000)")
    mode.add_argument("--ratio", type=float, default=None, help="Decimation ratio (0.0-1.0)")
    mode.add_argument("--clean-only", action="store_true", help="Merge+smooth only, no decimation")

    args = parser.parse_args()

    if args.clean_only:
        stats = clean_glb(args.input, args.output, merge_dist=args.merge_dist, smooth_factor=args.smooth_factor)
    elif args.ratio is not None:
        stats = simplify_lod(
            args.input, args.output, ratio=args.ratio, merge_dist=args.merge_dist, smooth_factor=args.smooth_factor
        )
    else:
        target = args.target_faces if args.target_faces is not None else 16000
        stats = simplify_glb(
            args.input,
            args.output,
            target_faces=target,
            merge_dist=args.merge_dist,
            smooth_factor=args.smooth_factor,
        )
    print(json.dumps(stats, indent=2))
