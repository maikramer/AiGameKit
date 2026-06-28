"""Testes para ``text3d.hy3dshape_paths`` — resolucao do codigo vendored."""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from text3d.hy3dshape_paths import ensure_hy3dshape_on_path, resolve_hy3dshape_root


@pytest.fixture(autouse=True)
def _restore_sys_path() -> None:
    saved = list(sys.path)
    yield
    sys.path[:] = saved


class TestResolveHy3dshapeRoot:
    def test_returns_path_named_hy3dshape(self) -> None:
        root = resolve_hy3dshape_root()
        assert root.name == "hy3dshape"

    def test_root_exists_as_directory(self) -> None:
        root = resolve_hy3dshape_root()
        assert root.is_dir()

    def test_root_inside_text3d_package(self) -> None:
        from pathlib import Path

        import text3d

        pkg_dir = Path(text3d.__file__).resolve().parent
        root = resolve_hy3dshape_root()
        assert root.parent == pkg_dir

    def test_has_init_py(self) -> None:
        assert (resolve_hy3dshape_root() / "__init__.py").is_file()


class TestEnsureHy3dshapeOnPath:
    def test_adds_parent_to_sys_path(self) -> None:
        parent = str(resolve_hy3dshape_root().parent)
        if parent in sys.path:
            sys.path.remove(parent)
        ensure_hy3dshape_on_path()
        assert parent in sys.path

    def test_idempotent_single_insertion(self) -> None:
        parent = str(resolve_hy3dshape_root().parent)
        ensure_hy3dshape_on_path()
        ensure_hy3dshape_on_path()
        assert sys.path.count(parent) == 1

    def test_inserts_at_front(self) -> None:
        parent = str(resolve_hy3dshape_root().parent)
        ensure_hy3dshape_on_path()
        assert sys.path[0] == parent

    def test_returns_root(self) -> None:
        result = ensure_hy3dshape_on_path()
        assert result == resolve_hy3dshape_root()

    def test_enables_absolute_import_findable(self) -> None:
        ensure_hy3dshape_on_path()
        spec = importlib.util.find_spec("hy3dshape")
        assert spec is not None

    def test_enables_absolute_import_executes(self) -> None:
        ensure_hy3dshape_on_path()
        mod = importlib.import_module("hy3dshape")
        assert mod.__file__ is not None
        assert "hy3dshape" in sys.modules
