"""Testes unitários das funções auxiliares do CLI (seeds, paths, argv text3d)."""

from __future__ import annotations

import zlib
from pathlib import Path

from gameassets.cli import (
    _extract_json_from_output,
    _paint3d_texture_argv,
    _paths_for_row,
    _rigging3d_output_path,
    _rigging3d_pipeline_argv,
    _seed_for_row,
    _text3d_argv,
    _texture_subprocess_argv,
)
from gameassets.manifest import ManifestRow
from gameassets.profile import GameProfile, Paint3DProfile, Rigging3DProfile, Text3DProfile


def test_extract_json_from_mixed_stdout() -> None:
    text = 'Blender 4.x\n{"armatures": [], "fps": 24.0}\nINFO done\n'
    d = _extract_json_from_output(text)
    assert d.get("fps") == 24.0
    assert d.get("armatures") == []


def test_extract_json_nested_object() -> None:
    text = 'x\n{"a": 1, "b": {"c": true}}\n'
    d = _extract_json_from_output(text)
    assert d["b"]["c"] is True


def test_seed_for_row_none_when_no_base() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        seed_base=None,
    )
    assert _seed_for_row(p, "abc") is None


def test_seed_for_row_deterministic() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        seed_base=1000,
    )
    h = zlib.adler32(b"row1") & 0x7FFFFFFF
    assert _seed_for_row(p, "row1") == 1000 + h
    assert _seed_for_row(p, "row2") != _seed_for_row(p, "row1")


def test_paths_for_row() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        output_dir="out",
        images_subdir="img",
        meshes_subdir="mesh",
        image_ext="png",
    )
    row = ManifestRow(id="x1", idea="i", kind=None, generate_3d=False)
    img, mesh = _paths_for_row(p, row)
    assert img == Path("out") / "img" / "x1.png"
    assert mesh == Path("out") / "mesh" / "x1.glb"


def test_paths_for_row_flat() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        output_dir="out",
        path_layout="flat",
        images_subdir="ignored",
        meshes_subdir="ignored",
        image_ext="png",
    )
    row = ManifestRow(id="Collectibles/core_01", idea="i", kind=None, generate_3d=True)
    img, mesh = _paths_for_row(p, row)
    assert img == Path("out") / "Collectibles" / "core_01.png"
    assert mesh == Path("out") / "Collectibles" / "core_01.glb"


def test_paths_for_row_flat_no_subdir() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        output_dir="out",
        path_layout="flat",
        image_ext="png",
    )
    row = ManifestRow(id="solo", idea="i", kind=None, generate_3d=False)
    img, mesh = _paths_for_row(p, row)
    assert img == Path("out") / "solo.png"
    assert mesh == Path("out") / "solo.glb"


def test_text3d_argv_minimal() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=None,
    )
    img = Path("/a.png")
    mesh = Path("/m.glb")
    argv = _text3d_argv("/bin/text3d", p, img, mesh)
    assert argv[:6] == [
        "/bin/text3d",
        "generate",
        "--from-image",
        str(img),
        "-o",
        str(mesh),
    ]


def test_text3d_argv_with_profile_options() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="hq"),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--preset" in argv and "hq" in argv
    assert "--low-vram" not in argv
    assert "--texture" not in argv
    assert "--export-origin" in argv
    assert argv[argv.index("--export-origin") + 1] == "feet"


def test_text3d_argv_export_origin_center() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="fast", export_origin="center"),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert argv[argv.index("--export-origin") + 1] == "center"


def test_text3d_argv_shape_only_no_texture_flag() -> None:
    """_text3d_argv is always shape-only; texture is a separate step."""
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="fast"),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--texture" not in argv


def test_text3d_argv_explicit_hunyuan_skips_preset() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(
            preset="fast",
            steps=28,
        ),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--preset" not in argv
    assert "--steps" in argv and "28" in argv


def test_paint3d_texture_argv_gpu_flags() -> None:
    p3 = Paint3DProfile(preserve_origin=True, low_vram_mode=True)
    argv = _paint3d_texture_argv(
        "/bin/paint3d",
        p3,
        Path("/shape.glb"),
        Path("/ref.png"),
        Path("/out.glb"),
    )
    assert argv[0] == "/bin/paint3d"
    assert argv[1] == "texture"
    assert "--materialize" not in argv
    assert "--low-vram-mode" not in argv
    assert "--preserve-origin" in argv


