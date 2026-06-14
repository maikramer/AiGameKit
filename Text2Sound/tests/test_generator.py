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
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})

        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="test sound", duration=1.0, steps=10)

        assert isinstance(result, GenerationResult)
        assert result.prompt == "test sound"
        assert result.duration == 1.0
        assert result.steps == 10
        assert result.audio.shape[0] == 2

    @patch("text2sound.generator.generate_diffusion_cond")
    @patch("text2sound.generator.get_pretrained_model")
    def test_generate_with_seed(self, mock_get, mock_gen_diff):
        mock_model = MagicMock()
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        mock_gen_diff.return_value = torch.randn(1, 2, 44100)

        gen = AudioGenerator(device="cpu", auto_clear=False)
        result = gen.generate(prompt="test", seed=42)
        assert result.seed == 42


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
    def test_fp16_fires_without_low_vram_on_small_gpu(self):
        props = MagicMock()
        props.total_memory = 8 * 1024**3
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            mock_torch.cuda.get_device_properties.return_value = props
            gen = AudioGenerator(device="cuda", low_vram=False)
        assert gen._half is True

    def test_fp16_stays_off_on_large_gpu_without_low_vram(self):
        props = MagicMock()
        props.total_memory = 12 * 1024**3
        with patch("text2sound.generator.torch") as mock_torch:
            mock_torch.cuda.is_available.return_value = True
            mock_torch.cuda.get_device_properties.return_value = props
            gen = AudioGenerator(device="cuda", low_vram=False)
        assert gen._half is False


class TestCpuOffloadFallback:
    @patch("text2sound.generator.get_pretrained_model")
    def test_cpu_offload_logs_warning_when_unsupported(self, mock_get):
        mock_model = MagicMock()
        mock_model.half.return_value = mock_model
        mock_model.to.return_value = mock_model
        del mock_model.enable_model_cpu_offload  # no such method
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        gen = AudioGenerator(device="cuda", low_vram=True)
        gen.load()
        assert gen._loaded is True

    @patch("text2sound.generator.get_pretrained_model")
    def test_cpu_offload_called_when_supported(self, mock_get):
        mock_model = MagicMock()
        mock_model.half.return_value = mock_model
        mock_model.to.return_value = mock_model
        mock_get.return_value = (mock_model, {"sample_rate": 44100, "sample_size": 65536})
        gen = AudioGenerator(device="cuda", low_vram=True)
        gen.load()
        mock_model.enable_model_cpu_offload.assert_called_once()
        assert gen._loaded is True
