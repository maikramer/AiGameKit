"""Política de GPU/resolução/offload."""

from __future__ import annotations

import subprocess
from dataclasses import FrozenInstanceError

import pytest
from hi3d import capability
from hi3d.capability import (
    MIN_COMPUTE_CAP,
    MIN_FREE_MIB_512,
    OFFLOAD_BELOW_FREE_MIB,
    GpuInfo,
    RefinePlan,
    pick_gpu,
    plan_for_gpu,
    query_gpus,
    resolution_for_free_mib,
    wants_offload,
)


def _gpu(
    *,
    index: int = 0,
    total: int = 6141,
    free: int = 5691,
    cc: tuple[int, int] | None = (8, 9),
) -> GpuInfo:
    return GpuInfo(index=index, name="Test GPU", total_mib=total, free_mib=free, compute_cap=cc)


class TestResolutionPolicy:
    @pytest.mark.parametrize(
        ("free_mib", "expected"),
        [
            (96 * 1024, 1536),
            (40 * 1024, 1536),
            (39 * 1024, 1024),
            (16 * 1024, 1024),
            (15 * 1024, 512),
            (6 * 1024, 512),
            (MIN_FREE_MIB_512, 512),
        ],
    )
    def test_thresholds(self, free_mib: int, expected: int) -> None:
        assert resolution_for_free_mib(free_mib) == expected

    def test_zero_free_still_defaults_to_512(self) -> None:
        assert resolution_for_free_mib(0) == 512


class TestOffloadPolicy:
    def test_offload_below_threshold(self) -> None:
        assert wants_offload(OFFLOAD_BELOW_FREE_MIB - 1)

    def test_no_offload_at_threshold(self) -> None:
        assert not wants_offload(OFFLOAD_BELOW_FREE_MIB)

    @pytest.mark.parametrize("free_mib", [1024, 4096, 8000, 11_999])
    def test_small_gpus_offload(self, free_mib: int) -> None:
        assert wants_offload(free_mib)


class TestPickGpu:
    def test_empty_list(self) -> None:
        assert pick_gpu([]) is None

    def test_picks_largest_vram(self) -> None:
        gpus = [_gpu(index=0, total=6141), _gpu(index=1, total=24576), _gpu(index=2, total=8192)]
        assert pick_gpu(gpus).index == 1

    def test_tie_breaks_on_lowest_index(self) -> None:
        gpus = [_gpu(index=3, total=6141), _gpu(index=1, total=6141)]
        assert pick_gpu(gpus).index == 1

    def test_single_gpu(self) -> None:
        assert pick_gpu([_gpu()]).name == "Test GPU"


class TestPlanForGpu:
    def test_no_gpu_is_unavailable(self) -> None:
        plan = plan_for_gpu(None)
        assert not plan.available
        assert "nvidia-smi" in plan.reason

    @pytest.mark.parametrize("cc", [None, (7, 5), (6, 1)])
    def test_old_compute_cap_is_unavailable(self, cc: tuple[int, int] | None) -> None:
        plan = plan_for_gpu(_gpu(cc=cc))
        assert not plan.available
        assert "compute capability" in plan.reason

    def test_ampere_is_supported(self) -> None:
        assert plan_for_gpu(_gpu(cc=MIN_COMPUTE_CAP)).available

    def test_low_vram_is_unavailable(self) -> None:
        plan = plan_for_gpu(_gpu(free=MIN_FREE_MIB_512 - 1))
        assert not plan.available
        assert "VRAM livre" in plan.reason

    def test_missing_stack_is_unavailable(self) -> None:
        plan = plan_for_gpu(_gpu(), stack_ready=False)
        assert not plan.available
        assert "sparc3d" in plan.reason

    def test_6gb_plan_is_512_with_offload(self) -> None:
        plan = plan_for_gpu(_gpu(free=5691))
        assert plan.available
        assert plan.resolution == 512
        assert plan.offload is True

    def test_large_gpu_plan_is_1536_without_offload(self) -> None:
        plan = plan_for_gpu(_gpu(total=96 * 1024, free=90 * 1024))
        assert plan.resolution == 1536
        assert plan.offload is False

    def test_plan_is_frozen(self) -> None:
        plan = plan_for_gpu(_gpu())
        with pytest.raises(FrozenInstanceError):
            plan.resolution = 1024  # type: ignore[misc]

    def test_plan_carries_gpu(self) -> None:
        gpu = _gpu()
        assert plan_for_gpu(gpu).gpu is gpu


class TestGpuInfo:
    def test_supported_property(self) -> None:
        assert _gpu(cc=(9, 0)).supported
        assert not _gpu(cc=(7, 0)).supported
        assert not _gpu(cc=None).supported

    def test_frozen(self) -> None:
        gpu = _gpu()
        with pytest.raises(FrozenInstanceError):
            gpu.free_mib = 0  # type: ignore[misc]


class TestQueryGpus:
    def test_missing_nvidia_smi(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(capability.shutil, "which", lambda _name: None)
        assert query_gpus() == []

    def test_parses_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = "0, NVIDIA GeForce RTX 4050 Laptop GPU, 6141, 5691, 8.9\n"
        monkeypatch.setattr(capability.shutil, "which", lambda _name: "/usr/bin/nvidia-smi")
        monkeypatch.setattr(
            capability.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout=out, stderr=""),
        )
        gpus = query_gpus()
        assert len(gpus) == 1
        assert gpus[0].name == "NVIDIA GeForce RTX 4050 Laptop GPU"
        assert gpus[0].total_mib == 6141
        assert gpus[0].compute_cap == (8, 9)

    def test_ignores_malformed_lines(self, monkeypatch: pytest.MonkeyPatch) -> None:
        out = "garbage\n0, GPU, 1000, 500, 8.6\n"
        monkeypatch.setattr(capability.shutil, "which", lambda _name: "/bin/nvidia-smi")
        monkeypatch.setattr(
            capability.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=0, stdout=out, stderr=""),
        )
        assert len(query_gpus()) == 1

    def test_nonzero_exit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(capability.shutil, "which", lambda _name: "/bin/nvidia-smi")
        monkeypatch.setattr(
            capability.subprocess,
            "run",
            lambda *a, **k: subprocess.CompletedProcess(args=[], returncode=9, stdout="", stderr="boom"),
        )
        assert query_gpus() == []

    def test_os_error_is_swallowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def _boom(*_a, **_k):
            raise OSError("no such binary")

        monkeypatch.setattr(capability.shutil, "which", lambda _name: "/bin/nvidia-smi")
        monkeypatch.setattr(capability.subprocess, "run", _boom)
        assert query_gpus() == []

    @pytest.mark.parametrize(("raw", "expected"), [("8.9", (8, 9)), ("12.0", (12, 0)), ("x.y", None), ("", None)])
    def test_parse_cap(self, raw: str, expected: tuple[int, int] | None) -> None:
        assert capability._parse_cap(raw) == expected


class TestDataclasses:
    def test_defaults(self) -> None:
        plan = RefinePlan(available=True)
        assert plan.resolution == 512
        assert plan.offload is True
        assert plan.reason == ""
        assert plan.gpu is None
