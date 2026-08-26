"""Tests for aigamekit_shared.quality — QualityEngine."""

from __future__ import annotations

import pytest

from aigamekit_shared.quality import QualityEngine, QualityResolution


@pytest.fixture()
def engine() -> QualityEngine:
    return QualityEngine()


class TestQualityProfiles:
    def test_quality_profiles_load(self, engine: QualityEngine) -> None:
        """All 5 quality tiers exist."""
        expected = {"fast", "low", "medium", "high", "highest"}
        assert set(engine.list_qualities()) == expected

    def test_list_qualities_sorted(self, engine: QualityEngine) -> None:
        """list_qualities returns sorted list."""
        qualities = engine.list_qualities()
        assert qualities == sorted(qualities)

    def test_list_qualities_count(self, engine: QualityEngine) -> None:
        assert len(engine.list_qualities()) == 5


class TestCategories:
    def test_list_categories_count(self, engine: QualityEngine) -> None:
        """Returns 14 categories."""
        assert len(engine.list_categories()) == 15

    def test_list_categories_sorted(self, engine: QualityEngine) -> None:
        categories = engine.list_categories()
        assert categories == sorted(categories)


class TestAudioKinds:
    def test_list_audio_kinds_count(self, engine: QualityEngine) -> None:
        """Returns 19 audio kinds."""
        assert len(engine.list_audio_kinds()) == 19

    def test_list_audio_kinds_sorted(self, engine: QualityEngine) -> None:
        kinds = engine.list_audio_kinds()
        assert kinds == sorted(kinds)


class TestResolveText2Sound:
    def test_resolve_text2sound_medium_weapon(self, engine: QualityEngine) -> None:
        """Weapon + medium quality → sfx_impact, SA3 sfx model, steps=12, cfg=1.0."""
        r = engine.resolve("text2sound", quality="medium", category="weapon")
        assert isinstance(r, QualityResolution)
        assert r.audio_kind == "sfx_impact"
        assert r.model_id == "stabilityai/stable-audio-3-small-sfx"
        assert r.params["steps"] == 12
        assert r.params["cfg_scale"] == 1.0
        assert r.params["sampler"] == "pingpong"
        assert r.source == "category"
        assert "immediate attack" in r.prompt_hints[0]

    def test_resolve_text2sound_high_humanoid(self, engine: QualityEngine) -> None:
        """Humanoid + high quality → music_loop, SA3 music model, steps=16, trim=false."""
        r = engine.resolve("text2sound", quality="high", category="humanoid")
        assert isinstance(r, QualityResolution)
        assert r.audio_kind == "music_loop"
        assert r.model_id == "stabilityai/stable-audio-3-small-music"
        assert r.params["steps"] == 16
        assert r.params["sampler"] == "pingpong"
        # Category audio.trim is false for humanoid, but trim is in category
        # metadata not in params — verify via category_info
        cat = engine.category_info("humanoid")
        assert cat["audio"]["trim"] is False
        # Prompt hint from loop_hint
        assert len(r.prompt_hints) >= 1
        assert "seamless loop" in r.prompt_hints[0]


class TestResolveText2SoundDSP:
    """Tests for the DSP mastering params and negative_prompt_hints."""

    def test_dsp_params_present_per_tier(self, engine: QualityEngine) -> None:
        """Every quality tier exposes the DSP mastering params for text2sound."""
        for q in ("fast", "low", "medium", "high", "highest"):
            r = engine.resolve("text2sound", quality=q)
            assert "lufs_target" in r.params, f"{q} missing lufs_target"
            assert "high_pass_hz" in r.params, f"{q} missing high_pass_hz"
            assert "compressor" in r.params, f"{q} missing compressor"
            assert "true_peak_db" in r.params, f"{q} missing true_peak_db"
            assert "ogg_quality" in r.params, f"{q} missing ogg_quality"
            assert "enhance" in r.params, f"{q} missing enhance"

    def test_enhance_off_on_fast_on(self, engine: QualityEngine) -> None:
        """fast tier disables prompt enhancement; higher tiers enable it."""
        assert engine.resolve("text2sound", quality="fast").params["enhance"] is False
        for q in ("low", "medium", "high", "highest"):
            assert engine.resolve("text2sound", quality=q).params["enhance"] is True

    def test_dsp_lufs_increases_with_quality(self, engine: QualityEngine) -> None:
        """Higher tiers target louder LUFS (less headroom for the mix)."""
        fast = engine.resolve("text2sound", quality="fast").params["lufs_target"]
        highest = engine.resolve("text2sound", quality="highest").params["lufs_target"]
        assert highest > fast

    def test_negative_prompt_hint_from_kind(self, engine: QualityEngine) -> None:
        """A kind with negative_prompt_hint surfaces it on negative_prompt_hints."""
        r = engine.resolve("text2sound", quality="high", category="weapon")
        assert r.audio_kind == "sfx_impact"
        assert len(r.negative_prompt_hints) == 1
        assert "music" in r.negative_prompt_hints[0]

    def test_negative_prompt_hint_loop_kind(self, engine: QualityEngine) -> None:
        """Loop kinds also carry a negative_prompt_hint."""
        r = engine.resolve("text2sound", quality="medium", category="humanoid")
        assert r.audio_kind == "music_loop"
        assert len(r.negative_prompt_hints) == 1
        assert "tempo" in r.negative_prompt_hints[0] or "stops" in r.negative_prompt_hints[0]

    def test_compressor_preset_in_kind_info(self, engine: QualityEngine) -> None:
        """compressor_preset is accessible via audio_kind_info (not in params)."""
        r = engine.resolve("text2sound", quality="high", category="weapon")
        ki = engine.audio_kind_info(r.audio_kind)
        assert ki["compressor_preset"] == "punch"

    def test_no_negative_hint_without_category(self, engine: QualityEngine) -> None:
        """Without a category there is no audio_kind, so no negative hint."""
        r = engine.resolve("text2sound", quality="medium")
        assert r.audio_kind is None
        assert r.negative_prompt_hints == []


