"""Testes do verificador GLB pós-export."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from gamedev_shared.glb_verify import (
    GlbVerifyError,
    extract_glb_meta,
    infer_stage_from_path,
    post_save_verify,
    verify_glb,
)


def _minimal_glb(
    *,
    attrs: dict[str, int] | None = None,
    vert_count: int = 3,
    index_count: int = 3,
) -> bytes:
    """GLB mínimo com um triangle (accessors fake com count)."""
    if attrs is None:
        attrs = {"POSITION": 0, "NORMAL": 1}
    accessors = []
    # POSITION
    accessors.append({"count": vert_count, "type": "VEC3", "componentType": 5126, "min": [0, 0, 0], "max": [1, 1, 1]})
    # NORMAL / UV placeholders matching indices in attrs
    max_idx = max(attrs.values()) if attrs else 0
    while len(accessors) <= max_idx:
        accessors.append({"count": vert_count, "type": "VEC3", "componentType": 5126})
    accessors.append({"count": index_count, "type": "SCALAR", "componentType": 5123})  # indices
    idx_acc = len(accessors) - 1
    doc = {
        "asset": {"version": "2.0"},
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": attrs,
                        "indices": idx_acc,
                    }
                ]
            }
        ],
        "accessors": accessors,
        "buffers": [{"byteLength": 1}],
        "bufferViews": [{"buffer": 0, "byteLength": 1}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    pad = (4 - (len(js) % 4)) % 4
    js = js + (b" " * pad)
    bin_chunk = b"\x00\x00\x00\x00"
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, total)
    out += struct.pack("<I4s", len(js), b"JSON")
    out += js
    out += struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
    out += bin_chunk
    return bytes(out)


class TestInferStage:
    def test_shape(self) -> None:
        assert infer_stage_from_path("foo_shape.glb") == "shape"

    def test_lod0(self) -> None:
        assert infer_stage_from_path(Path("/x/hero_lod0.glb")) == "lod0"

    def test_to_paint(self) -> None:
        assert infer_stage_from_path("chapel_to_paint.glb") == "to_paint"


class TestVerifyGlb:
    def test_ok_shape_normals(self, tmp_path: Path) -> None:
        p = tmp_path / "hero_shape.glb"
        # vpt = 3/1 = 3.0 with vert=3 idx=3 → faceted fail
        # Use vert=4 idx=12 → vpt=4/4=1.0
        p.write_bytes(_minimal_glb(vert_count=4, index_count=12))
        r = verify_glb(p, stage="shape")
        assert r.ok
        assert r.meta["has_normals"] is True
        assert r.meta["v_per_tri"] == 1.0

    def test_fail_no_normal(self, tmp_path: Path) -> None:
        p = tmp_path / "x_shape.glb"
        p.write_bytes(_minimal_glb(attrs={"POSITION": 0}, vert_count=4, index_count=12))
        r = verify_glb(p, stage="shape")
        assert not r.ok
        assert any(i.code == "NO_NORMAL" for i in r.fails())

    def test_fail_faceted_vpt3(self, tmp_path: Path) -> None:
        p = tmp_path / "x_shape.glb"
        # 9 verts / 3 tris = 3.0
        p.write_bytes(_minimal_glb(vert_count=9, index_count=9))
        r = verify_glb(p, stage="shape")
        assert not r.ok
        assert any(i.code == "FACETED_VPT3" for i in r.fails())

    def test_collision_ok_without_normal(self, tmp_path: Path) -> None:
        p = tmp_path / "x_collision.glb"
        p.write_bytes(_minimal_glb(attrs={"POSITION": 0}, vert_count=4, index_count=12))
        r = verify_glb(p)
        assert r.stage == "collision"
        assert r.ok

    def test_painted_requires_uv(self, tmp_path: Path) -> None:
        p = tmp_path / "x_painted.glb"
        p.write_bytes(_minimal_glb(attrs={"POSITION": 0, "NORMAL": 1}, vert_count=4, index_count=12))
        r = verify_glb(p, stage="painted")
        assert not r.ok
        assert any(i.code == "NO_UV" for i in r.fails())

    def test_strict_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_GLB_VERIFY", "1")
        monkeypatch.setenv("GAMEDEV_GLB_VERIFY_STRICT", "1")
        p = tmp_path / "bad_shape.glb"
        p.write_bytes(_minimal_glb(attrs={"POSITION": 0}, vert_count=4, index_count=12))
        with pytest.raises(GlbVerifyError):
            post_save_verify(p, stage="shape")

    def test_extract_meta(self, tmp_path: Path) -> None:
        p = tmp_path / "m.glb"
        p.write_bytes(_minimal_glb(vert_count=6, index_count=12))
        m = extract_glb_meta(p)
        assert m["has_normals"] is True
        assert m["triangle_count_total"] == 4
        assert m["v_per_tri"] == 1.5
