"""Regression: topology-fix stage must propagate ``--export-origin`` from the
text3d profile. Without it, the clean mesh retains its raw origin (often
min-Y < 0) which propagates to LOD0/rigged/animated and fails the validation
rule ``origin.y_min``.

See: gameassets.pipeline.run_master_pipeline Stage 2 (topology-fix).
"""

from __future__ import annotations

import inspect


def test_run_master_pipeline_topology_fix_passes_export_origin() -> None:
    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    assert "--export-origin" in src, (
        "run_master_pipeline deve propagar --export-origin para text3d topology-fix; "
        "sem ela o clean mesh fica com min-Y < 0 (feet drift)."
    )
    assert "t3_prof.export_origin" in src


def test_run_master_pipeline_topology_fix_stage_present() -> None:
    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    assert '"topology-fix"' in src or "'topology-fix'" in src
    assert src.count("topology-fix") >= 2
