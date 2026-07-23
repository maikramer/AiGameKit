"""Testes da stage split-at-height (árvores) no master pipeline."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from gameassets.manifest import ManifestRow
from gameassets.paths import (
    _lod_path,
    _split_path,
    _stump_path,
    _top_path,
    _unsplit_lod0_path,
)
from gameassets.pipeline import (
    StageResult,
    run_split_at_height_stage,
    wants_split_at_height,
)
from gameassets.profile import GameProfile, Text3DProfile


def _row(category: str = "tree", **kwargs: object) -> ManifestRow:
    defaults: dict[str, object] = {
        "id": "tree_oak",
        "idea": "oak",
        "kind": "environment",
        "category": category,
        "generate_3d": True,
    }
    defaults.update(kwargs)
    return ManifestRow(**defaults)  # type: ignore[arg-type]


def _profile(**kwargs: object) -> GameProfile:
    defaults: dict[str, object] = {
        "title": "t",
        "genre": "rpg",
        "tone": "neutral",
        "style_preset": "lowpoly",
    }
    defaults.update(kwargs)
    return GameProfile(**defaults)  # type: ignore[arg-type]


def test_wants_split_default_on_for_tree() -> None:
    assert wants_split_at_height(_profile(), _row("tree")) is True
    assert wants_split_at_height(_profile(), _row("rock")) is False


def test_wants_split_explicit_off() -> None:
    profile = _profile(text3d=Text3DProfile(split_at_height=False))
    assert wants_split_at_height(profile, _row("tree")) is False


def test_wants_split_explicit_on_non_tree() -> None:
    profile = _profile(text3d=Text3DProfile(split_at_height=True))
    assert wants_split_at_height(profile, _row("prop")) is True


def test_run_split_at_height_stage_mock(tmp_path: Path) -> None:
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "tree_oak.glb"
    lod0 = _lod_path(mesh_final, 0)
    lod0.write_bytes(b"unsplit-lod0")

    split_out = _split_path(mesh_final)
    raw_stump = split_out.with_name(f"{split_out.stem}_stump.glb")
    raw_top = split_out.with_name(f"{split_out.stem}_top.glb")

    def _fake_run(name: str, argv: list[str], output: Path | None = None) -> StageResult:
        assert name == "split-at-height"
        assert "split-at-height" in argv
        assert "--cut-height" in argv
        assert argv[argv.index("--cut-height") + 1] == "0.55"
        assert "--split-files" in argv
        # Input deve ser o unsplit em _intermediate/
        assert any("lod0_unsplit" in a for a in argv)
        split_out.write_bytes(b"split-composition")
        raw_stump.write_bytes(b"stump")
        raw_top.write_bytes(b"top")
        return StageResult(name, True, 0.1, "ok", split_out)

    profile = _profile(text3d=Text3DProfile(split_cut_height=0.55, split_files=True))
    result = run_split_at_height_stage(
        text3d_bin="text3d",
        mesh_final=mesh_final,
        profile=profile,
        run_stage=_fake_run,
    )
    assert result.ok
    assert _unsplit_lod0_path(mesh_final).is_file()
    assert split_out.is_file()
    assert lod0.read_bytes() == b"split-composition"
    assert _stump_path(mesh_final).read_bytes() == b"stump"
    assert _top_path(mesh_final).read_bytes() == b"top"
    assert not raw_stump.exists()
    assert not raw_top.exists()


def test_run_split_omits_cut_height_when_default(tmp_path: Path) -> None:
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "tree_oak.glb"
    lod0 = _lod_path(mesh_final, 0)
    lod0.write_bytes(b"unsplit-lod0")
    split_out = _split_path(mesh_final)

    def _fake_run(name: str, argv: list[str], output: Path | None = None) -> StageResult:
        assert "--cut-height" not in argv
        split_out.write_bytes(b"split")
        return StageResult(name, True, 0.1, "ok", split_out)

    result = run_split_at_height_stage(
        text3d_bin="text3d",
        mesh_final=mesh_final,
        profile=_profile(text3d=Text3DProfile(split_files=False)),
        run_stage=_fake_run,
    )
    assert result.ok
    assert "min(0.8,h/4)" in (result.error or "")


def test_run_split_skips_when_done(tmp_path: Path) -> None:
    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "tree_oak.glb"
    lod0 = _lod_path(mesh_final, 0)
    lod0.write_bytes(b"lod0")
    unsplit = _unsplit_lod0_path(mesh_final)
    unsplit.parent.mkdir(parents=True)
    unsplit.write_bytes(b"unsplit")
    split_out = _split_path(mesh_final)
    split_out.write_bytes(b"split")

    run_stage = MagicMock()
    result = run_split_at_height_stage(
        text3d_bin="text3d",
        mesh_final=mesh_final,
        profile=_profile(),
        run_stage=run_stage,
    )
    assert result.ok
    assert "skipped" in (result.error or "")
    run_stage.assert_not_called()


def test_profile_parses_split_fields() -> None:
    data = yaml.safe_load(
        "title: t\ngenre: rpg\ntone: neutral\nstyle_preset: lowpoly\n"
        "text3d:\n  split_at_height: false\n  split_cut_height: 0.8\n  split_files: false\n"
    )
    profile = GameProfile.from_dict(data)
    assert profile.text3d is not None
    assert profile.text3d.split_at_height is False
    assert profile.text3d.split_cut_height == 0.8
    assert profile.text3d.split_files is False
