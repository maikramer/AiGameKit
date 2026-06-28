"""Tests for gamedev_shared.quantization — quant config dispatcher and VRAM helpers."""

from __future__ import annotations

import builtins
import os
from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.quantization import (
    apply_torch_compile,
    enable_attention_optimizations,
    enable_model_cpu_offload_optimized,
    enable_vae_optimizations,
    format_quantization_info,
    get_gpu_compute_capability,
    get_quantization_config,
    get_suggested_quantization_for_vram,
    get_torch_compile_recommendation,
    is_bitsandbytes_available,
    is_quanto_available,
    is_sdnq_available,
    is_torchao_available,
    set_memory_optimization_env,
    suggest_environment_variables,
    supports_fp8,
)


def _block_import(blocked_name: str):
    real_import = builtins.__import__

    def fake_import(name: str, *args: object, **kwargs: object):
        if name == blocked_name:
            raise ImportError(f"mocked: {blocked_name} unavailable")
        return real_import(name, *args, **kwargs)

    return patch("builtins.__import__", side_effect=fake_import)


class TestAvailabilityGuards:
    def test_bitsandbytes_unavailable(self):
        with _block_import("bitsandbytes"):
            assert is_bitsandbytes_available() is False

    def test_bitsandbytes_available(self):
        assert is_bitsandbytes_available() is True

    def test_torchao_unavailable(self):
        with _block_import("torchao"):
            assert is_torchao_available() is False

    def test_torchao_available(self):
        assert is_torchao_available() is True

    def test_quanto_unavailable(self):
        with _block_import("optimum"):
            assert is_quanto_available() is False

    def test_quanto_available(self):
        assert is_quanto_available() is True

    def test_sdnq_unavailable(self):
        with patch("gamedev_shared.sdnq.is_available", return_value=False):
            assert is_sdnq_available() is False

    def test_sdnq_available(self):
        assert is_sdnq_available() is True


class TestGetGpuComputeCapability:
    def test_returns_major_minor_tuple(self):
        props = MagicMock()
        props.major = 8
        props.minor = 9
        with (
            patch("torch.cuda.is_available", return_value=True),
            patch("torch.cuda.get_device_properties", return_value=props),
        ):
            assert get_gpu_compute_capability() == (8, 9)

    def test_returns_none_when_cuda_absent(self):
        with patch("torch.cuda.is_available", return_value=False):
            assert get_gpu_compute_capability() is None


class TestSupportsFp8:
    @patch("gamedev_shared.quantization.get_gpu_compute_capability", return_value=(8, 9))
    def test_true_for_ada_lovelace(self, _mock):
        assert supports_fp8() is True

    @patch("gamedev_shared.quantization.get_gpu_compute_capability", return_value=(9, 0))
    def test_true_for_hopper(self, _mock):
        assert supports_fp8() is True

    @patch("gamedev_shared.quantization.get_gpu_compute_capability", return_value=(8, 6))
    def test_false_for_ampere(self, _mock):
        assert supports_fp8() is False

    @patch("gamedev_shared.quantization.get_gpu_compute_capability", return_value=None)
    def test_false_when_no_gpu(self, _mock):
        assert supports_fp8() is False


