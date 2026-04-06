"""Testes unitários de GameProfile (YAML → dataclass)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from gameassets.profile import GameProfile, load_profile


def test_from_dict_minimal() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
        }
    )
    assert p.title == "A"
    assert p.output_dir == "."
    assert p.image_ext == "png"
    assert p.text3d is None


def test_from_dict_rigging3d() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "rigging3d": {
                "output_suffix": "_skinned",
                "root": "/opt/unirig",
                "python": "/env/bin/python",
            },
        }
    )
    assert p.rigging3d is not None
    assert p.rigging3d.output_suffix == "_skinned"
    assert p.rigging3d.root == "/opt/unirig"
    assert p.rigging3d.python == "/env/bin/python"


def test_from_dict_rigging3d_output_suffix_adds_underscore() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "rigging3d": {"output_suffix": "rigged"},
        }
    )
    assert p.rigging3d is not None
    assert p.rigging3d.output_suffix == "_rigged"


def test_from_dict_animator3d() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "animator3d": {"preset": "humanoid"},
        }
    )
    assert p.animator3d is not None
    assert p.animator3d.preset == "humanoid"


def test_from_dict_text3d() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "text3d": {"preset": "fast", "low_vram": True, "texture": False},
        }
    )
    assert p.text3d is not None
    assert p.text3d.preset == "fast"
    assert p.text3d.low_vram is True
    assert p.text3d.texture is False


def test_from_dict_text3d_texture_defaults_true() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "text3d": {"preset": "balanced"},
        }
    )
    assert p.text3d is not None
    assert p.text3d.texture is True


def test_from_dict_path_layout_invalid() -> None:
    with pytest.raises(ValueError, match="path_layout"):
        GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "path_layout": "weird",
            }
        )


def test_from_dict_text3d_hunyuan_explicit() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "text3d": {
                "preset": "balanced",
                "steps": 26,
                "octree_resolution": 160,
                "low_vram": False,
                "texture": True,
                "no_mesh_repair": False,
            },
        }
    )
    assert p.text3d is not None
    assert p.text3d.steps == 26
    assert p.text3d.octree_resolution == 160


def test_from_dict_invalid_preset() -> None:
    with pytest.raises(ValueError, match="preset"):
        GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "text3d": {"preset": "invalid"},
            }
        )


def test_from_dict_texture2d_defaults() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "image_source": "texture2d",
        }
    )
    assert p.image_source == "texture2d"
    assert p.texture2d is not None
    assert p.texture2d.materialize is False


def test_from_dict_texture2d_materialize() -> None:
    p = GameProfile.from_dict(
        {
            "title": "A",
            "genre": "B",
            "tone": "C",
            "style_preset": "lowpoly",
            "image_source": "texture2d",
            "texture2d": {
                "width": 512,
                "materialize": True,
                "materialize_maps_subdir": "maps",
                "materialize_format": "png",
            },
        }
    )
    assert p.texture2d is not None
    assert p.texture2d.width == 512
    assert p.texture2d.materialize is True
    assert p.texture2d.materialize_maps_subdir == "maps"


def test_from_dict_image_source_invalid() -> None:
    with pytest.raises(ValueError, match="image_source"):
        GameProfile.from_dict(
            {
                "title": "A",
                "genre": "B",
                "tone": "C",
                "style_preset": "lowpoly",
                "image_source": "flux",
            }
        )


def test_load_profile_roundtrip() -> None:
    data = {
        "title": "T",
        "genre": "G",
        "tone": "light",
        "style_preset": "lowpoly",
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        yaml.safe_dump(data, f)
        path = Path(f.name)
    try:
        p = load_profile(path)
        assert p.title == "T"
    finally:
        path.unlink(missing_ok=True)
