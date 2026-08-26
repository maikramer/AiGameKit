"""Unit tests for the gameassets manifest path resolver."""

from __future__ import annotations

from pathlib import Path

from gameassets.helpers import _resolve_manifest_path


def test_resolve_plain_name_appends_yaml(tmp_path: Path) -> None:
    (tmp_path / "manifest.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "manifest")
    assert resolved == tmp_path / "manifest.yaml"


def test_resolve_dotted_name_does_not_replace(tmp_path: Path) -> None:
    """Regression: ``Path('manifest.dark_forest').with_suffix('.yaml')`` would
    wrongly produce ``manifest.yaml``. The resolver must keep the dotted name
    and only append the extension."""
    (tmp_path / "manifest.dark_forest.yaml").write_text("assets: []", encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "manifest.dark_forest")
    assert resolved == tmp_path / "manifest.dark_forest.yaml"
    assert resolved != tmp_path / "manifest.yaml"


def test_resolve_explicit_yaml_returned_as_is(tmp_path: Path) -> None:
    f = tmp_path / "profile.yaml"
    f.write_text("assets: []", encoding="utf-8")
    assert _resolve_manifest_path(f) == f


def test_resolve_explicit_yml_returned_as_is(tmp_path: Path) -> None:
    f = tmp_path / "alt.yml"
    f.write_text("assets: []", encoding="utf-8")
    assert _resolve_manifest_path(f) == f


def test_resolve_prefers_yaml_over_yml(tmp_path: Path) -> None:
    (tmp_path / "name.yaml").write_text("a: 1", encoding="utf-8")
    (tmp_path / "name.yml").write_text("a: 2", encoding="utf-8")
    resolved = _resolve_manifest_path(tmp_path / "name")
    assert resolved.suffix == ".yaml"


def test_resolve_missing_file_returns_yaml_appended(tmp_path: Path) -> None:
    resolved = _resolve_manifest_path(tmp_path / "nonexistent.dark_forest")
    assert resolved == tmp_path / "nonexistent.dark_forest.yaml"


def test_resolve_with_subdirectory(tmp_path: Path) -> None:
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "manifest.desert.yaml").write_text("assets: []", encoding="utf-8")
    resolved = _resolve_manifest_path(sub / "manifest.desert")
    assert resolved == sub / "manifest.desert.yaml"


class TestManifestOutputDirIsRelativeToManifest:
    """``output_dir`` resolve-se contra a pasta do **manifest**, não do perfil.

    Regressão dos packs partilhados: `shared-assets/manifests/*.yaml` traziam
    `output_dir: public/assets` (herdado de um layout em que o manifest vivia à
    raiz), o que apontava para `manifests/public/assets`. Resultado: `resume`
    classificava os 24 assets como `need_image` e o happy path do README
    regenerava tudo do zero em vez de fazer skip.
    """

    @staticmethod
    def _row():
        from gameassets.manifest import ManifestRow

        return ManifestRow(id="chapel", idea="a chapel", kind="environment", generate_3d=True)

    def test_output_dir_walks_up_from_manifest_subdir(self, tmp_path: Path) -> None:
        from gameassets.paths import _paths_for_row_manifest
        from gameassets.profile import GameProfile

        profile = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="painterly",
            output_dir="../public/assets",
            images_subdir="images/village",
            meshes_subdir="meshes/village",
            image_ext="png",
        )
        img, mesh = _paths_for_row_manifest(profile, tmp_path / "manifests", self._row())
        assert img == (tmp_path / "public/assets/images/village/chapel.png").resolve()
        assert mesh == (tmp_path / "public/assets/meshes/village/chapel.glb").resolve()

    def test_bare_output_dir_stays_under_the_manifest_dir(self, tmp_path: Path) -> None:
        from gameassets.paths import _paths_for_row_manifest
        from gameassets.profile import GameProfile

        profile = GameProfile(
            title="t",
            genre="g",
            tone="t",
            style_preset="painterly",
            output_dir="public/assets",
            images_subdir="images",
            image_ext="png",
        )
        img, _mesh = _paths_for_row_manifest(profile, tmp_path / "manifests", self._row())
        assert img == (tmp_path / "manifests/public/assets/images/chapel.png").resolve()