class TestGetQuantizationConfig:
    def test_none_mode_returns_none(self):
        assert get_quantization_config("none") is None

    @patch("gamedev_shared.quantization.is_bitsandbytes_available", return_value=False)
    def test_auto_bnb_unavailable_returns_none(self, _mock):
        assert get_quantization_config("auto") is None

    @patch("gamedev_shared.quantization.supports_fp8", return_value=True)
    def test_fp8_mode(self, _mock):
        assert get_quantization_config("fp8") == {"type": "fp8", "compute_dtype": "float16"}

    @patch("gamedev_shared.quantization.supports_fp8", return_value=True)
    def test_fp8_mode_custom_dtype(self, _mock):
        cfg = get_quantization_config("fp8", compute_dtype="bfloat16")
        assert cfg == {"type": "fp8", "compute_dtype": "bfloat16"}

    @patch("gamedev_shared.quantization.supports_fp8", return_value=True)
    def test_auto_resolves_fp8_when_supported(self, _mock):
        assert get_quantization_config("auto") == {"type": "fp8", "compute_dtype": "float16"}

    @patch("gamedev_shared.quantization.is_bitsandbytes_available", return_value=True)
    def test_int4_mode(self, _mock):
        cfg = get_quantization_config("int4")
        assert cfg["type"] == "bitsandbytes-4bit"
        assert "config" in cfg

    @patch("gamedev_shared.quantization.is_bitsandbytes_available", return_value=True)
    def test_4bit_alias(self, _mock):
        cfg = get_quantization_config("4bit")
        assert cfg["type"] == "bitsandbytes-4bit"

    @patch("gamedev_shared.quantization.is_bitsandbytes_available", return_value=True)
    def test_int8_mode(self, _mock):
        cfg = get_quantization_config("int8")
        assert cfg["type"] == "bitsandbytes-8bit"
        assert "config" in cfg

    @patch("gamedev_shared.quantization.is_bitsandbytes_available", return_value=True)
    def test_8bit_alias(self, _mock):
        cfg = get_quantization_config("8bit")
        assert cfg["type"] == "bitsandbytes-8bit"

    @patch("gamedev_shared.quantization.is_quanto_available", return_value=True)
    def test_quanto_int8(self, _mock):
        cfg = get_quantization_config("quanto-int8")
        assert cfg["type"] == "quanto-int8"
        assert "config" in cfg

    @patch("gamedev_shared.quantization.is_quanto_available", return_value=True)
    def test_quanto_int4(self, _mock):
        cfg = get_quantization_config("quanto-int4")
        assert cfg["type"] == "quanto-int4"
        assert "config" in cfg

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=True)
    def test_sdnq_valid_preset(self, _mock):
        cfg = get_quantization_config("sdnq-uint8")
        assert cfg["type"] == "sdnq-uint8"
        assert "config" in cfg

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=True)
    def test_sdnq_unknown_preset_returns_none(self, _mock):
        assert get_quantization_config("sdnq-unknown") is None

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    def test_sdnq_unavailable_returns_none(self, _mock):
        assert get_quantization_config("sdnq-uint8") is None

    def test_invalid_mode_returns_none(self):
        assert get_quantization_config("totally-bogus") is None

    @patch("gamedev_shared.quantization.supports_fp8", return_value=True)
    def test_mode_normalized_lower_strip(self, _mock):
        assert get_quantization_config("  FP8 ") == {"type": "fp8", "compute_dtype": "float16"}


class TestFormatQuantizationInfo:
    def test_none_config(self):
        assert format_quantization_info(None) == "sem quantização (FP16/FP32)"

    @pytest.mark.parametrize(
        "qtype,expected",
        [
            ("fp8", "FP8 (8-bit floating point)"),
            ("bitsandbytes-4bit", "BitsAndBytes 4-bit (NF4)"),
            ("bitsandbytes-8bit", "BitsAndBytes 8-bit"),
            ("quanto-int8", "Quanto INT8"),
            ("quanto-int4", "Quanto INT4"),
            ("sdnq-int8", "SDNQ INT8 (modern)"),
            ("sdnq-uint8", "SDNQ UINT8 (modern unsigned)"),
            ("sdnq-int4", "SDNQ INT4 (modern aggressive)"),
        ],
    )
    def test_known_keys(self, qtype, expected):
        assert format_quantization_info({"type": qtype}) == expected

    def test_unknown_key_fallback(self):
        assert format_quantization_info({"type": "weird"}) == "Quantização: weird"

    def test_missing_type_key(self):
        assert format_quantization_info({}) == "Quantização: unknown"


