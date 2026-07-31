"""Tests for bone-display helper stripping (Icosphere origin regression)."""

from __future__ import annotations

import pytest

bpy = pytest.importorskip("bpy")


def test_strip_bone_display_meshes_removes_icosphere() -> None:
    from aigamekit_shared.bpy_mesh import clear_scene, strip_bone_display_meshes

    clear_scene()
    # Helpers de bone só existem com armature; sem ela o strip é no-op.
    bpy.ops.object.armature_add()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0)
    ico = bpy.context.active_object
    assert ico is not None
    ico.name = "Icosphere"
    bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=8, radius=0.5)
    keep = bpy.context.active_object
    assert keep is not None
    keep.name = "HeroMesh"
    mat = bpy.data.materials.new("M")
    keep.data.materials.append(mat)

    removed = strip_bone_display_meshes()
    names = {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
    assert removed >= 1
    assert "Icosphere" not in names
    assert "HeroMesh" in names


def test_strip_bone_display_meshes_keeps_icosphere_without_armature() -> None:
    """Prop legítimo chamado ``Icosphere`` não pode desaparecer da cena."""
    from aigamekit_shared.bpy_mesh import clear_scene, strip_bone_display_meshes

    clear_scene()
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1, radius=1.0)
    obj = bpy.context.active_object
    assert obj is not None
    obj.name = "Icosphere"

    assert strip_bone_display_meshes() == 0
    assert "Icosphere" in {o.name for o in bpy.context.scene.objects if o.type == "MESH"}
