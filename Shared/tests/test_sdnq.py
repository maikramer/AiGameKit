"""Tests for gamedev_shared.sdnq — centralized SDNQ module."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.sdnq import (
    DEFAULT_PRESET,
    PRESETS,
    estimate_vram_mb,
    is_available,
    suggest_preset_for_vram,
)


class TestPresets:
    def test_all_presets_have_required_fields(self):
        for name, preset in PRESETS.items():
            assert preset.name == name
            assert preset.weights_dtype in ("uint8", "int8", "int4", "fp8")
            assert preset.group_size >= 0
            assert isinstance(preset.use_svd, bool)
            assert preset.dequantize_fp32 is True
            assert preset.description

    def test_default_preset_exists(self):
        assert DEFAULT_PRESET in PRESETS
        assert DEFAULT_PRESET == "sdnq-uint8"

    def test_preset_is_frozen(self):
        p = PRESETS["sdnq-uint8"]
        with pytest.raises(AttributeError):
            p.weights_dtype = "fp32"  # type: ignore[misc]

    def test_preset_names_match_keys(self):
        for key, preset in PRESETS.items():
            assert preset.name == key

    def test_int4_has_group_size_and_svd(self):
        p = PRESETS["sdnq-int4"]
        assert p.group_size > 0
        assert p.use_svd is True

    def test_uint8_no_svd(self):
        p = PRESETS["sdnq-uint8"]
        assert p.use_svd is False
        assert p.group_size == 0

    def test_int8_no_svd(self):
        p = PRESETS["sdnq-int8"]
        assert p.use_svd is False
        assert p.group_size == 0


class TestIsAvailable:
    def test_returns_false_when_sdnq_not_installed(self):
        with patch.dict(sys.modules, {"sdnq": None}):
            assert is_available() is False

    def test_returns_true_when_sdnq_importable(self):
        fake_sdnq = types.ModuleType("sdnq")
        fake_sdnq.SDNQConfig = MagicMock()  # type: ignore[attr-defined]
        with patch.dict(sys.modules, {"sdnq": fake_sdnq}):
            assert is_available() is True


class TestCreateConfig:
    def _setup_sdnq_mock(self) -> tuple[types.ModuleType, types.ModuleType]:
        fake_sdnq = types.ModuleType("sdnq")
        fake_sdnq.SDNQConfig = MagicMock()  # type: ignore[attr-defined]
        fake_common = types.ModuleType("sdnq.common")
        fake_common.use_torch_compile = False  # type: ignore[attr-defined]
        fake_sdnq.common = fake_common  # type: ignore[attr-defined]
        return fake_sdnq, fake_common

    def test_creates_config_from_default_preset(self):
        from gamedev_shared.sdnq import create_config

        fake_sdnq, fake_common = self._setup_sdnq_mock()
        with patch.dict(sys.modules, {"sdnq": fake_sdnq, "sdnq.common": fake_common}):
            create_config()
            fake_sdnq.SDNQConfig.assert_called_once()  # type: ignore[attr-defined]
            call_kwargs = fake_sdnq.SDNQConfig.call_args[1]  # type: ignore[attr-defined]
            assert call_kwargs["weights_dtype"] == "uint8"

    def test_raises_on_unknown_preset(self):
        from gamedev_shared.sdnq import create_config

        with pytest.raises(KeyError, match="Unknown SDNQ preset"):
            create_config("nonexistent")

    def test_overrides_forwarded(self):
        from gamedev_shared.sdnq import create_config

        fake_sdnq, fake_common = self._setup_sdnq_mock()
        with patch.dict(sys.modules, {"sdnq": fake_sdnq, "sdnq.common": fake_common}):
            create_config("sdnq-uint8", quantization_device="cpu", custom_flag=True)
            call_kwargs = fake_sdnq.SDNQConfig.call_args[1]  # type: ignore[attr-defined]
            assert call_kwargs["quantization_device"] == "cpu"
            assert call_kwargs["custom_flag"] is True


class TestQuantizeModel:
    def test_delegates_to_sdnq_post_load_quant(self):
        from gamedev_shared.sdnq import quantize_model

        fake_model = MagicMock()
        fake_result = MagicMock()

        fake_sdnq = types.ModuleType("sdnq")
        fake_sdnq.sdnq_post_load_quant = MagicMock(return_value=fake_result)  # type: ignore[attr-defined]
        fake_sdnq.SDNQConfig = MagicMock()  # type: ignore[attr-defined]
        fake_common = types.ModuleType("sdnq.common")
        fake_common.use_torch_compile = False  # type: ignore[attr-defined]

        with patch.dict(sys.modules, {"sdnq": fake_sdnq, "sdnq.common": fake_common}):
            result = quantize_model(fake_model, preset="sdnq-int8")
            assert result is fake_result
            fake_sdnq.sdnq_post_load_quant.assert_called_once()  # type: ignore[attr-defined]


class TestApplyQuantizedMatmul:
    def test_noop_when_disabled(self):
        from gamedev_shared.sdnq import apply_quantized_matmul

        pipe = MagicMock()
        apply_quantized_matmul(pipe, enabled=False)
        pipe.assert_not_called()

    def test_skips_modules_when_triton_missing(self):
        from gamedev_shared.sdnq import apply_quantized_matmul

        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = MagicMock()  # type: ignore[attr-defined]
        fake_torch.cuda.is_available = MagicMock(return_value=True)  # type: ignore[attr-defined]
        fake_torch.xpu = MagicMock()  # type: ignore[attr-defined]
        fake_torch.xpu.is_available = MagicMock(return_value=False)  # type: ignore[attr-defined]

        fake_loader = types.ModuleType("sdnq.loader")

        def _apply(_mod, **kw):
            raise RuntimeError("SDNQ Quantized MatMul requires a working Triton install.")

        fake_loader.apply_sdnq_options_to_model = _apply  # type: ignore[attr-defined]

        tr = MagicMock()
        te = MagicMock()
        pipe = MagicMock()
        pipe.transformer = tr
        pipe.text_encoder = te
        pipe.text_encoder_2 = None

        with patch.dict(sys.modules, {"torch": fake_torch, "sdnq.loader": fake_loader}):
            apply_quantized_matmul(pipe, enabled=True)

        assert pipe.transformer is tr
        assert pipe.text_encoder is te


class TestEstimateVramMb:
    def test_fp16_baseline(self):
        result = estimate_vram_mb(1000.0, "sdnq-uint8")
        assert result > 0.0
        assert result < 1000.0

    def test_int4_compresses_more(self):
        uint8_vram = estimate_vram_mb(1000.0, "sdnq-uint8")
        int4_vram = estimate_vram_mb(1000.0, "sdnq-int4")
        assert int4_vram < uint8_vram

    def test_unknown_preset_fallback(self):
        result = estimate_vram_mb(1000.0, "nonexistent")
        assert result == 1000.0 * 1.5

    def test_all_presets_reduce_vram(self):
        for name in PRESETS:
            result = estimate_vram_mb(1000.0, name)
            assert result < 1000.0 * 1.5


class TestSuggestPresetForVram:
    def test_high_vram_suggests_uint8(self):
        assert suggest_preset_for_vram(12.0) == "sdnq-uint8"

    def test_medium_vram_suggests_uint8(self):
        assert suggest_preset_for_vram(8.0) == "sdnq-uint8"

    def test_low_vram_suggests_int4(self):
        assert suggest_preset_for_vram(4.0) == "sdnq-int4"

    def test_very_low_vram_suggests_int4(self):
        assert suggest_preset_for_vram(2.0) == "sdnq-int4"


class TestPreQuantizeModel:
    def test_saves_model_and_metadata(self, tmp_path: Path):
        from gamedev_shared.sdnq import pre_quantize_model

        fake_model = MagicMock()
        fake_model.state_dict.return_value = {"weight": MagicMock()}
        fake_quantized = MagicMock()
        fake_quantized.state_dict.return_value = {"weight": MagicMock()}

        fake_sdnq = types.ModuleType("sdnq")
        fake_sdnq.SDNQConfig = MagicMock()  # type: ignore[attr-defined]
        fake_sdnq.sdnq_post_load_quant = MagicMock(return_value=fake_quantized)  # type: ignore[attr-defined]
        fake_common = types.ModuleType("sdnq.common")
        fake_common.use_torch_compile = False  # type: ignore[attr-defined]

        fake_safetensors = types.ModuleType("safetensors")
        fake_safetensors.torch = MagicMock()  # type: ignore[attr-defined]
        fake_safetensors.torch.save_file = MagicMock()  # type: ignore[attr-defined]

        fake_torch = types.ModuleType("torch")
        fake_torch.cuda = MagicMock()  # type: ignore[attr-defined]
        fake_torch.cuda.is_available = MagicMock(return_value=False)  # type: ignore[attr-defined]

        modules = {
            "sdnq": fake_sdnq,
            "sdnq.common": fake_common,
            "safetensors": fake_safetensors,
            "safetensors.torch": fake_safetensors.torch,  # type: ignore[attr-defined]
            "torch": fake_torch,
        }
        with patch.dict(sys.modules, modules):
            output = pre_quantize_model(fake_model, tmp_path / "out", preset="sdnq-uint8")
            assert (output / "quantization_meta.json").exists()
            meta = json.loads((output / "quantization_meta.json").read_text())
            assert meta["quantization"] == "sdnq-uint8"
            assert meta["preset"] == "sdnq-uint8"
