"""Tests for the no-bpy GLB binary metadata parser ``glb_meta``.

Builds minimal but structurally-valid GLB binaries in memory with ``struct`` +
``json`` and feeds them to ``glb_extract_meta``. Covers the happy path
(attributes, indices, extensions, texture mime types, bounding-box aggregation)
and the robustness path (wrong magic, truncated files, bad chunk lengths,
malformed JSON bodies). No Blender/bpy is required.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

import pytest

from gamedev_lab.glb_meta import glb_extract_meta

# GLB binary format constants (magic numbers — documented inline).
GLB_MAGIC = b"glTF"
JSON_CHUNK_TYPE = 0x4E4F534A  # b"JSON" read as a little-endian uint32.
BIN_CHUNK_TYPE = 0x004E4942  # b"BIN\0" read as a little-endian uint32.


def _pad4(data: bytes, fill: bytes = b"\x00") -> bytes:
    """Pad ``data`` with ``fill`` up to the next 4-byte boundary."""
    remainder = len(data) % 4
    return data if remainder == 0 else data + fill * (4 - remainder)


def build_glb(gltf: dict[str, Any], bin_data: bytes = b"") -> bytes:
    """Build a structurally-valid GLB binary in memory.

    Layout (matches what ``glb_extract_meta`` parses):
        header (12B): magic "glTF" + version uint32 + total length uint32
        JSON chunk:  length uint32 + type uint32 + JSON bytes
        BIN  chunk:  optional, length uint32 + type uint32 + bytes

    The JSON chunk is space-padded to 4-byte alignment (trailing whitespace is
    ignored by ``json.loads``), so the declared chunk length covers the padded
    payload exactly as the parser expects.
    """
    json_bytes = json.dumps(gltf).encode("utf-8")
    json_padded = _pad4(json_bytes, fill=b" ")
    chunks = struct.pack("<II", len(json_padded), JSON_CHUNK_TYPE) + json_padded
    if bin_data:
        bin_padded = _pad4(bin_data)
        chunks += struct.pack("<II", len(bin_padded), BIN_CHUNK_TYPE) + bin_padded
    total = 12 + len(chunks)
    header = struct.pack("<4sII", GLB_MAGIC, 2, total)
    return header + chunks


def write_glb(tmp_path: Path, gltf: dict[str, Any], name: str = "model.glb", bin_data: bytes = b"") -> Path:
    """Write a built GLB to ``tmp_path`` and return its path."""
    path = tmp_path / name
    path.write_bytes(build_glb(gltf, bin_data))
    return path


def base_gltf(**extra: Any) -> dict[str, Any]:
    """Return a minimal valid glTF document merged with ``extra`` keys."""
    gltf: dict[str, Any] = {"asset": {"version": "2.0"}}
    gltf.update(extra)
    return gltf


class TestMeshAttributes:
    """Coverage of attribute, primitive and bounding-box extraction."""

    def test_minimal_mesh(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, -1.0, 0.0]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
            nodes=[{"mesh": 0}],
            scenes=[{"nodes": [0]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["attributes_present"] == ["POSITION"]
        assert meta["attributes_per_primitive"] == [["POSITION"]]
        assert meta["primitive_count"] == 1
        assert meta["has_tangents"] is False
        assert meta["v_per_tri"] is None
        assert meta["world_bounds_y_min"] == pytest.approx(-1.0)
        assert meta["vertex_count_total"] == 3
        assert meta["triangle_count_total"] == 0
        assert meta["extensions_used"] == []
        assert meta["extensions_required"] == []
        assert meta["texture_mime_types"] == []

    @pytest.mark.parametrize(
        "attr_name",
        ["NORMAL", "TEXCOORD_0", "TANGENT", "COLOR_0"],
    )
    def test_extra_attribute_is_collected(self, tmp_path: Path, attr_name: str) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}, {"count": 1}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0, attr_name: 1}}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert attr_name in meta["attributes_present"]
        assert "POSITION" in meta["attributes_present"]

    def test_tangent_flag(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}, {"count": 3}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0, "TANGENT": 1}}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["has_tangents"] is True
        assert "TANGENT" in meta["attributes_present"]

    def test_indices_face_count(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[
                {"count": 4, "min": [0.0, 0.0, 0.0]},  # POSITION
                {"count": 6},  # indices -> 2 triangles
            ],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["triangle_count_total"] == 2
        assert meta["vertex_count_total"] == 4
        assert meta["v_per_tri"] == pytest.approx(2.0)

    def test_v_per_tri_rounding(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 10, "min": [0.0, 0.0, 0.0]}, {"count": 9}],  # 3 tris
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["v_per_tri"] == pytest.approx(3.3333)

    def test_multiple_primitives_union_and_order(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, -1.0, 0.0]}, {"count": 3}],
            meshes=[
                {
                    "primitives": [
                        {"attributes": {"POSITION": 0}},
                        {"attributes": {"POSITION": 0, "NORMAL": 1}},
                    ]
                }
            ],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["primitive_count"] == 2
        assert meta["attributes_per_primitive"] == [["POSITION"], ["NORMAL", "POSITION"]]
        assert meta["attributes_present"] == ["NORMAL", "POSITION"]
        assert meta["vertex_count_total"] == 6

    def test_bounds_aggregate_min_across_primitives(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[
                {"count": 3, "min": [0.0, 2.0, 0.0]},
                {"count": 3, "min": [0.0, -3.0, 0.0]},
            ],
            meshes=[
                {
                    "primitives": [
                        {"attributes": {"POSITION": 0}},
                        {"attributes": {"POSITION": 1}},
                    ]
                }
            ],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["world_bounds_y_min"] == pytest.approx(-3.0)

    def test_bounds_none_when_min_too_short(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0]}],  # only 2 components
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["world_bounds_y_min"] is None

    def test_out_of_range_accessor_is_skipped(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 99}}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["attributes_present"] == ["POSITION"]
        assert meta["vertex_count_total"] == 0
        assert meta["world_bounds_y_min"] is None

    def test_negative_indices_skipped(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}, "indices": -1}]}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["triangle_count_total"] == 0
        assert meta["v_per_tri"] is None

    def test_no_meshes_document(self, tmp_path: Path) -> None:
        meta = glb_extract_meta(write_glb(tmp_path, base_gltf()))
        assert meta["primitive_count"] == 0
        assert meta["attributes_present"] == []
        assert meta["vertex_count_total"] == 0
        assert meta["v_per_tri"] is None
        assert meta["world_bounds_y_min"] is None

    def test_accepts_str_and_path(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}],
            meshes=[{"primitives": [{"attributes": {"POSITION": 0}}]}],
        )
        path = write_glb(tmp_path, gltf)
        assert glb_extract_meta(path) == glb_extract_meta(str(path))


class TestExtensions:
    """Coverage of ``extensionsUsed`` / ``extensionsRequired`` surfacing."""

    @pytest.mark.parametrize(
        "ext",
        ["KHR_draco_mesh_compression", "EXT_meshopt_compression", "KHR_materials_pbrSpecularGlossiness"],
    )
    def test_extension_used_surfaced(self, tmp_path: Path, ext: str) -> None:
        gltf = base_gltf(extensionsUsed=[ext])
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert ext in meta["extensions_used"]

    def test_multiple_extensions(self, tmp_path: Path) -> None:
        exts = ["KHR_draco_mesh_compression", "EXT_meshopt_compression"]
        meta = glb_extract_meta(write_glb(tmp_path, base_gltf(extensionsUsed=exts)))
        assert meta["extensions_used"] == exts

    def test_extensions_required(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            extensionsUsed=["EXT_meshopt_compression"],
            extensionsRequired=["EXT_meshopt_compression"],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["extensions_required"] == ["EXT_meshopt_compression"]

    def test_draco_extension(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            extensionsUsed=["KHR_draco_mesh_compression"],
            accessors=[{"count": 3, "min": [0.0, 0.0, 0.0]}],
            meshes=[
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0},
                            "extensions": {"KHR_draco_mesh_compression": {"bufferView": 0}},
                        }
                    ]
                }
            ],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert "KHR_draco_mesh_compression" in meta["extensions_used"]

    def test_meshopt_extension(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            extensionsUsed=["EXT_meshopt_compression"],
            extensionsRequired=["EXT_meshopt_compression"],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert "EXT_meshopt_compression" in meta["extensions_used"]
        assert "EXT_meshopt_compression" in meta["extensions_required"]


class TestTextureMimeTypes:
    """Coverage of the image mime-type heuristics (ktx2/webp/png/unknown)."""

    @pytest.mark.parametrize(
        ("extra", "expected"),
        [
            ({"extensionsUsed": ["KHR_texture_basisu"], "images": [{}]}, ["image/ktx2"]),
            ({"images": [{"mimeType": "image/ktx2"}]}, ["image/ktx2"]),
            ({"images": [{"mimeType": "image/png"}]}, ["image/png"]),
            ({"images": [{"mimeType": "image/jpeg"}]}, ["image/jpeg"]),
            ({"images": [{"mimeType": "image/webp"}]}, ["image/webp"]),
            ({"images": [{"extensions": {"EXT_texture_webp": {}}}]}, ["image/webp"]),
            ({"images": [{}]}, ["unknown"]),
            ({"images": []}, []),
        ],
    )
    def test_mime_type_resolution(self, tmp_path: Path, extra: dict[str, Any], expected: list[str]) -> None:
        meta = glb_extract_meta(write_glb(tmp_path, base_gltf(**extra)))
        assert meta["texture_mime_types"] == expected

    def test_multiple_images(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            extensionsUsed=["KHR_texture_basisu"],
            images=[{"mimeType": "image/png"}, {}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["texture_mime_types"] == ["image/png", "image/ktx2"]


class TestMalformedInput:
    """Malformed or truncated inputs must fail cleanly, never with a traceback."""

    def test_wrong_magic_returns_error_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.glb"
        path.write_bytes(b"XXXX" + b"\x00" * 20)
        meta = glb_extract_meta(path)
        assert "_error" in meta
        assert "GLB" in meta["_error"]

    def test_truncated_under_minimum_length(self, tmp_path: Path) -> None:
        path = tmp_path / "short.glb"
        path.write_bytes(b"glTF\x00")
        assert "_error" in glb_extract_meta(path)

    def test_truncated_under_full_header(self, tmp_path: Path) -> None:
        path = tmp_path / "partial.glb"
        path.write_bytes(GLB_MAGIC + b"\x00" * 8)  # 12 bytes < 20-byte guard
        assert "_error" in glb_extract_meta(path)

    def test_bad_chunk_length_raises(self, tmp_path: Path) -> None:
        # Declared chunk length intentionally extends past the valid JSON into garbage.
        payload = json.dumps({"asset": {"version": "2.0"}, "meshes": []}).encode() + b"\x00\x00GARBAGE"
        json_len = len(payload)
        total = 12 + 8 + len(payload)
        blob = struct.pack("<4sII", GLB_MAGIC, 2, total) + struct.pack("<II", json_len, JSON_CHUNK_TYPE) + payload
        path = tmp_path / "badlen.glb"
        path.write_bytes(blob)
        with pytest.raises(json.JSONDecodeError):
            glb_extract_meta(path)

    def test_malformed_json_body_raises(self, tmp_path: Path) -> None:
        payload = b"{ not valid json"
        json_len = len(payload)
        total = 12 + 8 + len(payload)
        blob = struct.pack("<4sII", GLB_MAGIC, 2, total) + struct.pack("<II", json_len, JSON_CHUNK_TYPE) + payload
        path = tmp_path / "badjson.glb"
        path.write_bytes(blob)
        with pytest.raises(json.JSONDecodeError):
            glb_extract_meta(path)

    def test_empty_file_returns_error_dict(self, tmp_path: Path) -> None:
        path = tmp_path / "empty.glb"
        path.write_bytes(b"")
        assert "_error" in glb_extract_meta(path)


class TestCompositeDocument:
    """Rich documents with skins/nodes/animations must not confuse the mesh parser.

    ``glb_extract_meta`` intentionally does not extract armature/skin metadata —
    it only parses meshes, accessors, images and extensions. These tests pin
    that contract: a rich document must still yield correct *mesh* facts.
    """

    def test_document_with_skin_and_animation(self, tmp_path: Path) -> None:
        gltf = base_gltf(
            accessors=[
                {"count": 4, "min": [0.0, 0.0, 0.0]},  # 0 POSITION
                {"count": 6},  # 1 indices -> 2 triangles
                {"count": 4},  # 2 JOINTS_0
                {"count": 4},  # 3 WEIGHTS_0
            ],
            meshes=[
                {
                    "primitives": [
                        {
                            "attributes": {"POSITION": 0, "JOINTS_0": 2, "WEIGHTS_0": 3},
                            "indices": 1,
                        }
                    ]
                }
            ],
            nodes=[
                {"mesh": 0, "skin": 0},
                {"children": [2, 3], "name": "Root"},
                {"name": "BoneA"},
                {"name": "BoneB"},
            ],
            skins=[{"joints": [2, 3], "inverseBindMatrices": 0, "skeleton": 1}],
            animations=[{"channels": [], "samplers": []}],
        )
        meta = glb_extract_meta(write_glb(tmp_path, gltf))
        assert meta["attributes_present"] == ["JOINTS_0", "POSITION", "WEIGHTS_0"]
        assert meta["primitive_count"] == 1
        assert meta["triangle_count_total"] == 2
        assert meta["world_bounds_y_min"] == pytest.approx(0.0)
