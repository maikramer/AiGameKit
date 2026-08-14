"""Testes unitários para gameassets.dream.planner (lógica, não só dataclasses)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from gameassets.dream.planner import (
    AssetEntry,
    DreamPlan,
    Placement,
    SceneLayout,
    TerrainPlan,
    _extract_json,
    _extract_phrase,
    _fallback_plan,
    plan_game,
)


@pytest.fixture(autouse=True)
def _isolated_dream_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Cache de planos isolado por teste (evita hits entre testes e no ~/.cache real)."""
    monkeypatch.setenv("AIGAMEKIT_DREAM_CACHE", str(tmp_path / "dream-cache"))


class TestExtractJson:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ('{"a": 1}', {"a": 1}),
            ('prefix text {"a": 2} trailing', {"a": 2}),
            ('```json\n{"a": 3}\n```', {"a": 3}),
            ('prose before {"a": 4} and after', {"a": 4}),
            ('{"a": {"b": [1, 2, 3]}}', {"a": {"b": [1, 2, 3]}}),
            ('multiple {"x": 1} objects {"y": 2}', {"x": 1}),
        ],
    )
    def test_parses_valid_blocks(self, text: str, expected: dict[str, object]) -> None:
        assert _extract_json(text) == expected

    @pytest.mark.parametrize(
        "text",
        ["no json here at all", "just plain text without braces", ""],
    )
    def test_no_json_raises(self, text: str) -> None:
        with pytest.raises(ValueError, match="Nenhum JSON"):
            _extract_json(text)

    @pytest.mark.parametrize("text", ['{"a": 1', '{"a": {"b": 2}', "{ incomplete"])
    def test_incomplete_json_raises(self, text: str) -> None:
        with pytest.raises(ValueError, match="incompleto"):
            _extract_json(text)

    def test_nested_braces_balanced(self) -> None:
        text = 'noise {"a": {"b": {"c": 1}}} tail'
        assert _extract_json(text) == {"a": {"b": {"c": 1}}}


class TestExtractPhrase:
    def test_keyword_present_single_segment(self) -> None:
        out = _extract_phrase("a brave hero with a sword", "hero")
        assert out == "a brave hero with a sword"

    def test_keyword_between_commas(self) -> None:
        out = _extract_phrase("intro, the brave hero, continues here", "hero")
        assert "hero" in out
        assert "intro" not in out
        assert "continues" not in out

    def test_keyword_absent_returns_empty(self) -> None:
        assert _extract_phrase("a dark forest with trees", "hero") == ""

    def test_phrase_capped_to_80_chars(self) -> None:
        long_desc = "hero " + "word " * 40
        assert len(_extract_phrase(long_desc, "hero")) <= 80

    def test_deterministic(self) -> None:
        desc = "a scene, mighty hero, end"
        assert _extract_phrase(desc, "hero") == _extract_phrase(desc, "hero")


class TestFallbackPlan:
    def test_character_keyword_adds_rigged_hero(self) -> None:
        plan = _fallback_plan("a hero character with a glowing sword", "lowpoly")
        hero = next((a for a in plan.assets if a.id == "hero"), None)
        assert hero is not None
        assert hero.kind == "character"
        assert hero.generate_rig is True
        assert hero.generate_3d is True

    def test_prop_keywords_become_props(self) -> None:
        plan = _fallback_plan("a scene with a chest and a barrel", "lowpoly")
        ids = {a.id for a in plan.assets}
        assert "treasure_chest" in ids
        assert "barrel" in ids
        for asset in plan.assets:
            if asset.id in {"treasure_chest", "barrel"}:
                assert asset.kind == "prop"
                assert asset.generate_3d is True

    def test_audio_keyword_adds_audio_asset(self) -> None:
        plan = _fallback_plan("collect coins with a sound effect", "lowpoly")
        sfx = next((a for a in plan.assets if a.id == "collect_sfx"), None)
        assert sfx is not None
        assert sfx.generate_audio is True
        assert sfx.generate_3d is False

    def test_asset_count_within_bound(self) -> None:
        desc = "hero with sword shield chest barrel rock pillar lamp potion tree bush house"
        plan = _fallback_plan(desc, "lowpoly")
        assert 1 <= len(plan.assets) <= 8

    def test_all_placements_reference_existing_assets(self) -> None:
        plan = _fallback_plan("a hero, a chest, a tree and a barrel", "lowpoly")
        asset_ids = {a.id for a in plan.assets}
        assert asset_ids
        for placement in plan.scene.placements:
            assert placement.asset_id in asset_ids

    def test_terrain_is_none_in_fallback(self) -> None:
        plan = _fallback_plan("an open world RPG with mountains", "lowpoly")
        assert plan.terrain is None

    def test_empty_description_still_yields_plan(self) -> None:
        plan = _fallback_plan("zzz qqq", "lowpoly")
        assert len(plan.assets) >= 1
        assert plan.title
        assert plan.style_preset == "lowpoly"

    def test_style_preset_propagated_to_ideas(self) -> None:
        plan = _fallback_plan("a wooden crate on the ground", "pixel_art")
        crate = next(a for a in plan.assets if a.id == "wooden_crate")
        assert "pixel_art" in crate.idea

    def test_title_derived_from_description(self) -> None:
        plan = _fallback_plan("Crystal caves explorer, with gems", "lowpoly")
        assert plan.title == "Crystal Caves Explorer"

    def test_rpg_genre_detected(self) -> None:
        plan = _fallback_plan("an epic RPG adventure", "lowpoly")
        assert plan.genre == "3D exploration RPG"


