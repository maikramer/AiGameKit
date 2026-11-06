"""Núcleo de refine (sem carregar torch/sparc3d)."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import hi3d.refine as refine
import numpy as np
import pytest
import trimesh
from hi3d.align import face_count, frame_of, mesh_vertices
from hi3d.refine import (
    VALID_RESOLUTIONS,
    RefineResult,
    _progress_bridge,
    finalize_mesh,
    resolve_offload,
    stack_ready,
    stack_status,
)


def _sphere(radius: float = 1.0, offset: tuple[float, float, float] = (0.0, 0.0, 0.0)) -> trimesh.Trimesh:
    mesh = trimesh.creation.icosphere(subdivisions=3, radius=radius)
    mesh.apply_translation(offset)
    return mesh


class TestStackStatus:
    def test_reports_all_modules(self) -> None:
        status = stack_status()
        for key in ("torch", "flash_attn", "spconv", "sparc3d", "trimesh", "accelerate"):
            assert key in status
            assert isinstance(status[key], bool)

    def test_import_error_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(_name: str) -> Any:
            raise ImportError("nope")

        monkeypatch.setattr(refine.importlib.util, "find_spec", _boom)
        assert stack_status()["torch"] is False

    def test_value_error_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(_name: str) -> Any:
            raise ValueError("broken module")

        monkeypatch.setattr(refine.importlib.util, "find_spec", _boom)
        assert stack_status()["sparc3d"] is False

    def test_stack_ready_ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            refine,
            "stack_status",
            lambda: {"torch": True, "flash_attn": True, "spconv": True, "sparc3d": True},
        )
        ready, why = stack_ready()
        assert ready and why == ""

    @pytest.mark.parametrize("missing", ["torch", "flash_attn", "spconv", "sparc3d"])
    def test_stack_ready_reports_missing(self, monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
        status = {"torch": True, "flash_attn": True, "spconv": True, "sparc3d": True}
        status[missing] = False
        monkeypatch.setattr(refine, "stack_status", lambda: status)
        ready, why = stack_ready()
        assert not ready
        assert missing in why

    def test_real_environment_has_trimesh(self) -> None:
        assert importlib.util.find_spec("trimesh") is not None


class TestResolveOffload:
    def test_explicit_true(self) -> None:
        assert resolve_offload(True) is True

    def test_explicit_false(self) -> None:
        assert resolve_offload(False) is False

    def test_auto_uses_gpu(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hi3d import capability

        gpu = capability.GpuInfo(index=0, name="g", total_mib=6141, free_mib=2000, compute_cap=(8, 9))
        monkeypatch.setattr(capability, "query_gpus", lambda: [gpu])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: gpus[0])
        assert resolve_offload(None) is True

    def test_auto_without_gpu_defaults_to_offload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from hi3d import capability

        monkeypatch.setattr(capability, "query_gpus", lambda: [])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: None)
        assert resolve_offload(None) is True


class TestFinalizeMesh:
    def test_aligns_and_exports(self, tmp_path: Path) -> None:
        coarse = _sphere(radius=3.0, offset=(1.0, 2.0, 3.0))
        refined = _sphere(radius=1.0)  # já normalizado pelo upstream
        out, faces, raw = finalize_mesh(refined, coarse, tmp_path / "r.glb", align_to_coarse=True, target_faces=0)
        assert out.is_file()
        assert faces == raw == face_count(refined)
        loaded = trimesh.load(str(out), force="mesh", process=False)
        assert np.allclose(loaded.bounds, coarse.bounds, atol=1e-4)

    def test_no_align_keeps_unit_scale(self, tmp_path: Path) -> None:
        coarse = _sphere(radius=3.0)
        refined = _sphere(radius=1.0)
        out, _faces, _raw = finalize_mesh(refined, coarse, tmp_path / "r.glb", align_to_coarse=False, target_faces=0)
        loaded = trimesh.load(str(out), force="mesh", process=False)
        assert float(loaded.bounds[1][0]) == pytest.approx(1.0, abs=1e-3)

    def test_decimates_to_target(self, tmp_path: Path) -> None:
        coarse = _sphere(radius=1.0)
        refined = _sphere(radius=1.0)
        _out, faces, raw = finalize_mesh(refined, coarse, tmp_path / "r.glb", align_to_coarse=True, target_faces=200)
        assert raw > faces
        assert 0 < faces <= 400

    def test_strips_debris_after_decimation(self, tmp_path: Path) -> None:
        coarse = _sphere(radius=1.0)
        main = _sphere(radius=1.0)
        tiny = trimesh.creation.box(extents=(0.001, 0.001, 0.001))
        tiny.apply_translation((5.0, 0.0, 0.0))
        merged = trimesh.util.concatenate([main, tiny])
        out, _faces, _raw = finalize_mesh(merged, coarse, tmp_path / "r.glb", align_to_coarse=True, target_faces=300)
        loaded = trimesh.load(str(out), force="mesh", process=False)
        assert len(loaded.split(only_watertight=False)) == 1

    def test_creates_output_directory(self, tmp_path: Path) -> None:
        out, _f, _r = finalize_mesh(
            _sphere(), _sphere(radius=2.0), tmp_path / "x/y/z.glb", align_to_coarse=True, target_faces=0
        )
        assert out.parent.is_dir()


class TestProgressBridge:
    @staticmethod
    def _event(pass_index: int = 0, num_passes: int = 1, step: int = 5, num_inference_steps: int = 25) -> Any:
        return SimpleNamespace(
            pass_index=pass_index,
            num_passes=num_passes,
            step=step,
            num_inference_steps=num_inference_steps,
        )

    def test_emits_fraction_and_message(self) -> None:
        got: list[tuple[float | None, str | None]] = []
        bridge = _progress_bridge(None, lambda p, m: got.append((p, m)), None)
        bridge(self._event())
        assert got
        pct, msg = got[0]
        assert 0.12 <= pct <= 0.90
        assert "passe 1/1" in msg

    def test_pct_increases_across_passes(self) -> None:
        values: list[float] = []
        bridge = _progress_bridge(None, lambda p, _m: values.append(p), None)
        bridge(self._event(pass_index=0, num_passes=3))
        bridge(self._event(pass_index=1, num_passes=3))
        bridge(self._event(pass_index=2, num_passes=3))
        assert values == sorted(values)

    def test_abort_raises_cancelled(self) -> None:
        bridge = _progress_bridge(None, lambda p, m: None, lambda: True)
        with pytest.raises(refine._Cancelled):
            bridge(self._event())

    def test_zero_denominators_are_safe(self) -> None:
        got: list[float] = []
        bridge = _progress_bridge(None, lambda p, _m: got.append(p), None)
        bridge(self._event(num_passes=0, num_inference_steps=0))
        assert got and np.isfinite(got[0])


class TestRefineResult:
    def test_as_dict_rounds_elapsed(self) -> None:
        result = RefineResult(
            output=Path("/tmp/a.glb"),
            resolution=512,
            faces=100,
            coarse_faces=50,
            elapsed_sec=12.3456,
            offloaded=True,
            model="m",
        )
        payload = result.as_dict()
        assert payload["elapsed_sec"] == 12.35
        assert payload["output"] == "/tmp/a.glb"
        assert payload["offloaded"] is True

    def test_extra_fields_merged(self) -> None:
        result = RefineResult(
            output=Path("a"),
            resolution=512,
            faces=1,
            coarse_faces=1,
            elapsed_sec=1.0,
            offloaded=False,
            model="m",
            extra={"note": "x"},
        )
        assert result.as_dict()["note"] == "x"


class TestRefineMeshGuards:
    def test_invalid_resolution_raises_before_gpu(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="resolution"):
            refine.refine_mesh(
                tmp_path / "c.glb",
                tmp_path / "i.png",
                tmp_path / "o.glb",
                resolution=999,
            )

    @pytest.mark.parametrize("resolution", VALID_RESOLUTIONS)
    def test_valid_resolution_passes_validation(self, tmp_path: Path, resolution: int) -> None:
        # Sem stack/GPU (ou sem malha) deve falhar no load, não na validação do enum.
        with pytest.raises((RuntimeError, FileNotFoundError, ValueError)):
            refine.refine_mesh(
                tmp_path / "missing.glb",
                tmp_path / "i.png",
                tmp_path / "o.glb",
                resolution=resolution,
            )

    def test_missing_coarse_mesh_raises(self, tmp_path: Path) -> None:
        with pytest.raises((ValueError, FileNotFoundError)):
            refine.refine_mesh(tmp_path / "missing.glb", tmp_path / "i.png", tmp_path / "o.glb")


class TestFrameReuse:
    def test_frame_of_coarse_matches_restore(self, tmp_path: Path) -> None:
        coarse = _sphere(radius=2.5, offset=(0.0, 1.0, 0.0))
        refined = _sphere(radius=1.0)
        out, _f, _r = finalize_mesh(refined, coarse, tmp_path / "r.glb", align_to_coarse=True, target_faces=0)
        loaded = trimesh.load(str(out), force="mesh", process=False)
        assert float(loaded.extents[0]) == pytest.approx(float(coarse.extents[0]), rel=1e-3)
        frame = frame_of(mesh_vertices(coarse))
        assert frame.center[1] == pytest.approx(1.0, abs=1e-6)


class _ResidencyPipeline:
    def __init__(self) -> None:
        self.mode_calls: list[str] = []

    def to(self, device: Any) -> _ResidencyPipeline:
        self.mode_calls.append(f"to:{device}")
        return self

    def enable_sequential_cpu_offload(self, device: str = "cuda") -> None:
        self.mode_calls.append(f"offload:{device}")


class TestApplyResidency:
    def test_explicit_full_gpu(self) -> None:
        pipeline = _ResidencyPipeline()
        mode = refine._apply_residency(pipeline, offload=False, staging=None, fast_postprocess=False)
        assert mode == "full"
        assert pipeline.mode_calls == ["to:cuda"]

    def test_explicit_sequential(self) -> None:
        pipeline = _ResidencyPipeline()
        mode = refine._apply_residency(pipeline, offload=True, staging=None, fast_postprocess=False)
        assert mode == "sequential"
        assert pipeline.mode_calls == ["offload:cuda"]

    def test_staging_when_requested_and_supported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _ResidencyPipeline()
        monkeypatch.setattr(refine, "apply_phase_staging", lambda p, **_k: True)
        mode = refine._apply_residency(pipeline, offload=None, staging=True, fast_postprocess=False)
        assert mode == "staged"
        assert pipeline.mode_calls == []

    def test_auto_uses_staging_with_enough_vram(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _ResidencyPipeline()
        monkeypatch.setattr(refine, "_free_vram_mib", lambda: 5800)
        monkeypatch.setattr(refine, "staging_supported", lambda _p: True)
        monkeypatch.setattr(refine, "apply_phase_staging", lambda p, **_k: True)
        assert refine._apply_residency(pipeline, offload=None, staging=None, fast_postprocess=False) == "staged"

    def test_auto_falls_back_below_threshold(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _ResidencyPipeline()
        monkeypatch.setattr(refine, "_free_vram_mib", lambda: 2000)
        monkeypatch.setattr(refine, "staging_supported", lambda _p: True)
        mode = refine._apply_residency(pipeline, offload=None, staging=None, fast_postprocess=False)
        assert mode == "sequential"
        assert pipeline.mode_calls == ["offload:cuda"]

    def test_auto_falls_back_when_supported_is_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _ResidencyPipeline()
        monkeypatch.setattr(refine, "_free_vram_mib", lambda: 9000)
        monkeypatch.setattr(refine, "staging_supported", lambda _p: False)
        assert refine._apply_residency(pipeline, offload=None, staging=None, fast_postprocess=False) == "sequential"

    def test_auto_falls_back_when_apply_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pipeline = _ResidencyPipeline()
        monkeypatch.setattr(refine, "_free_vram_mib", lambda: 9000)
        monkeypatch.setattr(refine, "staging_supported", lambda _p: True)
        monkeypatch.setattr(refine, "apply_phase_staging", lambda p, **_k: False)
        assert refine._apply_residency(pipeline, offload=None, staging=None, fast_postprocess=False) == "sequential"


class _OomPipeline:
    def __init__(self) -> None:
        self.calls = 0
        self.offloaded = False
        self.progress_callback = None

    def __call__(self, *_a: Any, **_k: Any) -> Any:
        import torch

        self.calls += 1
        if self.calls == 1:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory (teste)")
        return "retry-ok"

    def enable_sequential_cpu_offload(self, _device: str = "cuda") -> None:
        self.offloaded = True


class _StagedOomPipeline(_OomPipeline):
    _hi3d_staged = True
    _hi3d_mode = "staged"


class TestOomFallback:
    def test_oom_retries_with_sequential_offload(self) -> None:
        pipeline = _StagedOomPipeline()
        result = refine.run_pipeline(
            pipeline,
            object(),
            object(),
            resolution=512,
            num_inference_steps=1,
            seed=0,
            emit=lambda *_a: None,
        )
        assert result == "retry-ok"
        assert pipeline.offloaded is True
        assert pipeline.calls == 2

    def test_oom_without_staging_propagates(self) -> None:
        import torch

        pipeline = _OomPipeline()
        with pytest.raises(torch.cuda.OutOfMemoryError):
            refine.run_pipeline(
                pipeline,
                object(),
                object(),
                resolution=512,
                num_inference_steps=1,
                seed=0,
                emit=lambda *_a: None,
            )

    def test_non_oom_error_propagates(self) -> None:
        class _Boom(_StagedOomPipeline):
            def __call__(self, *_a: Any, **_k: Any) -> Any:
                raise ValueError("outra coisa")

        with pytest.raises(ValueError):
            refine.run_pipeline(
                _Boom(),
                object(),
                object(),
                resolution=512,
                num_inference_steps=1,
                seed=0,
                emit=lambda *_a: None,
            )
