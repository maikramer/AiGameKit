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
from gameassets.profile import GameProfile, Rigging3DProfile, Text3DProfile


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
        text3d=Text3DProfile(preset="hq", low_vram=True, texture=True),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--preset" in argv and "hq" in argv
    assert "--low-vram" in argv
    assert "--texture" not in argv
    assert "--export-origin" in argv
    assert argv[argv.index("--export-origin") + 1] == "feet"


def test_text3d_argv_export_origin_center() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="fast", texture=True, export_origin="center"),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert argv[argv.index("--export-origin") + 1] == "center"


def test_text3d_argv_shape_only_skips_texture() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(preset="fast", texture=True),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"), shape_only=True)
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
            texture=False,
        ),
    )
    argv = _text3d_argv("text3d", p, Path("i.png"), Path("o.glb"))
    assert "--preset" not in argv
    assert "--steps" in argv and "28" in argv


def test_paint3d_texture_argv_gpu_flags() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(
            texture=True,
            allow_shared_gpu=True,
            gpu_kill_others=False,
            full_gpu=True,
        ),
    )
    argv = _paint3d_texture_argv(
        "/bin/paint3d",
        p,
        Path("/shape.glb"),
        Path("/ref.png"),
        Path("/out.glb"),
    )
    assert argv[0] == "/bin/paint3d"
    assert argv[1] == "texture"
    assert "--materialize" not in argv
    assert "--allow-shared-gpu" in argv
    assert "--no-gpu-kill-others" in argv
    assert "--paint-full-gpu" in argv
    assert "--preserve-origin" in argv


def test_paint3d_texture_argv_no_preserve_origin() -> None:
    p = GameProfile(
        title="T",
        genre="G",
        tone="t",
        style_preset="lowpoly",
        text3d=Text3DProfile(texture=True, paint_preserve_origin=False),
    )
    argv = _paint3d_texture_argv(
        "/bin/paint3d",
        p,
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
        text3d=Text3DProfile(texture=True),
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
        text3d=Text3DProfile(
            texture=True,
            paint_style="solid",
            paint_solid_color="#ff00aa",
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
        text3d=Text3DProfile(texture=True, paint_style="perlin", paint_perlin_seed=None),
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
            texture=True,
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
            texture=True,
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
