"""LOD faces + atlas por silhueta (char_m = ``sqrt(d1·d2)``)."""

from __future__ import annotations

from aigamekit_shared.lod_budget import lod_texture_ladder
from gameassets.categories import get_lod_ref_m, get_target_faces
from gameassets.manifest import ManifestRow
from gameassets.omni_ctrl import OmniControls
from gameassets.pipeline import (
    _resolve_lod0_texture_size,
    _resolve_lod_target_faces,
)
from gameassets.profile import GameProfile


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


class TestCharacterBudgetNotOnFloor:
    """Regressão: a métrica volumétrica punha o herói no ``LOD_FACE_SCALE_FLOOR``.

    ``size_m [0.55, 1.55, 0.4]`` → volume-equivalente 0.70 m (= um balde) → 3.9k
    faces → lod0 4.6k tris de um painted de 144.6k, com o atlas 2048→1024.
    """

    def test_hero_gets_most_of_the_humanoid_budget(self) -> None:
        profile = _profile()
        row = _row(rid="hero", category="humanoid", size_m=(0.55, 1.55, 0.4))
        faces = _resolve_lod_target_faces(profile, row)
        assert faces > 24_000, "herói de novo colado ao floor do orçamento"
        assert faces <= 32_000
        # Atlas lod0 não desce abaixo do painted (2048) por causa da silhueta.
        assert _resolve_lod0_texture_size(profile, row) == 2048

    def test_character_categories_have_lower_ref(self) -> None:
        assert get_lod_ref_m("humanoid") == 1.0
        assert get_lod_ref_m("creature") == 1.0
        assert get_lod_ref_m("prop") == 2.0
        assert get_lod_ref_m("desconhecida") == 2.0

    def test_small_creature_still_scales_down(self) -> None:
        """A ref por categoria não é um cheque em branco: bicho pequeno = menos faces."""
        profile = _profile()
        big = _row(rid="wolf", category="creature", size_m=(1.2, 0.9, 0.4))
        small = _row(rid="bogling", category="creature", size_m=(0.5, 0.55, 0.5))
        assert _resolve_lod_target_faces(profile, small) < _resolve_lod_target_faces(profile, big)

    def test_compact_prop_unchanged_by_silhouette(self) -> None:
        """Cubos: silhueta == volume-equivalente, logo props compactos não mexem."""
        profile = _profile()
        row = _row(rid="wooden_crate", category="prop", size_m=(0.8, 0.8, 0.8))
        assert _resolve_lod_target_faces(profile, row) == 3840
