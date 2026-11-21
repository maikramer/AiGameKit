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
        assert "stable-audio-3-small-music" in result.output or "44100" in result.output
        assert "stable-audio-3-small-sfx" in result.output or "Efeitos" in result.output

    def test_skill_help(self, runner):
        result = runner.invoke(cli, ["skill", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output


class TestGenerateValidation:
    def test_duration_too_high(self, runner):
        result = runner.invoke(cli, ["generate", "test", "--duration", "500"])
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
                "45",
            ],
        )
        assert result.exit_code != 0
        assert "30" in result.output or "entre 0.5" in result.output.lower()

    def test_music_sa3_defaults_applied(self, runner):
        """Sem flags explícitas, o CLI adota os defaults SA3 (steps 8, cfg 1.0, pingpong)."""
        result = runner.invoke(cli, ["generate", "epic battle theme", "--duration", "10"])
        if result.exit_code == 0:
            assert "pingpong" in result.output
            assert "8" in result.output

    def test_effects_model_is_sa3_sfx(self, runner):
        result = runner.invoke(cli, ["generate", "laser", "--profile", "effects"])
        if result.exit_code == 0:
            assert "stable-audio-3-small-sfx" in result.output

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
        # A valid LUFS value reaches duration validation without loading a model.
        result = runner.invoke(cli, ["generate", "test", "--lufs", "-5", "--duration", "121"])
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


class TestVramdDelegationAndWorkerParity:
    """Paridade CLI ↔ vramd: audio_kind com --model explícito + defaults seguros no worker."""

    def test_explicit_model_still_applies_audio_kind_and_forwards_vramd_fields(self, runner, tmp_path):
        from unittest.mock import patch

        out = tmp_path / "sword.ogg"
        captured: dict[str, object] = {}

        def fake_delegate(backend, payload, **kwargs):
            captured["backend"] = backend
            captured["payload"] = payload
            return True

        with patch("text2sound.cli.delegate_or_prepare", side_effect=fake_delegate):
            result = runner.invoke(
                cli,
                [
                    "generate",
                    "metallic sword clash",
                    "-o",
                    str(out),
                    "--model",
                    "effects",
                    "--category",
                    "weapon",
                    "--quality",
                    "high",
                    "--chunked-vae",
                ],
            )
        assert result.exit_code == 0, result.output
        payload = captured["payload"]
        assert isinstance(payload, dict)
        # Regressão do bug de indentação: --model explícito NÃO pode desligar audio_kind
        assert payload["audio_kind"] is not None
        assert payload["compressor_preset"] is not None
        # Campos que antes faltavam no payload vramd
        assert "prompt_hints" in payload
        assert payload["chunked_vae"] is True
        assert payload["quality"] == "high"
        assert payload["category"] == "weapon"

    def test_worker_serve_adapter_handles_minimal_request_and_writes_full_metadata(self, tmp_path):
        import json
        from types import SimpleNamespace

        import torch

        from text2sound.worker_serve_adapter import Adapter

        out = tmp_path / "minimal.wav"
        fake_result = SimpleNamespace(
            audio=torch.zeros(2, 44100, dtype=torch.float32),
            sample_rate=44100,
            seed=12345,
        )

        class FakeModel:
            model_id = "stabilityai/stable-audio-3-small-sfx"
            device = "cpu"

            def generate(self, **kwargs):
                return fake_result

        adapter = Adapter()
        resp = adapter.generate(
            FakeModel(),
            {
                "prompt": "wooden door creak",
                "output": str(out),
                "duration": 1.0,
                "steps": 12,
                "cfg_scale": 6.0,
                "seed": 12345,
                "quality": "high",
                "category": "sfx",
                "audio_kind": "sfx",
                "prompt_hints": ["clean studio foley"],
                # Omitir bit_depth, crossfade_ms, loop_edge_trim_s, fade_out_seconds, headroom_db
            },
        )
        assert resp["status"] == "ok"
        assert out.exists()
        meta = json.loads(out.with_suffix(".wav.json").read_text(encoding="utf-8"))
        assert meta["steps"] == 12
        assert meta["cfg_scale"] == 6.0
        assert meta["seed_effective"] == 12345
        assert meta["quality"] == "high"
        assert meta["category"] == "sfx"
        assert meta["audio_kind"] == "sfx"
        assert meta["prompt_hints"] == ["clean studio foley"]
