"""Regression: rigging3d low-VRAM skin config must be written inside the UniRig
``configs/model/`` tree, not in the per-run work directory.

Hydra resolves the ``components.model`` name against its package search path
(``configs/model/<name>.yaml``). Writing the low_vram override to the work dir
made Hydra look for ``configs/model/_low_vram_unirig_skin.yaml`` relative to
the caller's CWD, which fails when gameassets invokes rigging3d from
``sample-gameassets/`` (or any non-package-root dir). Symptom:
``FileNotFoundError: 'configs/model/_low_vram_unirig_skin.yaml'`` during the
skin phase, surfaced to gameassets as a generic "erro desconhecido".
"""

from __future__ import annotations

import inspect


def test_low_vram_skin_config_lives_under_configs_model() -> None:
    from rigging3d import cli as rig_cli

    src = inspect.getsource(rig_cli)
    assert 'root / "configs" / "model" / "_low_vram_unirig_skin.yaml"' in src, (
        "rigging3d low_vram skin config must be written to "
        "root/configs/model/_low_vram_unirig_skin.yaml so Hydra resolves it "
        "regardless of the caller's CWD."
    )


def test_low_vram_skin_config_not_in_work_dir() -> None:
    from rigging3d import cli as rig_cli

    src = inspect.getsource(rig_cli)
    assert 'wd / "_low_vram_unirig_skin.yaml"' not in src, (
        "rigging3d must NOT write the low_vram skin config to the work dir; "
        "Hydra cannot find it there when rigging3d is invoked from a non-root CWD."
    )
