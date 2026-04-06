"""Testes unitários para gameassets.dream.emitter."""

from __future__ import annotations

from pathlib import Path

import yaml

from gameassets.dream.emitter import (
    emit_all,
    emit_game_yaml,
    emit_index_html,
    emit_main_ts,
    emit_manifest_csv,
    emit_world_xml,
)
from gameassets.dream.planner import AssetEntry, DreamPlan, Placement, SceneLayout


def _sample_plan() -> DreamPlan:
    return DreamPlan(
        title="Crystal Clouds",
        genre="3D platformer",
        tone="colorido e mágico",
        style_preset="lowpoly",
        sky_prompt="céu com nuvens douradas, panorama 360",
        assets=[
            AssetEntry(id="ground", idea="large ground platform", kind="environment", generate_3d=True),
            AssetEntry(id="crystal", idea="blue floating crystal", kind="prop", generate_3d=True),
            AssetEntry(id="hero", idea="chibi character", kind="character", generate_3d=True, generate_rig=True),
            AssetEntry(
                id="collect_sfx",
                idea="crystal collect sound",
                kind="prop",
                generate_3d=False,
                generate_audio=True,
            ),
        ],
        scene=SceneLayout(
            sky_color="#87CEEB",
            ground_size=50,
            spawn_y=5,
            placements=[
                Placement(asset_id="ground", pos="0 0 0", scale="10 1 10"),
                Placement(asset_id="crystal", pos="3 4 0", scale="0.5 0.5 0.5"),
            ],
        ),
    )


class TestEmitGameYaml:
    def test_valid_yaml(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        assert doc["title"] == "Crystal Clouds"
        assert doc["style_preset"] == "lowpoly"

    def test_has_text3d_when_3d_assets(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        assert "text3d" in doc

    def test_has_rigging3d_when_rig(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        assert "rigging3d" in doc

    def test_has_animator3d_when_rig(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        assert "animator3d" in doc
        assert doc["animator3d"]["preset"] == "humanoid"

    def test_has_text2sound_when_audio(self) -> None:
        out = emit_game_yaml(_sample_plan(), with_audio=True)
        doc = yaml.safe_load(out)
        assert "text2sound" in doc

    def test_no_text2sound_when_disabled(self) -> None:
        out = emit_game_yaml(_sample_plan(), with_audio=False)
        doc = yaml.safe_load(out)
        assert "text2sound" not in doc


class TestEmitManifestCsv:
    def test_has_headers(self) -> None:
        out = emit_manifest_csv(_sample_plan())
        lines = out.strip().splitlines()
        header = lines[0]
        assert "id" in header
        assert "idea" in header
        assert "kind" in header
        assert "generate_animate" in header

    def test_hero_row_has_generate_animate_when_rig(self) -> None:
        out = emit_manifest_csv(_sample_plan())
        lines = out.strip().splitlines()
        hero_line = next(L for L in lines if L.startswith("hero,"))
        assert hero_line.split(",")[6] == "true"

    def test_row_count(self) -> None:
        out = emit_manifest_csv(_sample_plan())
        lines = out.strip().splitlines()
        assert len(lines) == 5  # header + 4 assets


class TestEmitWorldXml:
    def test_contains_world_tags(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert xml.startswith("<world")
        assert xml.strip().endswith("</world>")

    def test_contains_gltf_load(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert "gltf-load" in xml
        assert "/assets/models/crystal.glb" in xml

    def test_skips_gltf_for_rigged_character(self) -> None:
        plan = _sample_plan()
        plan.scene.placements.append(Placement(asset_id="hero", pos="0 1 0", scale="1 1 1"))
        xml = emit_world_xml(plan)
        assert "/assets/models/hero.glb" not in xml
        assert "/assets/models/crystal.glb" in xml

    def test_no_audio_placement(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert "collect_sfx" not in xml

    def test_ground_platform(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert "static-part" in xml

    def test_has_player(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert "<player" in xml
        assert 'pos="0 5 0"' in xml

    def test_has_orbit_camera(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert "<orbit-camera" in xml

    def test_ground_color_default_green(self) -> None:
        xml = emit_world_xml(_sample_plan())
        assert 'color="#4a7a3a"' in xml

    def test_ground_color_desert(self) -> None:
        plan = _sample_plan()
        plan.genre = "desert adventure"
        xml = emit_world_xml(plan)
        assert 'color="#c2a860"' in xml


class TestEmitMainTs:
    def test_imports_vibegame(self) -> None:
        ts = emit_main_ts(_sample_plan(), with_sky=True)
        assert "from 'vibegame'" in ts

    def test_has_sky_env(self) -> None:
        ts = emit_main_ts(_sample_plan(), with_sky=True)
        assert "applyEquirectSkyEnvironment" in ts

    def test_animator_clip_names_when_rig(self) -> None:
        ts = emit_main_ts(_sample_plan(), with_sky=True)
        assert "Animator3D_BreatheIdle" in ts
        assert "Animator3D_Walk" in ts
        assert "Animator3D_Run" in ts
        assert "GltfAnimator" in ts

    def test_no_gltf_animator_without_rig(self) -> None:
        plan = _sample_plan()
        plan.assets = [a for a in plan.assets if a.id != "hero"] + [
            AssetEntry(id="hero", idea="chibi", kind="character", generate_3d=True, generate_rig=False),
        ]
        ts = emit_main_ts(plan, with_sky=True)
        assert "GltfAnimator" not in ts

    def test_no_sky_env_when_disabled(self) -> None:
        ts = emit_main_ts(_sample_plan(), with_sky=False)
        assert "applyEquirectSkyEnvironment" not in ts


class TestEmitIndexHtml:
    def test_has_canvas(self) -> None:
        world = emit_world_xml(_sample_plan())
        html = emit_index_html(_sample_plan(), world)
        assert 'id="game-canvas"' in html
        assert "<world" in html

    def test_title(self) -> None:
        world = emit_world_xml(_sample_plan())
        html = emit_index_html(_sample_plan(), world)
        assert "Crystal Clouds" in html


class TestEmitAll:
    def test_creates_files(self, tmp_path: Path) -> None:
        paths = emit_all(_sample_plan(), tmp_path)
        assert paths["game_yaml"].is_file()
        assert paths["manifest_csv"].is_file()
        assert paths["world_xml"].is_file()
        assert paths["main_ts"].is_file()
        assert paths["index_html"].is_file()

    def test_game_yaml_parseable(self, tmp_path: Path) -> None:
        paths = emit_all(_sample_plan(), tmp_path)
        doc = yaml.safe_load(paths["game_yaml"].read_text(encoding="utf-8"))
        assert doc["title"] == "Crystal Clouds"
