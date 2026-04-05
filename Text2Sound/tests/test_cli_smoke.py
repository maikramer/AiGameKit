"""Smoke tests para text2sound CLI (sem modelo/GPU)."""

import pytest
from click.testing import CliRunner

from tests._heavy_deps import require_audio_stack

require_audio_stack()

from text2sound.cli import cli  # noqa: E402


@pytest.fixture
def runner():
    return CliRunner()


class TestCLISmoke:
    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "text2sound" in result.output.lower() or "Text2Sound" in result.output

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_generate_help(self, runner):
        result = runner.invoke(cli, ["generate", "--help"])
        assert result.exit_code == 0
        assert "prompt" in result.output.lower()
        assert "--duration" in result.output
        assert "--steps" in result.output
        assert "--cfg-scale" in result.output
        assert "--profile" in result.output

    def test_batch_help(self, runner):
        result = runner.invoke(cli, ["batch", "--help"])
        assert result.exit_code == 0
        assert "file" in result.output.lower()
        assert "--output-dir" in result.output or "-O" in result.output

    def test_presets_command(self, runner):
        result = runner.invoke(cli, ["presets"])
        assert result.exit_code == 0
        assert "ambient" in result.output
        assert "battle" in result.output

    def test_info_command(self, runner):
        result = runner.invoke(cli, ["info"])
        assert result.exit_code == 0
        assert "stable-audio-open-1.0" in result.output or "44100" in result.output
        assert "open-small" in result.output or "Efeitos" in result.output

    def test_skill_help(self, runner):
        result = runner.invoke(cli, ["skill", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output


class TestGenerateValidation:
    def test_duration_too_high(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--duration", "100"])
        assert result.exit_code != 0

    def test_duration_too_low(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--duration", "0"])
        assert result.exit_code != 0

    def test_steps_too_low(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--steps", "1"])
        assert result.exit_code != 0

    def test_steps_too_high(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--steps", "999"])
        assert result.exit_code != 0

    def test_invalid_format(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--format", "mp3"])
        assert result.exit_code != 0

    def test_effects_duration_over_max(self, runner):
        result = runner.invoke(
            cli,
            [
                "generate",
                "laser",
                "--profile",
                "effects",
                "--duration",
                "12",
            ],
        )
        assert result.exit_code != 0
        assert "11" in result.output or "excede" in result.output.lower()

    @pytest.mark.slow
    def test_effects_duration_ok_reaches_generate(self, runner):
        result = runner.invoke(
            cli,
            [
                "generate",
                "laser",
                "--profile",
                "effects",
                "--duration",
                "2",
            ],
        )
        assert "Configuração" in result.output
