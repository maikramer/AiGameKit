"""Helpers bpy 5.2 meshopt / version detection."""

from __future__ import annotations

from unittest.mock import patch

from gamedev_shared.bpy_mesh import (
    gltf_export_supports_meshopt,
    gltf_import_supports_meshopt,
    gltf_meshopt_export_kwargs,
)


def test_gltf_meshopt_export_kwargs_empty_when_unsupported() -> None:
    with patch("gamedev_shared.bpy_mesh.gltf_export_supports_meshopt", return_value=False):
        assert gltf_meshopt_export_kwargs(enable=True) == {}


def test_gltf_meshopt_export_kwargs_when_supported() -> None:
    with patch("gamedev_shared.bpy_mesh.gltf_export_supports_meshopt", return_value=True):
        kw = gltf_meshopt_export_kwargs(enable=True, extension="EXT_meshopt_compression")
        assert kw["export_meshopt_compression_enable"] is True
        assert kw["export_meshopt_extension"] == "EXT_meshopt_compression"


def test_gltf_meshopt_export_kwargs_disabled() -> None:
    with patch("gamedev_shared.bpy_mesh.gltf_export_supports_meshopt", return_value=True):
        assert gltf_meshopt_export_kwargs(enable=False) == {}


def test_gltf_import_supports_meshopt_version_gate() -> None:
    with patch("gamedev_shared.bpy_mesh.bpy_version_tuple", return_value=(5, 1, 2)):
        assert gltf_import_supports_meshopt() is False
    with patch("gamedev_shared.bpy_mesh.bpy_version_tuple", return_value=(5, 2, 0)):
        assert gltf_import_supports_meshopt() is True
    with patch("gamedev_shared.bpy_mesh.bpy_version_tuple", return_value=None):
        assert gltf_import_supports_meshopt() is False


def test_gltf_export_supports_meshopt_returns_bool() -> None:
    assert isinstance(gltf_export_supports_meshopt(), bool)
