"""Caminhos de extract/get_files (regressão: NPZ em tmp/ com --input absoluto)."""

from __future__ import annotations

import os
import tempfile

import pytest

try:
    from rigging3d.unirig.src.data.extract import _stem_from_input_path, get_files  # type: ignore
except ImportError as exc:
    if "bpy" in str(exc):
        pytest.skip("bpy not available", allow_module_level=True)
    raise


def test_stem_from_input_path() -> None:
    assert _stem_from_input_path("/home/foo/bar/animal.glb") == "animal"
    assert _stem_from_input_path("rel/my.mesh.glb") == "my.mesh"


def test_get_files_absolute_input_goes_under_output_dir() -> None:
    td = tempfile.mkdtemp()
    out = get_files(
        "raw_data.npz",
        td,
        td,
        inputs="/somewhere/deep/model.glb",
        force_override=True,
        warning=False,
    )
    assert len(out) == 1
    _inp, odir = out[0]
    assert odir == os.path.join(td, "model")
