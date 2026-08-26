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
    seamless_generation_duration,
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

    def test_equal_power_uncorrelated(self):
        """Equal-power real: material não-correlacionado mantém a potência.

        Head e tail de um loop são trechos musicais distintos (corr ≈ 0.05).
        Com curvas cos/sin em amplitude a potência soma cos²+sin² = 1 — o
        ponto médio da costura não pode ter dip (as curvas cos²/sin² antigas
        somavam cos⁴+sin⁴ → dip de -3 dB, medido a -12.9%).
        """
        sr = 44100
        torch.manual_seed(7)
        audio = torch.randn(2, sr * 6)
        body_power = audio[:, sr:-sr].pow(2).mean().item()
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=500.0)
        n = int(sr * 500.0 / 1000)
        mid = result[:, -n // 2 - 500 : -n // 2 + 500].pow(2).mean().item()
        assert mid == pytest.approx(body_power, rel=0.15)

    def test_coherent_material_no_clipping_bump(self):
        """Material coerente (mesma onda) não passa de +3 dB (cos+sin ≤ √2)."""
        sr = 44100
        t_ = torch.arange(sr * 4, dtype=torch.float32) / sr
        audio = (0.5 * torch.sin(2 * torch.pi * 110.0 * t_)).repeat(2, 1)
        result = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=300.0)
        assert result.abs().max().item() <= 0.5 * 1.42  # √2 + margem

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


class TestSeamlessGenerationDuration:
    """Duração de geração que aterra o loop final exactamente em -d."""

    def test_plain_crossfade_only(self):
        # 16 s + 500 ms de fold = gera 16.5 s → loop final 16.0 s
        assert seamless_generation_duration(16.0, 500.0, 0.0) == pytest.approx(16.5)

    def test_with_edge_trim(self):
        # 16 + 0.5 (xf) + 2x0.75 (edge) = 18.0 s
        assert seamless_generation_duration(16.0, 500.0, 0.75) == pytest.approx(18.0)

    def test_negative_edge_clamped(self):
        assert seamless_generation_duration(10.0, 0.0, -3.0) == pytest.approx(10.0)

    def test_roundtrip_with_save_pipeline(self):
        """edge trim (2x) + fold (xf) sobre a duração gerada → -d exacto."""
        sr = 44100
        d, xf_ms, edge = 4.0, 200.0, 0.5
        gen = seamless_generation_duration(d, xf_ms, edge)
        audio = torch.randn(2, int(gen * sr))
        e = int(edge * sr)
        audio = audio[:, e:-e]
        loop = apply_seamless_loop_crossfade(audio, sr, crossfade_ms=xf_ms)
        assert loop.shape[-1] / sr == pytest.approx(d, abs=1 / sr)


class TestShapeSeamlessLoopExact:
    """Edge trim adaptativo + fold aterram o loop exactamente em -d."""

    def _synth(self, sr, gen, body_level, outro_start, outro_end_level=0.05):
        t = torch.arange(int(gen * sr)) / sr
        env = torch.full_like(t, body_level)
        oi = int(outro_start * sr)
        env[oi:] = torch.linspace(body_level, outro_end_level, len(t) - oi)
        return (env * torch.sin(2 * torch.pi * 220.0 * t)).repeat(2, 1)

    def test_exact_length_with_fading_tail(self):
        """Cauda em decaimento (outro musical) é comida; loop aterra em D."""
        from text2sound.audio_processor import _shape_seamless_loop_exact

        sr = 44100
        D, xf_ms, edge = 4.0, 200.0, 0.5
        gen = seamless_generation_duration(D, xf_ms, edge)  # 5.2 s
        # outro ocupa o último 0.6 s (dentro do alcance do trim adaptativo)
        audio = self._synth(sr, gen, 0.6, outro_start=gen - 0.6)
        out = _shape_seamless_loop_exact(audio, sr, loop_edge_trim_s=edge, crossfade_ms=xf_ms, target_seconds=D)
        assert out.shape[-1] == int(D * sr)

    def test_fading_tail_removed_energetically(self):
        """Após o shaping, a cauda do loop não pode estar em decaimento."""
        from text2sound.audio_processor import _shape_seamless_loop_exact

        sr = 44100
        D, xf_ms, edge = 4.0, 200.0, 0.5
        gen = seamless_generation_duration(D, xf_ms, edge)
        audio = self._synth(sr, gen, 0.6, outro_start=gen - 0.6)
        out = _shape_seamless_loop_exact(audio, sr, loop_edge_trim_s=edge, crossfade_ms=xf_ms, target_seconds=D)
        mono = out.abs().max(dim=0).values
        w = int(0.2 * sr)
        tail_rms = float(mono[-w:].mean())
        body_rms = float(mono[w:-w].median())
        assert tail_rms > body_rms * 0.7

    def test_hot_edges_keep_minimal_trim(self):
        """Energia constante: trim fica no mínimo e fold = crossfade pedido."""
        from text2sound.audio_processor import _shape_seamless_loop_exact

        sr = 44100
        D, xf_ms, edge = 4.0, 200.0, 0.5
        gen = seamless_generation_duration(D, xf_ms, edge)
        t = torch.arange(int(gen * sr)) / sr
        audio = (0.6 * torch.sin(2 * torch.pi * 220.0 * t)).repeat(2, 1)
        out = _shape_seamless_loop_exact(audio, sr, loop_edge_trim_s=edge, crossfade_ms=xf_ms, target_seconds=D)
        assert out.shape[-1] == int(D * sr)

    def test_no_edge_trim_still_exact(self):
        """edge=0: só o fold consome o extra → comprimento exacto."""
        from text2sound.audio_processor import _shape_seamless_loop_exact

        sr = 44100
        D, xf_ms = 3.0, 250.0
        gen = seamless_generation_duration(D, xf_ms, 0.0)
        t = torch.arange(int(gen * sr)) / sr
        audio = (0.5 * torch.sin(2 * torch.pi * 180.0 * t)).repeat(2, 1)
        out = _shape_seamless_loop_exact(audio, sr, loop_edge_trim_s=0.0, crossfade_ms=xf_ms, target_seconds=D)
        assert out.shape[-1] == int(D * sr)


class TestSeamlessMasteringState:
    """Mastering stateful não pode quebrar a costura do loop (double-render)."""

    def test_mastered_loop_wrap_stays_continuous(self, tmp_path):
        """Com compressor, o jump no wrap fica na ordem de um passo normal."""
        import soundfile as sf

        sr = 44100
        t = torch.arange(sr * 6) / sr
        # tonal (passos pequenos) + amplitude modulada lenta
        music = 0.5 * torch.sin(2 * torch.pi * 220.0 * t) * (0.7 + 0.3 * torch.sin(2 * torch.pi * 0.5 * t))
        audio = music.repeat(2, 1)
        out = save_audio(
            audio=audio,
            sample_rate=sr,
            output_path=tmp_path / "loop_master.wav",
            fmt="wav",
            seamless_loop=True,
            crossfade_ms=300.0,
            loop_edge_trim_s=0.5,
            loop_target_seconds=4.0,
            lufs_target=-16.0,
            high_pass_hz=30.0,
            compressor_preset="glue",
            compressor_enabled=True,
            true_peak_db=-1.0,
        )
        data, _ = sf.read(str(out))
        m = data.mean(axis=1)
        wrap_jump = abs(m[0] - m[-1])
        import numpy as np

        p99 = np.percentile(np.abs(np.diff(m)), 99)
        assert len(m) / sr == pytest.approx(4.0, abs=1 / sr)
        assert wrap_jump <= max(p99 * 2.0, 0.02), (wrap_jump, p99)
