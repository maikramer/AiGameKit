"""Unit tests para resolve_torch_compile_mode / apply_channels_last helpers."""

from __future__ import annotations

from gamedev_shared.quantization import resolve_torch_compile_mode


class TestResolveTorchCompileMode:
    def test_default_unchanged(self) -> None:
        assert resolve_torch_compile_mode("default") == "default"
        assert resolve_torch_compile_mode("default", offload="model_cpu") == "default"

    def test_cudagraphs_full_gpu(self) -> None:
        assert resolve_torch_compile_mode("reduce-overhead", offload="none") == "reduce-overhead"
        assert resolve_torch_compile_mode("max-autotune", offload="none") == "max-autotune"

    def test_cudagraphs_downgrade_with_offload(self) -> None:
        assert resolve_torch_compile_mode("reduce-overhead", offload="model_cpu") == "default"
        assert resolve_torch_compile_mode("max-autotune", offload="group_stream") == "default"
        assert resolve_torch_compile_mode("reduce-overhead", group_offload_active=True) == "default"
