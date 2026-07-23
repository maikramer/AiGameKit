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


# ---------------------------------------------------------------------------
# Fallback procedural para clips humanoid-only em criaturas (Fase 2.1)
# ---------------------------------------------------------------------------


def test_humanoid_action_falls_back_to_procedural_when_not_humanoid(monkeypatch) -> None:
    """Quando try_humanoid_clip devolve False (criatura), _humanoid_action_keyframes
    delega para attack_keyframes em vez de produzir um clip vazio (no-op)."""
    from animator3d import humanoid

    calls: list[dict[str, object]] = []

    def fake_attack(*args, **kwargs):
        calls.append(kwargs)
        return {}

    # try_humanoid_clip deve retornar False para uma criatura.
    monkeypatch.setattr(humanoid, "try_humanoid_clip", lambda *a, **kw: False)
    monkeypatch.setattr(bpy_ops, "attack_keyframes", fake_attack)
    monkeypatch.setattr(bpy_ops, "_classify_bone_chains", lambda _name: {})

    bpy_ops._humanoid_action_keyframes("mine", "Armature", frame_start=1, frame_end=40, action_name="Animator3D_Mine")
    assert len(calls) == 1
    assert calls[0]["action_name"] == "Animator3D_Mine"
    # mine profile: strikes=1, arm_amp=1.20
    assert calls[0]["strikes"] == 1
    assert calls[0]["arm_amp"] == 1.20


def test_humanoid_action_uses_humanoid_path_when_humanoid(monkeypatch) -> None:
    """Quando try_humanoid_clip devolve True (humanoid), attack_keyframes NÃO é chamado."""
    from animator3d import humanoid

    attack_calls: list[dict[str, object]] = []

    monkeypatch.setattr(humanoid, "try_humanoid_clip", lambda *a, **kw: True)
    monkeypatch.setattr(bpy_ops, "attack_keyframes", lambda *a, **kw: attack_calls.append(kw) or {})
    monkeypatch.setattr(bpy_ops, "_classify_bone_chains", lambda _name: {})

    bpy_ops._humanoid_action_keyframes("chop", "Armature", frame_start=1, frame_end=40, action_name="Animator3D_Chop")
    assert attack_calls == []  # humanoid path, sem fallback procedural


# ---------------------------------------------------------------------------
# Multi-leg gait support (aracnídeos/insetos) — Fase de revisão profunda
# ---------------------------------------------------------------------------


def test_gait_phases_bipede() -> None:
    """1 pata/lado (bípede): phase [0.0]. O lado oposto fica anti-fase via +0.5."""
    from animator3d.bpy_ops import _gait_phases

    assert _gait_phases(1) == [0.0]


def test_gait_phases_quadrupede() -> None:
    """2 patas/lado (quadrúpede): trot diagonal — [0.0, 0.5]."""
    from animator3d.bpy_ops import _gait_phases

    assert _gait_phases(2) == [0.0, 0.5]


def test_gait_phases_hexapode() -> None:
    """3 patas/lado (inseto hexápode): trípede alternado — [0.0, 0.5, 0.5]."""
    from animator3d.bpy_ops import _gait_phases

    assert _gait_phases(3) == [0.0, 0.5, 0.5]


def test_gait_phases_aracnideo() -> None:
    """4 patas/lado (aracnídeo 8 patas): tetrapod alternado — [0.0, 0.5, 0.0, 0.5]."""
    from animator3d.bpy_ops import _gait_phases

    phases = _gait_phases(4)
    assert len(phases) == 4
    # Grupos anti-fásicos: patas 0,2 em fase; patas 1,3 anti-fase.
    assert phases[0] == phases[2]
    assert phases[1] == phases[3]
    assert phases[0] != phases[1]


def test_gait_phases_generalizacao() -> None:
    """5+ patas/lado: generaliza alternando em 2 grupos."""
    from animator3d.bpy_ops import _gait_phases

    phases = _gait_phases(5)
    assert len(phases) == 5
    assert all(p in (0.0, 0.5) for p in phases)
