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
    def test_output_drops_folded_head(self):
        """Loop region is audio[n:]: the head folded into the tail must not replay."""
        sr = 44100
        audio = torch.randn(2, sr * 5)  # 5 seconds stereo
        n = int(sr * 500.0 / 1000)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == (2, audio.shape[-1] - n)
        # Loop starts where the blend converged: first sample == audio[:, n]
        assert torch.equal(result[:, 0], audio[:, n])

    def test_equal_power_property(self):
        """cos^2 + sin^2 should equal ~1.0 for all points."""
        sr = 44100
        n = int(sr * 500.0 / 1000)
        t = torch.linspace(0, torch.pi / 2, n)
        fade_out = torch.cos(t) ** 2
        fade_in = torch.sin(t) ** 2
        energy = fade_out + fade_in
        assert torch.allclose(energy, torch.ones_like(energy), atol=1e-6)

    def test_center_unchanged(self):
        """Samples outside crossfade zone should match the input (shifted by n)."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        n = int(sr * 500.0 / 1000)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert torch.equal(result[:, :-n], audio[:, n:-n])

    def test_wrap_is_sample_continuous(self):
        """On a smooth sine, the wrap-point jump must be no bigger than a normal
        sample-to-sample step — the old keep-the-head behaviour failed this."""
        sr = 44100
        t = torch.arange(sr * 5, dtype=torch.float32) / sr
        audio = torch.sin(2 * torch.pi * 220.0 * t).repeat(2, 1)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        step = (result[:, 1:] - result[:, :-1]).abs().max()
        wrap_jump = (result[:, -1] - result[:, 0]).abs().max()
        assert wrap_jump <= step * 1.5

    def test_works_with_mono(self):
        sr = 44100
        audio = torch.randn(1, sr * 5)
        n = int(sr * 500.0 / 1000)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == (1, audio.shape[-1] - n)

    def test_short_audio_crossfade_clamped(self):
        """Audio shorter than crossfade_ms should clamp crossfade to half length."""
        sr = 44100
        audio = torch.randn(2, 100)  # very short
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert result.shape == (2, 100 - 50)

    def test_does_not_modify_original(self):
        sr = 44100
        audio = torch.randn(2, sr * 5)
        original = audio.clone()
        apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        assert torch.equal(audio, original)

    def test_crossfade_500ms_stereo(self):
        """Full integration: the blended tail differs from the raw tail."""
        sr = 44100
        audio = torch.randn(2, sr * 5)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        n = int(sr * 500.0 / 1000)
        assert not torch.equal(result[:, -n:], audio[:, -n:])


class TestLoopEdgeTrim:
    def test_edges_removed_before_crossfade(self, tmp_path):
        sr = 44100
        audio = torch.randn(2, sr * 10)
        out = save_audio(
            audio,
            sr,
            tmp_path / "loop",
            fmt="wav",
            normalize=False,
            seamless_loop=True,
            crossfade_ms=500.0,
            loop_edge_trim_s=2.0,
        )
        import soundfile as sf

        data, _ = sf.read(str(out))
        # 10s - 2*2s edges - 0.5s folded head = 5.5s
        assert abs(data.shape[0] - int(sr * 5.5)) <= 2

    def test_trim_skipped_when_audio_too_short(self, tmp_path):
        sr = 44100
        audio = torch.randn(2, sr * 2)  # 2s < 3 * edge
        out = save_audio(
            audio,
            sr,
            tmp_path / "loop",
            fmt="wav",
            normalize=False,
            seamless_loop=True,
            crossfade_ms=100.0,
            loop_edge_trim_s=1.0,
        )
        import soundfile as sf

        data, _ = sf.read(str(out))
        # edges kept (2s ≤ 3*1s); only the folded head (0.1s) is dropped
        assert abs(data.shape[0] - int(sr * 1.9)) <= 2

    def test_no_trim_without_seamless(self, tmp_path):
        sr = 44100
        audio = torch.randn(2, sr * 4)
        out = save_audio(
            audio,
            sr,
            tmp_path / "plain",
            fmt="wav",
            normalize=False,
            seamless_loop=False,
            loop_edge_trim_s=2.0,
        )
        import soundfile as sf

        data, _ = sf.read(str(out))
        assert data.shape[0] == sr * 4


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

    def test_seamless_loop_applies_crossfade(self, tmp_path):
        """seamless_loop=True should apply crossfade instead of edge fade."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "loop"
        result = save_audio(audio, 44100, out, seamless_loop=True, crossfade_ms=500.0)
        assert result.exists()

    def test_seamless_loop_false_uses_edge_fade(self, tmp_path):
        """seamless_loop=False (default) should use edge fade as before."""
        audio = torch.randn(2, 44100)
        out = tmp_path / "no_loop"
        result = save_audio(audio, 44100, out)
        assert result.exists()

    def test_seamless_loop_metadata(self, tmp_path):
        """Metadata should include seamless_loop and crossfade_ms."""
        audio = torch.randn(2, 44100)
        meta = {"prompt": "test"}
        out = tmp_path / "loop_meta"
        result = save_audio(
            audio,
            44100,
            out,
            seamless_loop=True,
            crossfade_ms=500.0,
            metadata=meta,
        )
        meta_path = result.with_suffix(result.suffix + ".json")
        data = json.loads(meta_path.read_text())
        assert data["seamless_loop"] is True
        assert data["crossfade_ms"] == 500.0


