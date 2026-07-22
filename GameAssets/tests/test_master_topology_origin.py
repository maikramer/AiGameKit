"""Regression: topology-fix stage must propagate ``--export-origin`` from the
text3d profile. Without it, the clean mesh retains its raw origin (often
min-Y < 0) which propagates to LOD0/rigged/animated and fails the validation
rule ``origin.y_min``.

See: gameassets.pipeline.run_master_pipeline Stage 2 (topology-fix).
"""

from __future__ import annotations

import inspect


def test_run_master_pipeline_topology_fix_passes_export_origin() -> None:
    from gameassets.pipeline import _topology_fix_extra_argv, run_master_pipeline

    master_src = inspect.getsource(run_master_pipeline)
    assert "_topology_fix_extra_argv" in master_src, (
        "run_master_pipeline deve chamar _topology_fix_extra_argv no Stage 2"
    )
    helper_src = inspect.getsource(_topology_fix_extra_argv)
    assert "--export-origin" in helper_src
    assert "export_origin" in helper_src


def test_run_master_pipeline_topology_fix_stage_present() -> None:
    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    assert '"topology-fix"' in src or "'topology-fix'" in src
    assert src.count("topology-fix") >= 2


def test_topology_fix_extra_argv_defaults_engine_arrays() -> None:
    from gameassets.pipeline import _topology_fix_extra_argv
    from gameassets.profile import GameProfile

    args = _topology_fix_extra_argv(GameProfile(title="t", genre="g", tone="t", style_preset="s", output_dir="."))
    assert "--engine" in args
    assert args[args.index("--engine") + 1] == "arrays"
