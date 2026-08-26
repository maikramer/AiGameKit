"""Testes para text2sound.generator."""

from unittest.mock import MagicMock, patch

import torch

from tests._heavy_deps import require_audio_stack

require_audio_stack()

from text2sound.generator import (
    DEFAULT_CFG_SCALE,
    DEFAULT_DURATION,
    DEFAULT_SAMPLER,
    DEFAULT_SIGMA_MAX,
    DEFAULT_SIGMA_MIN,
    DEFAULT_STEPS,
    AudioGenerator,
    GenerationResult,
)


class TestGenerationResult:
    def test_fields(self):
        audio = torch.randn(2, 44100)
        result = GenerationResult(
            audio=audio,
            sample_rate=44100,
            prompt="test",
            duration=1.0,
            steps=10,
            cfg_scale=7.0,
            seed=42,
            sampler="dpmpp-3m-sde",
            sigma_min=0.3,
            sigma_max=500.0,
            device="cpu",
        )
        assert result.prompt == "test"
        assert result.sample_rate == 44100
        assert result.seed == 42
        assert result.audio.shape == (2, 44100)

    def test_default_metadata(self):
        result = GenerationResult(
            audio=torch.zeros(2, 100),
            sample_rate=44100,
            prompt="x",
            duration=1.0,
            steps=10,
            cfg_scale=7.0,
            seed=None,
            sampler="dpmpp-3m-sde",
            sigma_min=0.3,
            sigma_max=500.0,
            device="cpu",
        )
        assert result.metadata == {}


class TestAudioGenerator:
    def setup_method(self):
        AudioGenerator.reset_instance()

    def teardown_method(self):
        AudioGenerator.reset_instance()

    def test_default_device_cpu(self):
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            gen = AudioGenerator(device=None)
        assert gen.device == "cpu"

    def test_explicit_device(self):
        gen = AudioGenerator(device="cpu")
        assert gen.device == "cpu"

    def test_model_id(self):
        gen = AudioGenerator(model_id="test/model")
        assert gen.model_id == "test/model"

    def test_half_precision_property(self):
        gen = AudioGenerator(device="cpu", half_precision=False)
        assert gen.half_precision is False
        gen_on = AudioGenerator(device="cpu", half_precision=True)
        assert gen_on.half_precision is True

    def test_singleton_same_model(self):
        inst1 = AudioGenerator.get_instance(model_id="m1", device="cpu")
        inst2 = AudioGenerator.get_instance(model_id="m1", device="cpu")
        assert inst1 is inst2

    def test_singleton_different_model_recreates(self):
        inst1 = AudioGenerator.get_instance(model_id="m1", device="cpu")
        inst2 = AudioGenerator.get_instance(model_id="m2", device="cpu")
        assert inst1 is not inst2

    def test_reset_instance(self):
        AudioGenerator.get_instance(model_id="m1", device="cpu")
        AudioGenerator.reset_instance()
        assert AudioGenerator._instance is None

    @patch("text2sound.generator.get_pretrained_model")
    def test_load_sets_loaded(self, mock_get):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        gen = AudioGenerator(device="cpu")
        gen.load()
        assert gen._loaded is True
        assert gen.sample_rate == 44100
        assert gen.sample_size == 65536
        mock_get.assert_called_once()

    @patch("text2sound.generator.get_pretrained_model")
    def test_load_idempotent(self, mock_get):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        gen = AudioGenerator(device="cpu")
        gen.load()
        gen.load()
        mock_get.assert_called_once()

    @patch("text2sound.generator.get_pretrained_model")
    def test_unload(self, mock_get):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        gen = AudioGenerator(device="cpu")
        gen.load()
        gen.unload()
        assert gen._loaded is False
        assert gen._model is None

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate(self, mock_get, mock_gen_diff):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None  # sem VAE → caminho de áudio direto
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="test sound", duration=1.0, steps=10)

        assert isinstance(result, GenerationResult)
        assert result.prompt == "test sound"
        assert result.duration == 1.0
        assert result.steps == 10
        assert result.audio.shape[0] == 2
        # Sem pretransform o decode fica no generate_diffusion_cond (sem latents).
        assert mock_gen_diff.call_args.kwargs["return_latents"] is False

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_with_seed(self, mock_get, mock_gen_diff):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="test", seed=42)
        assert result.seed == 42

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_with_negative_prompt(self, mock_get, mock_gen_diff):
        """A negative_prompt is passed through as negative_conditioning."""
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="explosion", duration=1.0, steps=10, negative_prompt="music, reverb")

        # negative_conditioning kwarg is forwarded to generate_diffusion_cond.
        kwargs = mock_gen_diff.call_args.kwargs
        assert "negative_conditioning" in kwargs
        neg = kwargs["negative_conditioning"]
        assert isinstance(neg, list) and len(neg) == 1
        assert neg[0]["prompt"] == "music, reverb"
        # And the result echoes the effective negative prompt back.
        assert result.negative_prompt == "music, reverb"

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_without_negative_prompt_omits_kwarg(self, mock_get, mock_gen_diff):
        """With no negative prompt, negative_conditioning is NOT forwarded (classic path)."""
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="test", duration=1.0, steps=10)

        kwargs = mock_gen_diff.call_args.kwargs
        assert "negative_conditioning" not in kwargs
        assert result.negative_prompt is None

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_empty_negative_prompt_omits_kwarg(self, mock_get, mock_gen_diff):
        """An empty/whitespace negative prompt is treated as 'no negative'."""
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        gen.generate(prompt="test", duration=1.0, steps=10, negative_prompt="   ")

        kwargs = mock_gen_diff.call_args.kwargs
        assert "negative_conditioning" not in kwargs

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_decodes_latents_via_pretransform(self, mock_get, mock_gen_diff):
        """Com pretransform, pede latents e decodifica fora do sampler (fallback OOM)."""

        class FakePretransform(torch.nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.scale = torch.nn.Parameter(torch.ones(1))
                self.chunked = False

            def decode(self, z: torch.Tensor) -> torch.Tensor:
                return z.repeat_interleave(4, dim=-1)  # latent 4x upsample fake

        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = FakePretransform()
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 1024)

        gen = AudioGenerator(device="cpu", auto_clear=False, chunked_vae=False)
        result = gen.generate(prompt="test", duration=1.0, steps=10)

        assert mock_gen_diff.call_args.kwargs["return_latents"] is True
        assert result.audio.shape == (2, 4096)


