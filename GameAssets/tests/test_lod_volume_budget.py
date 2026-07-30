"""LOD faces + atlas por volume (char_m)."""

from __future__ import annotations

from gameassets.categories import get_target_faces
from gameassets.manifest import ManifestRow
from gameassets.omni_ctrl import OmniControls
from gameassets.pipeline import (
    _resolve_lod0_texture_size,
    _resolve_lod_target_faces,
)
from gameassets.profile import GameProfile
from gamedev_shared.lod_budget import lod_texture_ladder


def _row(*, rid: str, category: str, size_m: tuple[float, float, float]) -> ManifestRow:
    return ManifestRow(
        id=rid,
        idea=rid,
        kind=None,
        generate_3d=True,
        category=category,
        omni=OmniControls(size_m=size_m),
    )


def _profile(*, generation: str = "medium") -> GameProfile:
    return GameProfile(
        title="t",
        genre="g",
        tone="t",
        style_preset="lowpoly",
        generation=generation,
    )


class TestGetTargetFacesVolume:
    def test_prop_without_char_legacy(self) -> None:
        assert get_target_faces("prop") == 24_000

    def test_horseshoe_scaled(self) -> None:
        # char≈0.37 → scale floor 0.12 → 24000*0.12 = 2880
        faces = get_target_faces("prop", char_m=0.37)
        assert faces == 2880

    def test_full_at_two_meters(self) -> None:
        assert get_target_faces("prop", char_m=2.0) == 24_000


class TestResolveLodBudget:
    def test_horseshoe_faces_and_tex(self) -> None:
        profile = _profile()
        row = _row(rid="horseshoe_pile", category="prop", size_m=(0.45, 0.25, 0.45))
        faces = _resolve_lod_target_faces(profile, row)
        tex = _resolve_lod0_texture_size(profile, row)
        assert 800 <= faces <= 4000
        assert faces == 2880
        assert tex == 512
        assert lod_texture_ladder(tex) == (512, 256, 128)

    def test_building_full_budget(self) -> None:
        profile = _profile()
        row = _row(rid="chapel", category="building", size_m=(10.0, 12.0, 10.0))
        assert _resolve_lod_target_faces(profile, row) == 24_000
        assert _resolve_lod0_texture_size(profile, row) == 2048
