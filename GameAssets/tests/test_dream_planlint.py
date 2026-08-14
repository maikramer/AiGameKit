"""Testes para gameassets.dream.planlint (validação, auto-reparo, stage chains)."""

from __future__ import annotations

import pytest

from gameassets.dream.planlint import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    asset_artifacts,
    asset_stage_chain,
    autorepair_plan,
    format_issue,
    lint_and_repair,
    parse_vec3,
    slugify_id,
    validate_plan,
)
from gameassets.dream.planner import (
    AssetEntry,
    DreamPlan,
    Placement,
    SceneLayout,
    TerrainPlan,
)


def _plan(
    assets: list[AssetEntry] | None = None,
    placements: list[Placement] | None = None,
    *,
    spawn_y: float = 5,
    terrain: TerrainPlan | None = None,
    title: str = "Test Game",
) -> DreamPlan:
    return DreamPlan(
        title=title,
        genre="platformer",
        tone="bright",
        style_preset="lowpoly",
        assets=assets if assets is not None else [],
        scene=SceneLayout(placements=placements if placements is not None else [], spawn_y=spawn_y),
        terrain=terrain,
    )


def _codes(issues: list) -> set[str]:
    return {i.code for i in issues}


class TestSlugifyId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("hero", "hero"),
            ("Hero Guy", "hero_guy"),
            ("  spaced  out  ", "spaced_out"),
            ("UPPER-case_2", "upper-case_2"),
            ("áéïõü", "asset"),  # acentos são stripados → fallback
            ("", "asset"),
            (None, "asset"),
            ("!!!", "asset"),
        ],
    )
    def test_cases(self, raw: str | None, expected: str) -> None:
        assert slugify_id(raw) == expected  # type: ignore[arg-type]

    def test_long_id_truncated(self) -> None:
        assert len(slugify_id("a" * 200)) == 48


class TestParseVec3:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("1 2 3", (1.0, 2.0, 3.0)),
            ("1, 2, 3", (1.0, 2.0, 3.0)),
            ("-1.5 0 2", (-1.5, 0.0, 2.0)),
            ("  0   0   0  ", (0.0, 0.0, 0.0)),
        ],
    )
    def test_valid(self, value: str, expected: tuple[float, float, float]) -> None:
        assert parse_vec3(value) == expected

    @pytest.mark.parametrize("value", ["1 2", "1 2 3 4", "a b c", "", "1,,2", None])
    def test_invalid(self, value: str | None) -> None:
        assert parse_vec3(value) is None


class TestStageChainAndArtifacts:
    def test_plain_prop(self) -> None:
        a = AssetEntry(id="crate", idea="crate", kind="prop", generate_3d=True)
        assert asset_stage_chain(a) == ["3d", "paint", "lod0", "collision", "validate"]

    def test_rigged_character(self) -> None:
        a = AssetEntry(id="hero", idea="hero", kind="character", generate_3d=True, generate_rig=True)
        assert asset_stage_chain(a) == ["3d", "paint", "rig", "lod0", "collision", "validate"]

    def test_animated_character(self) -> None:
        a = AssetEntry(
            id="hero", idea="hero", kind="character", generate_3d=True, generate_rig=True, generate_animate=True
        )
        assert asset_stage_chain(a) == ["3d", "paint", "rig", "animate", "lod0", "collision", "validate"]

    def test_audio_only(self) -> None:
        a = AssetEntry(id="sfx", idea="chime", kind="prop", generate_3d=False, generate_audio=True)
        assert asset_stage_chain(a) == ["audio", "handoff"]

    def test_no_output(self) -> None:
        a = AssetEntry(id="ghost", idea="nothing", generate_3d=False, generate_audio=False)
        assert asset_stage_chain(a) == []

    def test_artifacts_3d(self) -> None:
        a = AssetEntry(id="crate", idea="crate", generate_3d=True)
        assert asset_artifacts(a) == ["meshes/crate_lod0.glb", "meshes/crate_collision.glb"]

    def test_artifacts_audio(self) -> None:
        a = AssetEntry(id="sfx", idea="chime", generate_3d=False, generate_audio=True)
        assert asset_artifacts(a) == ["audio/sfx.wav"]


