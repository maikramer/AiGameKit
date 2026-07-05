"""Testes extra Rigging3D: CLI help/version."""

from __future__ import annotations

from click.testing import CliRunner
from rigging3d import __version__
from rigging3d.cli import cli


def test_version_string() -> None:
    assert len(__version__) >= 1


def test_cli_pipeline_help() -> None:
    r = CliRunner().invoke(cli, ["pipeline", "--help"])
    assert r.exit_code == 0
    assert "--input" in r.output or "-i" in r.output


def test_cli_transfer_weights_help() -> None:
    r = CliRunner().invoke(cli, ["transfer-weights", "--help"])
    assert r.exit_code == 0
    assert "--source" in r.output


def test_cli_root_help() -> None:
    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "rigging3d" in r.output.lower()


def test_cli_root_version() -> None:
    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert __version__ in r.output


def test_pipeline_requires_input() -> None:
    r = CliRunner().invoke(cli, ["pipeline"], catch_exceptions=False)
    assert r.exit_code != 0
