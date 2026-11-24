"""Atlas / ``_to_paint`` resolvidos por size_m (balde vs casa)."""

from __future__ import annotations

import aigamekit_shared.paint_budget as _pb
from aigamekit_shared.paint_budget import PAINT_FACES_MAX
from gameassets.manifest import ManifestRow
from gameassets.omni_ctrl import OmniControls
from gameassets.pipeline import (
    _character_paint_opts,
    _is_character_row,
    _paint_item_overrides,
    _resolve_paint_texture_size,
    _resolve_to_paint_faces,
)
from gameassets.profile import GameProfile, Paint3DProfile


def _row(*, rid: str, category: str, size_m: tuple[float, float, float]) -> ManifestRow:
    return ManifestRow(
        id=rid,
        idea=rid,
        kind=None,
        generate_3d=True,
        category=category,
        omni=OmniControls(size_m=size_m),
    )


def _profile(*, generation: str = "medium", paint3d: Paint3DProfile | None = None) -> GameProfile:
    return GameProfile(
        title="t",
        genre="g",
        tone="t",
        style_preset="lowpoly",
        generation=generation,
        paint3d=paint3d,
    )


class TestResolvePaintTextureBySize:
    def test_bucket_vs_house_medium(self, monkeypatch) -> None:
        # GPU grande explícita: numa 6 GiB o tecto por VRAM reduz o alvo.
        monkeypatch.setattr(_pb, "_VRAM_TOTAL_MIB", 16384)
        profile = _profile(generation="medium")
        bucket = _row(rid="bucket", category="prop", size_m=(0.35, 0.4, 0.35))
        house = _row(rid="house", category="building", size_m=(5.0, 4.2, 6.0))
        assert _resolve_paint_texture_size(profile, bucket) == 512
        assert _resolve_paint_texture_size(profile, house) == 2048
        bf = _resolve_to_paint_faces(profile, bucket)
        hf = _resolve_to_paint_faces(profile, house)
        assert 20_000 <= bf <= 40_000
        assert hf == PAINT_FACES_MAX

    def test_explicit_texture_override_wins(self) -> None:
        profile = _profile(
            generation="medium",
            paint3d=Paint3DProfile(texture_size=1024),
        )
        bucket = _row(rid="bucket", category="prop", size_m=(0.35, 0.4, 0.35))
        assert _resolve_paint_texture_size(profile, bucket) == 1024

    def test_highest_house_can_reach_4096(self) -> None:
        profile = _profile(generation="highest")
        house = _row(rid="house", category="building", size_m=(5.0, 4.2, 6.0))
        assert _resolve_paint_texture_size(profile, house) == 4096


def _char_row(
    *,
    rid: str = "boss_ogre",
    category: str = "creature",
    size_m: tuple[float, float, float] = (1.2, 2.8, 0.9),
    paint3d: dict | None = None,
) -> ManifestRow:
    return ManifestRow(
        id=rid,
        idea=rid,
        kind="character",
        generate_3d=True,
        category=category,
        omni=OmniControls(size_m=size_m),
        paint3d=paint3d,
    )


class TestCharacterPaintDefaults:
    def test_character_category_detected(self) -> None:
        assert _is_character_row(_char_row())
        assert _is_character_row(_char_row(category="humanoid"))
        assert not _is_character_row(_row(rid="house", category="building", size_m=(5, 4, 6)))

    def test_to_paint_faces_capped_for_xatlas(self, monkeypatch) -> None:
        # Sem cap a fórmula dá ~269k em 6 GiB / 461k em 16 GiB; o xatlas
        # rebentava os 300 s no personagem. O cap é o lever.
        monkeypatch.setattr(_pb, "_VRAM_TOTAL_MIB", 16384)
        profile = _profile(generation="medium")
        assert _resolve_to_paint_faces(profile, _char_row()) == 100_000

    def test_row_to_paint_override_still_wins(self) -> None:
        profile = _profile(generation="medium")
        row = _char_row(paint3d={"to_paint_faces": 60_000})
        assert _resolve_to_paint_faces(profile, row) == 60_000

    def test_paint_item_overrides_character(self) -> None:
        profile = _profile(paint3d=Paint3DProfile(pbr_enrich=False))
        opts = _paint_item_overrides(profile, _char_row())
        assert opts == {"max_views": 8, "view_resolution": 640, "pbr_enrich": False}

    def test_row_paint_override_beats_character_default(self) -> None:
        profile = _profile(paint3d=Paint3DProfile(pbr_enrich=False))
        row = _char_row(paint3d={"max_views": 6, "view_resolution": 512})
        opts = _paint_item_overrides(profile, row)
        assert opts["max_views"] == 6
        assert opts["view_resolution"] == 512

    def test_non_character_has_no_view_defaults(self) -> None:
        profile = _profile(paint3d=Paint3DProfile(pbr_enrich=False))
        opts = _paint_item_overrides(profile, _row(rid="house", category="building", size_m=(5, 4, 6)))
        assert "max_views" not in opts
        assert "view_resolution" not in opts
        assert opts["pbr_enrich"] is False

    def test_character_paint_opts(self) -> None:
        assert _character_paint_opts(_char_row()) == (8, 640)
        assert _character_paint_opts(_row(rid="prop", category="prop", size_m=(0.4, 0.4, 0.4))) == (None, None)

    def test_multiview_shape_and_multiref_paint_helpers(self, tmp_path) -> None:
        from gameassets.pipeline import (
            _concise_turnaround_subject,
            attach_multiref_paint_fields,
            attach_multiview_shape_fields,
        )
        from gameassets.profile import Text3DProfile

        assert (
            _concise_turnaround_subject(
                "stylized rural fantasy cartographer with 6.5-head craft proportions and sculpted chamfered "
                "facial planes, wearing linen shirt, front-facing"
            )
            == "stylized rural fantasy cartographer"
        )

        prof = _profile(paint3d=Paint3DProfile(multi_ref=True))
        prof.text3d = Text3DProfile(auto_turnaround_categories=("humanoid",), turnaround_include_right=True)
        shape_item: dict = {"id": "hero"}
        attach_multiview_shape_fields(shape_item, prof, _row(rid="hero", category="humanoid", size_m=(0.55, 1.55, 0.4)))
        assert shape_item["auto_turnaround"] is True
        assert shape_item["include_right"] is True

        front = tmp_path / "hero.png"
        back = tmp_path / "hero_back.png"
        right = tmp_path / "hero_right.png"
        for p in (front, back, right):
            p.write_bytes(b"PNG")
        paint_item: dict = {"id": "hero", "image": str(front)}
        attach_multiref_paint_fields(paint_item, prof, front)
        assert paint_item["blend_refs"] is True
        assert paint_item["image_azims"] == [0.0, 180.0, 90.0]
        assert len(paint_item["images"]) == 3
