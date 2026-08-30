"""Animator3DProfile.anim_pack — plumbing do --anim-pack (UAL1/UAL2/both)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gameassets.pipeline import _animator3d_game_pack_argv
from gameassets.profile import Animator3DProfile, GameProfile


class TestArgvAnimPack:
    def test_default_quaternius_omits_flag(self) -> None:
        argv = _animator3d_game_pack_argv("animator3d", Path("a.glb"), Path("b.glb"), preset="humanoid")
        assert argv[0] == "animator3d"
        assert argv[1] == "game-pack"
        assert "--anim-pack" not in argv

    def test_both_appends_flag(self) -> None:
        argv = _animator3d_game_pack_argv(
            "animator3d", Path("a.glb"), Path("b.glb"), preset="humanoid", anim_pack="both"
        )
        i = argv.index("--anim-pack")
        assert argv[i + 1] == "both"

    def test_quaternius2_appends_flag(self) -> None:
        argv = _animator3d_game_pack_argv(
            "animator3d", Path("a.glb"), Path("b.glb"), preset="humanoid", anim_pack="quaternius2"
        )
        i = argv.index("--anim-pack")
        assert argv[i + 1] == "quaternius2"


class TestProfileAnimPack:
    def _profile(self, anim3d: dict) -> Animator3DProfile:
        p = GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "animator3d": anim3d,
            }
        )
        assert p.animator3d is not None
        return p.animator3d

    def test_default(self) -> None:
        assert self._profile({"preset": "humanoid"}).anim_pack == "quaternius"

    @pytest.mark.parametrize("pack", ["quaternius", "quaternius2", "both"])
    def test_valid_values(self, pack: str) -> None:
        assert self._profile({"anim_pack": pack}).anim_pack == pack

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="anim_pack"):
            self._profile({"anim_pack": "mixamo"})

    def test_strips_and_lowercases(self) -> None:
        assert self._profile({"anim_pack": " Both "}).anim_pack == "both"


class TestLodRigMaxLevel:
    def _lod(self, lod: dict):
        p = GameProfile.from_dict({"title": "A", "genre": "B", "tone": "C", "style_preset": "lowpoly", "lod": lod})
        assert p.lod is not None
        return p.lod

    def test_default_is_one(self) -> None:
        assert self._lod({}).rig_max_level == 1

    @pytest.mark.parametrize("lvl", [0, 1, 2])
    def test_valid_values(self, lvl: int) -> None:
        assert self._lod({"rig_max_level": lvl}).rig_max_level == lvl

    def test_invalid_raises(self) -> None:
        with pytest.raises(ValueError, match="rig_max_level"):
            self._lod({"rig_max_level": 3})


class TestManifestAnimPack:
    def test_row_animate_anim_pack(self, tmp_path: Path) -> None:
        from gameassets.manifest import load_manifest

        mf = tmp_path / "manifest.yaml"
        mf.write_text(
            "\n".join(
                [
                    "assets:",
                    "  - id: hero",
                    "    idea: hero",
                    "    pipeline: [animate]",
                    "    animate:",
                    "      anim_pack: both",
                ]
            ),
            encoding="utf-8",
        )
        rows = load_manifest(mf)
        assert rows[0].animate_anim_pack == "both"
        assert rows[0].generate_animate is True

    def test_row_default_none(self, tmp_path: Path) -> None:
        from gameassets.manifest import load_manifest

        mf = tmp_path / "manifest.yaml"
        mf.write_text(
            "\n".join(
                [
                    "assets:",
                    "  - id: hero",
                    "    idea: hero",
                    "    pipeline: [animate]",
                ]
            ),
            encoding="utf-8",
        )
        rows = load_manifest(mf)
        assert rows[0].animate_anim_pack is None


class TestCompositeAnimPackGrammar:
    """Gramática partilhada: all e listas por vírgulas validam como os singles."""

    @pytest.mark.parametrize("pack", ["villager", "all", "both,villager", "quaternius2,villager", "ALL"])
    def test_valid_composites(self, pack: str) -> None:
        p = GameProfile.from_dict(
            {"title": "A", "genre": "B", "tone": "C", "style_preset": "lowpoly", "animator3d": {"anim_pack": pack}}
        )
        assert p.animator3d is not None
        assert p.animator3d.anim_pack == pack.strip().lower()

    def test_invalid_composite_raises(self) -> None:
        with pytest.raises(ValueError, match="anim_pack"):
            GameProfile.from_dict(
                {
                    "title": "A",
                    "genre": "B",
                    "tone": "C",
                    "style_preset": "lowpoly",
                    "animator3d": {"anim_pack": "both,mixamo"},
                }
            )

    def test_composite_appends_flag_verbatim(self) -> None:
        argv = _animator3d_game_pack_argv(
            "animator3d", Path("a.glb"), Path("b.glb"), preset="humanoid", anim_pack="both,villager"
        )
        i = argv.index("--anim-pack")
        assert argv[i + 1] == "both,villager"
