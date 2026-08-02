"""Testes do verificador GLB pós-export."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import pytest

from aigamekit_shared.glb_verify import (
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
        monkeypatch.setenv("AIGAMEKIT_GLB_VERIFY", "1")
        monkeypatch.setenv("AIGAMEKIT_GLB_VERIFY_STRICT", "1")
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


def _glb_with_nodes(
    *,
    acc_min: list[float],
    acc_max: list[float],
    component_type: int = 5126,
    normalized: bool = False,
    node: dict | None = None,
) -> bytes:
    """GLB com cena/nó — permite exercitar bounds em espaço-mundo."""
    accessors = [
        {
            "count": 3,
            "type": "VEC3",
            "componentType": component_type,
            "min": acc_min,
            "max": acc_max,
        },
        {"count": 3, "type": "SCALAR", "componentType": 5123},
    ]
    if normalized:
        accessors[0]["normalized"] = True
    mesh_node: dict = {"mesh": 0}
    mesh_node.update(node or {})
    doc = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [mesh_node],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        "accessors": accessors,
        "buffers": [{"byteLength": 1}],
        "bufferViews": [{"buffer": 0, "byteLength": 1}],
    }
    js = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    js += b" " * ((4 - (len(js) % 4)) % 4)
    bin_chunk = b"\x00\x00\x00\x00"
    out = bytearray()
    out += struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(bin_chunk))
    out += struct.pack("<I4s", len(js), b"JSON")
    out += js
    out += struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
    out += bin_chunk
    return bytes(out)


class TestGlbWorldBounds:
    def test_identity_node_passthrough(self, tmp_path: Path) -> None:
        from aigamekit_shared.glb_verify import glb_world_bounds

        p = tmp_path / "a.glb"
        p.write_bytes(_glb_with_nodes(acc_min=[-1.0, 0.0, -2.0], acc_max=[1.0, 3.0, 2.0]))
        lo, hi = glb_world_bounds(p)
        assert lo == pytest.approx([-1.0, 0.0, -2.0])
        assert hi == pytest.approx([1.0, 3.0, 2.0])

    def test_node_translation_and_scale_applied(self, tmp_path: Path) -> None:
        from aigamekit_shared.glb_verify import glb_world_bounds

        p = tmp_path / "b.glb"
        p.write_bytes(
            _glb_with_nodes(
                acc_min=[-1.0, -1.0, -1.0],
                acc_max=[1.0, 1.0, 1.0],
                node={"translation": [0.0, 5.0, 0.0], "scale": [2.0, 2.0, 2.0]},
            )
        )
        lo, hi = glb_world_bounds(p)
        assert lo == pytest.approx([-2.0, 3.0, -2.0])
        assert hi == pytest.approx([2.0, 7.0, 2.0])

    def test_quantized_positions_dequantized(self, tmp_path: Path) -> None:
        """KHR_mesh_quantization: SHORT normalizado + escala no nó.

        Regressão: o accessor cru dava y_min=-32767 e o check ORIGIN_Y
        disparava em todo o lod0 finalizado do projecto.
        """
        from aigamekit_shared.glb_verify import extract_glb_meta, glb_world_bounds

        p = tmp_path / "q.glb"
        p.write_bytes(
            _glb_with_nodes(
                acc_min=[-32767, -32767, -32767],
                acc_max=[32767, 32767, 32767],
                component_type=5122,
                normalized=True,
                node={"translation": [0.0, 3.5, 0.0], "scale": [3.5, 3.5, 3.5]},
            )
        )
        lo, hi = glb_world_bounds(p)
        assert lo == pytest.approx([-3.5, 0.0, -3.5])
        assert hi == pytest.approx([3.5, 7.0, 3.5])
        assert extract_glb_meta(p)["world_bounds_y_min"] == pytest.approx(0.0)

    def test_quantized_lod0_does_not_warn_origin_y(self, tmp_path: Path) -> None:
        p = tmp_path / "thing_lod0.glb"
        p.write_bytes(
            _glb_with_nodes(
                acc_min=[-32767, -32767, -32767],
                acc_max=[32767, 32767, 32767],
                component_type=5122,
                normalized=True,
                node={"translation": [0.0, 3.5, 0.0], "scale": [3.5, 3.5, 3.5]},
            )
        )
        r = verify_glb(p, stage="lod0")
        assert not any(i.code == "ORIGIN_Y" for i in r.issues)

    def test_no_nodes_returns_none(self, tmp_path: Path) -> None:
        from aigamekit_shared.glb_verify import glb_world_bounds

        p = tmp_path / "c.glb"
        p.write_bytes(_minimal_glb())
        assert glb_world_bounds(p) is None
