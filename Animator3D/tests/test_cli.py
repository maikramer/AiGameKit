"""Testes da CLI (sem exigir bpy para --help)."""

from __future__ import annotations

from animator3d.cli import main
from click.testing import CliRunner


def test_help_lists_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "wave-idle" in result.output
    assert "attack" in result.output
    assert "walk" in result.output
    assert "hover" in result.output
    assert "soar" in result.output
    assert "dive" in result.output
    assert "fire" in result.output
    assert "land" in result.output
    assert "roar" in result.output
    assert "list-clips" in result.output
    assert "inspect" in result.output
    assert "texture-project" in result.output


def test_screenshot_help_lists_frame_list() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["screenshot", "--help"])
    assert result.exit_code == 0
    assert "frame-list" in result.output


def test_texture_project_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["texture-project", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.output
    assert "--resolution" in result.output
    assert "--margin" in result.output
