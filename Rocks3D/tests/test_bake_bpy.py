"""Tests for the bpy seamless-bake backend (skipped when bpy is absent)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest
from rocks3d.bake_bpy import bpy_available

pytestmark = pytest.mark.skipif(not bpy_available(), reason="bpy not installed")


def _primitive_attrs(glb: Path) -> list[str]:
    data = glb.read_bytes()
    off = 12
    js = None
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        off += 8
        if ctype == 0x4E4F534A:
            js = json.loads(data[off : off + clen])
            break
        off += clen
    assert js is not None
    return list(js["meshes"][0]["primitives"][0]["attributes"].keys())


def test_bpy_bake_exports_normals_and_tangents(tmp_path: Path) -> None:
    from rocks3d.build import build_rock_glb

    out = tmp_path / "boulder.glb"
    summary = build_rock_glb("boulder", out, seed=1, quality="fast", use_bpy=True)
    assert out.exists()
    assert summary["backend"] == "bpy"
    attrs = _primitive_attrs(out)
    # The whole point: seam-free shading needs exported NORMAL + TANGENT.
    assert "NORMAL" in attrs
    assert "TANGENT" in attrs
    assert "TEXCOORD_0" in attrs