class TestTorchCompileRecommendation:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("unet", {"mode": "reduce-overhead", "fullgraph": False, "dynamic": True}),
            ("transformer", {"mode": "reduce-overhead", "fullgraph": False, "dynamic": True}),
            ("vae", {"mode": "default", "fullgraph": False, "dynamic": False}),
            ("unknown", {"mode": "reduce-overhead", "fullgraph": False}),
        ],
    )
    def test_recommendation(self, name, expected):
        assert get_torch_compile_recommendation(name) == expected

    def test_case_insensitive(self):
        assert get_torch_compile_recommendation("UNET") == {
            "mode": "reduce-overhead",
            "fullgraph": False,
            "dynamic": True,
        }


class TestSuggestEnvironmentVariables:
    @pytest.mark.parametrize(
        "vram_gb,expected",
        [
            (
                4,
                {
                    "PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:128",
                    "CUDA_LAUNCH_BLOCKING": "0",
                },
            ),
            (
                12,
                {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True,max_split_size_mb:512"},
            ),
            (24, {"PYTORCH_CUDA_ALLOC_CONF": "expandable_segments:True"}),
        ],
    )
    def test_vram_tiers(self, vram_gb, expected):
        assert suggest_environment_variables(vram_gb) == expected

    def test_low_vram_under_8(self):
        s = suggest_environment_variables(2)
        assert "max_split_size_mb:128" in s["PYTORCH_CUDA_ALLOC_CONF"]
        assert s["CUDA_LAUNCH_BLOCKING"] == "0"

    def test_high_vram_no_cuda_blocking(self):
        assert "CUDA_LAUNCH_BLOCKING" not in suggest_environment_variables(32)


class TestSetMemoryOptimizationEnv:
    def test_sets_expandable_segments_when_empty(self, monkeypatch):
        monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
        monkeypatch.delenv("CUBLAS_WORKSPACE_CONFIG", raising=False)
        set_memory_optimization_env()
        assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"
        assert os.environ["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"

    def test_appends_to_existing_value(self, monkeypatch):
        monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "custom:True")
        set_memory_optimization_env()
        assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "custom:True,expandable_segments:True"

    def test_existing_not_duplicated(self, monkeypatch):
        monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        set_memory_optimization_env()
        assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"

    def test_disable_expandable_segments(self, monkeypatch):
        monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
        set_memory_optimization_env(enable_expandable_segments=False)
        assert "PYTORCH_CUDA_ALLOC_CONF" not in os.environ
        assert os.environ["CUBLAS_WORKSPACE_CONFIG"] == ":4096:8"


class TestGetSuggestedQuantizationForVram:
    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    @patch("gamedev_shared.quantization.supports_fp8", return_value=False)
    def test_large_vram_returns_none(self, _fp8, _sdnq):
        assert get_suggested_quantization_for_vram(24) == "none"

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    @patch("gamedev_shared.quantization.supports_fp8", return_value=True)
    def test_mid_vram_fp8(self, _fp8, _sdnq):
        assert get_suggested_quantization_for_vram(2) == "fp8"

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    @patch("gamedev_shared.quantization.supports_fp8", return_value=False)
    def test_low_vram_int8(self, _fp8, _sdnq):
        assert get_suggested_quantization_for_vram(1.6) == "int8"

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    @patch("gamedev_shared.quantization.supports_fp8", return_value=False)
    def test_tiny_vram_int4(self, _fp8, _sdnq):
        assert get_suggested_quantization_for_vram(1) == "int4"

    @patch("gamedev_shared.sdnq.suggest_preset_for_vram", return_value="sdnq-uint8")
    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=True)
    def test_sdnq_available_delegates(self, _sdnq, _suggest):
        assert get_suggested_quantization_for_vram(2) == "sdnq-uint8"

    @patch("gamedev_shared.quantization.is_sdnq_available", return_value=False)
    @patch("gamedev_shared.quantization.supports_fp8", return_value=False)
    def test_large_model_raises_required_threshold(self, _fp8, _sdnq):
        assert get_suggested_quantization_for_vram(24, model_size_gb=16) == "none"