def test_paint3d_texture_argv_no_preserve_origin() -> None:
    p3 = Paint3DProfile(preserve_origin=False)
    argv = _paint3d_texture_argv(
        "/bin/paint3d",
        p3,
        Path("/shape.glb"),
        Path("/ref.png"),
        Path("/out.glb"),
    )
    assert "--no-preserve-origin" in argv


def test_texture_subprocess_delegates_to_paint3d() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        paint3d=Paint3DProfile(style="hunyuan"),
    )
    argv = _texture_subprocess_argv(
        "/bin/paint3d",
        p,
        Path("/a.glb"),
        Path("/b.png"),
        Path("/c.glb"),
    )
    assert argv[0] == "/bin/paint3d"
    assert argv[1] == "texture"


def test_texture_subprocess_solid_uses_quick() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        paint3d=Paint3DProfile(
            style="solid",
            solid_color="#ff00aa",
        ),
    )
    argv = _texture_subprocess_argv(
        "/bin/paint3d",
        p,
        Path("/shape.glb"),
        Path("/ref.png"),
        Path("/out.glb"),
        row_id="Props/rock",
    )
    assert argv[:2] == ["/bin/paint3d", "quick"]
    assert "--style" in argv
    assert argv[argv.index("--style") + 1] == "solid"
    assert "--color" in argv
    assert argv[argv.index("--color") + 1] == "#ff00aa"


def test_texture_subprocess_perlin_uses_row_seed_when_unset() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        seed_base=1000,
        paint3d=Paint3DProfile(style="perlin", perlin_seed=None),
    )
    rid = "Env/stone_01"
    expected = _seed_for_row(p, rid)
    assert expected is not None
    argv = _texture_subprocess_argv(
        "/bin/paint3d",
        p,
        Path("/s.glb"),
        Path("/i.png"),
        Path("/o.glb"),
        row_id=rid,
    )
    assert argv[1] == "quick"
    assert argv[argv.index("--seed") + 1] == str(expected)


def test_text3d_argv_allow_shared_and_no_gpu_kill() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(
            preset="fast",
            allow_shared_gpu=True,
            gpu_kill_others=False,
        ),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--allow-shared-gpu" in argv
    assert "--no-gpu-kill-others" in argv


def test_text3d_argv_mc_level() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(
            preset="balanced",
            mc_level=0.0,
        ),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--mc-level" in argv and "0.0" in argv


def test_rigging3d_output_path() -> None:
    m = Path("meshes") / "hero.glb"
    assert _rigging3d_output_path(m, "_rigged").name == "hero_rigged.glb"
    assert _rigging3d_output_path(m, "rigged").name == "hero_rigged.glb"


def test_rigging3d_pipeline_argv_minimal() -> None:
    argv = _rigging3d_pipeline_argv(
        "rigging3d",
        Path("/in.glb"),
        Path("/out_rigged.glb"),
        seed=42,
        rig_profile=None,
    )
    assert argv[0] == "rigging3d"
    assert argv[1] == "pipeline"
    assert "--seed" in argv
    assert "42" in argv


def test_rigging3d_pipeline_argv_with_profile() -> None:
    rg = Rigging3DProfile(root="/u", python="/py/bin/python")
    argv = _rigging3d_pipeline_argv(
        "rigging3d",
        Path("/a.glb"),
        Path("/b.glb"),
        seed=None,
        rig_profile=rg,
    )
    assert argv.index("--root") + 1 < len(argv)
    assert argv[argv.index("--root") + 1] == "/u"
    assert argv[argv.index("--python") + 1] == "/py/bin/python"


def test_rigging3d_pipeline_argv_with_gpu_ids() -> None:
    argv = _rigging3d_pipeline_argv(
        "rigging3d",
        Path("/in.glb"),
        Path("/out.glb"),
        seed=None,
        rig_profile=None,
        gpu_ids=[0, 1],
    )
    gpu_idx = argv.index("--gpu-ids")
    assert argv[gpu_idx + 1] == "0,1"
    assert "pipeline" in argv
    assert argv.index("--gpu-ids") < argv.index("pipeline")


def test_terrain3d_profile_effective_default() -> None:
    """When no terrain3d block in profile, returns default Terrain3DProfile."""
    from gameassets.helpers import _terrain3d_profile_effective
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict({"title": "X", "genre": "X", "tone": "X", "style_preset": "lowpoly"})
    ter = _terrain3d_profile_effective(p)
    assert ter.prompt is None
    assert ter.seed is None