class TestDefaults:
    def test_default_values(self):
        assert DEFAULT_STEPS == 100
        assert DEFAULT_CFG_SCALE == 7.0
        assert DEFAULT_DURATION == 30.0
        assert DEFAULT_SIGMA_MIN == 0.3
        assert DEFAULT_SIGMA_MAX == 500.0
        assert DEFAULT_SAMPLER == "dpmpp-3m-sde"


class TestShouldUseHalf:
    def test_returns_true_below_8_5_gib(self):
        props = MagicMock()
        props.total_memory = 8 * 1024**3
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            mock_torch.cuda.get_device_properties.return_value = props
            assert AudioGenerator._should_use_half() is True

    def test_returns_false_at_or_above_8_5_gib(self):
        props = MagicMock()
        props.total_memory = 12 * 1024**3
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            mock_torch.cuda.get_device_properties.return_value = props
            assert AudioGenerator._should_use_half() is False

    def test_returns_false_no_cuda(self):
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = False
            assert AudioGenerator._should_use_half() is False


class TestShouldUseHalfExceptionPath:
    def test_returns_false_on_exception(self):
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            mock_torch.cuda.get_device_properties.side_effect = RuntimeError("fail")
            assert AudioGenerator._should_use_half() is False


class TestHalfPrecisionDecoupled:
    # half agora vem do perfil hw-auto (text2sound.hardware); os specs são
    # mockados na fonte (aigamekit_shared.hardware) para o teste não depender
    # da GPU real da máquina.
    def test_fp16_fires_on_small_gpu(self):
        with patch("aigamekit_shared.hardware.cuda_gpu_specs", return_value=[(0, 6 * 1024**3)]):
            gen = AudioGenerator(device="cuda")
        assert gen._half is True
        assert gen._chunked_vae is True

    def test_fp16_stays_off_on_large_gpu(self):
        with patch("aigamekit_shared.hardware.cuda_gpu_specs", return_value=[(0, 16 * 1024**3)]):
            gen = AudioGenerator(device="cuda")
        assert gen._half is False
        assert gen._chunked_vae is False


