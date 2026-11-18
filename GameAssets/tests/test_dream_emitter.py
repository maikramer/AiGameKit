"""Testes unitários para gameassets.dream.emitter."""

from __future__ import annotations

from pathlib import Path

import yaml

from gameassets.dream.emitter import (
    emit_all,
    emit_game_yaml,
    emit_world_bsn,
)
from gameassets.dream.planner import AssetEntry, DreamPlan, Placement, SceneLayout, TerrainPlan


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
            AssetEntry(
                id="hero",
                idea="chibi character",
                kind="character",
                generate_3d=True,
                generate_rig=True,
                generate_animate=True,
            ),
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

    def test_has_icons_block_when_icon_prompts(self) -> None:
        plan = _sample_plan()
        plan.icon_prompts = ["red health potion", "steel sword"]
        out = emit_game_yaml(plan)
        doc = yaml.safe_load(out)
        assert doc["icons"]["prompts"] == ["red health potion", "steel sword"]
        assert doc["icons"]["transparent"] is True
        # Sem width/height: o modo icon do text2d tem defaults próprios (512x512).
        assert "width" not in doc["icons"]
        assert "height" not in doc["icons"]

    def test_no_icons_block_without_icon_prompts(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        assert "icons" not in doc

    def test_no_text2sound_when_disabled(self) -> None:
        out = emit_game_yaml(_sample_plan(), with_audio=False)
        doc = yaml.safe_load(out)
        assert "text2sound" not in doc

    def test_no_hardcoded_low_vram(self) -> None:
        out = emit_game_yaml(_sample_plan())
        doc = yaml.safe_load(out)
        for block in ("text2d", "text3d", "paint3d"):
            if block in doc:
                assert "low_vram" not in doc[block], f"{block} should not have low_vram hardcoded"


class TestEmitWorldBsn:
    def test_world_root(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert bsn.startswith('#"world"')
        assert 'tag: "world"' in bsn
        assert 'key: "static", value: "true"' in bsn
        assert 'key: "clear-color", value: "#87CEEB"' in bsn

    def test_contains_gltf_scene(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert 'tag: "GltfScene"' in bsn
        assert "/assets/models/crystal_lod0.glb" in bsn

    def test_emits_player_gltf_for_rigged_character(self) -> None:
        plan = _sample_plan()
        plan.scene.placements.append(Placement(asset_id="hero", pos="0 1 0", scale="1 1 1"))
        bsn = emit_world_bsn(plan)
        assert 'tag: "PlayerGLTF"' in bsn
        assert "/assets/models/hero_lod0.glb" in bsn
        assert "/assets/models/crystal_lod0.glb" in bsn

    def test_player_gltf_default_pos_high_y(self) -> None:
        plan = _sample_plan()
        plan.scene.placements.append(Placement(asset_id="hero", pos="", scale="1 1 1"))
        bsn = emit_world_bsn(plan)
        assert 'key: "pos", value: "0 60 0"' in bsn

    def test_no_audio_placement(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert "collect_sfx" not in bsn

    def test_ground_platform(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert 'tag: "Composition"' in bsn
        assert 'tag: "Box"' in bsn
        assert 'key: "half-size", value: "25 0.5 25"' in bsn

    def test_terrain_node(self) -> None:
        plan = _sample_plan()
        plan.terrain = TerrainPlan(enabled=True, world_size=512, max_height=40)
        bsn = emit_world_bsn(plan)
        assert 'tag: "Terrain"' in bsn
        assert 'key: "heightmap", value: "world.data/terrain.ahgt"' in bsn
        assert "bevy_ext_terrain::voxel::VoxelTerrain" in bsn
        assert 'tag: "Composition"' not in bsn

    def test_has_orbit_camera(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert 'tag: "OrbitCamera"' in bsn

    def test_ground_color_default_green(self) -> None:
        bsn = emit_world_bsn(_sample_plan())
        assert 'value: "#4a7a3a"' in bsn

    def test_ground_color_desert(self) -> None:
        plan = _sample_plan()
        plan.genre = "desert adventure"
        bsn = emit_world_bsn(plan)
        assert 'value: "#c2a860"' in bsn


class TestEmitAll:
    def test_creates_files(self, tmp_path: Path) -> None:
        paths = emit_all(_sample_plan(), tmp_path)
        assert paths["game_yaml"].is_file()
        assert paths["manifest_yaml"].is_file()
        assert paths["world_bsn"].is_file()

    def test_game_yaml_parseable(self, tmp_path: Path) -> None:
        paths = emit_all(_sample_plan(), tmp_path)
        doc = yaml.safe_load(paths["game_yaml"].read_text(encoding="utf-8"))
        assert doc["title"] == "Crystal Clouds"
