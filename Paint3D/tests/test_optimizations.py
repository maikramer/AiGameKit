"""Testes das otimizações de pipeline: knobs de config, colocação de modelos e SDPA."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import Any

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
        _apply_optimization_config(config, memory_efficient=True, gpu_ids=None)
        assert config.cfg_batch_chunking is True
        assert config.offload_ref_unet is True
        assert config.dino_device == "cpu"
        assert config.realesrgan_tile == _defaults.ESRGAN_TILE_MEMORY_EFFICIENT

    def test_full_profile_disables_chunking(self, monkeypatch):
        monkeypatch.delenv("PAINT3D_CFG_CHUNKING", raising=False)
        monkeypatch.delenv("PAINT3D_OFFLOAD_REF_UNET", raising=False)
        config = _FakeConfig()
        _apply_optimization_config(config, memory_efficient=False, gpu_ids=None)
        assert config.cfg_batch_chunking is False
        assert config.offload_ref_unet is False
        assert config.realesrgan_tile == _defaults.ESRGAN_TILE

    def test_env_kill_switch(self, monkeypatch):
        monkeypatch.setenv("PAINT3D_CFG_CHUNKING", "0")
        monkeypatch.setenv("PAINT3D_OFFLOAD_REF_UNET", "0")
        config = _FakeConfig()
        _apply_optimization_config(config, memory_efficient=True, gpu_ids=None)
        assert config.cfg_batch_chunking is False
        assert config.offload_ref_unet is False

    def test_env_device_override(self, monkeypatch):
        monkeypatch.setenv("PAINT3D_DINO_DEVICE", "cuda:1")
        monkeypatch.setenv("PAINT3D_ESRGAN_DEVICE", "cpu")
        config = _FakeConfig()
        _apply_optimization_config(config, memory_efficient=True, gpu_ids=None)
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


class TestGroupOffloadPartialPlacement:
    """Group offload parcial (só um dos UNets) não pode largar o park do dual."""

    @staticmethod
    def _wrapper(unet: object, dual: object) -> Any:
        return SimpleNamespace(unet=unet, unet_dual=dual, offload_ref_unet=True)

    def test_active_requires_every_present_unet(self, monkeypatch) -> None:
        import paint3d.hy3dpaint.hunyuanpaintpbr.unet.modules as modules
        import paint3d.painter as painter

        monkeypatch.setattr(modules, "_group_offload_managed", lambda m: bool(getattr(m, "_managed", False)))
        unet = SimpleNamespace(_managed=True)
        dual = SimpleNamespace(_managed=False)
        # Só o unet gerido: o dual ainda precisa do park/offload custom.
        assert painter._paint_group_offload_active(self._wrapper(unet, dual)) is False
        dual._managed = True
        assert painter._paint_group_offload_active(self._wrapper(unet, dual)) is True
        # Sem dual (variante single-stream) basta o unet.
        assert painter._paint_group_offload_active(self._wrapper(unet, None)) is True
        assert painter._paint_group_offload_active(SimpleNamespace(unet=None, unet_dual=None)) is False

    def test_dual_without_hooks_keeps_offload_ref_unet(self, monkeypatch) -> None:
        """Se o ``unet_dual`` não aceita hooks, desligar o offload deixava-o na GPU."""
        import paint3d.painter as painter
        from aigamekit_shared.group_offload import GroupOffloadConfig

        class _UNet:
            def __init__(self, fail: bool = False) -> None:
                self.fail = fail
                self.calls = 0

            def enable_group_offload(self, **_kwargs) -> None:
                self.calls += 1
                if self.fail:
                    raise RuntimeError("sem suporte a hooks")

        unet = _UNet()
        dual = _UNet(fail=True)
        wrapper = self._wrapper(unet, dual)
        diff_pipe = SimpleNamespace(unet=wrapper, text_encoder=None)
        inner = SimpleNamespace(pipeline=diff_pipe, _unet_quantized=False)
        pipe = SimpleNamespace(models={"multiview_model": inner})

        monkeypatch.setattr("aigamekit_shared.group_offload.is_group_offload_enabled", lambda **_k: True)
        monkeypatch.setattr(painter, "_paint_go_cfg", lambda: GroupOffloadConfig())
        monkeypatch.setattr("aigamekit_shared.group_offload.try_group_offloading", lambda *_a, **_k: False)

        assert painter._try_paint_group_offload(pipe, allow=True) is True
        assert unet.calls == 1
        assert dual.calls == 1
        assert wrapper.offload_ref_unet is True

    def test_dual_with_hooks_disables_offload_ref_unet(self, monkeypatch) -> None:
        import paint3d.painter as painter
        from aigamekit_shared.group_offload import GroupOffloadConfig

        class _UNet:
            def enable_group_offload(self, **_kwargs) -> None:
                return None

        wrapper = self._wrapper(_UNet(), _UNet())
        diff_pipe = SimpleNamespace(unet=wrapper, text_encoder=None)
        pipe = SimpleNamespace(models={"multiview_model": SimpleNamespace(pipeline=diff_pipe, _unet_quantized=False)})

        monkeypatch.setattr("aigamekit_shared.group_offload.is_group_offload_enabled", lambda **_k: True)
        monkeypatch.setattr(painter, "_paint_go_cfg", lambda: GroupOffloadConfig())
        monkeypatch.setattr("aigamekit_shared.group_offload.try_group_offloading", lambda *_a, **_k: False)

        assert painter._try_paint_group_offload(pipe, allow=True) is True
        assert wrapper.offload_ref_unet is False


class TestCudnnAsymSdpa:
    """Prioridade cuDNN no SDPA assimétrico (RefAttn: head_dim_qk != head_dim_v)."""

    def test_env_disable_turns_off_resolver(self, monkeypatch) -> None:
        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        monkeypatch.setenv("PAINT3D_CUDNN_ASYM_SDPA", "0")
        assert attn_processor._resolve_cudnn_asym_sdpa() is None

    def test_symmetric_and_cpu_bypass_cudnn_priority(self, monkeypatch) -> None:
        from contextlib import contextmanager

        import torch

        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        entered: list[bool] = []

        @contextmanager
        def _fake_ctx(backends, *, set_priority=False):
            entered.append(set_priority)
            yield

        monkeypatch.setattr(attn_processor, "_CUDNN_ASYM_SDPA", (_fake_ctx, ["cudnn"]))

        q_cpu = torch.randn(1, 2, 8, 16)
        k_cpu = torch.randn(1, 2, 8, 16)
        v_cpu_asym = torch.randn(1, 2, 8, 32)
        out = attn_processor._run_sdpa(q_cpu, k_cpu, v_cpu_asym, None)
        assert out.shape == (1, 2, 8, 32)
        assert entered == []

    def test_asym_cuda_fp16_enters_cudnn_priority(self, monkeypatch) -> None:
        from contextlib import contextmanager

        import torch

        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        entered: list[tuple[list[str], bool]] = []

        @contextmanager
        def _fake_ctx(backends, *, set_priority=False):
            entered.append((list(backends), set_priority))
            yield

        class _FakeTensor:
            def __init__(self, shape, dtype, is_cuda=True):
                self.shape = shape
                self.dtype = dtype
                self.is_cuda = is_cuda

        calls: list[tuple] = []

        def _fake_sdpa(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False):
            calls.append((q.shape, v.shape, attn_mask))
            return "ok"

        monkeypatch.setattr(attn_processor, "_CUDNN_ASYM_SDPA", (_fake_ctx, ["CUDNN", "EFFICIENT", "MATH"]))
        monkeypatch.setattr(attn_processor, "scaled_dot_product_attention", _fake_sdpa)
        monkeypatch.setattr(torch.backends.cuda, "cudnn_sdp_enabled", lambda: True)

        q = _FakeTensor((1, 5, 16, 64), torch.float16)
        k = _FakeTensor((1, 5, 16, 64), torch.float16)
        v_asym = _FakeTensor((1, 5, 16, 128), torch.float16)
        v_sym = _FakeTensor((1, 5, 16, 64), torch.float16)

        assert attn_processor._run_sdpa(q, k, v_asym, None) == "ok"
        assert entered == [(["CUDNN", "EFFICIENT", "MATH"], True)]

        # Simétrico não entra no context manager
        entered.clear()
        assert attn_processor._run_sdpa(q, k, v_sym, None) == "ok"
        assert entered == []

        # Com attn_mask também não entra
        assert attn_processor._run_sdpa(q, k, v_asym, object()) == "ok"
        assert entered == []

    def test_cuda_refattn_numerical_parity(self) -> None:
        import torch
        import torch.nn.functional as F

        if not torch.cuda.is_available():
            pytest.skip("CUDA indisponível")

        from paint3d.hy3dpaint.hunyuanpaintpbr.unet import attn_processor

        torch.manual_seed(0)
        q = torch.randn((1, 5, 256, 64), device="cuda", dtype=torch.float16)
        k = torch.randn((1, 5, 64, 64), device="cuda", dtype=torch.float16)
        v = torch.randn((1, 5, 64, 128), device="cuda", dtype=torch.float16)

        out_cudnn = attn_processor._run_sdpa(q, k, v, None)
        out_default = F.scaled_dot_product_attention(q, k, v, attn_mask=None, dropout_p=0.0, is_causal=False)
        assert out_cudnn.shape == (1, 5, 256, 128)
        assert (out_cudnn - out_default).abs().max().item() < 1e-3
