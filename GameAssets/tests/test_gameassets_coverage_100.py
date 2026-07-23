"""Suite de cobertura elaborada (≥100 casos) — gameassets, sem GPU/subprocess."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest
import yaml

from gameassets.dream.emitter import (
    _ground_color_for_genre,
    _xml_escape,
    emit_game_yaml,
    emit_index_html,
    emit_main_ts,
    emit_manifest_yaml,
    emit_world_xml,
)
from gameassets.dream.planner import AssetEntry, DreamPlan, SceneLayout, TerrainPlan
from gameassets.handoff_export import _safe_public_id, resolve_handoff_mesh
from gameassets.helpers import (
    _append_gpu_kill_flag,
    _append_quality,
    _resolve_manifest_path,
    _row_wants_animate,
    _row_wants_audio,
    _row_wants_rig,
    _safe_row_dirname,
    _seed_for_row,
    effective_face_ratio,
)
from gameassets.manifest import (
    ManifestRow,
    RowText3D,
    _row_text3d_from_dict,
    apply_row_text3d_overrides,
    effective_image_source,
    load_manifest,
    row_mc_level,
)
from gameassets.paths import (
    _animator3d_output_path,
    _base_stem,
    _clean_path,
    _collision_path,
    _intermediate_dir,
    _lod_path,
    _painted_path,
    _rigged_path,
    _rigging3d_output_path,
    _shape_path,
)
from gameassets.pipeline import (
    StageResult,
    _glb_has_animations,
    _glb_has_duplicate_clips,
    _glb_has_materials,
    _glb_has_skin,
    _lod_output_paths,
    aggregate_master_results,
    wants_split_at_height,
)
from gameassets.profile import GameProfile, Text3DProfile, load_profile


def _row(**kw: Any) -> ManifestRow:
    d: dict[str, Any] = {
        "id": "hero",
        "idea": "brave knight",
        "kind": "character",
        "category": "humanoid",
        "generate_3d": True,
    }
    d.update(kw)
    return ManifestRow(**d)


def _profile(**kw: Any) -> GameProfile:
    d: dict[str, Any] = {
        "title": "T",
        "genre": "rpg",
        "tone": "epic",
        "style_preset": "lowpoly",
    }
    d.update(kw)
    return GameProfile(**d)


def _plan(**kw: Any) -> DreamPlan:
    assets = kw.pop("assets", [AssetEntry(id="prop_a", idea="crate", generate_3d=True)])
    return DreamPlan(
        title=kw.get("title", "Dream"),
        genre=kw.get("genre", "rpg"),
        tone=kw.get("tone", "fun"),
        style_preset=kw.get("style_preset", "lowpoly"),
        assets=assets,
        scene=kw.get("scene", SceneLayout()),
        sky_prompt=kw.get("sky_prompt", ""),
        terrain=kw.get("terrain"),
    )


PIPELINE_CASES = [
    (["3d"], True, False, False, False, False),
    (["3d", "rig", "animate"], True, False, True, True, False),
    (["audio"], False, True, False, False, False),
    (["3d", "paint", "lod", "collision"], True, False, False, False, True),
    (["3d", "rig"], True, False, True, False, False),
    (["animate"], False, False, False, True, False),
]


@pytest.mark.parametrize("pipeline,g3d,gaud,rig,anim,lod", PIPELINE_CASES)
def test_manifest_pipeline_flags(
    pipeline: list[str],
    g3d: bool,
    gaud: bool,
    rig: bool,
    anim: bool,
    lod: bool,
    tmp_path: Path,
) -> None:
    doc = {"assets": [{"id": "x", "idea": "y", "pipeline": pipeline}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    row = load_manifest(p)[0]
    assert row.generate_3d is g3d
    assert row.generate_audio is gaud
    assert row.generate_rig is rig
    assert row.generate_animate is anim
    assert row.generate_lod is lod


@pytest.mark.parametrize("pipeline_item", ["3d", "rig", "animate", "audio", "paint", "lod", "collision"])
def test_manifest_single_pipeline_item(pipeline_item: str, tmp_path: Path) -> None:
    doc = {"assets": [{"id": "a", "idea": "b", "pipeline": [pipeline_item]}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    assert load_manifest(p)[0].id == "a"


@pytest.mark.parametrize(
    "raw,expect_steps",
    [
        ({"steps": 30}, 30),
        ({"octree_resolution": 384}, None),
        ({"mc_level": 0.0}, None),
        ({"voxel_merge": 0.25}, None),
    ],
)
def test_row_text3d_from_dict(raw: dict, expect_steps: int | None) -> None:
    rt = _row_text3d_from_dict(raw, "id1")
    assert rt is not None
    if expect_steps is not None:
        assert rt.steps == expect_steps


def test_row_text3d_none() -> None:
    assert _row_text3d_from_dict(None, "x") is None


def test_row_text3d_bad_type() -> None:
    with pytest.raises(ValueError):
        _row_text3d_from_dict("nope", "x")


def test_row_text3d_unknown_key() -> None:
    with pytest.raises(ValueError, match="desconhecidas"):
        _row_text3d_from_dict({"bogus": 1}, "x")


@pytest.mark.parametrize("src,prof", [("text2d", "texture2d"), (None, "text2d")])
def test_effective_image_source(src: str | None, prof: str) -> None:
    p = _profile(image_source=prof)
    r = _row(image_source=src)
    assert effective_image_source(p, r) == (src or prof)


@pytest.mark.parametrize("row_mc,prof_mc,exp", [(0.5, 0.0, 0.5), (None, 0.25, 0.25)])
def test_row_mc_level(row_mc: float | None, prof_mc: float, exp: float) -> None:
    rt = RowText3D(mc_level=row_mc) if row_mc is not None else None
    row = _row(text3d=rt)
    assert row_mc_level(row, prof_mc) == exp


def test_apply_row_text3d_overrides() -> None:
    row = _row(text3d=RowText3D(steps=99, octree_resolution=512))
    out = apply_row_text3d_overrides({"prompt": "x"}, row)
    assert out["steps"] == 99 and out["octree_resolution"] == 512


@pytest.mark.parametrize("rig,audio,anim", [(True, False, True), (False, True, False)])
def test_row_wants_flags(rig: bool, audio: bool, anim: bool) -> None:
    row = _row(generate_rig=rig, generate_audio=audio, generate_animate=anim)
    assert _row_wants_rig(row, True) is rig
    assert _row_wants_audio(row, True) is audio
    assert _row_wants_animate(row, rig, True) is anim


@pytest.mark.parametrize("row_id,expected", [("hero/knight", "hero__knight"), ("simple", "simple"), ("a\\b", "a_b")])
def test_safe_row_dirname(row_id: str, expected: str) -> None:
    assert _safe_row_dirname(row_id) == expected


def test_seed_for_row_deterministic() -> None:
    p = _profile(seed_base=100)
    assert _seed_for_row(p, "same") == _seed_for_row(p, "same")
    assert _seed_for_row(p, "same") != _seed_for_row(p, "other")


def test_resolve_manifest_path_yaml(tmp_path: Path) -> None:
    y = tmp_path / "manifest.yaml"
    y.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: [3d]\n", encoding="utf-8")
    assert _resolve_manifest_path(tmp_path / "manifest").suffix == ".yaml"


def test_append_quality_argv() -> None:
    argv: list[str] = []
    _append_quality(argv, _profile(generation="high"))
    assert argv == ["--quality", "high"]


def test_append_gpu_kill() -> None:
    argv: list[str] = []
    _append_gpu_kill_flag(argv, False)
    assert "--no-gpu-kill-others" in argv


@pytest.mark.parametrize("gen", ["fast", "medium", None])
def test_effective_face_ratio(gen: str | None) -> None:
    p = _profile(generation=gen)
    row = _row(generation=gen)
    assert effective_face_ratio(p, row) > 0


@pytest.mark.parametrize(
    "stem,base",
    [
        ("hero_lod0_painted", "hero"),
        ("tree_lod1", "tree"),
        ("crate_painted", "crate"),
        ("foo_rigged_animated", "foo"),
        ("bar_clean", "bar"),
    ],
)
def test_base_stem(stem: str, base: str) -> None:
    assert _base_stem(stem) == base


@pytest.mark.parametrize("level", [0, 1, 2])
def test_lod_path(level: int, tmp_path: Path) -> None:
    mf = tmp_path / "hero.glb"
    p = _lod_path(mf, level)
    assert p.name == f"hero_lod{level}.glb"


def test_intermediate_paths(tmp_path: Path) -> None:
    mf = tmp_path / "id.glb"
    assert _shape_path(mf).stem.endswith("_shape")
    assert _clean_path(mf).stem.endswith("_clean")
    assert _painted_path(mf).stem.endswith("_painted")
    assert _rigged_path(mf).stem.endswith("_rigged")
    assert _collision_path(mf).stem.endswith("_collision")
    assert _intermediate_dir(mf).name == "_intermediate"


def test_rigging_and_animator_paths(tmp_path: Path) -> None:
    mf = tmp_path / "hero.glb"
    assert _rigging3d_output_path(mf, "_rigged").name == "hero_rigged.glb"
    assert _animator3d_output_path(mf).name == "hero_rigged_animated.glb"


def test_lod_output_paths_list(tmp_path: Path) -> None:
    assert len(_lod_output_paths(tmp_path / "x.glb", "x", num_levels=3)) == 3


def _write_glb(path: Path, gltf: dict) -> None:
    jb = json.dumps(gltf, separators=(",", ":")).encode("utf-8")
    while len(jb) % 4:
        jb += b" "
    chunk = struct.pack("<I", len(jb)) + b"JSON" + jb
    total = 12 + len(chunk)
    path.write_bytes(b"glTF" + struct.pack("<II", 2, total) + chunk)


@pytest.mark.parametrize("rid,safe", [("a/b", "a__b"), ("x", "x"), ("p\\q", "p_q")])
def test_safe_public_id(rid: str, safe: str) -> None:
    assert _safe_public_id(rid) == safe


def test_resolve_handoff_lod0_only(tmp_path: Path) -> None:
    mf = tmp_path / "hero.glb"
    lod0 = _lod_path(mf, 0)
    _write_glb(lod0, {"asset": {"version": "2.0"}, "meshes": []})
    row = _row(generate_animate=False)
    p, kind = resolve_handoff_mesh(mf, row, prefer_animated=True, prefer_rigged=True)
    assert p == lod0 and kind == "lod0"


def test_resolve_handoff_animated_pref(tmp_path: Path) -> None:
    mf = tmp_path / "hero.glb"
    lod0 = _lod_path(mf, 0)
    _write_glb(lod0, {"asset": {"version": "2.0"}, "animations": [{"name": "run"}]})
    row = _row(generate_animate=True)
    _p, kind = resolve_handoff_mesh(mf, row, prefer_animated=True, prefer_rigged=False)
    assert kind == "animated"


def test_resolve_handoff_missing(tmp_path: Path) -> None:
    mf = tmp_path / "missing.glb"
    p, kind = resolve_handoff_mesh(mf, _row(), prefer_animated=False, prefer_rigged=False)
    assert p is None and kind == "missing"


@pytest.mark.parametrize(
    "gltf,skin,mat,anim,dup",
    [
        ({"asset": {"version": "2.0"}, "skins": [{}], "nodes": [{"skin": 0}]}, True, False, False, False),
        (
            {"asset": {"version": "2.0"}, "materials": [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]},
            False,
            True,
            False,
            False,
        ),
        ({"asset": {"version": "2.0"}, "animations": [{}]}, False, False, True, False),
        ({"asset": {"version": "2.0"}, "animations": [{"name": "a"}, {"name": "a.001"}]}, False, False, True, True),
        ({"asset": {"version": "2.0"}, "nodes": [{"mesh": 0}]}, False, False, False, False),
    ],
)
def test_glb_probes(gltf: dict, skin: bool, mat: bool, anim: bool, dup: bool, tmp_path: Path) -> None:
    p = tmp_path / "t.glb"
    _write_glb(p, gltf)
    assert _glb_has_skin(p) is skin
    assert _glb_has_materials(p) is mat
    assert _glb_has_animations(p) is anim
    assert _glb_has_duplicate_clips(p) is dup


@pytest.mark.parametrize("category,split", [("tree", True), ("rock", False), ("prop", False)])
def test_wants_split_at_height(category: str, split: bool) -> None:
    assert wants_split_at_height(_profile(), _row(category=category)) is split


def test_wants_split_override() -> None:
    p = _profile(text3d=Text3DProfile(split_at_height=False))
    assert wants_split_at_height(p, _row(category="tree")) is False


def test_aggregate_master_results() -> None:
    rec: dict[str, Any] = {}
    stages = [StageResult("a", True, 1.0), StageResult("b", False, 2.5, error="x")]
    aggregate_master_results(stages, rec)
    assert rec["total_elapsed_s"] == 3.5
    assert len(rec["stages"]) == 2


@pytest.mark.parametrize(
    "genre,color",
    [
        ("desert", "#c2a860"),
        ("snow", "#e8e8f0"),
        ("space", "#2a2a3a"),
        ("dungeon", "#3a3a3a"),
        ("rpg", "#4a7a3a"),
    ],
)
def test_ground_color_for_genre(genre: str, color: str) -> None:
    assert _ground_color_for_genre(genre) == color


@pytest.mark.parametrize(
    "raw,escaped",
    [
        ("a&b", "a&amp;b"),
        ('say "hi"', "say &quot;hi&quot;"),
        ("<tag>", "&lt;tag&gt;"),
    ],
)
def test_xml_escape(raw: str, escaped: str) -> None:
    assert _xml_escape(raw) == escaped


def test_emit_game_yaml_parses() -> None:
    doc = yaml.safe_load(emit_game_yaml(_plan()))
    assert doc["title"] == "Dream" and "text3d" in doc


def test_emit_manifest_yaml_pipeline() -> None:
    plan = _plan(assets=[AssetEntry(id="h", idea="hero", generate_3d=True, generate_rig=True, generate_animate=True)])
    doc = yaml.safe_load(emit_manifest_yaml(plan))
    assert "rig" in doc["assets"][0]["pipeline"]


def test_emit_world_xml_scene() -> None:
    xml = emit_world_xml(_plan())
    assert "<Scene" in xml and "</Scene>" in xml


def test_emit_world_xml_terrain() -> None:
    plan = _plan(terrain=TerrainPlan(enabled=True, world_size=512, max_height=40))
    assert "Terrain" in emit_world_xml(plan)


def test_emit_main_ts_imports() -> None:
    assert "vibegame" in emit_main_ts(_plan())


def test_emit_index_html_title() -> None:
    html = emit_index_html(_plan(title="My Game"), "<Scene></Scene>")
    assert "My Game" in html


@pytest.mark.parametrize("with_audio", [True, False])
def test_emit_game_yaml_audio(with_audio: bool) -> None:
    plan = _plan(assets=[AssetEntry(id="s", idea="sfx", generate_audio=True)])
    doc = yaml.safe_load(emit_game_yaml(plan, with_audio=with_audio))
    assert ("text2sound" in doc) is with_audio


@pytest.mark.parametrize(
    "field,val",
    [
        ("master_pipeline", True),
        ("generation", "high"),
        ("image_source", "text2d"),
    ],
)
def test_load_profile_fields(field: str, val: Any, tmp_path: Path) -> None:
    doc = {"title": "G", "genre": "rpg", "tone": "x", "style_preset": "lowpoly", field: val}
    p = tmp_path / "game.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    assert getattr(load_profile(p), field) == val


@pytest.mark.parametrize("preset", ["lowpoly", "pixel_art"])
def test_profile_style_preset(preset: str, tmp_path: Path) -> None:
    doc = {"title": "G", "genre": "rpg", "tone": "x", "style_preset": preset}
    p = tmp_path / "game.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    assert load_profile(p).style_preset == preset


@pytest.mark.parametrize("seed", [1, 42, 99999])
def test_manifest_row_seed(seed: int, tmp_path: Path) -> None:
    doc = {"assets": [{"id": "a", "idea": "b", "pipeline": ["3d"], "seed": seed}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    assert load_manifest(p)[0].seed == seed


@pytest.mark.parametrize(
    "animate_cfg,expect_clips",
    [({"clips": "run,jump"}, "run,jump"), ({"preset": "humanoid"}, None)],
)
def test_manifest_animate_block(animate_cfg: dict, expect_clips: str | None, tmp_path: Path) -> None:
    doc = {"assets": [{"id": "a", "idea": "b", "pipeline": ["3d"], "animate": animate_cfg}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    row = load_manifest(p)[0]
    assert row.animate_clips == expect_clips


@pytest.mark.parametrize(
    "audio_cfg,expect_dur",
    [({"duration": 3.5, "profile": "effects"}, 3.5), ({"profile": "music"}, None)],
)
def test_manifest_audio_block(audio_cfg: dict, expect_dur: float | None, tmp_path: Path) -> None:
    doc = {"assets": [{"id": "a", "idea": "b", "pipeline": ["audio"], "audio": audio_cfg}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    row = load_manifest(p)[0]
    assert row.audio_duration == expect_dur


@pytest.mark.parametrize("category", ["humanoid", "weapon", "tree", "prop", "rock"])
def test_manifest_category_lowercase(category: str, tmp_path: Path) -> None:
    doc = {"assets": [{"id": "a", "idea": "b", "pipeline": ["3d"], "category": category.upper()}]}
    p = tmp_path / "m.yaml"
    p.write_text(yaml.dump(doc), encoding="utf-8")
    assert load_manifest(p)[0].category == category.lower()


@pytest.mark.parametrize("split_flag", [True, False])
def test_wants_split_text3d_flag(split_flag: bool) -> None:
    p = _profile(text3d=Text3DProfile(split_at_height=split_flag))
    assert wants_split_at_height(p, _row(category="prop")) is split_flag


def test_emit_manifest_no_3d(tmp_path: Path) -> None:
    plan = _plan(assets=[AssetEntry(id="s", idea="sound", generate_3d=False, generate_audio=True)])
    doc = yaml.safe_load(emit_manifest_yaml(plan))
    assert doc["assets"][0]["pipeline"] == ["audio"]


def test_emit_game_yaml_master_pipeline() -> None:
    doc = yaml.safe_load(emit_game_yaml(_plan()))
    assert doc.get("master_pipeline") is True


def test_resolve_handoff_base_mesh(tmp_path: Path) -> None:
    mf = tmp_path / "hero.glb"
    _write_glb(mf, {"asset": {"version": "2.0"}})
    p, kind = resolve_handoff_mesh(mf, _row(), prefer_animated=False, prefer_rigged=False)
    assert p == mf and kind == "base"
