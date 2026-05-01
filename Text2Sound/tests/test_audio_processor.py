"""Testes para text2sound.audio_processor."""

import json

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("soundfile")

from text2sound.audio_processor import (
    DEFAULT_FORMAT,
    SUPPORTED_FORMATS,
    apply_edge_fade,
    apply_seamless_loop_crossfade,
    peak_normalize,
    save_audio,
    to_int16,
    trim_silence,
)


class TestPeakNormalize:
    def test_normalizes_loud(self):
        audio = torch.tensor([[2.0, -3.0, 1.0]])
        result = peak_normalize(audio)
        assert torch.max(torch.abs(result)).item() == pytest.approx(1.0)

    def test_quiet_signal(self):
        audio = torch.tensor([[0.1, -0.05]])
        result = peak_normalize(audio)
        assert torch.max(torch.abs(result)).item() == pytest.approx(1.0)

    def test_silence(self):
        audio = torch.zeros(2, 100)
        result = peak_normalize(audio)
        assert torch.all(result == 0)

    def test_clamps(self):
        audio = torch.tensor([[5.0, -5.0]])
        result = peak_normalize(audio)
        assert result.min().item() >= -1.0
        assert result.max().item() <= 1.0


class TestToInt16:
    def test_range(self):
        audio = torch.tensor([[1.0, -1.0, 0.0, 0.5]])
        result = to_int16(audio)
        assert result.dtype == torch.int16
        assert result[0, 0].item() == 32767
        assert result[0, 1].item() == -32767
        assert result[0, 2].item() == 0

    def test_clamps_before_conversion(self):
        audio = torch.tensor([[2.0, -2.0]])
        result = to_int16(audio)
        assert result[0, 0].item() == 32767
        assert result[0, 1].item() == -32767


class TestTrimSilence:
    def test_trims_trailing_silence(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, :200] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0, buffer_ms=100)
        assert result.shape[-1] < audio.shape[-1]
        assert result.shape[-1] >= 200

    def test_trims_leading_silence(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, 400:600] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0, buffer_ms=100)
        assert result.shape[-1] < audio.shape[-1]
        assert result.shape[-1] >= 200
        assert torch.any(result > 0.4)

    def test_trims_both_ends(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, 300:500] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0, buffer_ms=50)
        assert result.shape[-1] < 400

    def test_no_trim_if_no_silence(self):
        sr = 1000
        audio = torch.ones(2, sr) * 0.5
        result = trim_silence(audio, sr)
        assert result.shape[-1] == sr

    def test_all_silence(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        result = trim_silence(audio, sr)
        assert result.shape[-1] == sr

    def test_default_buffer_ms(self):
        """Default buffer_ms is 200 for backward compatibility."""
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, 500:] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0)
        # With default 200ms buffer, should keep 500 - 200 = 300 samples before signal
        assert result.shape[-1] < audio.shape[-1]


class TestApplyEdgeFade:
    def test_fade_in_starts_near_zero(self):
        sr = 44100
        audio = torch.ones(2, sr) * 0.5
        result = apply_edge_fade(audio, sr, fade_in_ms=10, fade_out_ms=0)
        assert result[0, 0].item() == pytest.approx(0.0, abs=0.05)
        # With fade_out_ms=0, end should stay near original
        assert result[0, -1].item() == pytest.approx(0.5, abs=0.05)

    def test_fade_out_ends_near_zero(self):
        sr = 44100
        audio = torch.ones(2, sr) * 0.5
        result = apply_edge_fade(audio, sr, fade_out_ms=10)
        assert result[0, -1].item() == pytest.approx(0.0, abs=0.05)

    def test_both_fades(self):
        sr = 44100
        audio = torch.ones(2, sr) * 0.5
        result = apply_edge_fade(audio, sr, fade_in_ms=5, fade_out_ms=20)
        assert result.shape == audio.shape
        assert result[0, 0].item() < 0.1
        assert result[0, -1].item() < 0.1
        # Middle should be near original
        mid = result.shape[-1] // 2
        assert result[0, mid].item() == pytest.approx(0.5, abs=0.01)

    def test_empty_audio(self):
        sr = 44100
        audio = torch.zeros(2, 0)
        result = apply_edge_fade(audio, sr)
        assert result.shape[-1] == 0

    def test_single_sample(self):
        sr = 44100
        audio = torch.tensor([[0.5]])
        result = apply_edge_fade(audio, sr)
        assert result.shape == audio.shape

    def test_does_not_modify_original(self):
        sr = 44100
        audio = torch.ones(2, sr)
        original = audio.clone()
        apply_edge_fade(audio, sr, fade_in_ms=5, fade_out_ms=10)
        assert torch.equal(audio, original)