class TestDreamPlanRoundtrip:
    def test_terrain_roundtrip(self) -> None:
        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[AssetEntry(id="ground", idea="platform", kind="environment")],
            scene=SceneLayout(placements=[Placement(asset_id="ground")]),
            terrain=TerrainPlan(enabled=True, seed=7, prompt="mountain island", world_size=512.0),
        )
        d = plan.to_dict()
        assert d["terrain"]["enabled"] is True
        assert d["terrain"]["seed"] == 7
        rebuilt = DreamPlan.from_dict(d)
        assert rebuilt.terrain is not None
        assert rebuilt.terrain.enabled is True
        assert rebuilt.terrain.seed == 7
        assert rebuilt.terrain.world_size == 512.0

    def test_no_terrain_omitted_in_dict(self) -> None:
        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[],
            scene=SceneLayout(),
        )
        assert "terrain" not in plan.to_dict()


def _llm_json_response() -> str:
    return json.dumps(
        {
            "title": "Test Game",
            "genre": "platformer",
            "tone": "bright",
            "style_preset": "lowpoly",
            "sky_prompt": "blue sky 360",
            "assets": [
                {"id": "ground", "idea": "platform", "kind": "environment", "generate_3d": True},
                {"id": "coin", "idea": "gold coin", "kind": "prop", "generate_3d": True},
            ],
            "scene": {
                "sky_color": "#87CEEB",
                "ground_size": 50,
                "spawn_y": 5,
                "placements": [
                    {"asset_id": "ground", "pos": "0 0 0", "scale": "10 1 10"},
                    {"asset_id": "coin", "pos": "3 1 0"},
                ],
            },
        }
    )