class TestNoCpuOffload:
    def test_try_cpu_offload_not_on_class(self):
        assert not hasattr(AudioGenerator, "_try_cpu_offload")

    def test_try_cpu_offload_not_on_instance(self):
        gen = AudioGenerator(device="cpu")
        assert not hasattr(gen, "_try_cpu_offload")


class TestSingletonCacheKey:
    def test_same_flags_reuses_instance(self):
        inst1 = AudioGenerator.get_instance(model_id="m1", device="cpu", half_precision=False)
        inst2 = AudioGenerator.get_instance(model_id="m1", device="cpu", half_precision=False)
        assert inst1 is inst2

    def test_different_half_precision_recreates(self):
        inst1 = AudioGenerator.get_instance(model_id="m1", device="cpu", half_precision=False)
        inst2 = AudioGenerator.get_instance(model_id="m1", device="cpu", half_precision=True)
        assert inst1 is not inst2
        assert inst2._half is True

    def test_different_gpu_ids_recreates(self):
        inst1 = AudioGenerator.get_instance(model_id="m1", device="cpu", gpu_ids=None)
        inst2 = AudioGenerator.get_instance(model_id="m1", device="cpu", gpu_ids=[0, 1])
        assert inst1 is not inst2


class TestPretransformFp32:
    @patch("text2sound.generator.get_pretrained_model")
    def test_half_restores_pretransform_to_fp32(self, mock_get):
        mock_pretransform = MagicMock()
        mock_model = MagicMock()
        mock_model.pretransform = mock_pretransform
        mock_model.half.return_value = mock_model
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        gen = AudioGenerator(device="cpu", half_precision=True)
        gen.load()
        mock_model.half.assert_called_once()
        mock_pretransform.float.assert_called_once()

    @patch("text2sound.generator.get_pretrained_model")
    def test_half_without_pretransform_does_not_error(self, mock_get):
        mock_model = MagicMock()
        del mock_model.pretransform
        mock_model.half.return_value = mock_model
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        gen = AudioGenerator(device="cpu", half_precision=True)
        gen.load()
        assert gen._loaded is True


class TestCropToDurationDeclick:
    """Crop do buffer SA3 (duration + headroom) ao -d pedido."""

    def test_crops_to_requested_duration(self):
        from text2sound.generator import _crop_to_duration_declick

        audio = torch.ones(1, 2, 44100 * 21)  # 21 s de buffer
        out = _crop_to_duration_declick(audio, 44100, 15.0)
        assert out.shape == (1, 2, 44100 * 15)

    def test_noop_when_shorter(self):
        from text2sound.generator import _crop_to_duration_declick

        audio = torch.ones(1, 2, 1000)
        out = _crop_to_duration_declick(audio, 44100, 30.0)
        assert out is audio  # sem corte devolve o tensor original

    def test_fade_applied_only_on_cut_edge(self):
        from text2sound.generator import _crop_to_duration_declick

        audio = torch.ones(1, 2, 44100 * 2)
        out = _crop_to_duration_declick(audio, 44100, 1.0)
        fade_len = int(0.01 * 44100)
        # Fora da zona de fade o sinal fica intacto; na borda desce até ~0.
        assert torch.allclose(out[..., : -fade_len - 1], torch.ones(1, 2, 44100 - fade_len - 1))
        assert out[..., -1].max().item() < 0.01


