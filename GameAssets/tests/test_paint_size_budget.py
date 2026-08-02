"""Atlas / ``_to_paint`` resolvidos por size_m (balde vs casa)."""

from __future__ import annotations

from aigamekit_shared.paint_budget import PAINT_FACES_MAX
from gameassets.manifest import ManifestRow
from gameassets.omni_ctrl import OmniControls
from gameassets.pipeline import (
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
    def test_bucket_vs_house_medium(self) -> None:
        profile = _profile(generation="medium")
        bucket = _row(rid="bucket", category="prop", size_m=(0.35, 0.4, 0.35))
        house = _row(rid="house", category="building", size_m=(5.0, 4.2, 6.0))
        assert _resolve_paint_texture_size(profile, bucket) == 512
        assert _resolve_paint_texture_size(profile, house) == 2048
        bf = _resolve_to_paint_faces(profile, bucket)
        hf = _resolve_to_paint_faces(profile, house)
        assert 6_000 <= bf <= 20_000
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
