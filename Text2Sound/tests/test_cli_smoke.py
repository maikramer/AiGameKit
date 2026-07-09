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
        # New generation + DSP flags
        assert "--negative" in result.output
        assert "--no-negative" in result.output
        assert "--lufs" in result.output
        assert "--no-loudness" in result.output
        assert "--high-pass" in result.output
        assert "--compressor" in result.output
        assert "--compressor-preset" in result.output
        assert "--true-peak" in result.output
        assert "--bit-depth" in result.output
        assert "--enhance" in result.output

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


class TestGenerateDSPValidation:
    """Validation of the new DSP/negative-prompt flags (no model load)."""

    def test_lufs_out_of_range(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--lufs", "5.0"])
        assert result.exit_code != 0

    def test_lufs_valid_accepted_at_parse(self, runner):
        # -5 is within range; will fail later at model load but parsing is fine.
        result = runner.invoke(cli, ["generate", "test", "--lufs", "-5", "--duration", "100"])
        # Duration error proves we got past LUFS parsing.
        assert result.exit_code != 0
        assert "dura" in result.output.lower() or "duration" in result.output.lower()

    def test_high_pass_out_of_range(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--high-pass", "500"])
        assert result.exit_code != 0

    def test_bit_depth_invalid(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--bit-depth", "20"])
        assert result.exit_code != 0

    def test_compressor_preset_invalid(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--compressor-preset", "bogus"])
        assert result.exit_code != 0

    def test_true_peak_out_of_range(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--true-peak", "1.0"])
        assert result.exit_code != 0
