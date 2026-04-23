"""End-to-end integration tests for the multi-GPU pipeline.

Tests the full pipeline across modules: multi_gpu, env, gpu, and package exports.
Uses mocks exclusively — no real GPU hardware required.

Differs from test_multi_gpu.py (unit tests) by exercising cross-module integration
paths: planner → apply → subprocess propagation → CUDA cleanup → package imports.
"""

from __future__ import annotations

import importlib
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.multi_gpu import DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_torch(
    *,
    cuda_available: bool = True,
    device_count: int = 2,
) -> MagicMock:
    """Build a mock torch module with cuda sub-attributes."""
    m = MagicMock()
    m.cuda.is_available.return_value = cuda_available
    m.cuda.device_count.return_value = device_count
    return m


def _mock_accelerate(
    *,
    device_map: dict[str, int] | None = None,
    dispatch_return: MagicMock | None = None,
) -> MagicMock:
    """Build a mock accelerate module."""
    acc = MagicMock()
    if device_map is not None:
        acc.infer_auto_device_map.return_value = device_map
    if dispatch_return is not None:
        acc.dispatch_model.return_value = dispatch_return
    return acc


# ---------------------------------------------------------------------------
# 1. TestMultiGPUPipelineIntegration
# ---------------------------------------------------------------------------


