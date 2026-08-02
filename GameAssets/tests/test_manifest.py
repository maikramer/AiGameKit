"""Testes do manifest YAML."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
import yaml

from gameassets.manifest import ManifestRow, effective_collision_args, effective_image_source, load_manifest
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
        assert rows[0].generate_paint is False
        assert rows[1].generate_3d is False
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_yaml_pipeline() -> None:
    content = yaml.dump(
        {
            "assets": [
                {
                    "id": "hero",
                    "idea": "chibi hero",
                    "kind": "character",
                    "pipeline": ["3d", "paint", "rig", "animate"],
                },
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
        assert rows[0].generate_paint is True
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


def test_load_manifest_seed_override() -> None:
    content = yaml.dump(
        {
            "assets": [
                {"id": "a", "idea": "casa", "pipeline": ["3d"], "seed": 90210},
                {"id": "b", "idea": "muro", "pipeline": ["3d"]},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].seed == 90210
        assert rows[1].seed is None
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_seed_invalid() -> None:
    import pytest

    content = yaml.dump({"assets": [{"id": "a", "idea": "casa", "pipeline": ["3d"], "seed": "abc"}]})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="seed inválido"):
            load_manifest(path)
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_text3d_block() -> None:
    content = yaml.dump(
        {
            "assets": [
                {
                    "id": "hero",
                    "idea": "herói",
                    "pipeline": ["3d"],
                    "text3d": {"steps": 40, "octree_resolution": 384, "mc_level": 0},
                },
                {"id": "prop", "idea": "prop", "pipeline": ["3d"]},
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
        assert rows[0].text3d is not None
        assert rows[0].text3d.steps == 40
        assert rows[0].text3d.octree_resolution == 384
        assert rows[0].text3d.mc_level == 0.0
        assert rows[1].text3d is None
    finally:
        path.unlink(missing_ok=True)


def test_load_manifest_text3d_unknown_key_fails() -> None:
    import pytest

    content = yaml.dump({"assets": [{"id": "a", "idea": "x", "pipeline": ["3d"], "text3d": {"octree": 384}}]})
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        with pytest.raises(ValueError, match="chaves desconhecidas"):
            load_manifest(path)
    finally:
        path.unlink(missing_ok=True)


def test_apply_row_text3d_overrides_and_mc_level() -> None:
    from gameassets.manifest import RowText3D, apply_row_text3d_overrides, row_mc_level

    row = ManifestRow(
        id="hero",
        idea="x",
        kind="character",
        generate_3d=True,
        text3d=RowText3D(steps=40, octree_resolution=384, mc_level=0.0),
    )
    item: dict = {"steps": 20, "octree_resolution": 256, "num_chunks": 4096}
    out = apply_row_text3d_overrides(item, row)
    assert out["steps"] == 40
    assert out["octree_resolution"] == 384
    assert out["mc_level"] == 0.0
    assert out["num_chunks"] == 4096  # não tocado
    # row_mc_level: override ganha do profile
    assert row_mc_level(row, "auto") == 0.0
    row_plain = ManifestRow(id="p", idea="x", kind="prop", generate_3d=True)
    assert row_mc_level(row_plain, "auto") == "auto"
    # Sem override: item intacto
    item2: dict = {"steps": 20}
    assert apply_row_text3d_overrides(item2, row_plain) == {"steps": 20}


def test_row_collision_and_effective_args() -> None:
    content = yaml.dump(
        {
            "assets": [
                {
                    "id": "city_gate_arch",
                    "idea": "arch",
                    "pipeline": ["3d", "collision"],
                    "collision": {
                        "mode": "envelope",
                        "max_faces": 256,
                        "voxel_size": 0.08,
                        "inflate": 0.1,
                    },
                }
            ]
        }
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(content)
        path = Path(f.name)
    try:
        rows = load_manifest(path)
    finally:
        path.unlink(missing_ok=True)
    row = rows[0]
    assert row.collision is not None
    assert row.collision.mode == "envelope"
    assert row.collision.inflate == 0.1
    from gameassets.profile import GameProfile

    gp = GameProfile.from_dict(
        {
            "title": "t",
            "genre": "g",
            "tone": "x",
            "style_preset": "lowpoly",
            "output_dir": "/tmp",
            "collision": {"mode": "hull", "max_faces": 64},
        }
    )
    args = effective_collision_args(gp, row)
    assert args == {"mode": "envelope", "max_faces": 256, "voxel_size": 0.08, "inflate": 0.1}


class TestManifestConfig:
    """Manifest define a pasta: chaves top-level ``output_dir``/subdirs antes de ``assets:``."""

    @staticmethod
    def _write(content: str) -> Path:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
            f.write(content)
            return Path(f.name)

    def test_manifest_config_parsed(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump(
            {
                "output_dir": "../public/assets",
                "meshes_subdir": "meshes/characters",
                "images_subdir": "images/characters",
                "assets": [{"id": "hero", "idea": "hero", "pipeline": ["3d"]}],
            }
        )
        path = self._write(content)
        try:
            rows, cfg = load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)
        assert len(rows) == 1
        assert cfg.output_dir == "../public/assets"
        assert cfg.meshes_subdir == "meshes/characters"
        assert cfg.images_subdir == "images/characters"
        assert cfg.audio_subdir is None
        assert cfg.path_layout is None
        assert cfg.image_ext is None

    def test_manifest_config_all_keys(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump(
            {
                "output_dir": "/tmp/out",
                "path_layout": "flat",
                "images_subdir": "imgs/",
                "meshes_subdir": "/glbs/",
                "audio_subdir": "snd",
                "image_ext": "jpg",
                "assets": [{"id": "a", "idea": "i", "pipeline": []}],
            }
        )
        path = self._write(content)
        try:
            _rows, cfg = load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)
        assert cfg.output_dir == "/tmp/out"
        assert cfg.path_layout == "flat"
        assert cfg.images_subdir == "imgs"
        assert cfg.meshes_subdir == "glbs"
        assert cfg.audio_subdir == "snd"
        assert cfg.image_ext == "jpg"

    def test_manifest_config_list_doc_is_empty(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump([{"id": "a", "idea": "i", "pipeline": []}])
        path = self._write(content)
        try:
            rows, cfg = load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)
        assert len(rows) == 1
        assert cfg.output_dir is None
        assert cfg.meshes_subdir is None

    def test_manifest_config_unknown_top_level_ignored(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump(
            {
                "title": "grupo qualquer",
                "output_dir": "../out",
                "assets": [{"id": "a", "idea": "i", "pipeline": []}],
            }
        )
        path = self._write(content)
        try:
            _rows, cfg = load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)
        assert cfg.output_dir == "../out"

    def test_manifest_config_invalid_path_layout(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump({"path_layout": "nested", "assets": [{"id": "a", "idea": "i", "pipeline": []}]})
        path = self._write(content)
        try:
            with pytest.raises(ValueError, match="path_layout"):
                load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)

    def test_manifest_config_empty_subdir_rejected(self) -> None:
        from gameassets.manifest import load_manifest_bundle

        content = yaml.dump({"meshes_subdir": "", "assets": [{"id": "a", "idea": "i", "pipeline": []}]})
        path = self._write(content)
        try:
            with pytest.raises(ValueError, match="meshes_subdir"):
                load_manifest_bundle(path)
        finally:
            path.unlink(missing_ok=True)

    def test_load_manifest_ignores_config(self) -> None:
        content = yaml.dump(
            {
                "output_dir": "/tmp/out",
                "meshes_subdir": "meshes/x",
                "assets": [{"id": "a", "idea": "i", "pipeline": []}],
            }
        )
        path = self._write(content)
        try:
            rows = load_manifest(path)
        finally:
            path.unlink(missing_ok=True)
        assert [r.id for r in rows] == ["a"]