class TestEnableVaeOptimizations:
    def test_enable_slicing_and_tiling(self):
        vae = MagicMock()
        enable_vae_optimizations(vae)
        vae.enable_slicing.assert_called_once_with()
        vae.enable_tiling.assert_called_once_with(tile_sample_min_size=256)

    def test_custom_tile_size(self):
        vae = MagicMock()
        enable_vae_optimizations(vae, tile_sample_min_size=512)
        vae.enable_tiling.assert_called_once_with(tile_sample_min_size=512)

    def test_tiling_typeerror_fallback(self):
        vae = MagicMock()

        def tiling(*args, **kwargs):
            if kwargs:
                raise TypeError("unexpected keyword arg")

        vae.enable_tiling.side_effect = tiling
        enable_vae_optimizations(vae, tile_sample_min_size=256)
        assert vae.enable_tiling.call_count == 2
        vae.enable_slicing.assert_called_once_with()

    def test_tile_min_size_zero_uses_no_arg(self):
        vae = MagicMock()
        enable_vae_optimizations(vae, tile_sample_min_size=0)
        vae.enable_tiling.assert_called_once_with()

    def test_disabled_flags(self):
        vae = MagicMock()
        enable_vae_optimizations(vae, enable_slicing=False, enable_tiling=False)
        vae.enable_slicing.assert_not_called()
        vae.enable_tiling.assert_not_called()

    def test_missing_methods_no_crash(self):
        vae = MagicMock(spec=[])
        enable_vae_optimizations(vae)


class TestEnableAttentionOptimizations:
    def test_auto_slicing(self):
        pipe = MagicMock()
        enable_attention_optimizations(pipe)
        pipe.enable_attention_slicing.assert_called_once_with()
        pipe.vae.enable_slicing.assert_called_once_with()

    def test_explicit_size(self):
        pipe = MagicMock()
        enable_attention_optimizations(pipe, slicing_size=2)
        pipe.enable_attention_slicing.assert_called_once_with(2)

    def test_disabled(self):
        pipe = MagicMock()
        enable_attention_optimizations(pipe, enable_slicing=False)
        pipe.enable_attention_slicing.assert_not_called()


class TestEnableModelCpuOffloadOptimized:
    def test_default_model_offload(self):
        pipe = MagicMock()
        enable_model_cpu_offload_optimized(pipe)
        pipe.enable_model_cpu_offload.assert_called_once_with(device="cuda")
        pipe.enable_sequential_cpu_offload.assert_not_called()

    def test_sequential_offload(self):
        pipe = MagicMock()
        enable_model_cpu_offload_optimized(pipe, use_sequential=True)
        pipe.enable_sequential_cpu_offload.assert_called_once_with(device="cuda")
        pipe.enable_model_cpu_offload.assert_not_called()

    def test_custom_device(self):
        pipe = MagicMock()
        enable_model_cpu_offload_optimized(pipe, device="cpu")
        pipe.enable_model_cpu_offload.assert_called_once_with(device="cpu")


class TestApplyTorchCompile:
    def test_compiles_when_available(self):
        model = MagicMock()
        compiled = MagicMock()
        with patch("torch.compile", return_value=compiled) as mock_compile:
            result = apply_torch_compile(model)
        assert result is compiled
        mock_compile.assert_called_once_with(model, mode="reduce-overhead", fullgraph=False)

    def test_custom_mode_and_fullgraph(self):
        model = MagicMock()
        compiled = MagicMock()
        with patch("torch.compile", return_value=compiled) as mock_compile:
            result = apply_torch_compile(model, mode="max-autotune", fullgraph=True)
        assert result is compiled
        mock_compile.assert_called_once_with(model, mode="max-autotune", fullgraph=True)

    def test_returns_original_on_exception(self):
        model = MagicMock()
        with patch("torch.compile", side_effect=RuntimeError("compile failed")):
            assert apply_torch_compile(model) is model

    def test_returns_original_when_torch_unimportable(self):
        model = MagicMock()
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            assert apply_torch_compile(model) is model
