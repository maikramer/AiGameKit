"""Testes do manifest CSV."""

import tempfile
from pathlib import Path

import pytest
import yaml

from gameassets.manifest import ManifestRow, effective_image_source, load_manifest
from gameassets.profile import load_profile


def test_load_basic() -> None:
    content = "id,idea,kind,generate_3d\na,idea one,prop,true\nb,idea two,,false\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
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


def test_quoted_commas() -> None:
    content = 'id,idea,kind,generate_3d\nx,"hello, world",prop,sim\n'
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].idea == "hello, world"
    finally:
        path.unlink(missing_ok=True)


def test_generate_animate_column() -> None:
    content = (
        "id,idea,kind,generate_3d,generate_rig,generate_animate\na,one,prop,true,true,true\nb,two,,false,false,false\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].generate_animate is True
        assert rows[1].generate_animate is False
    finally:
        path.unlink(missing_ok=True)


def test_generate_rig_column() -> None:
    content = "id,idea,kind,generate_3d,generate_rig\na,one,prop,true,true\nb,two,,false,false\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].generate_rig is True
        assert rows[1].generate_rig is False
    finally:
        path.unlink(missing_ok=True)


def test_image_source_column() -> None:
    content = (
        "id,idea,kind,generate_3d,image_source\n"
        "a,idea one,prop,false,text2d\n"
        "b,idea two,,false,texture2d\n"
        "c,idea three,,false,skymap2d\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].image_source == "text2d"
        assert rows[1].image_source == "texture2d"
        assert rows[2].image_source == "skymap2d"
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


def test_image_source_invalid_raises() -> None:
    content = "id,idea,image_source\na,idea one,blender\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="image_source"):
            load_manifest(path)
    finally:
        path.unlink(missing_ok=True)


def test_missing_columns_raises() -> None:
    content = "a,b\n1,2\n"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="id"):
            load_manifest(path)
    finally:
        path.unlink(missing_ok=True)
