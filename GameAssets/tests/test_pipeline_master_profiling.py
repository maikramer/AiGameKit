"""Round 2 — observabilidade do master pipeline (Fase 5)."""

from __future__ import annotations

import inspect


def test_master_pipeline_result_has_observability_fields() -> None:
    from gameassets.pipeline_master import MasterPipelineResult

    r = MasterPipelineResult(asset_id="t", ok=True)
    assert hasattr(r, "total_elapsed_s")
    assert hasattr(r, "cumulative_vram_mb_peak")
    assert r.total_elapsed_s == 0.0
    assert r.cumulative_vram_mb_peak == 0.0


def test_master_pipeline_result_recompute_totals() -> None:
    from gameassets.pipeline_master import MasterPipelineResult, StageResult

    r = MasterPipelineResult(asset_id="t", ok=True)
    r.stages.append(StageResult("a", True, 1.5))
    r.stages.append(StageResult("b", True, 2.5))
    r.recompute_totals()
    assert r.total_elapsed_s == 4.0


def test_stage_function_signature_includes_profiling_kwargs() -> None:
    from gameassets.pipeline_master import _stage

    sig = inspect.signature(_stage)
    assert "item_id" in sig.parameters
    assert "profile_enabled" in sig.parameters


def test_stage_uses_profiler_session() -> None:
    """ProfilerSession deve ser referenciada no corpo de _stage."""
    from gameassets import pipeline_master

    src = inspect.getsource(pipeline_master._stage)
    assert "ProfilerSession" in src
    assert "emit_progress" in src


def test_aggregate_master_results_writes_total_elapsed_s() -> None:
    from gameassets.pipeline_master import StageResult, aggregate_master_results

    rec: dict = {}
    aggregate_master_results([StageResult("a", True, 1.0), StageResult("b", True, 2.0)], rec)
    assert rec["total_elapsed_s"] == 3.0
    assert "stages" in rec
    assert len(rec["stages"]) == 2


def test_run_master_pipeline_signature_bake_normals_default_none() -> None:
    """Round 2: bake_normals default agora é None (= resolução por categoria)."""
    from gameassets.pipeline_master import run_master_pipeline

    sig = inspect.signature(run_master_pipeline)
    assert sig.parameters["bake_normals"].default is None
