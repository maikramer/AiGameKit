"""Tests for gamedev_shared.multi_gpu (DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner).

All tests use mocked torch.cuda — no real GPU required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.multi_gpu import DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_torch(
    *,
    cuda_available: bool = True,
    device_count: int = 2,
) -> MagicMock:
    """Build a mock torch module with cuda sub-attributes."""
    torch_mock = MagicMock()
    torch_mock.cuda.is_available.return_value = cuda_available
    torch_mock.cuda.device_count.return_value = device_count
    return torch_mock


def _make_mock_accelerate(
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


def _simple_model() -> MagicMock:
    """A lightweight stand-in for an nn.Module — no GPU required."""
    return MagicMock(name="mock_model")


# ---------------------------------------------------------------------------
# 1. TestDevicePlan
# ---------------------------------------------------------------------------


class TestDevicePlan:
    def test_defaults(self):
        plan = DevicePlan()
        assert plan.device_map == {}
        assert plan.status == "cpu_only"
        assert plan.max_memory == {}
        assert plan.primary_device == "cpu"
        assert plan.warnings == []

    def test_custom_status_values(self):
        plan = DevicePlan(status="multi_gpu", primary_device=1)
        assert plan.status == "multi_gpu"
        assert plan.primary_device == 1

    def test_device_map_structure(self):
        plan = DevicePlan(device_map={"encoder": 0, "decoder": 1})
        assert plan.device_map["encoder"] == 0
        assert plan.device_map["decoder"] == 1

    def test_warnings_list(self):
        plan = DevicePlan(warnings=["GPU 1 unavailable"])
        assert "GPU 1 unavailable" in plan.warnings

    def test_max_memory_mapping(self):
        plan = DevicePlan(max_memory={0: 20 * 1024**3, 1: 12 * 1024**3})
        assert plan.max_memory[0] == 20 * 1024**3
        assert plan.max_memory[1] == 12 * 1024**3


# ---------------------------------------------------------------------------
# 2. TestModelArchitectureRegistry
# ---------------------------------------------------------------------------


class TestModelArchitectureRegistry:
    def test_defaults_populated(self):
        reg = ModelArchitectureRegistry()
        assert "hunyuan3d" in reg._registry
        assert "flux" in reg._registry
        assert "dit" in reg._registry
        assert "unet" in reg._registry

    def test_get_known_architecture(self):
        reg = ModelArchitectureRegistry()
        result = reg.get("flux")
        assert result == ["FluxSingleTransformerBlock"]

    def test_get_unknown_returns_empty(self):
        reg = ModelArchitectureRegistry()
        assert reg.get("nonexistent_arch") == []

    def test_register_custom(self):
        reg = ModelArchitectureRegistry()
        reg.register("custom_arch", ["CustomBlock", "OtherBlock"])
        assert reg.get("custom_arch") == ["CustomBlock", "OtherBlock"]

    def test_get_returns_copy(self):
        reg = ModelArchitectureRegistry()
        result = reg.get("dit")
        result.append("ExtraBlock")
        assert "ExtraBlock" not in reg.get("dit")

    def test_register_overwrites_existing(self):
        reg = ModelArchitectureRegistry()
        reg.register("flux", ["NewBlock"])
        assert reg.get("flux") == ["NewBlock"]


# ---------------------------------------------------------------------------
# 3. TestMultiGPUPlannerBuilder
# ---------------------------------------------------------------------------


class TestMultiGPUPlannerBuilder:
    def test_for_model_returns_self(self):
        p = MultiGPUPlanner()
        result = p.for_model(_simple_model())
        assert result is p

    def test_with_gpus_returns_self(self):
        p = MultiGPUPlanner()
        result = p.with_gpus([0, 1])
        assert result is p

    def test_with_gpus_stores_ids(self):
        p = MultiGPUPlanner()
        p.with_gpus([0, 1, 2])
        assert p._gpu_ids == [0, 1, 2]

    def test_max_memory_returns_self(self):
        p = MultiGPUPlanner()
        result = p.max_memory({0: "20GiB", 1: "12GiB"})
        assert result is p

    def test_max_memory_stores_mapping(self):
        p = MultiGPUPlanner()
        p.max_memory({0: "20GiB", 1: "12GiB"})
        assert p._max_memory == {0: "20GiB", 1: "12GiB"}

    def test_architecture_returns_self(self):
        p = MultiGPUPlanner()
        result = p.architecture("hunyuan3d")
        assert result is p

    def test_architecture_looks_up_registry(self):
        p = MultiGPUPlanner()
        p.architecture("dit")
        assert p._no_split_classes == ["DiTBlock"]

    def test_no_split_overrides(self):
        p = MultiGPUPlanner()
        p.architecture("dit")
        p.no_split(["CustomBlock"])
        assert p._no_split_classes == ["CustomBlock"]

    def test_dtype_returns_self(self):
        p = MultiGPUPlanner()
        result = p.dtype("float16")
        assert result is p

    def test_dtype_stores_value(self):
        p = MultiGPUPlanner()
        p.dtype("bfloat16")
        assert p._dtype == "bfloat16"

    def test_fluent_chain(self):
        model = _simple_model()
        p = (
            MultiGPUPlanner()
            .for_model(model)
            .with_gpus([0, 1])
            .max_memory({0: "20GiB"})
            .architecture("flux")
            .dtype("float16")
        )
        assert p._model is model
        assert p._gpu_ids == [0, 1]
        assert p._max_memory == {0: "20GiB"}
        assert p._no_split_classes == ["FluxSingleTransformerBlock"]
        assert p._dtype == "float16"


# ---------------------------------------------------------------------------
# 4. TestMultiGPUPlannerFallback
# ---------------------------------------------------------------------------


class TestMultiGPUPlannerFallback:
    @patch("gamedev_shared.multi_gpu._torch")
    def test_cpu_only_when_no_cuda(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=False)
        p = MultiGPUPlanner().for_model(_simple_model())
        plan = p.plan()
        assert plan.status == "cpu_only"
        assert plan.primary_device == "cpu"
        assert any("No CUDA" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._torch")
    def test_single_gpu_fallback(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=1)
        p = MultiGPUPlanner().for_model(_simple_model())
        plan = p.plan()
        assert plan.status == "single_gpu"
        assert plan.primary_device == 0
        assert any("Fewer than 2" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_multi_gpu_with_two_gpus(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _make_mock_accelerate(device_map={"layer.0": 0, "layer.1": 1})
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3, "cpu": 40 * 1024**3}

        p = MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1])
        plan = p.plan()
        assert plan.status == "multi_gpu"
        assert plan.device_map == {"layer.0": 0, "layer.1": 1}

    @patch("gamedev_shared.multi_gpu._torch")
    def test_cpu_only_logs_warning(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=False)
        p = MultiGPUPlanner().for_model(_simple_model())
        plan = p.plan()
        assert len(plan.warnings) >= 1
        assert "CPU only" in plan.warnings[0]

    @patch("gamedev_shared.multi_gpu._torch")
    def test_single_gpu_logs_warning(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=1)
        p = MultiGPUPlanner().for_model(_simple_model())
        plan = p.plan()
        assert any("single-GPU" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# 5. TestMultiGPUPlannerPlan
# ---------------------------------------------------------------------------


class TestMultiGPUPlannerPlan:
    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_returns_correct_device_map(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        expected_map = {"encoder": 0, "decoder": 1}
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _make_mock_accelerate(device_map=expected_map)
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3, "cpu": 40 * 1024**3}

        plan = MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).plan()
        assert plan.device_map == expected_map

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_primary_device_is_max_device(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        expected_map = {"layer.0": 0, "layer.1": 1}
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _make_mock_accelerate(device_map=expected_map)
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3}

        plan = MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).plan()
        assert plan.primary_device == 1

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_with_user_max_memory_skips_probe(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _make_mock_accelerate(device_map={"layer": 0})

        (MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).max_memory({0: "20GiB", 1: "12GiB"}).plan())
        mock_probe.assert_not_called()

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_accelerate_failure_falls_back(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel = MagicMock()
        mock_accel.infer_auto_device_map.side_effect = RuntimeError("OOM during planning")
        mock_accel_fn.return_value = mock_accel
        mock_probe.return_value = {0: 20 * 1024**3, 1: 12 * 1024**3}

        plan = MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).plan()
        assert plan.status == "single_gpu"
        assert plan.primary_device == 0
        assert any("accelerate planning failed" in w for w in plan.warnings)

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_with_no_split_classes(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel = MagicMock()
        mock_accel.infer_auto_device_map.return_value = {"layer": 0}
        mock_accel_fn.return_value = mock_accel
        mock_probe.return_value = {0: 20 * 1024**3}

        MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).architecture("dit").plan()

        call_kwargs = mock_accel.infer_auto_device_map.call_args
        assert call_kwargs is not None
        assert "no_split_module_classes" in call_kwargs[1]
        assert call_kwargs[1]["no_split_module_classes"] == ["DiTBlock"]

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_with_dtype(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel = MagicMock()
        mock_accel.infer_auto_device_map.return_value = {"layer": 0}
        mock_accel_fn.return_value = mock_accel
        mock_probe.return_value = {0: 20 * 1024**3}

        MultiGPUPlanner().for_model(_simple_model()).with_gpus([0, 1]).dtype("float16").plan()

        call_kwargs = mock_accel.infer_auto_device_map.call_args
        assert call_kwargs is not None
        assert "dtype" in call_kwargs[1]
        assert call_kwargs[1]["dtype"] == "float16"


# ---------------------------------------------------------------------------
# 6. TestMultiGPUPlannerApply
# ---------------------------------------------------------------------------


class TestMultiGPUPlannerApply:
    def test_apply_without_plan_raises(self):
        p = MultiGPUPlanner()
        with pytest.raises(RuntimeError, match="Call plan\\(\\) before apply\\(\\)"):
            p.apply()

    @patch("gamedev_shared.multi_gpu._torch")
    def test_apply_returns_model_on_single_gpu(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=1)
        model = _simple_model()
        p = MultiGPUPlanner().for_model(model)
        p.plan()
        result = p.apply()
        assert result is model

    @patch("gamedev_shared.multi_gpu._torch")
    def test_apply_returns_model_on_cpu_only(self, mock_torch_fn: MagicMock):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=False)
        model = _simple_model()
        p = MultiGPUPlanner().for_model(model)
        p.plan()
        result = p.apply()
        assert result is model

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_apply_calls_dispatch_model_on_multi_gpu(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        dispatched_model = MagicMock(name="dispatched_model")
        mock_accel_fn.return_value = _make_mock_accelerate(
            device_map={"layer.0": 0},
            dispatch_return=dispatched_model,
        )
        mock_probe.return_value = {0: 20 * 1024**3}

        model = _simple_model()
        p = MultiGPUPlanner().for_model(model).with_gpus([0, 1])
        p.plan()
        result = p.apply()

        assert result is dispatched_model
        mock_accel_fn.return_value.dispatch_model.assert_called_once()
        call_kwargs = mock_accel_fn.return_value.dispatch_model.call_args
        assert "device_map" in call_kwargs[1]


# ---------------------------------------------------------------------------
# 7. TestMultiGPUPlannerWithQuantization
# ---------------------------------------------------------------------------


class TestMultiGPUPlannerWithQuantization:
    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_plan_with_quantized_model(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        """Planner should handle a model with quantized parameters without error."""
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        mock_accel_fn.return_value = _make_mock_accelerate(device_map={"quantized_block": 0})
        mock_probe.return_value = {0: 12 * 1024**3, 1: 12 * 1024**3}

        quant_model = MagicMock(name="quantized_model")
        quant_model.dtype = "int4"

        p = MultiGPUPlanner().for_model(quant_model).with_gpus([0, 1]).architecture("dit")
        plan = p.plan()
        assert plan.status == "multi_gpu"
        assert "quantized_block" in plan.device_map

    @patch("gamedev_shared.multi_gpu._accelerate")
    @patch("gamedev_shared.multi_gpu._torch")
    @patch("gamedev_shared.multi_gpu.MultiGPUPlanner._probe_gpu_memory")
    def test_apply_with_quantized_model(
        self,
        mock_probe: MagicMock,
        mock_torch_fn: MagicMock,
        mock_accel_fn: MagicMock,
    ):
        """apply() should dispatch a quantized model just like any other."""
        mock_torch_fn.return_value = _make_mock_torch(cuda_available=True, device_count=2)
        dispatched = MagicMock(name="dispatched_quantized")
        mock_accel_fn.return_value = _make_mock_accelerate(
            device_map={"block.a": 0, "block.b": 1},
            dispatch_return=dispatched,
        )
        mock_probe.return_value = {0: 10 * 1024**3, 1: 10 * 1024**3}

        quant_model = MagicMock(name="quantized_model")
        p = MultiGPUPlanner().for_model(quant_model).with_gpus([0, 1])
        plan = p.plan()
        assert plan.status == "multi_gpu"
        result = p.apply()
        assert result is dispatched
