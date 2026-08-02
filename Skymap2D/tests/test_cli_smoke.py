"""Smoke tests do CLI Skymap2D (sem inferência)."""

from __future__ import annotations

from click.testing import CliRunner

from skymap2d.cli import cli


def test_root_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Skymap2D" in r.output or "skymap" in r.output.lower()


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
    assert "Sunset" in r.output or "Night" in r.output


def test_info() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "Modelo" in r.output or "HF" in r.output


def test_batch_requires_file() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["batch"])
    assert r.exit_code != 0


def test_skill_install_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["skill", "install", "--help"])
    assert r.exit_code == 0
    assert "--target" in r.output or "-t" in r.output
