"""End-to-end: runtime → NPZ → source GLB → rigged GLB.

Skipped unless the GGUF is on disk (``SPRONG_MODEL`` or HF cache) and, for the
retarget step, a SkinTokens rigged GLB is available (``MOTION3D_TEST_RIGGED``).
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pytest
from motion3d.generator import MotionGenerator
from motion3d.npz_io import read_clip
from motion3d.skeleton import SOURCE_TRACK

RIGGED_CANDIDATES = (
    Path.home() / "GitClones/Vael/assets/public/assets/meshes/characters/_intermediate/hero_rigged.glb",
    Path(
        "/media/maikeu/b1e73891-ddde-49a0-9382-903accb68b49/GitClones/Vael/assets/public/assets/meshes/characters/_intermediate/hero_rigged.glb"
    ),
)


def rigged_glb() -> Path:
    override = os.environ.get("MOTION3D_TEST_RIGGED", "").strip()
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path
        pytest.skip(f"MOTION3D_TEST_RIGGED inexistente: {path}")
    for candidate in RIGGED_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("sem GLB SkinTokens rigged para o teste de retarget")


class TestGenerateToNpz:
    def test_npz_roundtrip(self, sprong_model: Path, tmp_path: Path) -> None:
        with MotionGenerator(sprong_model) as generator:
            saved = generator.generate("a person walks forward", tmp_path / "walk.npz", seconds=1.0, seed=7)
        motion = read_clip(saved)
        assert motion.frame_count == 20
        assert motion.fps == 20
        assert motion.joints.shape == (20, 22, 3)
        assert motion.prompt == "a person walks forward"
        assert motion.seed == 7
        assert motion.model.startswith("SprongMotion-14M@")
        assert motion.root is not None and motion.contacts is not None
        assert np.isfinite(motion.joints).all()

    def test_attempts_pick_a_seed_deterministically(self, sprong_model: Path, tmp_path: Path) -> None:
        with MotionGenerator(sprong_model) as generator:
            first = generator.generate("a person walks forward", tmp_path / "a.npz", seconds=1.0, seed=7, attempts=3)
            first_seed = generator.last_seed
            second = generator.generate("a person walks forward", tmp_path / "b.npz", seconds=1.0, seed=7, attempts=3)
        assert first_seed == generator.last_seed
        assert np.array_equal(read_clip(first).joints, read_clip(second).joints)

    def test_bvh_output(self, sprong_model: Path, tmp_path: Path) -> None:
        with MotionGenerator(sprong_model) as generator:
            saved = generator.generate("a person waves with the right hand", tmp_path / "wave.bvh", seconds=1.5)
        assert saved.suffix == ".bvh"
        assert saved.read_text(encoding="utf-8").startswith("HIERARCHY")


class TestGenerateToGlb:
    def test_source_glb_is_written(self, sprong_model: Path, tmp_path: Path) -> None:
        pytest.importorskip("bpy", reason="bpy ausente")
        with MotionGenerator(sprong_model) as generator:
            saved = generator.generate(
                "a person walks forward",
                tmp_path / "walk.glb",
                seconds=1.0,
                seed=3,
                also_npz=True,
            )
        assert saved.is_file() and saved.stat().st_size > 10_000
        assert (tmp_path / "walk.npz").is_file()


class TestApplyRigged:
    def test_retarget_onto_a_skintokens_rig(self, sprong_model: Path, tmp_path: Path) -> None:
        pytest.importorskip("bpy", reason="bpy ausente")
        pytest.importorskip("animator3d", reason="animator3d ausente")
        source = rigged_glb()
        from motion3d.apply_rigged import apply_npz_to_rigged

        with MotionGenerator(sprong_model) as generator:
            npz = generator.generate("a person walks forward", tmp_path / "walk.npz", seconds=1.0, seed=5)
        result = apply_npz_to_rigged(npz, source, tmp_path / "hero_walk.glb", clip_name="walk")
        retarget = result["retarget"]
        assert Path(result["output"]).is_file()
        assert retarget["bones_mapped"] >= 20
        assert retarget["clip"] == "walk"
        assert retarget["frames"][1] - retarget["frames"][0] >= 19  # 1 s at 20 fps

    def test_source_track_name_is_stable(self) -> None:
        assert SOURCE_TRACK == "sprong_motion"