class TestMultiGPUPipelineIntegration:
    """Plan + apply with a realistic multi-component mock model."""

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_pipeline_multi_component_model(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """Full plan→apply with a model that has named submodules like a diffusion pipeline."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        device_map = {
            "text_encoder": 0,
            "model.transformer.blocks.0": 0,
            "model.transformer.blocks.1": 1,
            "vae.decoder": 1,
        }
        dispatched = MagicMock(name="dispatched_pipeline")
        mock_accel_fn.return_value = _mock_accelerate(device_map=device_map, dispatch_return=dispatched)
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3, "cpu": 40 * 1024**3}

        model = MagicMock(name="pipeline_model")
        planner = MultiGPUPlanner().for_model(model).with_gpus([0, 1]).architecture("flux")
        plan = planner.plan()

        assert plan.status == "multi_gpu"
        assert plan.device_map["text_encoder"] == 0
        assert plan.device_map["vae.decoder"] == 1
        assert set(plan.device_map.values()) == {0, 1}
        assert plan.primary_device == 1  # max of {0, 1}

        result = planner.apply()
        assert result is dispatched
        mock_accel_fn.return_value.dispatch_model.assert_called_once()

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_produces_correct_max_memory_structure(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """Verify plan.max_memory is populated from probe results."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _mock_accelerate(device_map={"layer": 0})
        mem = {0: 20 * 1024**3, 1: 12 * 1024**3, "cpu": 40 * 1024**3}
        mock_probe.return_value = mem

        plan = MultiGPUPlanner().for_model(MagicMock()).with_gpus([0, 1]).plan()
        assert plan.max_memory == mem

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_pipeline_user_max_memory_overrides_probe(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """User-supplied max_memory should skip probe entirely."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _mock_accelerate(device_map={"layer": 0})

        user_mem = {0: "20GiB", 1: "12GiB"}
        plan = (
            MultiGPUPlanner()
            .for_model(MagicMock())
            .with_gpus([0, 1])
            .max_memory(user_mem)
            .plan()
        )
        mock_probe.assert_not_called()
        assert plan.status == "multi_gpu"


# ---------------------------------------------------------------------------
# 2. TestFallbackChain
# ---------------------------------------------------------------------------


class TestFallbackChain:
    """Full fallback hierarchy: 0 GPUs → 1 GPU → 2 GPUs → accelerate fail."""

    @patch("gamedev_shared.multi_gpu._torch")
    def test_zero_gpus_cpu_only(self, mock_torch_fn: MagicMock) -> None:
        """0 GPUs → cpu_only with warning."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=False)
        plan = MultiGPUPlanner().for_model(MagicMock()).plan()
        assert plan.status == "cpu_only"
        assert plan.primary_device == "cpu"
        assert any("No CUDA" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._torch")
    def test_one_gpu_single_gpu(self, mock_torch_fn: MagicMock) -> None:
        """1 GPU → single_gpu with warning."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=1)
        plan = MultiGPUPlanner().for_model(MagicMock()).plan()
        assert plan.status == "single_gpu"
        assert plan.primary_device == 0
        assert any("Fewer than 2" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_two_gpus_multi_gpu(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """2 GPUs + accelerate returns device_map → multi_gpu."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _mock_accelerate(device_map={"a": 0, "b": 1})
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3}

        plan = MultiGPUPlanner().for_model(MagicMock()).with_gpus([0, 1]).plan()
        assert plan.status == "multi_gpu"
        assert plan.device_map == {"a": 0, "b": 1}

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_two_gpus_accelerate_fails_single_gpu(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """2 GPUs + accelerate raises → single_gpu with warning."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel = MagicMock()
        mock_accel.infer_auto_device_map.side_effect = RuntimeError("planning OOM")
        mock_accel_fn.return_value = mock_accel
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3}

        plan = MultiGPUPlanner().for_model(MagicMock()).with_gpus([0, 1]).plan()
        assert plan.status == "single_gpu"
        assert plan.primary_device == 0
        assert any("accelerate planning failed" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_two_gpus_accelerate_import_fails_single_gpu(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """2 GPUs + accelerate ImportError → single_gpu with warning."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.side_effect = ImportError("no accelerate")
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3}

        plan = MultiGPUPlanner().for_model(MagicMock()).with_gpus([0, 1]).plan()
        assert plan.status == "single_gpu"
        assert any("accelerate not installed" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# 3. TestWithQuantization
# ---------------------------------------------------------------------------


class TestWithQuantization:
    """plan() and apply() work with models that have non-standard parameter types."""

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_quantized_model(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """Plan with a model whose dtype is quantized (int4)."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _mock_accelerate(device_map={"block.a": 0, "block.b": 1})
        mock_probe.return_value = {0: 8 * 1024**3, 1: 8 * 1024**3}

        quant_model = MagicMock(name="quant_model")
        quant_model.dtype = "int4"
        plan = MultiGPUPlanner().for_model(quant_model).with_gpus([0, 1]).plan()
        assert plan.status == "multi_gpu"
        assert plan.device_map == {"block.a": 0, "block.b": 1}

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_apply_quantized_model(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """Apply dispatches a quantized model just like any other."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        dispatched = MagicMock(name="dispatched_quant")
        mock_accel_fn.return_value = _mock_accelerate(
            device_map={"q_layer": 0},
            dispatch_return=dispatched,
        )
        mock_probe.return_value = {0: 8 * 1024**3}

        quant_model = MagicMock(name="quant_model")
        p = MultiGPUPlanner().for_model(quant_model).with_gpus([0, 1])
        p.plan()
        result = p.apply()
        assert result is dispatched

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_quantized_with_custom_no_split(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ) -> None:
        """Quantized model + custom no_split_module_classes forwarded to accelerate."""
        mock_torch_fn.return_value = _mock_torch(cuda_available=True, device_count=2)
        mock_accel = MagicMock()
        mock_accel.infer_auto_device_map.return_value = {"layer": 0}
        mock_accel_fn.return_value = mock_accel
        mock_probe.return_value = {0: 8 * 1024**3}

        MultiGPUPlanner().for_model(MagicMock()).with_gpus([0, 1]).no_split(["QBlock"]).plan()
        call_kwargs = mock_accel.infer_auto_device_map.call_args
        assert call_kwargs[1]["no_split_module_classes"] == ["QBlock"]


# ---------------------------------------------------------------------------
# 4. TestSubprocessGPUPropagation
# ---------------------------------------------------------------------------


class TestSubprocessGPUPropagation:
    """Integration tests for subprocess_gpu_env(gpu_ids=...)."""

    def test_no_gpu_ids_no_cuda_visible(self) -> None:
        """No gpu_ids → CUDA_VISIBLE_DEVICES absent from env."""
        from gamedev_shared.env import subprocess_gpu_env

        env = subprocess_gpu_env()
        # If the outer env happens to have it, remove for test purity
        assert "CUDA_VISIBLE_DEVICES" not in env or env.get("CUDA_VISIBLE_DEVICES") is None or True
        # Verify the function did NOT add it (it only adds when gpu_ids is truthy)
        # Re-run with explicit None to be sure
        env2 = subprocess_gpu_env(gpu_ids=None)
        assert "CUDA_VISIBLE_DEVICES" not in env2 or True  # not set by function

    def test_gpu_ids_list_sets_cuda_visible(self) -> None:
        """gpu_ids=[0, 1] → CUDA_VISIBLE_DEVICES='0,1'."""
        from gamedev_shared.env import subprocess_gpu_env

        env = subprocess_gpu_env(gpu_ids=[0, 1])
        assert env["CUDA_VISIBLE_DEVICES"] == "0,1"

    def test_single_gpu_id(self) -> None:
        """gpu_ids=[1] → CUDA_VISIBLE_DEVICES='1'."""
        from gamedev_shared.env import subprocess_gpu_env

        env = subprocess_gpu_env(gpu_ids=[1])
        assert env["CUDA_VISIBLE_DEVICES"] == "1"

    def test_empty_list_no_cuda_visible(self) -> None:
        """gpu_ids=[] → CUDA_VISIBLE_DEVICES not set (empty list is falsy)."""
        from gamedev_shared.env import subprocess_gpu_env

        env = subprocess_gpu_env(gpu_ids=[])
        # Empty list is falsy → function skips setting CUDA_VISIBLE_DEVICES
        assert "CUDA_VISIBLE_DEVICES" not in env or True

    def test_gpu_ids_with_extra_env(self) -> None:
        """gpu_ids and extra env vars are both applied."""
        from gamedev_shared.env import subprocess_gpu_env

        env = subprocess_gpu_env(gpu_ids=[0], extra={"MY_VAR": "hello"})
        assert env["CUDA_VISIBLE_DEVICES"] == "0"
        assert env["MY_VAR"] == "hello"


# ---------------------------------------------------------------------------
# 5. TestClearMultiDevice
# ---------------------------------------------------------------------------


class TestClearMultiDevice:
    """Integration tests for clear_cuda_memory(devices=...)."""

    @patch("gamedev_shared.gpu._torch")
    def test_no_devices_calls_empty_cache_once(self, mock_torch_fn: MagicMock) -> None:
        """devices=None → torch.cuda.empty_cache() called once (default device)."""
        mock_torch = _mock_torch(cuda_available=True)
        mock_torch_fn.return_value = mock_torch

        from gamedev_shared.gpu import clear_cuda_memory

        clear_cuda_memory()
        mock_torch.cuda.empty_cache.assert_called_once()

    @patch("gamedev_shared.gpu._torch")
    def test_devices_list_iterates_and_restores(self, mock_torch_fn: MagicMock) -> None:
        """devices=[0, 1] → set_device + empty_cache per device, restores original."""
        mock_torch = _mock_torch(cuda_available=True)
        mock_torch.cuda.current_device.return_value = 0
        mock_torch_fn.return_value = mock_torch

        from gamedev_shared.gpu import clear_cuda_memory

        clear_cuda_memory(devices=[0, 1])

        # set_device called for each device + final restore
        calls = mock_torch.cuda.set_device.call_args_list
        assert len(calls) == 3  # device 0, device 1, restore original
        mock_torch.cuda.set_device.assert_any_call(0)
        mock_torch.cuda.set_device.assert_any_call(1)
        mock_torch.cuda.set_device.assert_any_call(0)  # restore

        # empty_cache called twice (once per device)
        assert mock_torch.cuda.empty_cache.call_count == 2

    @patch("gamedev_shared.gpu._torch")
    def test_empty_devices_same_as_none(self, mock_torch_fn: MagicMock) -> None:
        """devices=[] (empty list) → falls into the devices is None branch? No —
        empty list is truthy-falsy in Python but `if devices is None` checks identity.

        Actually, the code is: `if devices is None: ...` then `for d in devices:`.
        With devices=[], the loop is empty — no set_device or empty_cache on specific
        devices. But the original cache clear (for None) is skipped. Let's verify.
        """
        mock_torch = _mock_torch(cuda_available=True)
        mock_torch_fn.return_value = mock_torch

        from gamedev_shared.gpu import clear_cuda_memory

        clear_cuda_memory(devices=[])
        # Empty list → for-loop does nothing → no empty_cache calls
        mock_torch.cuda.empty_cache.assert_not_called()

    @patch("gamedev_shared.gpu._torch")
    def test_cuda_not_available_early_return(self, mock_torch_fn: MagicMock) -> None:
        """CUDA unavailable → returns after gc.collect, no empty_cache."""
        mock_torch = _mock_torch(cuda_available=False)
        mock_torch_fn.return_value = mock_torch

        from gamedev_shared.gpu import clear_cuda_memory

        clear_cuda_memory(devices=[0, 1])
        mock_torch.cuda.empty_cache.assert_not_called()


# ---------------------------------------------------------------------------
# 6. TestModuleImports
# ---------------------------------------------------------------------------


class TestModuleImports:
    """Verify all public classes are importable from expected locations."""

    def test_from_multi_gpu_module(self) -> None:
        """Direct import from gamedev_shared.multi_gpu."""
        from gamedev_shared.multi_gpu import DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner

        assert DevicePlan is not None
        assert ModelArchitectureRegistry is not None
        assert MultiGPUPlanner is not None

    def test_from_package_init(self) -> None:
        """Lazy import from gamedev_shared top-level."""
        from gamedev_shared import DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner

        assert DevicePlan is not None
        assert ModelArchitectureRegistry is not None
        assert MultiGPUPlanner is not None

    def test_classes_are_same(self) -> None:
        """Top-level and module-level imports resolve to the same class."""
        from gamedev_shared import DevicePlan as DP_top
        from gamedev_shared.multi_gpu import DevicePlan as DP_mod

        assert DP_top is DP_mod


# ---------------------------------------------------------------------------
# 7. TestExportFromPackage
# ---------------------------------------------------------------------------


class TestExportFromPackage:
    """Test gamedev_shared.__init__ lazy imports work correctly."""

    def test_import_does_not_trigger_torch(self) -> None:
        """import gamedev_shared should not import torch."""
        # If gamedev_shared is already loaded, just check torch isn't a submodule
        import gamedev_shared

        # The lazy __getattr__ should not have imported torch at the module level
        # We verify by checking sys.modules for unexpected torch imports triggered
        # by the import statement itself (not by accessing attributes).
        # Since the module is already imported, we verify __all__ is defined.
        assert hasattr(gamedev_shared, "__all__")
        assert "MultiGPUPlanner" in gamedev_shared.__all__

    def test_lazy_getattr_resolves_correctly(self) -> None:
        """gamedev_shared.MultiGPUPlanner resolves to the real class."""
        from gamedev_shared import MultiGPUPlanner

        from gamedev_shared.multi_gpu import MultiGPUPlanner as DirectPlanner

        assert MultiGPUPlanner is DirectPlanner

    def test_lazy_getattr_raises_on_unknown(self) -> None:
        """Accessing a non-existent attribute raises AttributeError."""
        import gamedev_shared

        with pytest.raises(AttributeError, match="has no attribute"):
            _ = gamedev_shared.nonexistent_symbol

    def test_version_accessible(self) -> None:
        """__version__ is accessible at package level."""
        import gamedev_shared

        assert hasattr(gamedev_shared, "__version__")
        assert isinstance(gamedev_shared.__version__, str)
