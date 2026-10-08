"""Smoke tests — CLI sem carregar o modelo."""

from click.testing import CliRunner

from text2d.cli import cli


def test_root_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Text2D" in r.output


def test_generate_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "PROMPT" in r.output or "prompt" in r.output.lower()


def test_info():
    runner = CliRunner()
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "PyTorch" in r.output


def test_models():
    runner = CliRunner()
    r = runner.invoke(cli, ["models"])
    assert r.exit_code == 0
    assert "FLUX.2-klein" in r.output
