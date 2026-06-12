"""Testes das otimizações de pipeline: knobs de config, colocação de modelos e SDPA."""

from __future__ import annotations

import importlib

import pytest

from paint3d import defaults as _defaults
from paint3d.painter import (
    _apply_optimization_config,
    _auto_dino_device,
    _auto_esrgan_device,
    _env_flag,
)


class _FakeConfig:
    pass


class TestEnvFlag:
    def test_default_passthrough(self, monkeypatch):
        monkeypatch.delenv("PAINT3D_TEST_FLAG", raising=False)
        assert _env_flag("PAINT3D_TEST_FLAG", True) is True
        assert _env_flag("PAINT3D_TEST_FLAG", False) is False

    @pytest.mark.parametrize("value,expected", [("0", False), ("off", False), ("1", True), ("yes", True)])
    def test_env_overrides(self, monkeypatch, value, expected):
        monkeypatch.setenv("PAINT3D_TEST_FLAG", value)
        assert _env_flag("PAINT3D_TEST_FLAG", not expected) is expected


class TestOptimizationConfig:
    def test_low_vram_enables_chunking_and_offload(self, monkeypatch):
        monkeypatch.delenv("PAINT3D_CFG_CHUNKING", raising=False)
        monkeypatch.delenv("PAINT3D_OFFLOAD_REF_UNET", raising=False)
        monkeypatch.delenv("PAINT3D_DINO_DEVICE", raising=False)
        monkeypatch.delenv("PAINT3D_ESRGAN_DEVICE", raising=False)
        config = _FakeConfig()
        _apply_optimization_config(config, low_vram=True, gpu_ids=None)
        assert config.cfg_batch_chunking is True
        assert config.offload_ref_unet is True
        assert config.dino_device == "cpu"
        assert config.realesrgan_tile == _defaults.ESRGAN_TILE_LOW_VRAM

    def test_full_profile_disables_chunking(self, monkeypatch):
        monkeypatch.delenv("PAINT3D_CFG_CHUNKING", raising=False)
        monkeypatch.delenv("PAINT3D_OFFLOAD_REF_UNET", raising=False)
        config = _FakeConfig()
        _apply_optimization_config(config, low_vram=False, gpu_ids=None)
        assert config.cfg_batch_chunking is False
        assert config.offload_ref_unet is False
        assert config.realesrgan_tile == _defaults.ESRGAN_TILE

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("PAINT3D_CFG_CHUNKING", "0")
        monkeypatch.setenv("PAINT3D_OFFLOAD_REF_UNET", "0")
        config = _FakeConfig()
        _apply_optimization_config(config, low_vram=True, gpu_ids=None)
        assert config.cfg_batch_chunking is False
        assert config.offload_ref_unet is False

    def test_env_device_override(self, monkeypatch):
        monkeypatch.setenv("PAINT3D_DINO_DEVICE", "cuda:1")
        monkeypatch.setenv("PAINT3D_ESRGAN_DEVICE", "cpu")
        config = _FakeConfig()
        _apply_optimization_config(config, low_vram=True, gpu_ids=None)
        assert config.dino_device == "cuda:1"
        assert config.realesrgan_device == "cpu"


class TestDevicePlacement:
    def test_dino_low_vram_is_cpu(self):
        assert _auto_dino_device(True, None) == "cpu"
        assert _auto_dino_device(True, [0, 1]) == "cpu"

    def test_dino_multi_gpu_secondary(self, monkeypatch):
        import torch

        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert _auto_dino_device(False, [0, 1]) == "cuda:1"

    def test_esrgan_multi_gpu_secondary(self, monkeypatch):
        import torch

        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
        assert _auto_esrgan_device(False, [0, 1]) == "cuda:1"
        assert _auto_esrgan_device(True, [0, 1]) == "cuda"

    def test_no_cuda_everything_cpu(self, monkeypatch):
        import torch

        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert _auto_dino_device(False, None) == "cpu"
        assert _auto_esrgan_device(False, None) == "cpu"


class TestSdpaResolver:
    def _reload(self, monkeypatch, env_value):
        monkeypatch.setenv("PAINT3D_USE_SAGEATTN", env_value)
        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        return importlib.reload(attn_processor)

    def test_default_is_torch_sdpa(self, monkeypatch):
        import torch.nn.functional as F

        mod = self._reload(monkeypatch, "0")
        assert mod.scaled_dot_product_attention is F.scaled_dot_product_attention

    def test_enabled_without_package_falls_back(self, monkeypatch):
        import torch.nn.functional as F

        try:
            import sageattention  # noqa: F401

            pytest.skip("sageattention instalado — fallback não aplicável")
        except ImportError:
            pass
        mod = self._reload(monkeypatch, "1")
        assert mod.scaled_dot_product_attention is F.scaled_dot_product_attention

    def teardown_method(self):
        import os

        os.environ.pop("PAINT3D_USE_SAGEATTN", None)
        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        importlib.reload(attn_processor)