class TestPlanGameProviders:
    def test_openai_provider_success(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()) as mock_call:
            plan = plan_game(
                "a platformer",
                preset_names=["lowpoly"],
                provider="openai",
                model="gpt-4o-mini",
                api_key="secret-key",
                base_url="https://example.test/v1",
            )
        mock_call.assert_called_once()
        _args, kwargs = mock_call.call_args
        assert kwargs["model"] == "gpt-4o-mini"
        assert kwargs["api_key"] == "secret-key"
        assert kwargs["base_url"] == "https://example.test/v1"
        assert plan.title == "Test Game"
        assert len(plan.assets) == 2

    def test_huggingface_provider_success(self) -> None:
        with patch("gameassets.dream.planner._call_huggingface", return_value=_llm_json_response()) as mock_call:
            plan = plan_game("a platformer", preset_names=["lowpoly"], provider="huggingface", model="llama")
        mock_call.assert_called_once()
        _args, kwargs = mock_call.call_args
        assert kwargs["model"] == "llama"
        assert plan.title == "Test Game"

    def test_stdin_provider_success(self) -> None:
        with patch("gameassets.dream.planner._call_stdin", return_value=_llm_json_response()) as mock_call:
            plan = plan_game("a platformer", preset_names=["lowpoly"], provider="stdin")
        mock_call.assert_called_once()
        assert plan.title == "Test Game"

    def test_malformed_response_falls_back(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value="totally not json"):
            plan = plan_game("a hero game", preset_names=["lowpoly"], provider="openai")
        assert isinstance(plan, DreamPlan)
        assert plan.assets
        assert plan.style_preset == "lowpoly"

    def test_provider_exception_falls_back(self) -> None:
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("network down")):
            plan = plan_game("a hero game", preset_names=["lowpoly"], provider="openai")
        assert plan.assets

    def test_unknown_provider_falls_back_not_raises(self) -> None:
        # plan_game envolve o dispatch num try/except global: um provider
        # desconhecido cai no fallback em vez de propagar a exceção.
        plan = plan_game("a hero game", preset_names=["lowpoly"], provider="bogus")
        assert plan.assets

    def test_plan_json_written(self, tmp_path: Path) -> None:
        out = tmp_path / "deep" / "plan.json"
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()):
            plan_game("a platformer", preset_names=["lowpoly"], provider="openai", plan_json_path=str(out))
        assert out.is_file()
        data = json.loads(out.read_text(encoding="utf-8"))
        assert data["title"] == "Test Game"

    def test_style_preset_default_when_none(self) -> None:
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("fail")):
            plan = plan_game("a game", preset_names=[], style_preset=None, provider="openai")
        assert plan.style_preset == "lowpoly"

    def test_system_prompt_built_from_context(self) -> None:
        captured: dict[str, str] = {}

        def _capture(system_prompt: str, user_prompt: str, **_kwargs: object) -> str:
            captured["system"] = system_prompt
            captured["user"] = user_prompt
            return _llm_json_response()

        with patch("gameassets.dream.planner._call_openai", side_effect=_capture):
            plan_game("a game", preset_names=["lowpoly", "painterly"], provider="openai", max_assets=5)
        assert "JSON Schema" in captured["system"]
        assert "lowpoly" in captured["system"]
        assert "Maximum total assets: 5" in captured["system"]
        assert "Maximum assets: 5" in captured["user"]


class TestProvenanceAndLint:
    def test_llm_source_tagged(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()):
            plan = plan_game("a platformer", preset_names=["lowpoly"], provider="openai", model="gpt-4o-mini")
        assert plan.source == "llm:openai"
        assert plan.source_detail == "gpt-4o-mini"

    def test_fallback_source_carries_reason_and_hint(self) -> None:
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("network down")):
            plan = plan_game("a hero game", preset_names=["lowpoly"], provider="openai")
        assert plan.source == "fallback"
        assert "network down" in plan.source_detail
        assert "OPENAI_API_KEY" in plan.source_detail

    def test_llm_garbage_plan_autorepaired(self) -> None:
        garbage = json.dumps(
            {
                "title": "Garbage",
                "genre": "x",
                "tone": "x",
                "style_preset": "lowpoly",
                "assets": [
                    {"id": "Hero Guy", "idea": "hero", "kind": "npc", "generate_animate": True},
                    {"id": "Hero Guy", "idea": "dup"},
                ],
                "scene": {
                    "placements": [
                        {"asset_id": "Hero Guy", "pos": "junk", "scale": "junk"},
                        {"asset_id": "ghost", "pos": "0 0 0"},
                    ]
                },
            }
        )
        with patch("gameassets.dream.planner._call_openai", return_value=garbage):
            plan = plan_game("a game", preset_names=["lowpoly"], provider="openai")
        ids = [a.id for a in plan.assets]
        assert ids == ["hero_guy", "hero_guy_2"]
        assert plan.assets[0].kind == "character"
        assert plan.assets[0].generate_rig is True
        assert [p.asset_id for p in plan.scene.placements] == ["hero_guy"]
        assert plan.scene.placements[0].pos == "0 0 0"
        assert plan.repairs

    def test_roundtrip_provenance_fields(self) -> None:
        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[AssetEntry(id="a", idea="x")],
            scene=SceneLayout(placements=[]),
            seed=99,
            source="llm:openai",
            source_detail="gpt-4o-mini",
            repairs=["fixed x"],
        )
        d = plan.to_dict()
        assert d["seed"] == 99
        assert d["source"] == "llm:openai"
        assert d["repairs"] == ["fixed x"]
        rebuilt = DreamPlan.from_dict(d)
        assert rebuilt.seed == 99
        assert rebuilt.source == "llm:openai"
        assert rebuilt.repairs == ["fixed x"]

    def test_provenance_fields_omitted_when_empty(self) -> None:
        plan = DreamPlan(title="T", genre="g", tone="t", style_preset="lowpoly", assets=[], scene=SceneLayout())
        d = plan.to_dict()
        for key in ("seed", "source", "source_detail", "repairs"):
            assert key not in d