def test_terrain3d_profile_effective_from_yaml() -> None:
    from gameassets.helpers import _terrain3d_profile_effective
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict(
        {
            "title": "X",
            "genre": "X",
            "tone": "X",
            "style_preset": "lowpoly",
            "terrain3d": {"prompt": "mountains", "size": 1024, "world_size": 256.0},
        }
    )
    ter = _terrain3d_profile_effective(p)
    assert ter.prompt == "mountains"
    assert ter.size == 1024


def test_append_terrain3d_profile_args() -> None:
    from gameassets.helpers import _append_terrain3d_profile_args
    from gameassets.profile import Terrain3DProfile

    ter = Terrain3DProfile(size=1024, world_size=256.0, quality="high")
    argv: list[str] = []
    _append_terrain3d_profile_args(ter, argv)
    assert "--size" in argv
    assert "1024" in argv
    assert "--world-size" in argv
    assert "--quality" in argv


def test_skymap2d_profile_effective_default() -> None:
    from gameassets.helpers import _skymap2d_profile_effective
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict({"title": "X", "genre": "X", "tone": "X", "style_preset": "lowpoly"})
    sky = _skymap2d_profile_effective(p)
    assert sky.prompt is None


def test_append_skymap2d_profile_args() -> None:
    from gameassets.helpers import _append_skymap2d_profile_args
    from gameassets.profile import Skymap2DProfile

    sky = Skymap2DProfile(width=2048, height=1024, steps=20)
    argv: list[str] = []
    _append_skymap2d_profile_args(sky, argv)
    assert "-W" in argv
    assert "2048" in argv
    assert "-s" in argv


def test_text3d_argv_includes_quality_and_category() -> None:
    from gameassets.profile import Text3DProfile

    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="fast"),
        generation="high",
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"), quality="high", category="humanoid")
    assert "--quality" in argv
    assert "high" in argv
    assert "--category" in argv
    assert "humanoid" in argv


def test_paint3d_texture_argv_includes_quality_and_category() -> None:
    argv = _paint3d_texture_argv(
        "paint3d",
        Paint3DProfile(),
        Path("m.glb"),
        Path("i.png"),
        Path("o.glb"),
        quality="high",
        category="chest",
    )
    assert "--quality" in argv
    assert "high" in argv
    assert "--category" in argv
    assert "chest" in argv


def test_rigging3d_pipeline_argv_accepts_quality() -> None:
    argv = _rigging3d_pipeline_argv(
        "rigging3d",
        Path("in.glb"),
        Path("out.glb"),
        seed=42,
        rig_profile=None,
        quality="high",
    )
    assert "--quality" in argv
    assert "high" in argv


def test_rigging3d_pipeline_argv_no_text3d_low_vram_coupling() -> None:
    argv = _rigging3d_pipeline_argv(
        "rigging3d",
        Path("in.glb"),
        Path("out.glb"),
        seed=None,
        rig_profile=Rigging3DProfile(),
    )
    assert "--low-vram" not in argv


def test_append_text2d_profile_args_includes_quality() -> None:
    from gameassets.helpers import _append_text2d_profile_args
    from gameassets.profile import Text2DProfile

    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text2d=Text2DProfile(),
        generation="high",
    )
    argv: list[str] = []
    _append_text2d_profile_args(p, argv)
    assert "--quality" in argv
    assert "high" in argv


def test_append_texture2d_profile_args_includes_quality() -> None:
    from gameassets.helpers import _append_texture2d_profile_args
    from gameassets.profile import Texture2DProfile

    tt = Texture2DProfile(width=512, height=512)
    argv: list[str] = []
    _append_texture2d_profile_args(tt, argv, quality="high")
    assert "--quality" in argv
    assert "high" in argv


def test_append_skymap2d_profile_args_includes_quality() -> None:
    from gameassets.helpers import _append_skymap2d_profile_args
    from gameassets.profile import Skymap2DProfile

    sky = Skymap2DProfile(width=2048, height=1024)
    argv: list[str] = []
    _append_skymap2d_profile_args(sky, argv, quality="high")
    assert "--quality" in argv
    assert "high" in argv


def test_text3d_low_vram_is_noop_after_removal() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--low-vram" not in argv