class TestFormatIssue:
    def test_error_with_asset(self) -> None:
        from gameassets.dream.planlint import PlanIssue

        i = PlanIssue(SEVERITY_ERROR, "DREAM_DUP_ID", "id duplicado", asset_id="hero")
        out = format_issue(i)
        assert out.startswith("ERROR DREAM_DUP_ID [hero]:")
        assert "id duplicado" in out

    def test_warning_without_asset(self) -> None:
        from gameassets.dream.planlint import PlanIssue

        i = PlanIssue(SEVERITY_WARNING, "DREAM_NO_3D", "sem 3d")
        assert format_issue(i) == "WARN DREAM_NO_3D: sem 3d"


class TestValidatePlan:
    def test_valid_plan_no_errors(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="crate", idea="crate")],
            placements=[Placement(asset_id="crate", pos="0 0 0")],
        )
        issues = validate_plan(plan)
        assert not [i for i in issues if i.severity == SEVERITY_ERROR]

    def test_no_assets_error(self) -> None:
        assert "DREAM_NO_ASSETS" in _codes(validate_plan(_plan()))

    def test_empty_title_error(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x")], title="  ")
        assert "DREAM_EMPTY_TITLE" in _codes(validate_plan(plan))

    def test_duplicate_ids_error(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x"), AssetEntry(id="a", idea="y")])
        assert "DREAM_DUP_ID" in _codes(validate_plan(plan))

    def test_bad_kind_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", kind="npc")])
        assert "DREAM_BAD_KIND" in _codes(validate_plan(plan))

    def test_animate_without_rig_error(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_animate=True)])
        assert "DREAM_ANIMATE_NO_RIG" in _codes(validate_plan(plan))

    def test_rig_not_character_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_rig=True, kind="prop")])
        assert "DREAM_RIG_NOT_CHARACTER" in _codes(validate_plan(plan))

    def test_dangling_placement_error(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="nope", pos="0 0 0")],
        )
        assert "DREAM_PLACEMENT_DANGLING" in _codes(validate_plan(plan))

    def test_audio_only_placement_warning(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="sfx", idea="x", generate_3d=False, generate_audio=True)],
            placements=[Placement(asset_id="sfx", pos="0 0 0")],
        )
        assert "DREAM_PLACEMENT_AUDIO_ONLY" in _codes(validate_plan(plan))

    def test_bad_pos_and_scale_errors(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="a", pos="one two", scale="nope")],
        )
        codes = _codes(validate_plan(plan))
        assert "DREAM_BAD_POS" in codes
        assert "DREAM_BAD_SCALE" in codes

    def test_max_assets_warning(self) -> None:
        assets = [AssetEntry(id=f"a{i}", idea="x") for i in range(10)]
        assert "DREAM_MAX_ASSETS" in _codes(validate_plan(_plan(assets=assets), max_assets=8))

    def test_no_3d_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="sfx", idea="x", generate_3d=False, generate_audio=True)])
        assert "DREAM_NO_3D" in _codes(validate_plan(plan))

    def test_spawn_y_low_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x")], spawn_y=0.1)
        assert "DREAM_SPAWN_Y_LOW" in _codes(validate_plan(plan))

    def test_terrain_no_prompt_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x")], terrain=TerrainPlan(enabled=True, prompt=""))
        assert "DREAM_TERRAIN_NO_PROMPT" in _codes(validate_plan(plan))

    def test_asset_no_output_warning(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_3d=False, generate_audio=False)])
        assert "DREAM_ASSET_NO_OUTPUT" in _codes(validate_plan(plan))


