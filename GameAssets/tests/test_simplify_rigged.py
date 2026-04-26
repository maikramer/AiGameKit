"""Test: simplify a rigged+animated GLB while preserving texture, rig, skin, and animations.

Uses bpy (Decimate modifier with collapse) which preserves topology,
UVs, vertex groups, and animations natively — zero weight transfer needed.

Run from Animator3D venv:
    cd GameDev/Animator3D && source .venv/bin/activate
    python -m pytest ../GameAssets/tests/test_simplify_rigged.py -v -s
"""

from __future__ import annotations

import json
import struct
import tempfile
from pathlib import Path

import bpy
import pytest

GOBLIN_SRC = (
    Path(__file__).resolve().parents[2]
    / "VibeGame/examples/simple-rpg/public/assets/meshes/goblin_rigged_animated.glb"
)
TARGET_FACES = 16000
MERGE_DIST = 0.0001


def _gltf_stats(path: Path) -> dict:
    with open(path, "rb") as f:
        f.read(8)
        f.read(4)
        json_len = struct.unpack("<I", f.read(4))[0]
        f.read(4)
        gltf = json.loads(f.read(json_len))
    return {
        "skins": len(gltf.get("skins", [])),
        "animations": len(gltf.get("animations", [])),
        "meshes": len(gltf.get("meshes", [])),
        "joints": len(gltf["skins"][0]["joints"]) if gltf.get("skins") else 0,
        "channels": sum(len(a.get("channels", [])) for a in gltf.get("animations", [])),
    }


def _clean_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete()
    for block in bpy.data.meshes, bpy.data.armatures, bpy.data.actions, bpy.data.cameras, bpy.data.lights:
        for item in block:
            block.remove(item)


@pytest.fixture(scope="module")
def simplified_glb(tmp_path_factory):
    _clean_scene()
    assert GOBLIN_SRC.is_file(), f"Source GLB not found: {GOBLIN_SRC}"
    out = tmp_path_factory.mktemp("simplify_test") / "goblin_simplified.glb"

    bpy.ops.import_scene.gltf(filepath=str(GOBLIN_SRC))

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    assert len(mesh_objs) >= 1, f"No meshes found"
    mesh_obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    src_faces = len(mesh_obj.data.polygons)
    src_verts = len(mesh_obj.data.vertices)
    src_vgroups = len(mesh_obj.vertex_groups)
    print(f"\nSource: {src_faces:,} faces, {src_verts:,} verts, {src_vgroups} vertex groups")

    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    assert len(arm_objs) == 1, f"Expected 1 armature, got {len(arm_objs)}"
    armature = arm_objs[0]
    n_bones = len(armature.data.bones)
    print(f"Armature: {n_bones} bones")

    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=MERGE_DIST)
    bpy.ops.object.mode_set(mode="OBJECT")
    after_merge = len(mesh_obj.data.polygons)
    print(f"After merge-by-distance ({MERGE_DIST}): {after_merge:,} faces")

    ratio = TARGET_FACES / max(after_merge, 1)
    mod = mesh_obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.context.view_layer.objects.active = mesh_obj
    bpy.ops.object.modifier_apply(modifier=mod.name)

    final_faces = len(mesh_obj.data.polygons)
    print(f"After decimate: {final_faces:,} faces")

    # Post-simplify: merge-by-distance + vertex smooth
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=MERGE_DIST)
    bpy.ops.mesh.vertices_smooth(factor=0.2, repeat=1)
    bpy.ops.object.mode_set(mode="OBJECT")

    final_faces = len(mesh_obj.data.polygons)
    final_verts = len(mesh_obj.data.vertices)
    final_vgroups = len(mesh_obj.vertex_groups)
    print(f"After decimate: {final_faces:,} faces, {final_verts:,} verts, {final_vgroups} vertex groups")

    mesh_obj.select_set(True)
    armature.select_set(True)
    bpy.context.view_layer.objects.active = armature

    bpy.ops.export_scene.gltf(
        filepath=str(out),
        export_format="GLB",
        export_apply=False,
        export_animations=True,
        export_skins=True,
        export_all_influences=False,
        export_image_format="AUTO",
    )

    print(f"Exported: {out} ({out.stat().st_size / 1024:.0f} KB)")
    yield out, {
        "src_faces": src_faces,
        "src_verts": src_verts,
        "src_vgroups": src_vgroups,
        "n_bones": n_bones,
        "final_faces": final_faces,
        "final_verts": final_verts,
        "final_vgroups": final_vgroups,
    }
    _clean_scene()