class TestPlanCache:
    def test_second_call_hits_cache_without_provider(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()):
            plan_game("cache me", preset_names=["lowpoly"], provider="openai")
        with patch("gameassets.dream.planner._call_openai") as mock_call2:
            plan2 = plan_game("cache me", preset_names=["lowpoly"], provider="openai")
        mock_call2.assert_not_called()
        assert plan2.source == "cache"
        assert plan2.title == "Test Game"

    def test_replan_flag_bypasses_cache(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()):
            plan_game("cache me", preset_names=["lowpoly"], provider="openai")
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()) as mock_call:
            plan2 = plan_game("cache me", preset_names=["lowpoly"], provider="openai", use_cache=False)
        mock_call.assert_called_once()
        assert plan2.source == "llm:openai"

    def test_different_model_is_cache_miss(self) -> None:
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()):
            plan_game("cache me", preset_names=["lowpoly"], provider="openai", model="m1")
        with patch("gameassets.dream.planner._call_openai", return_value=_llm_json_response()) as mock_call:
            plan_game("cache me", preset_names=["lowpoly"], provider="openai", model="m2")
        mock_call.assert_called_once()

    def test_fallback_not_cached(self) -> None:
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("down")):
            plan_game("no cache for fallback", preset_names=["lowpoly"], provider="openai")
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("down")) as mock_call:
            plan_game("no cache for fallback", preset_names=["lowpoly"], provider="openai")
        mock_call.assert_called_once()  # segunda chamada foi ao provider de novo

    def test_stdin_provider_not_cached(self) -> None:
        with patch("gameassets.dream.planner._call_stdin", return_value=_llm_json_response()):
            plan_game("stdin no cache", preset_names=["lowpoly"], provider="stdin")
        with patch("gameassets.dream.planner._call_stdin", return_value=_llm_json_response()) as mock_call:
            plan_game("stdin no cache", preset_names=["lowpoly"], provider="stdin")
        mock_call.assert_called_once()


class _FakeOllamaResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self) -> _FakeOllamaResponse:
        return self

    def __exit__(self, *args: object) -> None:
        return None


class TestOllamaProvider:
    def test_success_via_native_api(self) -> None:

        captured: dict = {}

        def _fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["data"] = json.loads(req.data.decode("utf-8"))
            return _FakeOllamaResponse({"message": {"content": '{"title": "Ollama Game"}'}})

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen):
            plan = plan_game("a game", preset_names=["lowpoly"], provider="ollama")
        assert captured["url"].endswith("/api/chat")
        assert captured["data"]["format"] == "json"
        assert captured["data"]["model"] == "llama3.1:8b"
        assert plan.title == "Ollama Game"
        assert plan.source == "llm:ollama"

    def test_host_without_scheme_gets_http(self) -> None:
        from gameassets.dream.planner import _ollama_base_url

        assert _ollama_base_url(None) == "http://127.0.0.1:11434"
        assert _ollama_base_url("localhost:11434") == "http://localhost:11434"
        assert _ollama_base_url("http://x:1/") == "http://x:1"

    def test_connection_failure_falls_back_with_hint(self) -> None:
        with patch("gameassets.dream.planner._call_ollama", side_effect=RuntimeError("connection refused")):
            plan = plan_game("a game", preset_names=["lowpoly"], provider="ollama")
        assert plan.source == "fallback"
        assert "ollama serve" in plan.source_detail


