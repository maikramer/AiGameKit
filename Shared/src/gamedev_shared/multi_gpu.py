"""Multi-GPU weight splitting planner — wraps accelerate for intelligent device placement.

Provides a fluent builder API (``MultiGPUPlanner``) that uses HuggingFace
accelerate's ``dispatch_model`` / ``infer_auto_device_map`` to split model
weights across multiple GPUs with mixed VRAM.
"""

from __future__ import annotations

import logging
import types
from dataclasses import dataclass, field
from typing import Any, ClassVar


def _torch() -> types.ModuleType:
    """Lazy import of torch."""
    try:
        import torch

        return torch  # type: ignore[no-any-return]
    except ImportError:
        raise ImportError("torch is not installed. Install with: pip install gamedev-shared[gpu]") from None


def _accelerate() -> types.ModuleType:
    """Lazy import of accelerate."""
    try:
        import accelerate

        return accelerate  # type: ignore[no-any-return]
    except ImportError:
        raise ImportError("accelerate is not installed. It should be available via diffusers.") from None


@dataclass
class DevicePlan:
    """Result of multi-GPU planning — device placement for model components."""

    device_map: dict[str, int | str] = field(default_factory=dict)
    status: str = "cpu_only"
    max_memory: dict[int | str, int] = field(default_factory=dict)
    primary_device: int | str = "cpu"
    warnings: list[str] = field(default_factory=list)


class ModelArchitectureRegistry:
    """Registry of ``no_split_module_classes`` per model architecture.

    Pre-populated with common diffusion / transformer architectures.
    """

    DEFAULTS: ClassVar[dict[str, list[str]]] = {
        "hunyuan3d": ["DoubleStreamBlock", "SingleStreamBlock"],
        "flux": ["FluxSingleTransformerBlock", "FluxTransformerBlock"],
        "dit": ["DiTBlock"],
        "unet": ["BasicTransformerBlock", "UNetMidBlock2D"],
    }

    def __init__(self) -> None:
        self._registry: dict[str, list[str]] = dict(self.DEFAULTS)

    def get(self, name: str) -> list[str]:
        """Return no_split classes for *name*, empty list if unknown."""
        return list(self._registry.get(name, []))

    def register(self, name: str, classes: list[str]) -> None:
        """Register a custom architecture."""
        self._registry[name] = list(classes)