def test_face_count_within_tolerance(simplified_glb):
    _, info = simplified_glb
    assert abs(info["final_faces"] - TARGET_FACES) < TARGET_FACES * 0.3, (
        f"Face count {info['final_faces']} too far from target {TARGET_FACES}"
    )
    print(f"✓ Face count: {info['final_faces']:,} (target ~{TARGET_FACES:,})")


def test_reduced_from_source(simplified_glb):
    _, info = simplified_glb
    assert info["final_faces"] < info["src_faces"], "Mesh should be simplified"
    print(f"✓ Reduction: {info['src_faces']:,} → {info['final_faces']:,} "
          f"({info['final_faces'] / info['src_faces'] * 100:.1f}%)")


def test_preserves_vertex_groups(simplified_glb):
    _, info = simplified_glb
    assert info["final_vgroups"] == info["src_vgroups"], (
        f"Lost vertex groups: {info['src_vgroups']} → {info['final_vgroups']}"
    )
    print(f"✓ Vertex groups preserved: {info['final_vgroups']}")


def test_glb_has_skin(simplified_glb):
    path, _ = simplified_glb
    stats = _gltf_stats(path)
    assert stats["skins"] == 1, f"Expected 1 skin, got {stats['skins']}"
    print(f"✓ Skin present: {stats['joints']} joints")


def test_glb_preserves_animations(simplified_glb):
    path, _ = simplified_glb
    stats = _gltf_stats(path)
    src_stats = _gltf_stats(GOBLIN_SRC)
    assert stats["animations"] == src_stats["animations"], (
        f"Lost animations: {src_stats['animations']} → {stats['animations']}"
    )
    assert stats["channels"] == src_stats["channels"], (
        f"Lost animation channels: {src_stats['channels']} → {stats['channels']}"
    )
    print(f"✓ Animations preserved: {stats['animations']} ({stats['channels']} channels)")


def test_glb_has_texture(simplified_glb):
    path, _ = simplified_glb
    with open(path, "rb") as f:
        f.read(8)
        f.read(4)
        json_len = struct.unpack("<I", f.read(4))[0]
        f.read(4)
        gltf = json.loads(f.read(json_len))
    images = gltf.get("images", [])
    assert len(images) >= 1, "No images found in GLB"
    print(f"✓ Texture present: {len(images)} image(s)")


def test_skin_weights_valid(simplified_glb):
    path, _ = simplified_glb
    with open(path, "rb") as f:
        f.read(12)
        json_len = struct.unpack("<I", f.read(4))[0]
        f.read(4)
        gltf = json.loads(f.read(json_len))

    accessors = gltf.get("accessors", [])
    skin_accessor = None
    for acc_idx in [gltf["skins"][0].get("inverseBindMatrices")] if gltf.get("skins") else []:
        if acc_idx is not None:
            skin_accessor = acc_idx
            break

    for mesh_def in gltf.get("meshes", []):
        for prim in mesh_def.get("primitives", []):
            attrs = prim.get("attributes", {})
            assert "JOINTS_0" in attrs, "Missing JOINTS_0 attribute"
            assert "WEIGHTS_0" in attrs, "Missing WEIGHTS_0 attribute"

    print("✓ JOINTS_0 + WEIGHTS_0 attributes present in primitives")
