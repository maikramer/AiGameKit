"""Testes unitários para gameassets.generation_profiles (5 tiers + get_profile)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields

import pytest

from gameassets.generation_profiles import (
    PROFILES,
    VALID_GENERATION_PROFILES,
    GenerationProfile,
    get_profile,
)


class TestProfileRegistry:
    def test_all_tiers_registered(self) -> None:
        assert set(PROFILES) == set(VALID_GENERATION_PROFILES)
        assert VALID_GENERATION_PROFILES == ("fast", "low", "medium", "high", "highest")

    def test_get_profile_returns_instance(self) -> None:
        p = get_profile("medium")
        assert isinstance(p, GenerationProfile)
        assert p.name == "medium"

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(ValueError, match="Perfil de geração desconhecido"):
            get_profile("ultra")

    def test_unknown_profile_error_lists_valid(self) -> None:
        with pytest.raises(ValueError, match="fast"):
            get_profile("bogus")


@pytest.mark.parametrize(
    ("tier", "expected"),
    [
        ("fast", dict(text2d_width=512, text2d_height=512, text2d_steps=4, text3d_preset="fast")),
        ("low", dict(text2d_width=768, text2d_height=768, text2d_steps=4, text3d_preset="fast")),
        ("medium", dict(text2d_width=1024, text2d_height=1024, text2d_steps=4, text3d_preset="balanced")),
        ("high", dict(text2d_width=1024, text2d_height=1024, text2d_steps=8, text3d_preset="hq")),
        ("highest", dict(text2d_width=1024, text2d_height=1024, text2d_steps=8, text3d_preset="hq")),
    ],
)
def test_text2d_and_text3d_per_tier(tier: str, expected: dict[str, object]) -> None:
    p = get_profile(tier)
    for key, value in expected.items():
        assert getattr(p, key) == value, f"{tier}.{key}"


@pytest.mark.parametrize(
    ("tier", "views", "view_res", "render", "tex", "bake"),
    [
        ("fast", 2, 384, 1024, 1024, 6),
        ("low", 4, 384, 1024, 2048, 6),
        ("medium", 6, 512, 2048, 2048, 6),
        ("high", 8, 512, 2048, 4096, 6),
        ("highest", 10, 512, 2048, 4096, 6),
    ],
)
def test_paint3d_per_tier(tier: str, views: int, view_res: int, render: int, tex: int, bake: int) -> None:
    p = get_profile(tier)
    assert p.paint_max_views == views
    assert p.paint_view_resolution == view_res
    assert p.paint_render_size == render
    assert p.paint_texture_size == tex
    assert p.paint_bake_exp == bake


@pytest.mark.parametrize(
    ("tier", "ratio", "tex_size", "sound_steps"),
    [
        ("fast", 0.25, 1024, 4),
        ("low", 0.5, 1024, 8),
        ("medium", 1.0, 2048, 16),
        ("high", 2.0, 2048, 24),
        ("highest", 0.0, 4096, 32),
    ],
)
def test_simplify_and_audio_per_tier(tier: str, ratio: float, tex_size: int, sound_steps: int) -> None:
    p = get_profile(tier)
    assert p.simplify_face_ratio == ratio
    assert p.simplify_texture_size == tex_size
    assert p.text2sound_steps == sound_steps


class TestProfileInvariants:
    def test_all_profiles_smooth_and_bake(self) -> None:
        for tier in VALID_GENERATION_PROFILES:
            p = get_profile(tier)
            assert p.paint_smooth is True, tier
            assert p.paint_bake_exp == 6, tier

    def test_fast_uses_two_smooth_passes_others_three(self) -> None:
        assert get_profile("fast").paint_smooth_passes == 2
        for tier in ("low", "medium", "high", "highest"):
            assert get_profile(tier).paint_smooth_passes == 3, tier

    def test_guidance_constant_across_tiers(self) -> None:
        for tier in VALID_GENERATION_PROFILES:
            p = get_profile(tier)
            assert p.text2d_guidance == 1.0, tier
            assert p.text3d_guidance == 5.0, tier

    def test_quality_monotonic_text2d_resolution(self) -> None:
        sizes = [get_profile(t).text2d_width for t in VALID_GENERATION_PROFILES]
        assert sizes == sorted(sizes)
        assert sizes[-1] == 1024

    def test_quality_monotonic_paint_texture(self) -> None:
        sizes = [get_profile(t).paint_texture_size for t in VALID_GENERATION_PROFILES]
        assert sizes == sorted(sizes)

    def test_highest_skips_simplification(self) -> None:
        assert get_profile("highest").simplify_face_ratio == 0.0


class TestFrozenDataclass:
    def test_profile_is_frozen(self) -> None:
        p = get_profile("medium")
        with pytest.raises(FrozenInstanceError):
            p.text2d_steps = 99  # type: ignore[misc]

    def test_all_fields_present(self) -> None:
        names = {f.name for f in fields(GenerationProfile)}
        expected = {
            "name",
            "text2d_width",
            "text2d_height",
            "text2d_steps",
            "text2d_guidance",
            "text3d_preset",
            "text3d_guidance",
            "paint_max_views",
            "paint_view_resolution",
            "paint_render_size",
            "paint_texture_size",
            "paint_bake_exp",
            "paint_smooth",
            "paint_smooth_passes",
            "simplify_face_ratio",
            "simplify_texture_size",
            "text2sound_steps",
        }
        assert names == expected