class TestResolveText3D:
    def test_resolve_text3d_medium_humanoid(self, engine: QualityEngine) -> None:
        """Humanoid + medium → preset=balanced, octree=192, chunks=6000."""
        r = engine.resolve("text3d", quality="medium", category="humanoid")
        assert isinstance(r, QualityResolution)
        # Quality profile sets preset=balanced; category text3d has octree/chunks/steps
        assert r.params["preset"] == "balanced"
        assert r.params["octree"] == 192
        assert r.params["chunks"] == 6000
        assert r.params["steps"] == 24  # category overrides profile
        assert r.source == "category"


class TestResolveOverrides:
    def test_resolve_with_overrides(self, engine: QualityEngine) -> None:
        """Explicit steps=200 overrides quality profile value."""
        r = engine.resolve(
            "text2sound",
            quality="medium",
            category="weapon",
            overrides={"steps": 200},
        )
        assert r.params["steps"] == 200
        assert r.source == "explicit"


class TestErrorCases:
    def test_resolve_unknown_quality_raises(self, engine: QualityEngine) -> None:
        """KeyError for bogus quality name."""
        with pytest.raises(KeyError, match="Unknown quality"):
            engine.resolve("text2sound", quality="bogus_quality")


class TestCategoryInfo:
    def test_category_info_weapon(self, engine: QualityEngine) -> None:
        """Weapon has target_faces=2500."""
        info = engine.category_info("weapon")
        assert info["target_faces"] == 2500

    def test_category_info_unknown_raises(self, engine: QualityEngine) -> None:
        with pytest.raises(KeyError, match="Unknown category"):
            engine.category_info("nonexistent")


class TestAudioKindInfo:
    def test_audio_kind_info_sfx_ui(self, engine: QualityEngine) -> None:
        """sfx_ui has SA3 defaults (cfg_scale_default=1.0, pingpong)."""
        info = engine.audio_kind_info("sfx_ui")
        assert info["cfg_scale_default"] == 1.0
        assert info["sampler"] == "pingpong"

    def test_audio_kind_info_unknown_raises(self, engine: QualityEngine) -> None:
        with pytest.raises(KeyError, match="Unknown audio kind"):
            engine.audio_kind_info("nonexistent")


class TestResolveNoCategory:
    def test_resolve_no_category(self, engine: QualityEngine) -> None:
        """Works without category; no audio_kind set for non-audio tools."""
        r = engine.resolve("text3d", quality="medium")
        assert r.category is None
        assert r.audio_kind is None
        assert r.model_id is None
        assert r.params["preset"] == "balanced"
        assert r.source == "quality_profile"


class TestResolvePaint3D:
    def test_resolve_paint3d(self, engine: QualityEngine) -> None:
        """Paint3D resolution from quality profile."""
        r = engine.resolve("paint3d", quality="medium")
        assert r.params["max_views"] == 6
        assert r.params["view_resolution"] == 512
        assert r.params["render_size"] == 2048
        assert r.params["texture_size"] == 2048
        assert r.params["bake_exp"] == 6

    def test_resolve_paint3d_with_category(self, engine: QualityEngine) -> None:
        """Paint3D with humanoid category — category overrides max_views."""
        r = engine.resolve("paint3d", quality="medium", category="humanoid")
        # Category paint.max_views=6 (same as medium profile)
        assert r.params["max_views"] == 6
        assert r.params["texture_size"] == 2048
        assert r.source == "category"


class TestResolveText2D:
    def test_resolve_text2d(self, engine: QualityEngine) -> None:
        """Text2D resolution from quality profile."""
        r = engine.resolve("text2d", quality="medium")
        assert r.params["width"] == 1024
        assert r.params["height"] == 1024
        assert r.params["steps"] == 4
        assert r.params["guidance"] == 1.0

    def test_resolve_text2d_fast(self, engine: QualityEngine) -> None:
        """Fast quality uses smaller resolution."""
        r = engine.resolve("text2d", quality="fast")
        assert r.params["width"] == 512
        assert r.params["height"] == 512