class TestGenerateSA3Path:
    """Caminho SA3 (diffusion_cond_inpaint): buffer cortado ao -d, sem sigmas."""

    def setup_method(self):
        AudioGenerator.reset_instance()

    def teardown_method(self):
        AudioGenerator.reset_instance()

    def _make_gen(self, mock_get):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_model.conditioner.conditioners.keys.return_value = ["prompt", "seconds_total"]
        mock_get.return_value = (
            mock_model,
            {"model_type": "diffusion_cond_inpaint", "sample_rate": 44100, "sample_size": 5292032},
        )
        return AudioGenerator(device="cpu", auto_clear=False)

    @patch("stable_audio_tools.inference.generation.generate_diffusion_cond_inpaint")
    @patch("text2sound.generator.get_pretrained_model")
    def test_sa3_uses_inpaint_and_crops_buffer(self, mock_get, mock_inpaint):
        # Buffer simulado: 15 s pedidos + 6 s de headroom (21 s no total)
        mock_inpaint.return_value = torch.randn(1, 2, 44100 * 21)
        gen = self._make_gen(mock_get)
        result = gen.generate(prompt="battle theme", duration=15.0, steps=8)

        mock_inpaint.assert_called_once()
        assert result.audio.shape == (2, 44100 * 15)

    @patch("stable_audio_tools.inference.generation.generate_diffusion_cond_inpaint")
    @patch("text2sound.generator.get_pretrained_model")
    def test_sa3_no_sigma_leak_and_no_seconds_start(self, mock_get, mock_inpaint):
        mock_inpaint.return_value = torch.randn(1, 2, 44100 * 3)
        gen = self._make_gen(mock_get)
        gen.generate(prompt="laser", duration=2.0, steps=8, sampler_type="pingpong")

        kwargs = mock_inpaint.call_args.kwargs
        assert "sigma_min" not in kwargs and "sigma_max" not in kwargs
        assert kwargs["sampler_type"] == "pingpong"
        cond = kwargs["conditioning"][0]
        assert "seconds_start" not in cond
        assert cond["seconds_total"] == 2.0

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_legacy_path_keeps_sigmas_and_seconds_start(self, mock_get, mock_gen_diff):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_model.pretransform = None
        mock_model.conditioner.conditioners.keys.return_value = ["prompt", "seconds_start", "seconds_total"]
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 2097152})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        gen.generate(prompt="forest", duration=1.0, steps=10)

        kwargs = mock_gen_diff.call_args.kwargs
        assert kwargs["sigma_min"] == 0.3 and kwargs["sigma_max"] == 500.0
        assert kwargs["conditioning"][0]["seconds_start"] == 0

    @patch("stable_audio_tools.inference.generation.generate_diffusion_cond_inpaint")
    @patch("text2sound.generator.get_pretrained_model")
    def test_sa3_negative_prompt_skipped_at_cfg1(self, mock_get, mock_inpaint):
        mock_inpaint.return_value = torch.randn(1, 2, 44100 * 3)
        gen = self._make_gen(mock_get)
        result = gen.generate(prompt="boom", duration=2.0, steps=8, cfg_scale=1.0, negative_prompt="music")

        assert "negative_conditioning" not in mock_inpaint.call_args.kwargs
        assert result.negative_prompt is None


class TestPlacementCpuFallback:
    """Offload plan no-op (modelo fica na CPU) → degrada para CPU em vez de crash."""

    @patch("text2sound.generator.get_pretrained_model")
    def test_broken_offload_plan_falls_back_to_cpu(self, mock_get):
        import torch
        from torch import nn

        mock_model = nn.Linear(4, 4)  # nasce e fica na CPU
        mock_model.diffusion_objective = "rf_denoiser"
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        plan = MagicMock()
        plan.offload = "group_stream"
        plan.multi_gpu_ids = None

        with (
            patch("aigamekit_shared.hardware.cuda_gpu_free_specs", return_value=[(0, 8 * 1024**3)]),
            patch("aigamekit_shared.lowvram.place_pipeline", return_value=plan),
        ):
            gen = AudioGenerator(device="cuda", auto_clear=False, half_precision=True)
            gen.load()

        assert gen.device == "cpu"
        assert gen.half_precision is False
        assert next(gen._model.parameters()).dtype == torch.float32

    @patch("text2sound.generator.get_pretrained_model")
    def test_full_gpu_plan_keeps_cuda(self, mock_get):
        from torch import nn

        mock_model = nn.Linear(4, 4)
        mock_model.diffusion_objective = "rf_denoiser"
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        def fake_place(model, *a, **k):
            model.cuda()
            return MagicMock(offload="none", multi_gpu_ids=None)

        with (
            patch("aigamekit_shared.hardware.cuda_gpu_free_specs", return_value=[(0, 8 * 1024**3)]),
            patch("aigamekit_shared.lowvram.place_pipeline", side_effect=fake_place),
        ):
            gen = AudioGenerator(device="cuda", auto_clear=False, half_precision=False)
            gen.load()

        assert gen.device.startswith("cuda")
