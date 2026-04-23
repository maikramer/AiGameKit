"""Testes unitários para operações `bpy` isoladas."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from animator3d import bpy_ops


def test_export_glb_uses_actions_mode_for_multi_clip_exports(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_gltf(**kwargs):
        calls.append(kwargs)
        return {"FINISHED"}

    fake_bpy = SimpleNamespace(
        ops=SimpleNamespace(
            export_scene=SimpleNamespace(
                gltf=fake_gltf,
            )
        )
    )

    monkeypatch.setattr(bpy_ops, "_bpy", lambda: fake_bpy)

    out = tmp_path / "dragon.glb"
    bpy_ops.export_glb(out)

    assert calls, "export_glb() deve chamar bpy.ops.export_scene.gltf"
    assert calls[0]["filepath"] == str(out.resolve())
    assert calls[0]["export_format"] == "GLB"
    assert calls[0]["export_animations"] is True
    assert calls[0]["export_animation_mode"] == "ACTIONS"
    assert calls[0]["export_all_influences"] is False


def test_get_pose_bone_preserves_quaternion_mode(monkeypatch) -> None:
    bone = SimpleNamespace(rotation_mode="QUATERNION")
    armature = SimpleNamespace(type="ARMATURE", pose=SimpleNamespace(bones=SimpleNamespace(get=lambda _name: bone)))
    fake_bpy = SimpleNamespace(data=SimpleNamespace(objects=SimpleNamespace(get=lambda _name: armature)))

    monkeypatch.setattr(bpy_ops, "_bpy", lambda: fake_bpy)

    out = bpy_ops._get_pose_bone("Armature", "Bone")
    assert out is bone
    assert bone.rotation_mode == "QUATERNION"


def test_insert_pose_keyframe_uses_quaternion_path_for_quaternion_bones(monkeypatch) -> None:
    calls: list[tuple[str, int]] = []

    class FakeBone:
        rotation_mode = "QUATERNION"

        def keyframe_insert(self, *, data_path: str, frame: int) -> None:
            calls.append((data_path, frame))

    bone = FakeBone()
    armature = SimpleNamespace(type="ARMATURE", pose=SimpleNamespace(bones=SimpleNamespace(get=lambda _name: bone)))
    fake_bpy = SimpleNamespace(data=SimpleNamespace(objects=SimpleNamespace(get=lambda _name: armature)))

    monkeypatch.setattr(bpy_ops, "_bpy", lambda: fake_bpy)
    monkeypatch.setattr(bpy_ops, "_ensure_pose_mode", lambda _name: None)

    bpy_ops.insert_pose_keyframe("Armature", "Bone", 12)

    assert calls == [("rotation_quaternion", 12)]
