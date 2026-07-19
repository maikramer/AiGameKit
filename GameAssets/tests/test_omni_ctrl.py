"""Testes dos controlos Omni no GameAssets."""

from __future__ import annotations

from pathlib import Path

import pytest

from gameassets.omni_ctrl import (
    OmniControls,
    merge_omni,
    omni_from_dict,
    omni_is_active,
    omni_to_batch_item,
    omni_to_cli_flags,
    prepare_shape_for_generation,
    resolve_point_from,
    shape_omni_stale,
)


class TestOmniFromDict:
    def test_empty(self) -> None:
        assert omni_from_dict(None) == OmniControls()

    def test_pose_preset(self) -> None:
        o = omni_from_dict({"control_type": "pose", "pose_preset": "quaternius-tpose"})
        assert o.control_type == "pose"
        assert o.pose_preset == "quaternius-tpose"
        assert omni_is_active(o)


class TestMergeAndFlags:
    def test_row_overrides_profile(self) -> None:
        base = OmniControls(bbox_preset="crate")
        row = OmniControls(pose_preset="quaternius-tpose", control_type="pose")
        m = merge_omni(base, row)
        assert m.control_type == "pose"
        assert m.pose_preset == "quaternius-tpose"
        assert m.bbox_preset == "crate"

    def test_cli_flags(self) -> None:
        flags = omni_to_cli_flags(OmniControls(control_type="pose", pose_preset="quaternius-tpose"))
        assert "--pose-preset" in flags
        assert "quaternius-tpose" in flags

    def test_batch_item(self) -> None:
        item = omni_to_batch_item(OmniControls(bbox_preset="sword", control_type="bbox"))
        assert item["bbox_preset"] == "sword"
        assert item["control_type"] == "bbox"

    def test_point_from(self, tmp_path: Path) -> None:
        shape = tmp_path / "hero_shape.glb"
        shape.write_bytes(b"glTF")
        o = resolve_point_from(OmniControls(point_from="hero"), sibling_shape=shape)
        assert o.control_type == "point"
        assert o.point_cloud == str(shape.resolve())

    def test_point_from_missing(self) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_point_from(OmniControls(point_from="hero"), sibling_shape=None)


class TestPrepareShapeForGeneration:
    def test_missing_shape_needs_gen(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        assert prepare_shape_for_generation(shape, OmniControls(), force=False) is True
        assert not shape.is_file()

    def test_missing_shape_with_clean_skips(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        clean = tmp_path / "chapel_clean.glb"
        clean.write_bytes(b"glTF")
        assert (
            prepare_shape_for_generation(
                shape,
                OmniControls(bbox_preset="chapel", control_type="bbox"),
                force=False,
                clean_glb=clean,
            )
            is False
        )

    def test_force_unlinks_existing(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"old")
        side = Path(str(shape) + ".omni.json")
        side.write_text("{}")
        assert prepare_shape_for_generation(shape, OmniControls(), force=True) is True
        assert not shape.is_file()
        assert not side.is_file()

    def test_fresh_shape_skips_without_force(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        monkeypatch.setattr(
            "gameassets.omni_ctrl.shape_omni_stale",
            lambda *_a, **_k: False,
        )
        assert prepare_shape_for_generation(shape, OmniControls(size_m=(6, 7, 4.5)), force=False) is False
        assert shape.is_file()

    def test_stale_unlinks_and_regens(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"old")
        monkeypatch.setattr(
            "gameassets.omni_ctrl.shape_omni_stale",
            lambda *_a, **_k: True,
        )
        assert prepare_shape_for_generation(shape, OmniControls(bbox_preset="chapel"), force=False) is True
        assert not shape.is_file()


class TestShapeOmniStaleDecodeKnobs:
    def test_bounds_mode_change_stales(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        write_omni_fingerprint(
            shape,
            {
                "control_type": "bbox",
                "bbox_preset": "chapel",
                "size_m": [6.0, 7.0, 4.5],
                "bounds_mode": "auto",
            },
        )
        omni = OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(6.0, 7.0, 4.5))
        assert shape_omni_stale(shape, omni, bounds_mode="auto") is False
        assert shape_omni_stale(shape, omni, bounds_mode="cube") is True

    def test_size_m_change_stales(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        write_omni_fingerprint(
            shape,
            {"control_type": "bbox", "bbox_preset": "chapel", "size_m": [6.0, 7.0, 4.5]},
        )
        assert (
            shape_omni_stale(
                shape,
                OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(6.0, 7.0, 4.5)),
            )
            is False
        )
        assert (
            shape_omni_stale(
                shape,
                OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(8.0, 7.0, 4.5)),
            )
            is True
        )
