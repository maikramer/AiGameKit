"""Tests for aigamekit_lab.bench_sdnq — SDNQ recipe benchmark."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from aigamekit_lab.bench_sdnq import (
    CANDIDATES,
    Candidate,
    LayerResult,
    _group_checkpoints,
    aggregate,
    format_table,
    select_layers,
)


class TestSelectLayers:
    def _shapes(self) -> dict[str, tuple[int, ...]]:
        return {
            "small_1d": (4096,),
            "tiny_2d": (64, 64),  # numel < min_numel
            "narrow": (8, 4096),  # dim < min_dim
            "linear_small": (128, 256),
            "linear_medium": (512, 512),
            "linear_large": (4096, 4096),
        }

    def test_filters_non_linear_and_small(self) -> None:
        picked = select_layers(self._shapes(), max_layers=10)
        assert "small_1d" not in picked
        assert "tiny_2d" not in picked
        assert "narrow" not in picked
        assert set(picked) <= {"linear_small", "linear_medium", "linear_large"}

    def test_deterministic_with_same_seed(self) -> None:
        first = select_layers(self._shapes(), max_layers=2, seed=7)
        second = select_layers(self._shapes(), max_layers=2, seed=7)
        assert first == second

    def test_respects_max_layers(self) -> None:
        assert len(select_layers(self._shapes(), max_layers=1)) <= 1

    def test_empty_when_no_eligible(self) -> None:
        assert select_layers({"x": (4, 4)}) == []


class TestGroupCheckpoints:
    def test_alternate_formats_keep_one(self) -> None:
        grouped = _group_checkpoints(
            [
                ("unet", Path("diffusion_pytorch_model.fp16.safetensors")),
                ("unet", Path("diffusion_pytorch_model.safetensors")),
            ]
        )
        assert len(grouped) == 1
        assert len(grouped[0][1]) == 1

    def test_numbered_shards_keep_all(self) -> None:
        grouped = _group_checkpoints(
            [
                ("model", Path("model-00001-of-00002.safetensors")),
                ("model", Path("model-00002-of-00002.safetensors")),
            ]
        )
        assert len(grouped) == 1
        assert len(grouped[0][1]) == 2


class TestMeasureCandidate:
    def test_uint4_recipe_roundtrip(self) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("sdnq")

        from aigamekit_lab.bench_sdnq import measure_candidate

        torch.manual_seed(0)
        # 1024²: com SVD, camadas pequenas pagam overhead de correção (o bench
        # real usa camadas grandes; aqui queremos o b/w da receita, não o ruído).
        weight = torch.randn(1024, 1024) * 0.02
        candidate = CANDIDATES["uint4-proposed"]
        weight_rel, output_rel, bits, seconds, dtype = measure_candidate(weight, candidate, device="cpu")

        assert 0.0 < weight_rel < 0.25
        assert 0.0 < output_rel < 0.25
        assert 4.0 < bits < 8.0
        assert seconds >= 0.0
        assert dtype == "uint4"


class TestAggregate:
    def test_groups_by_checkpoint_and_candidate(self) -> None:
        results = [
            LayerResult("m", "a", "x", [128, 256], 0.1, 0.11, 5.0, 0.5, "uint4"),
            LayerResult("m", "a", "y", [128, 256], 0.2, 0.21, 5.2, 0.6, "uint4"),
            LayerResult("m", "b", "x", [128, 256], 0.3, 0.31, 8.0, 0.1, "uint8"),
        ]
        rows = aggregate(results)
        assert len(rows) == 2
        row_a = next(row for row in rows if row["candidate"] == "a")
        assert row_a["layers"] == 2
        assert row_a["weight_rel_mean"] == pytest.approx(0.15)
        assert row_a["bits_per_weight_mean"] == pytest.approx(5.1)

    def test_table_has_header_and_rows(self) -> None:
        rows = aggregate([LayerResult("m", "a", "x", [128, 256], 0.1, 0.11, 5.0, 0.5, "uint4")])
        table = format_table(rows)
        assert "checkpoint" in table
        assert "uint4" in table


class TestRunSdnqBench:
    def test_no_sdnq_returns_1(self) -> None:
        from aigamekit_lab import bench_sdnq

        with patch.dict("sys.modules", {"sdnq": None}):
            assert bench_sdnq.run_sdnq_bench(checkpoints=["x"]) == 1

    def test_unknown_candidate_returns_1(self) -> None:
        pytest.importorskip("sdnq")
        from aigamekit_lab import bench_sdnq

        assert bench_sdnq.run_sdnq_bench(checkpoints=["x"], candidates=["nope"]) == 1

    def test_missing_checkpoint_returns_1(self) -> None:
        pytest.importorskip("sdnq")
        from aigamekit_lab import bench_sdnq

        assert bench_sdnq.run_sdnq_bench(checkpoints=["/definitely/not/here"]) == 1

    def test_synthetic_checkpoint(self, tmp_path: Path) -> None:
        torch = pytest.importorskip("torch")
        pytest.importorskip("safetensors")
        from safetensors.torch import save_file

        pytest.importorskip("sdnq")
        from aigamekit_lab import bench_sdnq

        tensors = {"layer.weight": torch.randn(128, 256) * 0.02}
        path = tmp_path / "model.safetensors"
        save_file(tensors, str(path))

        output = tmp_path / "report.json"
        code = bench_sdnq.run_sdnq_bench(
            checkpoints=[str(path)],
            candidates=["uint8-plain"],
            max_layers=1,
            device="cpu",
            output_json=output,
        )
        assert code == 0
        assert output.exists()


class TestCli:
    def test_bench_sdnq_help(self) -> None:
        from aigamekit_lab.cli import main

        result = CliRunner().invoke(main, ["bench", "sdnq", "--help"])
        assert result.exit_code == 0
        assert "SDNQ" in result.output

    def test_candidate_dataclass_defaults(self) -> None:
        candidate = Candidate("x", "desc")
        assert candidate.kwargs == {}
        assert candidate.dynamic is False
