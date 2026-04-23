"""gamedev-shared — biblioteca partilhada do monorepo GameDev."""

from __future__ import annotations

from typing import Any

__version__ = "0.2.0"

__all__ = ["DevicePlan", "ModelArchitectureRegistry", "MultiGPUPlanner"]


def __getattr__(name: str) -> Any:
    """Lazy import for heavy multi-GPU symbols (avoids top-level torch import)."""
    if name in __all__:
        from gamedev_shared.multi_gpu import DevicePlan, ModelArchitectureRegistry, MultiGPUPlanner

        _exports = {
            "MultiGPUPlanner": MultiGPUPlanner,
            "DevicePlan": DevicePlan,
            "ModelArchitectureRegistry": ModelArchitectureRegistry,
        }
        return _exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