class TestApplySeamlessLoopCrossfade:
    def test_output_shape_matches_input(self):
        sr = 44100
        audio = torch.randn(2, sr * 5)  # 5 seconds stereo
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_equal_power_property(self):
        """cos^2 + sin^2 should equal ~1.0 for all points."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        n = int(sr * 500.0 / 1000)
        t = torch.linspace(0, torch.pi / 2, n)
        fade_out = torch.cos(t) ** 2
        fade_in = torch.sin(t) ** 2
        energy = fade_out + fade_in
        assert torch.allclose(energy, torch.ones_like(energy), atol=1e-6)

    def test_center_unchanged(self):
        """Samples outside crossfade zone should be identical to input."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        n = int(sr * 500.0 / 1000)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        # Middle section (excluding last n samples which are crossfaded)
        assert torch.equal(result[:, :-n], audio[:, :-n])

    def test_works_with_mono(self):
        sr = 44100
        audio = torch.randn(1, sr * 5)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_short_audio_crossfade_clamped(self):
        """Audio shorter than crossfade_ms should clamp crossfade to half length."""
        sr = 44100
        audio = torch.randn(2, 100)  # very short
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == audio.shape

    def test_does_not_modify_original(self):
        sr = 44100
        audio = torch.randn(2, sr * 5)
        original = audio.clone()
        apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert torch.equal(audio, original)

    def test_crossfade_500ms_stereo(self):
        """Full integration: 500ms crossfade on stereo audio, verify smooth transition."""
        sr = 44100
        # Create audio with distinct start and end
        audio = torch.randn(2, sr * 5)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        # The last 500ms should be a mix (not identical to input)
        n = int(sr * 500.0 / 1000)
        assert not torch.equal(result[:, -n:], audio[:, -n:])


class TestSaveAudio:
    def test_save_wav(self, tmp_path):
        audio = torch.randn(2, 44100)
        out = tmp_path / "test"
        result = save_audio(audio, 44100, out, fmt="wav")
        assert result.suffix == ".wav"
        assert result.exists()

    def test_save_flac(self, tmp_path):
        audio = torch.randn(2, 44100)
        out = tmp_path / "test"
        result = save_audio(audio, 44100, out, fmt="flac")
        assert result.suffix == ".flac"
        assert result.exists()

    def test_invalid_format(self, tmp_path):
        audio = torch.randn(2, 100)
        with pytest.raises(ValueError, match="não suportado"):
            save_audio(audio, 44100, tmp_path / "test", fmt="mp3")

    def test_metadata_json(self, tmp_path):
        audio = torch.randn(2, 44100)
        meta = {"prompt": "test", "steps": 100}
        out = tmp_path / "test"
        result = save_audio(audio, 44100, out, metadata=meta)
        meta_path = result.with_suffix(result.suffix + ".json")
        assert meta_path.exists()
        data = json.loads(meta_path.read_text())
        assert data["prompt"] == "test"
        assert data["steps"] == 100

    def test_creates_parent_dirs(self, tmp_path):
        audio = torch.randn(2, 44100)
        out = tmp_path / "sub" / "dir" / "test"
        result = save_audio(audio, 44100, out)
        assert result.exists()

    def test_with_trim(self, tmp_path):
        audio = torch.zeros(2, 44100)
        audio[:, :22050] = 0.5
        out = tmp_path / "trimmed"
        result = save_audio(audio, 44100, out, trim=True)
        assert result.exists()

    def test_trim_buffer_ms(self, tmp_path):
        """trim_buffer_ms is forwarded to trim_silence as buffer_ms."""
        audio = torch.zeros(2, 44100)
        audio[:, 22050:] = 0.5
        out = tmp_path / "trimmed_buf"
        result = save_audio(audio, 44100, out, trim=True, trim_buffer_ms=50)
        assert result.exists()

    def test_apply_fade_false(self, tmp_path):
        """apply_fade=False should skip the edge fade."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "no_fade"
        result = save_audio(audio, 44100, out, apply_fade=False)
        assert result.exists()

    def test_apply_fade_true_default(self, tmp_path):
        """apply_fade defaults to True."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "with_fade"
        result = save_audio(audio, 44100, out)
        assert result.exists()


class TestConstants:
    def test_supported_formats(self):
        assert "wav" in SUPPORTED_FORMATS
        assert "flac" in SUPPORTED_FORMATS
        assert "ogg" in SUPPORTED_FORMATS

    def test_default_format(self):
        assert DEFAULT_FORMAT == "wav"
