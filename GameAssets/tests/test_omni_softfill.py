"""Tests soft-fill Omni por categoria + shape UMS sdnq defaults."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from gameassets.omni_ctrl import (
    OmniControls,
    omni_has_geom_control,
    omni_is_active,
    softfill_omni_from_category,
)
from gameassets.profile import GameProfile, Text3DProfile
from gameassets.ums_batch import shape_specs_from_items


class TestSoftfillOmniFromCategory:
    def test_inactive_gets_humanoid_tpose(self) -> None:
        filled = softfill_omni_from_category(OmniControls(), "humanoid")
        assert filled.control_type == "pose"
        assert filled.pose_preset == "quaternius-apose"

    def test_inactive_vegetation_gets_tree_bbox(self) -> None:
        filled = softfill_omni_from_category(OmniControls(), "vegetation")
        assert filled.control_type == "bbox"
        assert filled.bbox_preset == "tree"

    def test_active_omni_not_overwritten(self) -> None:
        base = OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(6.0, 7.0, 4.5))
        filled = softfill_omni_from_category(base, "humanoid")
        assert filled.bbox_preset == "chapel"
        assert filled.pose_preset is None

    def test_creature_has_no_soft_pose(self) -> None:
        filled = softfill_omni_from_category(OmniControls(), "creature")
        assert not omni_is_active(filled)

    def test_size_m_alone_still_softfills_humanoid_pose(self) -> None:
        filled = softfill_omni_from_category(
            OmniControls(size_m=(0.55, 1.55, 0.4)),
            "humanoid",
        )
        assert filled.control_type == "pose"
        assert filled.pose_preset == "quaternius-apose"
        assert filled.size_m == (0.55, 1.55, 0.4)
        assert omni_has_geom_control(filled)


class TestResolveRowOmniSoftfill:
    def test_row_without_omni_softfills(self) -> None:
        from gameassets.manifest import ManifestRow
        from gameassets.pipeline import resolve_row_omni

        row = ManifestRow(id="npc", idea="x", kind="character", generate_3d=True, category="humanoid")
        profile = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="s",
            text3d=Text3DProfile(),
        )
        omni = resolve_row_omni(profile, row)
        assert omni.pose_preset == "quaternius-apose"


class TestShapeSpecsSdnq:
    def test_default_sdnq_uint8_in_payload(self, tmp_path: Path) -> None:
        items = [
            {
                "id": "a",
                "image": "a.png",
                "output": "a_shape.glb",
                "category": "prop",
                "control_type": "bbox",
                "bbox_preset": "crate",
            }
        ]
        (tmp_path / "a.png").write_bytes(b"x")
        with patch("text3d.ums_payload.build_generate_request") as mock_build:
            mock_build.return_value = {"ok": True}
            shape_specs_from_items(items, manifest_dir=tmp_path)
            assert mock_build.called
            kw = mock_build.call_args.kwargs
            assert kw.get("sdnq_preset") == "sdnq-int4"
            assert kw.get("memory_efficient") is True