class TestAutorepairPlan:
    def test_dedupes_ids(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x"), AssetEntry(id="a", idea="y")])
        repaired, fixes = autorepair_plan(plan)
        ids = [a.id for a in repaired.assets]
        assert len(set(ids)) == 2
        assert any("duplicado" in f for f in fixes)

    def test_slugifies_ids_and_remaps_placements(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="Hero Guy", idea="x")],
            placements=[Placement(asset_id="Hero Guy", pos="0 0 0")],
        )
        repaired, _fixes = autorepair_plan(plan)
        assert repaired.assets[0].id == "hero_guy"
        assert repaired.scene.placements[0].asset_id == "hero_guy"

    def test_drops_empty_idea(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="ok"), AssetEntry(id="b", idea="  ")])
        repaired, fixes = autorepair_plan(plan)
        assert [a.id for a in repaired.assets] == ["a"]
        assert any("sem idea" in f for f in fixes)

    def test_coerces_kind(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", kind="npc")])
        repaired, fixes = autorepair_plan(plan)
        assert repaired.assets[0].kind == "character"
        assert any("kind" in f for f in fixes)

    def test_animate_implies_rig(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_animate=True)])
        repaired, fixes = autorepair_plan(plan)
        assert repaired.assets[0].generate_rig is True
        assert any("rig=True" in f for f in fixes)

    def test_rig_forces_character(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_rig=True, kind="prop")])
        repaired, _ = autorepair_plan(plan)
        assert repaired.assets[0].kind == "character"

    def test_no_output_asset_gets_3d(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", generate_3d=False, generate_audio=False)])
        repaired, fixes = autorepair_plan(plan)
        assert repaired.assets[0].generate_3d is True
        assert any("generate_3d=True" in f for f in fixes)

    def test_truncates_to_max(self) -> None:
        assets = [AssetEntry(id=f"a{i}", idea="x") for i in range(10)]
        repaired, fixes = autorepair_plan(_plan(assets=assets), max_assets=4)
        assert len(repaired.assets) == 4
        assert any("truncate" in f for f in fixes)

    def test_drops_dangling_and_audio_only_placements(self) -> None:
        plan = _plan(
            assets=[
                AssetEntry(id="a", idea="x"),
                AssetEntry(id="sfx", idea="x", generate_3d=False, generate_audio=True),
            ],
            placements=[
                Placement(asset_id="ghost", pos="0 0 0"),
                Placement(asset_id="sfx", pos="0 0 0"),
                Placement(asset_id="a", pos="1 1 1"),
            ],
        )
        repaired, fixes = autorepair_plan(plan)
        assert [p.asset_id for p in repaired.scene.placements] == ["a"]
        assert len([f for f in fixes if "dangling" in f or "audio-only" in f]) == 2

    def test_bad_pos_and_scale_defaulted(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="a", pos="boom", scale="nah")],
        )
        repaired, fixes = autorepair_plan(plan)
        assert repaired.scene.placements[0].pos == "0 0 0"
        assert repaired.scene.placements[0].scale == "1 1 1"
        assert len([f for f in fixes if "pos" in f or "scale" in f]) == 2

    def test_comma_vec_normalized(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="a", pos="1, 2, 3")],
        )
        repaired, _ = autorepair_plan(plan)
        assert repaired.scene.placements[0].pos == "1 2 3"

    def test_spawn_y_clamped(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x")], spawn_y=-2)
        repaired, fixes = autorepair_plan(plan)
        assert repaired.scene.spawn_y == 1.0
        assert any("spawn_y" in f for f in fixes)

    def test_terrain_prompt_derived(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            terrain=TerrainPlan(enabled=True, prompt=""),
        )
        repaired, fixes = autorepair_plan(plan)
        assert repaired.terrain is not None
        assert repaired.terrain.prompt
        assert any("terrain" in f for f in fixes)

    def test_empty_title_filled(self) -> None:
        repaired, _ = autorepair_plan(_plan(assets=[AssetEntry(id="a", idea="x")], title=""))
        assert repaired.title == "Untitled Dream"

    def test_original_not_mutated(self) -> None:
        plan = _plan(assets=[AssetEntry(id="a", idea="x", kind="npc")])
        autorepair_plan(plan)
        assert plan.assets[0].kind == "npc"


class TestLintAndRepair:
    def test_residual_after_repair(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="ghost", pos="0 0 0")],
        )
        repaired, fixes, residual = lint_and_repair(plan)
        assert fixes
        assert repaired.scene.placements == []
        # placements vazios permanecem como warning residual
        assert "DREAM_NO_PLACEMENTS" in _codes(residual)
        assert not [i for i in residual if i.severity == SEVERITY_ERROR]

    def test_clean_plan_no_fixes(self) -> None:
        plan = _plan(
            assets=[AssetEntry(id="a", idea="x")],
            placements=[Placement(asset_id="a", pos="0 0 0")],
        )
        repaired, fixes, residual = lint_and_repair(plan)
        assert fixes == []
        assert not [i for i in residual if i.severity == SEVERITY_ERROR]
        assert repaired.assets[0].id == "a"
