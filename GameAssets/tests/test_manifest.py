"""Testes do manifest YAML."""

from __future__ import annotations

import tempfile
from pathlib import Path

import yaml

from gameassets.manifest import ManifestRow, effective_image_source, load_manifest
from gameassets.profile import load_profile


def test_load_manifest_yaml_basic() -> None:
    content = yaml.dump(
        {
            "assets": [
                {"id": "a", "idea": "idea one", "kind": "prop", "pipeline": ["3d"]},
                {"id": "b", "idea": "idea two", "pipeline": []},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert len(rows) == 2
        assert rows[0].id == "a"
        assert rows[0].generate_3d is True
        assert rows[1].generate_3d is False
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_yaml_pipeline() -> None:
    content = yaml.dump(
        {
            "assets": [
                {"id": "hero", "idea": "chibi hero", "kind": "character", "pipeline": ["3d", "rig", "animate"]},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].generate_3d is True
        assert rows[0].generate_rig is True
        assert rows[0].generate_animate is True
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_yaml_audio() -> None:
    content = yaml.dump(
        {
            "assets": [
                {"id": "sfx", "idea": "collect sound", "kind": "prop", "pipeline": ["audio"]},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].generate_audio is True
        assert rows[0].generate_3d is False
    finally:
        path.unlink(missing_ok=True)


def test_effective_image_source_row_override() -> None:
    data = {
        "title": "T",
        "genre": "G",
        "tone": "t",
        "style_preset": "lowpoly",
        "image_source": "text2d",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(data, f)
        path = Path(f.name)
    try:
        p = load_profile(path)
        row_default = ManifestRow(id="x", idea="y", kind=None, generate_3d=False, image_source=None)
        assert effective_image_source(p, row_default) == "text2d"
        row_tex = ManifestRow(
            id="x",
            idea="y",
            kind=None,
            generate_3d=False,
            image_source="texture2d",
        )
        assert effective_image_source(p, row_tex) == "texture2d"
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_yaml_image_source() -> None:
    content = yaml.dump(
        {
            "assets": [
                {"id": "a", "idea": "tile", "pipeline": [], "image_source": "texture2d"},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].image_source == "texture2d"
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_empty_raises() -> None:
    content = yaml.dump({"assets": []})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        import pytest

        with pytest.raises(ValueError, match="id"):
            load_manifest(path)
    finally:
        path.unlink(missing_ok=True)
