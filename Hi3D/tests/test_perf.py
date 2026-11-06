"""Residência por fase e knobs de execução (sem GPU real)."""

from __future__ import annotations

import os
from typing import Any

import pytest
from hi3d import perf
from hi3d.perf import (
    STAGING_MIN_FREE_MIB,
    PerfConfig,
    apply_phase_staging,
    configure_torch,
    restore_phase_staging,
    staging_supported,
    use_sequential_offload,
)


class _FakeModule:
    def __init__(self, name: str = "m") -> None:
        self.name = name
        self.devices: list[str] = []

    def to(self, device: Any, **_kw: Any) -> _FakeModule:
        self.devices.append(str(device))
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        return ("called", self.name, args)


class _FakePipeline:
    def __init__(self) -> None:
        self.conditioner = _FakeModule("cond")
        self.vae = _FakeModule("vae")
        self.transformer_512 = _FakeModule("t512")
        self._device = "cpu"
        self.sample_calls: list[Any] = []
        self.decode_calls: list[Any] = []
        self.offload_calls: list[str] = []

    def _sample_latents(self, transformer: Any, *args: Any, **kwargs: Any) -> Any:
        self.sample_calls.append((transformer, args))
        return "sample-out"

    def _decode_latents(self, latents: Any, structure_resolution: int) -> Any:
        self.decode_calls.append((latents, structure_resolution))
        return "decode-out"

    def enable_sequential_cpu_offload(self, device: str = "cuda") -> None:
        self.offload_calls.append(device)


class TestPerfConfig:
    def test_defaults(self) -> None:
        cfg = PerfConfig()
        assert cfg.staging is True
        assert cfg.tf32 is True
        assert cfg.cudnn_benchmark is True
        assert cfg.fast_postprocess is False

    def test_frozen(self) -> None:
        from dataclasses import FrozenInstanceError

        with pytest.raises(FrozenInstanceError):
            PerfConfig().staging = False  # type: ignore[misc]


class TestConfigureTorch:
    def test_alloc_conf_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
        configure_torch()
        assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == perf.ALLOC_CONF

    def test_alloc_conf_respected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:128")
        configure_torch()
        assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "max_split_size_mb:128"

    def test_tf32_enabled(self) -> None:
        import torch

        configure_torch()
        assert torch.backends.cuda.matmul.allow_tf32 is True
        assert torch.backends.cudnn.allow_tf32 is True

    def test_returns_config(self) -> None:
        cfg = PerfConfig(tf32=False)
        assert configure_torch(cfg) is cfg

    def test_torch_missing_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import builtins

        real_import = builtins.__import__

        def _fake_import(name: str, *a: Any, **k: Any) -> Any:
            if name == "torch":
                raise ImportError("no torch")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", _fake_import)
        assert configure_torch() is not None


class TestStagingSupported:
    def test_true_for_complete_pipeline(self) -> None:
        assert staging_supported(_FakePipeline())

    @pytest.mark.parametrize("attr", ["conditioner", "vae", "_sample_latents", "_decode_latents", "_device"])
    def test_false_when_missing(self, attr: str) -> None:
        pipeline = _FakePipeline()
        monkeypatch_cls = type("P", (), {})
        obj = monkeypatch_cls()
        for name in ("conditioner", "vae", "_sample_latents", "_decode_latents", "_device"):
            if name != attr:
                setattr(obj, name, getattr(pipeline, name))
        assert staging_supported(obj) is False


class TestApplyStaging:
    def test_apply_sets_device_and_flag(self) -> None:
        pipeline = _FakePipeline()
        assert apply_phase_staging(pipeline, device="cpu") is True
        assert str(pipeline._device) == "cpu"
        assert pipeline._hi3d_staged is True

    def test_idempotent(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        wrapper = pipeline._sample_latents
        assert apply_phase_staging(pipeline, device="cpu") is True
        assert pipeline._sample_latents is wrapper

    def test_unsupported_returns_false(self) -> None:
        assert apply_phase_staging(object(), device="cpu") is False

    def test_sample_moves_transformer_in_and_out(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        out = pipeline._sample_latents(pipeline.transformer_512, "a")
        assert out == "sample-out"
        assert pipeline.transformer_512.devices[0] == "cpu"  # move-in + move-out
        assert len(pipeline.transformer_512.devices) == 2
        assert len(pipeline.sample_calls) == 1

    def test_decode_moves_vae(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        assert pipeline._decode_latents("lat", 64) == "decode-out"
        assert pipeline.vae.devices[0] == "cpu"
        assert len(pipeline.vae.devices) == 2

    def test_conditioner_wrapper_moves_in_and_out(self) -> None:
        pipeline = _FakePipeline()
        original = pipeline.conditioner
        apply_phase_staging(pipeline, device="cpu")
        pipeline.conditioner("img")
        assert len(original.devices) == 2

    def test_conditioner_forwards_attributes(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        assert pipeline.conditioner.name == "cond"

    def test_fast_postprocess_patches_and_restores(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import sparc3d.sparcubes.postprocess as pp

        original = pp._filter_components
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu", fast_postprocess=True)
        assert pp._filter_components is not original
        restore_phase_staging(pipeline)
        assert pp._filter_components is original


class TestRestore:
    def test_restore_removes_instance_overrides(self) -> None:
        pipeline = _FakePipeline()
        original_sample = pipeline._sample_latents
        original_conditioner = pipeline.conditioner
        apply_phase_staging(pipeline, device="cpu")
        assert restore_phase_staging(pipeline) is True
        assert pipeline._sample_latents.__func__ is original_sample.__func__
        assert pipeline.conditioner is original_conditioner
        assert pipeline._hi3d_staged is False

    def test_restore_without_staging_is_noop(self) -> None:
        assert restore_phase_staging(_FakePipeline()) is False

    def test_double_restore(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        assert restore_phase_staging(pipeline) is True
        assert restore_phase_staging(pipeline) is False


class TestSequentialFallback:
    def test_calls_upstream_offload(self) -> None:
        pipeline = _FakePipeline()
        apply_phase_staging(pipeline, device="cpu")
        assert use_sequential_offload(pipeline, device="cuda") is True
        assert pipeline.offload_calls == ["cuda"]
        assert pipeline._hi3d_staged is False

    def test_failure_returns_false(self) -> None:
        class _Boom(_FakePipeline):
            def enable_sequential_cpu_offload(self, device: str = "cuda") -> None:
                raise RuntimeError("sem accelerate")

        assert use_sequential_offload(_Boom()) is False


class TestStagingThreshold:
    def test_threshold_is_above_measured_peak(self) -> None:
        # Pico medido: 3.74 GiB -> 4600 MiB deixa ~700 MiB de folga.
        assert 3744 < STAGING_MIN_FREE_MIB < 5000