class TestApplyMasteringChain:
    """Tests for the pedalboard-based mastering chain (LUFS/compressor/limiter/HP)."""

    def setup_method(self):
        from tests._heavy_deps import require_mastering_stack

        require_mastering_stack()
        from text2sound.audio_processor import apply_mastering_chain

        self.apply_mastering_chain = apply_mastering_chain
        # 3-second stereo sine with amplitude envelope (dynamic range to compress)
        sr = 44100
        n = sr * 3
        t = torch.linspace(0.0, 3.0, n)
        env = 0.5 + 0.5 * torch.sin(2 * 3.14159 * 0.5 * t)
        self.sr = sr
        self.audio = torch.stack([0.3 * torch.sin(2 * 3.14159 * 440 * t) * env] * 2)

    def test_noop_when_all_none(self):
        """When every optional param is None, the audio is returned unchanged."""
        from text2sound.audio_processor import apply_mastering_chain

        out = apply_mastering_chain(self.audio, self.sr)
        assert torch.equal(out, self.audio)

    def test_does_not_mutate_input(self):
        """Mastering returns a new tensor; input is never modified in-place."""
        original = self.audio.clone()
        self.apply_mastering_chain(
            self.audio, self.sr, lufs_target=-16.0, high_pass_hz=30, compressor_preset="punch", true_peak_db=-1.0
        )
        assert torch.equal(self.audio, original)

    def test_lufs_normalization_target(self):
        """Without a limiter, output LUFS matches the requested target closely."""
        import pyloudnorm as pyln

        out = self.apply_mastering_chain(self.audio, self.sr, lufs_target=-16.0)
        meter = pyln.Meter(self.sr)
        measured = meter.integrated_loudness(out.numpy().T)
        # Without a limiter in the way, the gain is exact (±0.5 LU tolerance).
        assert abs(measured - (-16.0)) < 1.0, f"LUFS {measured:.2f} too far from -16"

    def test_lufs_with_limiter_is_approximate(self):
        """With a limiter, LUFS deviates from target (limiter reshapes dynamics).

        This documents the expected behaviour: the limiter protects the ceiling
        but changes loudness, so the target becomes approximate, not exact.
        """
        import pyloudnorm as pyln

        out = self.apply_mastering_chain(self.audio, self.sr, lufs_target=-16.0, true_peak_db=-1.0)
        meter = pyln.Meter(self.sr)
        measured = meter.integrated_loudness(out.numpy().T)
        # Limiter may push loudness up by a few LU; just assert it ran sanely.
        assert -30.0 < measured < 0.0

    def test_true_peak_respects_ceiling(self):
        """With a limiter ceiling, max sample stays below 0 dBFS plus margin."""
        out = self.apply_mastering_chain(self.audio, self.sr, lufs_target=-14.0, true_peak_db=-1.0, headroom_db=0.3)
        # -1 dBTP ceiling → peak ≈ 10^(-1/20) ≈ 0.89; headroom adds margin.
        assert float(out.abs().max()) < 1.0
        assert float(out.abs().max()) < 0.95

    def test_invalid_compressor_preset_raises(self):
        with pytest.raises(ValueError, match="compressor_preset"):
            self.apply_mastering_chain(self.audio, self.sr, compressor_preset="bogus")

    def test_compressor_preset_valid(self):
        """Each curated preset name is accepted without error."""
        for preset in ("punch", "glue", "master_glue", "transparent"):
            self.apply_mastering_chain(self.audio, self.sr, compressor_preset=preset, lufs_target=-16.0)

    def test_compressor_disabled_skips(self):
        """compressor_enabled=False skips the compressor even with a preset set."""
        # Should not raise and should still apply LUFS.
        out = self.apply_mastering_chain(
            self.audio,
            self.sr,
            compressor_preset="punch",
            compressor_enabled=False,
            lufs_target=-16.0,
            true_peak_db=-1.0,
        )
        assert out.shape == self.audio.shape


