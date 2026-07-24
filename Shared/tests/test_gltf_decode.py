"""Tests for gamedev_shared.gltf_decode (KTX2/meshopt pre-decode for bpy)."""

from __future__ import annotations

import json
import struct
from pathlib import Path
from unittest.mock import patch

from gamedev_shared.gltf_decode import (
    EXT_BASISU,
    EXT_MESHOPT,
    bpy_decode_subcommand,
    bpy_readable_glb,
    glb_extensions,
    run_gltf_transform,
)


def make_glb(tmp_path: Path, doc: dict, name: str = "test.glb") -> Path:
    """Build a minimal valid GLB container with *doc* as the JSON chunk."""
    payload = json.dumps(doc).encode("utf-8")
    payload += b" " * (-len(payload) % 4)
    chunk = struct.pack("<I4s", len(payload), b"JSON") + payload
    header = struct.pack("<4sII", b"glTF", 2, 12 + len(chunk))
    path = tmp_path / name
    path.write_bytes(header + chunk)
    return path


class TestGlbExtensions:
    def test_reads_used_and_required(self, tmp_path):
        path = make_glb(
            tmp_path,
            {
                "extensionsUsed": [EXT_BASISU, EXT_MESHOPT, "KHR_mesh_quantization"],
                "extensionsRequired": [EXT_MESHOPT],
            },
        )
        used, required = glb_extensions(path)
        assert EXT_BASISU in used
        assert required == [EXT_MESHOPT]

    def test_no_extensions(self, tmp_path):
        path = make_glb(tmp_path, {"asset": {"version": "2.0"}})
        assert glb_extensions(path) == ([], [])

    def test_non_glb_file(self, tmp_path):
        path = tmp_path / "not.glb"
        path.write_bytes(b"nope")
        assert glb_extensions(path) == ([], [])

    def test_missing_file(self, tmp_path):
        assert glb_extensions(tmp_path / "absent.glb") == ([], [])


class TestBpyDecodeSubcommand:
    def test_basisu_needs_ktxdecompress(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsUsed": [EXT_BASISU], "extensionsRequired": [EXT_BASISU]})
        assert bpy_decode_subcommand(path) == "ktxdecompress"

    def test_basisu_plus_meshopt_still_ktxdecompress(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": [EXT_BASISU, EXT_MESHOPT]})
        assert bpy_decode_subcommand(path) == "ktxdecompress"

    def test_meshopt_native_bpy_no_decode(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": [EXT_MESHOPT]})
        with patch("gamedev_shared.bpy_mesh.gltf_import_supports_meshopt", return_value=True):
            assert bpy_decode_subcommand(path) is None

    def test_meshopt_old_bpy_copy(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": [EXT_MESHOPT]})
        with patch("gamedev_shared.bpy_mesh.gltf_import_supports_meshopt", return_value=False):
            assert bpy_decode_subcommand(path) == "copy"

    def test_quantization_only_no_decode(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": ["KHR_mesh_quantization"]})
        assert bpy_decode_subcommand(path) is None


class TestRunGltfTransform:
    def test_npx_missing(self, tmp_path):
        with patch("gamedev_shared.gltf_decode.shutil.which", return_value=None):
            ok, err = run_gltf_transform("copy", tmp_path / "a.glb", tmp_path / "b.glb")
        assert not ok
        assert "npx" in err


class TestBpyReadableGlb:
    def test_yields_original_when_native(self, tmp_path):
        path = make_glb(tmp_path, {"asset": {"version": "2.0"}})
        with bpy_readable_glb(path) as readable:
            assert readable == path.resolve()

    def test_falls_back_to_original_on_decode_failure(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": [EXT_BASISU]})
        with (
            patch("gamedev_shared.gltf_decode.run_gltf_transform", return_value=(False, "boom")),
            bpy_readable_glb(path) as readable,
        ):
            assert readable == path.resolve()

    def test_yields_decoded_temp_and_cleans_up(self, tmp_path):
        path = make_glb(tmp_path, {"extensionsRequired": [EXT_BASISU]})

        def fake_transform(subcmd, src, dst):
            assert subcmd == "ktxdecompress"
            dst.write_bytes(b"decoded")
            return True, ""

        with patch("gamedev_shared.gltf_decode.run_gltf_transform", side_effect=fake_transform):
            with bpy_readable_glb(path) as readable:
                assert readable != path.resolve()
                assert readable.read_bytes() == b"decoded"
            assert not readable.exists()
