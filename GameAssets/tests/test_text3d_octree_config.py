"""text3d.octree_resolution: override opcional; omitido → size-tune Text3D."""

from __future__ import annotations

from gameassets.manifest import ManifestRow, RowText3D, _row_text3d_from_dict, apply_row_text3d_overrides
from gameassets.profile import GameProfile


def test_game_yaml_text3d_octree_optional() -> None:
    """Profile sem octree_resolution é válido — bbox_tune trata do eixo."""
    p = GameProfile.from_dict(
        {
            "title": "t",
            "genre": "g",
            "tone": "x",
            "style_preset": "painterly",
            "text3d": {"export_origin": "feet", "sdnq_preset": "sdnq-int4"},
        }
    )
    assert p.text3d is not None
    assert p.text3d.octree_resolution is None


def test_game_yaml_text3d_octree_explicit_still_parses() -> None:
    p = GameProfile.from_dict(
        {
            "title": "t",
            "genre": "g",
            "tone": "x",
            "style_preset": "painterly",
            "text3d": {"octree_resolution": 384, "export_origin": "feet"},
        }
    )
    assert p.text3d is not None
    assert p.text3d.octree_resolution == 384


def test_manifest_row_octree_overrides_item() -> None:
    rt3 = _row_text3d_from_dict({"octree_resolution": 448}, "village_longhouse")
    assert rt3 == RowText3D(octree_resolution=448)
    row = ManifestRow(
        id="village_longhouse",
        idea="hall",
        kind="environment",
        generate_3d=True,
        text3d=rt3,
    )
    item = apply_row_text3d_overrides({"octree_resolution": 256}, row)
    assert item["octree_resolution"] == 448


def test_text3d_argv_skips_face_tier_octree_when_size_m() -> None:
    """Com size_m, face-optimizer não passa --octree-resolution (deixa bbox_tune)."""
    from pathlib import Path

    from gameassets.omni_ctrl import OmniControls
    from gameassets.pipeline import _text3d_argv
    from gameassets.profile import Text3DProfile

    profile = GameProfile(
        title="t",
        genre="g",
        tone="t",
        style_preset="s",
        output_dir=".",
        text3d=Text3DProfile(export_origin="feet"),  # sem octree → optimize ON
    )
    row = ManifestRow(
        id="village_longhouse",
        idea="hall",
        kind="environment",
        category="building",
        generate_3d=True,
        omni=OmniControls(control_type="bbox", bbox_preset="building", size_m=(10.0, 5.0, 6.0)),
    )
    argv = _text3d_argv("text3d", profile, Path("img.png"), Path("out.glb"), row)
    assert "--octree-resolution" not in argv
