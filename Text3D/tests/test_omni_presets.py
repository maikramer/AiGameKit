"""Testes dos presets Omni embutidos."""

from __future__ import annotations

from pathlib import Path

import pytest

from text3d.omni_presets import (
    BBOX_PRESETS,
    category_omni_defaults,
    merge_omni_controls,
    omni_fingerprint,
    omni_fingerprint_matches,
    quaternius_tpose_glb,
    resolve_bbox_preset,
    resolve_pose_preset,
    size_m_to_bbox,
    write_omni_fingerprint,
)


class TestPosePreset:
    def test_resolve_quaternius_tpose(self) -> None:
        path = resolve_pose_preset("quaternius-tpose")
        assert path.is_file()
        assert path.name == "quaternius_tpose_bone.txt"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 51

    def test_glb_reference_exists(self) -> None:
        glb = quaternius_tpose_glb()
        assert glb.is_file()
        assert glb.suffix == ".glb"

    def test_unknown_pose_raises(self) -> None:
        with pytest.raises(KeyError, match="pose_preset"):
            resolve_pose_preset("nope")


class TestBboxPreset:
    def test_all_presets_three_floats(self) -> None:
        for name in BBOX_PRESETS:
            dims = resolve_bbox_preset(name)
            assert len(dims) == 3
            assert max(dims) == pytest.approx(1.0)

    def test_sword_aspect(self) -> None:
        L, H, W = resolve_bbox_preset("sword")
        assert pytest.approx(1.0) == H
        assert L < H and W < H

    def test_size_m_normalize(self) -> None:
        assert size_m_to_bbox([0.1, 1.2, 0.1]) == pytest.approx([0.1 / 1.2, 1.0, 0.1 / 1.2])


class TestMergeOmniControls:
    def test_pose_preset_implies_pose(self) -> None:
        out = merge_omni_controls(pose_preset="quaternius-tpose")
        assert out["control_type"] == "pose"
        assert Path(out["pose_file"]).is_file()

    def test_bbox_preset_implies_bbox(self) -> None:
        out = merge_omni_controls(bbox_preset="door")
        assert out["control_type"] == "bbox"
        assert out["bbox"] == list(BBOX_PRESETS["door"])

    def test_size_alias(self) -> None:
        out = merge_omni_controls(size=[0.2, 1.0, 0.1])
        assert out["control_type"] == "bbox"
        assert out["bbox"] == [0.2, 1.0, 0.1]

    def test_category_humanoid_soft(self) -> None:
        out = merge_omni_controls(category="humanoid")
        assert out["control_type"] == "pose"
        assert out["pose_preset"] == "quaternius-tpose"

    def test_explicit_none_beats_category(self) -> None:
        # control_type none + no other fields → category fills
        assert category_omni_defaults("weapon")["bbox_preset"] == "sword"
        out = merge_omni_controls(control_type="bbox", bbox=[1, 1, 1], category="humanoid")
        assert out["control_type"] == "bbox"
        assert out["bbox"] == [1, 1, 1]


class TestFingerprint:
    def test_roundtrip(self, tmp_path: Path) -> None:
        glb = tmp_path / "hero_shape.glb"
        glb.write_bytes(b"glTF")
        ctrl = {"control_type": "pose", "pose_preset": "quaternius-tpose"}
        write_omni_fingerprint(glb, ctrl)
        assert omni_fingerprint_matches(glb, ctrl)
        assert not omni_fingerprint_matches(glb, {"control_type": "bbox", "bbox_preset": "sword"})
        assert omni_fingerprint(ctrl)["pose_preset"] == "quaternius-tpose"

    def test_size_m_in_merge_and_fingerprint(self) -> None:
        out = merge_omni_controls(size_m=[6.0, 7.0, 4.5], bbox_preset="chapel")
        assert out["size_m"] == [6.0, 7.0, 4.5]
        fp = omni_fingerprint(out)
        assert fp["size_m"] == [6.0, 7.0, 4.5]
        assert omni_fingerprint({**out, "size_m": [6.0, 7.0, 5.0]})["size_m"] != fp["size_m"]

    def test_bounds_auto_normalizes_like_missing(self) -> None:
        a = omni_fingerprint({"control_type": "bbox", "bounds_mode": "auto"})
        b = omni_fingerprint({"control_type": "bbox"})
        assert a["bounds_mode"] is None
        assert a == b
        cube = omni_fingerprint({"control_type": "bbox", "bounds_mode": "cube"})
        assert cube["bounds_mode"] == "cube"

    def test_mc_auto_normalizes_like_missing(self) -> None:
        a = omni_fingerprint({"control_type": "bbox", "mc_level": "auto"})
        b = omni_fingerprint({"control_type": "bbox"})
        assert a["mc_level"] is None
        assert a == b
        explicit = omni_fingerprint({"control_type": "bbox", "mc_level": -0.002})
        assert explicit["mc_level"] == pytest.approx(-0.002)
