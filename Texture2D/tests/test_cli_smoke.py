"""Smoke tests do CLI Texture2D (sem carregar o pipeline SD1.5)."""

from __future__ import annotations

from click.testing import CliRunner

from texture2d.cli import cli


def test_root_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Texture2D" in r.output or "seamless" in r.output.lower()


def test_version() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_generate_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--output" in r.output or "-o" in r.output


def test_presets_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["presets", "--help"])
    assert r.exit_code == 0


def test_presets_runs() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["presets"])
    assert r.exit_code == 0
    assert "Stone" in r.output or "Wood" in r.output


def test_info() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "stable" in r.output.lower() or "SD" in r.output or "Modelo" in r.output


def test_batch_requires_file() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["batch"])
    assert r.exit_code != 0


def test_skill_install_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["skill", "install", "--help"])
    assert r.exit_code == 0
    assert "--target" in r.output or "-t" in r.output
