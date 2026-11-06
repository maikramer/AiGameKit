"""Adapter do worker vramd (sem torch real)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import trimesh
from hi3d.worker_serve_adapter import Adapter


class _FakePipeline:
    progress_callback = None
    _hi3d_offloaded = True
    _hi3d_model_ref = "fake-model"

    def __call__(self, image: Any, coarse: Any, *, resolution: int, num_inference_steps: int, seed: Any) -> Any:
        from types import SimpleNamespace

        return SimpleNamespace(mesh=_cube(), resolution=resolution)


def _cube() -> trimesh.Trimesh:
    return trimesh.creation.box(extents=(1.0, 1.0, 1.0))


class TestAdapterBasics:
    def test_name(self) -> None:
        assert Adapter().name == "hi3d"

    def test_vram_mib_is_int(self) -> None:
        assert isinstance(Adapter().vram_mib(), int)

    def test_unload_with_fake_model_is_safe(self) -> None:
        Adapter().unload({"pipeline": object()})

    def test_unload_with_none_is_safe(self) -> None:
        Adapter().unload(None)

    def test_unload_is_idempotent(self) -> None:
        adapter = Adapter()
        adapter.unload({"pipeline": object()})
        adapter.unload({"pipeline": object()})


class TestGenerateValidation:
    def test_missing_fields_raise(self) -> None:
        with pytest.raises(ValueError, match="obrigatórios"):
            Adapter().generate({"pipeline": object()}, {"coarse_mesh": "a.glb"})

    def test_all_fields_present_passes_validation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import hi3d.worker_serve_adapter as mod

        monkeypatch.setattr(mod, "load_mesh", lambda _p: _cube())
        monkeypatch.setattr(mod, "_open_image", lambda _p, _rm: object())
        monkeypatch.setattr(mod, "run_pipeline", lambda *a, **k: _FakeResult())
        out = tmp_path / "out.glb"
        result = Adapter().generate(
            {"pipeline": _FakePipeline()},
            {"coarse_mesh": "a.glb", "image": "i.png", "output": str(out)},
        )
        assert out.is_file()
        assert result["faces"] > 0
        assert result["resolution"] == 512

    def test_request_resolution_is_forwarded(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import hi3d.worker_serve_adapter as mod

        seen: dict[str, Any] = {}

        def _fake_run(pipeline: Any, coarse: Any, image: Any, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return _FakeResult()

        monkeypatch.setattr(mod, "load_mesh", lambda _p: _cube())
        monkeypatch.setattr(mod, "_open_image", lambda _p, _rm: object())
        monkeypatch.setattr(mod, "run_pipeline", _fake_run)
        Adapter().generate(
            {"pipeline": _FakePipeline()},
            {
                "coarse_mesh": "a.glb",
                "image": "i.png",
                "output": str(tmp_path / "o.glb"),
                "resolution": 1024,
                "num_inference_steps": 7,
                "seed": 42,
            },
        )
        assert seen["resolution"] == 1024
        assert seen["num_inference_steps"] == 7
        assert seen["seed"] == 42

    def test_cancelled_pipeline_raises_runtime(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import hi3d.worker_serve_adapter as mod

        def _cancel(*_a: Any, **_k: Any) -> Any:
            raise mod._Cancelled("x")

        monkeypatch.setattr(mod, "load_mesh", lambda _p: _cube())
        monkeypatch.setattr(mod, "_open_image", lambda _p, _rm: object())
        monkeypatch.setattr(mod, "run_pipeline", _cancel)
        with pytest.raises(RuntimeError, match="cancelado"):
            Adapter().generate(
                {"pipeline": _FakePipeline()},
                {"coarse_mesh": "a.glb", "image": "i.png", "output": str(tmp_path / "o.glb")},
            )


class _FakeResult:
    def __init__(self) -> None:
        self.mesh = _cube()
        self.resolution = 512


class TestLoad:
    def test_load_wraps_pipeline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import hi3d.worker_serve_adapter as mod

        monkeypatch.setattr(mod, "load_pipeline", lambda **kwargs: _FakePipeline())
        model = Adapter().load(model="x", offload=True)
        assert "pipeline" in model
        assert isinstance(model["pipeline"], _FakePipeline)

    def test_load_forwards_kwargs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import hi3d.worker_serve_adapter as mod

        seen: dict[str, Any] = {}

        def _fake(**kwargs: Any) -> Any:
            seen.update(kwargs)
            return _FakePipeline()

        monkeypatch.setattr(mod, "load_pipeline", _fake)
        Adapter().load(model="m", offload=False, local_files_only=True, dino_model="d", clip_model="c")
        assert seen == {
            "model": "m",
            "offload": False,
            "staging": None,
            "fast_postprocess": False,
            "local_files_only": True,
            "dino_model": "d",
            "clip_model": "c",
        }


class TestResolutionResolution:
    def test_auto_uses_capability_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hi3d import capability
        from hi3d import worker_serve_adapter as mod

        gpu = capability.GpuInfo(index=0, name="g", total_mib=6141, free_mib=5691, compute_cap=(8, 9))
        monkeypatch.setattr(capability, "query_gpus", lambda: [gpu])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: gpus[0])
        assert mod._resolve_resolution("auto") == 512

    def test_none_uses_capability_policy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hi3d import capability
        from hi3d import worker_serve_adapter as mod

        gpu = capability.GpuInfo(index=0, name="g", total_mib=96 * 1024, free_mib=90 * 1024, compute_cap=(9, 0))
        monkeypatch.setattr(capability, "query_gpus", lambda: [gpu])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: gpus[0])
        assert mod._resolve_resolution(None) == 1536

    @pytest.mark.parametrize("raw", [512, "512", 1024, "1536"])
    def test_explicit_is_passthrough(self, raw: Any) -> None:
        from hi3d.worker_serve_adapter import _resolve_resolution

        assert _resolve_resolution(raw) == int(raw)


class TestVramdResultContract:
    """O ``done`` do worker tem de trazer ``status: ok`` — o vramd decide por ele."""

    def test_generate_result_has_status_ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import hi3d.worker_serve_adapter as mod

        monkeypatch.setattr(mod, "load_mesh", lambda _p: _cube())
        monkeypatch.setattr(mod, "_open_image", lambda _p, _rm: object())
        monkeypatch.setattr(mod, "run_pipeline", lambda *a, **k: _FakeResult())
        result = Adapter().generate(
            {"pipeline": _FakePipeline()},
            {"coarse_mesh": "a.glb", "image": "i.png", "output": str(tmp_path / "o.glb")},
        )
        assert result["status"] == "ok"