class TestRefinePlan:
    def _base_plan(self) -> DreamPlan:
        return DreamPlan(
            title="Base",
            genre="rpg",
            tone="dawn",
            style_preset="lowpoly",
            assets=[AssetEntry(id="hero", idea="hero", kind="character", generate_3d=True, generate_rig=True)],
            scene=SceneLayout(placements=[Placement(asset_id="hero", pos="0 1 0")]),
            terrain=TerrainPlan(enabled=True, seed=7, prompt="hills"),
            seed=1234,
        )

    def _refined_json(self) -> str:
        return json.dumps(
            {
                "title": "Base",
                "genre": "rpg",
                "tone": "dusk",
                "style_preset": "lowpoly",
                "assets": [
                    {"id": "hero", "idea": "hero", "kind": "character", "generate_3d": True, "generate_rig": True},
                    {"id": "dragon", "idea": "dragon boss", "kind": "character", "generate_3d": True},
                ],
                "scene": {"placements": [{"asset_id": "dragon", "pos": "8 0 8"}]},
            }
        )

    def test_success_sets_source_and_lint(self) -> None:
        from gameassets.dream.planner import refine_plan

        with patch("gameassets.dream.planner._call_openai", return_value=self._refined_json()) as mock_call:
            refined = refine_plan(self._base_plan(), "add a dragon", preset_names=["lowpoly"], provider="openai")
        mock_call.assert_called_once()
        assert refined.source == "refine:openai"
        ids = [a.id for a in refined.assets]
        assert "dragon" in ids

    def test_failure_returns_original_with_refine_failed(self) -> None:
        from gameassets.dream.planner import refine_plan

        base = self._base_plan()
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("boom")):
            refined = refine_plan(base, "add a dragon", preset_names=["lowpoly"], provider="openai")
        assert refined.source == "refine-failed"
        assert "boom" in refined.source_detail
        assert [a.id for a in refined.assets] == ["hero"]  # original intocado
        assert base.source == ""  # nem mutamos o input

    def test_seed_preserved(self) -> None:
        from gameassets.dream.planner import refine_plan

        with patch("gameassets.dream.planner._call_openai", return_value=self._refined_json()):
            refined = refine_plan(self._base_plan(), "x", preset_names=["lowpoly"], provider="openai")
        assert refined.seed == 1234

    def test_terrain_preserved_when_omitted(self) -> None:
        from gameassets.dream.planner import refine_plan

        with patch("gameassets.dream.planner._call_openai", return_value=self._refined_json()):
            refined = refine_plan(self._base_plan(), "x", preset_names=["lowpoly"], provider="openai")
        assert refined.terrain is not None
        assert refined.terrain.seed == 7
        assert refined.terrain.enabled is True

    def test_terrain_disabled_when_llm_says_so(self) -> None:
        from gameassets.dream.planner import refine_plan

        payload = json.loads(self._refined_json())
        payload["terrain"] = {"enabled": False}
        with patch("gameassets.dream.planner._call_openai", return_value=json.dumps(payload)):
            refined = refine_plan(self._base_plan(), "remove terrain", preset_names=["lowpoly"], provider="openai")
        assert refined.terrain is not None
        assert refined.terrain.enabled is False

    def test_prompt_embeds_current_plan_without_provenance(self) -> None:
        from gameassets.dream.planner import refine_plan

        captured: dict = {}

        def _capture(system_prompt: str, user_prompt: str, **_kw: object) -> str:
            captured["user"] = user_prompt
            return self._refined_json()

        with patch("gameassets.dream.planner._call_openai", side_effect=_capture):
            refine_plan(self._base_plan(), "add a dragon", preset_names=["lowpoly"], provider="openai")
        assert "CURRENT PLAN JSON:" in captured["user"]
        assert "INSTRUCTION: add a dragon" in captured["user"]
        assert "source_detail" not in captured["user"]


class TestApplySeedAndLoad:
    def test_apply_seed_sets_plan_and_terrain(self) -> None:
        from gameassets.dream.planner import apply_seed

        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[],
            scene=SceneLayout(),
            terrain=TerrainPlan(enabled=True),
        )
        apply_seed(plan, 42)
        assert plan.seed == 42
        assert plan.terrain is not None and plan.terrain.seed == 42

    def test_apply_seed_respects_pinned_terrain_seed(self) -> None:
        from gameassets.dream.planner import apply_seed

        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[],
            scene=SceneLayout(),
            terrain=TerrainPlan(enabled=True, seed=9),
        )
        apply_seed(plan, 42)
        assert plan.terrain is not None and plan.terrain.seed == 9

    def test_load_plan_path_roundtrip(self, tmp_path: Path) -> None:
        from gameassets.dream.planner import load_plan_path

        plan = DreamPlan(
            title="T",
            genre="g",
            tone="t",
            style_preset="lowpoly",
            assets=[AssetEntry(id="a", idea="x")],
            scene=SceneLayout(),
            seed=5,
        )
        p = tmp_path / "plan.json"
        p.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
        loaded = load_plan_path(p)
        assert loaded.title == "T"
        assert loaded.seed == 5
        assert loaded.assets[0].id == "a"
