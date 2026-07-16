"""MultiGPU: gpu_ids no payload UMS chega ao adapter.load."""

from __future__ import annotations

from typing import Any

from modelserver.backend_manager import BackendManager
from modelserver.registry import BackendDescriptor, Registry

from gamedev_shared.cli_helpers import with_ums_load_opts

from .conftest_helpers import MockAdapter


class TestWithUmsLoadOpts:
    def test_injects_list_gpu_ids(self) -> None:
        out = with_ums_load_opts({"prompt": "x", "output": "/tmp/a.png"}, gpu_ids=[0, 1])
        assert out["gpu_ids"] == [0, 1]
        assert out["prompt"] == "x"

    def test_parses_csv_string(self) -> None:
        out = with_ums_load_opts({"output": "/tmp/a.png"}, gpu_ids="0,1")
        assert out["gpu_ids"] == [0, 1]

    def test_omits_empty(self) -> None:
        out = with_ums_load_opts({"output": "/tmp/a.png"}, gpu_ids=[])
        assert "gpu_ids" not in out


class TestGpuIdsReachLoad:
    def test_generate_passes_gpu_ids_to_adapter_load(self) -> None:
        descriptors = {
            "alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10),
        }
        registry = Registry(descriptors=descriptors)
        adapter = MockAdapter(name="alpha")
        registry._adapter_instances["alpha"] = adapter
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)

        resp = mgr.generate(
            "alpha",
            {"prompt": "x", "output": "/tmp/x.png", "gpu_ids": [0, 1]},
        )
        assert resp["status"] == "ok"
        assert adapter.load_calls == 1
        # MockAdapter devolve MockModel com kwargs do load.
        # Re-load via ensure_loaded path — inspect last model kwargs.
        state_model = mgr._states["alpha"].model
        assert state_model is not None
        assert state_model.kwargs.get("gpu_ids") == [0, 1]

    def test_load_kwargs_filter_keeps_gpu_ids(self) -> None:
        """Regressão: BackendManager só passa subset de keys a load()."""
        seen: dict[str, Any] = {}

        class CapturingAdapter(MockAdapter):
            def load(self, **kwargs: Any) -> Any:
                seen.update(kwargs)
                return super().load(**kwargs)

        descriptors = {
            "alpha": BackendDescriptor(name="alpha", adapter="_mock_alpha", vram_mib=1000, priority=10),
        }
        registry = Registry(descriptors=descriptors)
        registry._adapter_instances["alpha"] = CapturingAdapter(name="alpha")
        mgr = BackendManager(registry, query_free_mib=lambda: 99999, clear_vram=lambda: None)
        mgr.generate(
            "alpha",
            {
                "prompt": "x",
                "output": "/tmp/x.png",
                "gpu_ids": [1],
                "verbose": True,
                "noise": "drop-me",
            },
        )
        assert seen.get("gpu_ids") == [1]
        assert seen.get("verbose") is True
        assert "noise" not in seen
        assert "prompt" not in seen
