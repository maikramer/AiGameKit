"""Testes para text2sound.audio_processor."""

import json

import pytest

torch = pytest.importorskip("torch")
pytest.importorskip("soundfile")

from text2sound.audio_processor import (
    DEFAULT_FORMAT,
    SUPPORTED_FORMATS,
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
        result = trim_silence(audio, sr, threshold_db=-40.0, min_silence_ms=100)
        assert result.shape[-1] < audio.shape[-1]
        assert result.shape[-1] >= 200

    def test_trims_leading_silence(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, 400:600] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0, min_silence_ms=100)
        assert result.shape[-1] < audio.shape[-1]
        assert result.shape[-1] >= 200
        assert torch.any(result > 0.4)

    def test_trims_both_ends(self):
        sr = 1000
        audio = torch.zeros(2, sr)
        audio[:, 300:500] = 0.5
        result = trim_silence(audio, sr, threshold_db=-40.0, min_silence_ms=50)
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


class TestConstants:
    def test_supported_formats(self):
        assert "wav" in SUPPORTED_FORMATS
        assert "flac" in SUPPORTED_FORMATS
        assert "ogg" in SUPPORTED_FORMATS

    def test_default_format(self):
        assert DEFAULT_FORMAT == "wav"
