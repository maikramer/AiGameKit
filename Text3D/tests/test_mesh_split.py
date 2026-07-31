"""Testes do comando / wrapper Text3D split-at-height."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

bpy = pytest.importorskip("bpy")

from aigamekit_shared.bpy_mesh import clear_scene, load_glb, save_glb  # noqa: E402
from text3d.cli import cli  # noqa: E402
from text3d.utils.mesh_split import split_at_height_glb  # noqa: E402


def _save_box_glb(path: Path) -> Path:
    # Blender Z-up: altura no Z (vira Y no glTF exportado).
    clear_scene()
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0, 0, 1))
    obj = bpy.context.active_object
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    save_glb([obj], path)
    clear_scene()
    return path


def test_split_at_height_glb_wrapper(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "tree.glb")
    out = tmp_path / "tree_split.glb"
    result = split_at_height_glb(inp, out, cut_height=0.5, split_files=True)
    assert result.output.is_file()
    assert result.stump_path is not None and result.stump_path.is_file()
    assert result.top_path is not None and result.top_path.is_file()
    names = {o.name for o in load_glb(out)}
    assert names == {"Stump", "Top"}
    clear_scene()


def test_cli_split_at_height(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "oak.glb")
    out = tmp_path / "oak_out.glb"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["split-at-height", str(inp), "-o", str(out), "--cut-height", "0.6"],
        catch_exceptions=False,
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "split-at-height" in result.output


def test_cli_rejects_both_cut_flags(tmp_path: Path) -> None:
    inp = _save_box_glb(tmp_path / "oak.glb")
    out = tmp_path / "oak_out.glb"
    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            "split-at-height",
            str(inp),
            "-o",
            str(out),
            "--cut-height",
            "0.5",
            "--cut-ratio",
            "0.3",
        ],
    )
    assert result.exit_code != 0
    assert "não ambos" in result.output
