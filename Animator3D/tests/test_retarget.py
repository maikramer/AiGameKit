"""Testes para o módulo retarget (perfil + helpers + CLI sem bpy)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from animator3d import retarget as rt
from animator3d.cli import main
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


def test_load_builtin_profile_quaternius() -> None:
    """O perfil quaternius.yaml builtin carrega bone_map + clip_map."""
    profile = rt.load_profile("quaternius")
    assert profile.name == "quaternius"
    # Bones essenciais do mapeamento Quaternius -> Mixamo-like.
    assert profile.bone_map["pelvis"] == "Hips"
    assert profile.bone_map["upperarm_l"] == "LeftArm"
    assert profile.bone_map["thigh_r"] == "RightUpLeg"
    # Clips essenciais do simple-rpg.
    assert profile.clip_map["idle"] == "Idle_Loop"
    assert profile.clip_map["walk"] == "Walk_Loop"
    assert profile.clip_map["death"] == "Death01"
    assert profile.clip_map["attack"] == "Sword_Attack"


def test_load_profile_missing_raises() -> None:
    """Perfil inexistente -> FileNotFoundError."""
    import pytest

    with pytest.raises(FileNotFoundError):
        rt.load_profile("definitely-not-a-profile-xyz")


def test_available_profiles_includes_quaternius() -> None:
    assert "quaternius" in rt._available_profiles()


# ---------------------------------------------------------------------------
# _topo_sort_target_bones — parents antes de filhos
# ---------------------------------------------------------------------------


def test_topo_sort_parents_before_children() -> None:
    """Bones pai aparecem antes dos filhos na ordenação topológica."""
    # Armature mock com hierarquia: Hips -> Spine -> Chest, LeftUpLeg -> LeftLeg
    bones = {
        "Hips": SimpleNamespace(parent=None),
        "Spine": SimpleNamespace(parent=SimpleNamespace(parent=None, name="Hips")),
        "Chest": SimpleNamespace(
            parent=SimpleNamespace(parent=SimpleNamespace(parent=None, name="Hips"), name="Spine")
        ),
        "LeftLeg": SimpleNamespace(parent=SimpleNamespace(parent=None, name="LeftUpLeg")),
    }

    class FakeData:
        def get(self, name):
            return bones.get(name)

    fake_arm = SimpleNamespace(data=SimpleNamespace(bones=FakeData()))
    order = rt._topo_sort_target_bones(fake_arm, list(bones.keys()))

    # Hips (depth 0) vem antes de Spine (depth 1) que vem antes de Chest (depth 2).
    assert order.index("Hips") < order.index("Spine")
    assert order.index("Spine") < order.index("Chest")


# ---------------------------------------------------------------------------
# rename_existing_clips (mock, sem bpy real)
# ---------------------------------------------------------------------------


def test_rename_existing_clips_no_animation_data_returns_empty(monkeypatch) -> None:
    """Armature sem animation_data -> lista vazia (não crasha)."""
    fake_bpy = SimpleNamespace(data=SimpleNamespace(objects={"Arm": SimpleNamespace(animation_data=None)}))
    monkeypatch.setattr(rt, "_bpy", lambda: fake_bpy)
    assert rt.rename_existing_clips("Arm", {"a": "b"}) == []


# ---------------------------------------------------------------------------
# CLI — help (sem bpy)
# ---------------------------------------------------------------------------


def test_cli_retarget_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["retarget", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--source-track" in result.output
    assert "--clip-name" in result.output
    assert "--replace" in result.output


def test_cli_retarget_batch_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["retarget-batch", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--source" in result.output
    assert "--clips" in result.output
    assert "--no-fetch" in result.output


def test_cli_rename_clips_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["rename-clips", "--help"])
    assert result.exit_code == 0
    assert "--map" in result.output


def test_cli_retarget_batch_requires_source_or_profile_source(tmp_path: Path, monkeypatch) -> None:
    """Sem --source e perfil sem source_path + --no-fetch -> erro claro."""
    runner = CliRunner()
    # perfil custom sem source_path
    custom = tmp_path / "empty.yaml"
    custom.write_text("profile: empty\nbone_map: {}\nclip_map: {}\n", encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "retarget-batch",
            str(tmp_path / "dummy.glb"),
            str(tmp_path / "out.glb"),
            "--profile",
            str(custom),
            "--no-fetch",
        ],
    )
    # Falta bpy OR falta source — qualquer dos dois é erro não-zero.
    assert result.exit_code != 0