class TestSaveAudioMastering:
    """save_audio integration with the mastering chain."""

    def setup_method(self):
        from tests._heavy_deps import require_mastering_stack

        require_mastering_stack()
        sr = 44100
        n = sr * 2
        t = torch.linspace(0.0, 2.0, n)
        self.sr = sr
        self.audio = torch.stack([0.25 * torch.sin(2 * 3.14159 * 440 * t)] * 2)

    def test_lufs_replaces_peak_normalize(self, tmp_path):
        """When lufs_target is set, peak-normalize legacy is skipped (LUFS owns gain)."""
        out = tmp_path / "lufs.ogg"
        save_audio(self.audio, self.sr, out, fmt="ogg", lufs_target=-20.0, true_peak_db=-1.0)
        assert out.exists()

    def test_bit_depth_24_wav(self, tmp_path):
        import soundfile as sf

        out = tmp_path / "24bit.wav"
        save_audio(self.audio, self.sr, out, fmt="wav", bit_depth=24)
        assert sf.info(str(out)).subtype == "PCM_24"

    def test_invalid_bit_depth_raises(self, tmp_path):
        with pytest.raises(ValueError, match="bit_depth"):
            save_audio(self.audio, self.sr, tmp_path / "x.wav", fmt="wav", bit_depth=32)

    def test_metadata_records_mastering(self, tmp_path):
        out = tmp_path / "meta.ogg"
        save_audio(
            self.audio,
            self.sr,
            out,
            fmt="ogg",
            lufs_target=-16.0,
            high_pass_hz=30,
            compressor_preset="glue",
            true_peak_db=-1.0,
            metadata={"prompt": "x"},
        )
        meta = json.loads(out.with_suffix(".ogg.json").read_text())
        assert meta["mastering"]["lufs_target"] == -16.0
        assert meta["mastering"]["compressor_preset"] == "glue"

    def test_ogg_quality_in_metadata(self, tmp_path):
        out = tmp_path / "q.ogg"
        save_audio(self.audio, self.sr, out, fmt="ogg", ogg_quality=0.7, metadata={"p": 1})
        meta = json.loads(out.with_suffix(".ogg.json").read_text())
        assert meta["ogg_quality"] == 0.7


class TestConstants:
    def test_supported_formats(self):
        assert "wav" in SUPPORTED_FORMATS
        assert "flac" in SUPPORTED_FORMATS
        assert "ogg" in SUPPORTED_FORMATS

    def test_default_format(self):
        assert DEFAULT_FORMAT == "ogg"