class MultiGPUPlanner:
    """Fluent builder for multi-GPU model weight splitting.

    Usage::

        planner = (
            MultiGPUPlanner()
            .for_model(pipe)
            .model_attr("model")
            .with_gpus([0, 1])
            .architecture("hunyuan3d")
        )
        plan = planner.plan()
        if plan.status == "multi_gpu":
            pipe = planner.apply()
    """

    def __init__(self) -> None:
        self._model: Any = None
        self._model_attr: str | None = None
        self._gpu_ids: list[int] = []
        self._max_memory: dict[int | str, str] = {}
        self._no_split_classes: list[str] = []
        self._dtype: Any = None
        self._registry = ModelArchitectureRegistry()
        self._plan: DevicePlan | None = None

    def for_model(self, model: Any) -> MultiGPUPlanner:
        """Set the target model or pipeline to split across devices."""
        self._model = model
        return self

    def model_attr(self, name: str) -> MultiGPUPlanner:
        """Specify which attribute of the model/pipeline to dispatch.

        Use when ``for_model()`` receives a pipeline object that is not an
        :class:`~torch.nn.Module`.  The planner will dispatch
        ``getattr(model, name)`` and (on :meth:`apply`) re-attach the
        dispatched sub-module via ``setattr``.

        Args:
            name: Attribute name (e.g. ``"model"`` for the DiT inside a
                Hunyuan3D pipeline).
        """
        self._model_attr = name
        return self

    def with_gpus(self, gpu_ids: list[int]) -> MultiGPUPlanner:
        """Specify which GPU IDs to use (e.g. ``[0, 1]``)."""
        self._gpu_ids = list(gpu_ids)
        return self

    def max_memory(self, mapping: dict[int | str, str]) -> MultiGPUPlanner:
        """Set per-device memory limits (e.g. ``{0: "20GiB", 1: "12GiB"}``)."""
        self._max_memory = dict(mapping)
        return self

    def architecture(self, name: str) -> MultiGPUPlanner:
        """Look up ``no_split_module_classes`` from the built-in registry."""
        self._no_split_classes = self._registry.get(name)
        return self

    def no_split(self, classes: list[str]) -> MultiGPUPlanner:
        """Manually override ``no_split_module_classes``."""
        self._no_split_classes = list(classes)
        return self

    def dtype(self, t: Any) -> MultiGPUPlanner:
        """Set model dtype for size estimation."""
        self._dtype = t
        return self

    # ------------------------------------------------------------------
    # Plan
    # ------------------------------------------------------------------

    def plan(self) -> DevicePlan:
        """Compute device placement plan.

        Returns a :class:`DevicePlan` with ``status`` of
        ``"multi_gpu"``, ``"single_gpu"``, or ``"cpu_only"``.
        """
        torch = _torch()
        plan = DevicePlan()

        if not torch.cuda.is_available():
            plan.status = "cpu_only"
            plan.warnings.append("No CUDA devices available — using CPU only.")
            logging.getLogger("gamedev_shared.multi_gpu").warning(plan.warnings[-1])
            self._plan = plan
            return plan

        device_count = torch.cuda.device_count()
        gpu_ids = self._gpu_ids or list(range(device_count))

        if len(gpu_ids) < 2:
            plan.status = "single_gpu"
            plan.primary_device = gpu_ids[0] if gpu_ids else 0
            plan.warnings.append("Fewer than 2 GPUs available — falling back to single-GPU mode.")
            logging.getLogger("gamedev_shared.multi_gpu").warning(plan.warnings[-1])
            self._plan = plan
            return plan

        # --- Multi-GPU path ---
        plan.status = "multi_gpu"

        max_mem = self._max_memory or self._probe_gpu_memory(gpu_ids, torch)

        try:
            accelerate = _accelerate()
        except ImportError:
            plan.status = "single_gpu"
            plan.primary_device = gpu_ids[0]
            plan.warnings.append("accelerate not installed — falling back to single-GPU.")
            self._plan = plan
            return plan

        target = self._target_model()

        try:
            kwargs: dict[str, Any] = {
                "max_memory": max_mem,
            }
            if self._no_split_classes:
                kwargs["no_split_module_classes"] = self._no_split_classes
            if self._dtype is not None:
                kwargs["dtype"] = self._dtype

            device_map = accelerate.infer_auto_device_map(target, **kwargs)
            plan.device_map = device_map
            plan.max_memory = {k: int(v) if isinstance(v, (int, float)) else v for k, v in max_mem.items()}

            devices_used = set(device_map.values())
            plan.primary_device = max(devices_used) if devices_used else gpu_ids[0]

        except Exception as exc:
            plan.status = "single_gpu"
            plan.primary_device = gpu_ids[0]
            msg = f"accelerate planning failed ({exc}) — falling back to single-GPU."
            plan.warnings.append(msg)
            logging.getLogger("gamedev_shared.multi_gpu").warning(msg)

        self._plan = plan
        return plan

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def apply(self) -> Any:
        """Dispatch model across devices according to the computed plan.

        Returns the original model/pipeline object with the dispatched
        sub-module re-attached when ``model_attr`` was used.

        Raises:
            RuntimeError: If :meth:`plan` was not called first.
        """
        if self._plan is None:
            raise RuntimeError("Call plan() before apply().")

        if self._plan.status != "multi_gpu" or not self._plan.device_map:
            return self._model

        accelerate = _accelerate()
        target = self._target_model()
        dispatched = accelerate.dispatch_model(target, device_map=self._plan.device_map)

        if self._model_attr is not None:
            setattr(self._model, self._model_attr, dispatched)
            return self._model

        return dispatched

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _target_model(self) -> Any:
        """Resolve the actual nn.Module to dispatch.

        When ``model_attr`` is set, returns ``getattr(self._model, name)``.
        Otherwise returns ``self._model`` directly.
        """
        if self._model_attr is not None:
            return getattr(self._model, self._model_attr)
        return self._model

    @staticmethod
    def _probe_gpu_memory(
        gpu_ids: list[int],
        torch: types.ModuleType,
        margin_gb: float = 1.0,
    ) -> dict[int | str, int]:
        """Build max_memory dict by probing free VRAM per GPU.

        Sorts GPUs by free memory descending and leaves *margin_gb* headroom.
        """
        from gamedev_shared.gpu import get_gpu_info

        gpu_info = get_gpu_info()
        if not gpu_info:
            return {}

        max_mem: dict[int | str, int] = {}
        for gpu in gpu_info:
            gid = gpu["id"]
            if gid not in gpu_ids:
                continue
            free = gpu["free_memory"]
            margin = int(margin_gb * 1024**3)
            max_mem[gid] = max(0, free - margin)

        # Add CPU fallback budget
        max_mem["cpu"] = 40 * 1024**3  # 40 GB CPU RAM fallback

        return max_mem
