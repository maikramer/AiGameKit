"""Testes para gamedev_shared.installer.monorepo."""

from pathlib import Path

import pytest

from gamedev_shared.installer.monorepo import (
    find_monorepo_root,
    try_find_monorepo_root,
)


class TestFindMonorepoRoot:
    def test_finds_root(self):
        root = find_monorepo_root()
        assert (root / ".git").exists()
        assert (root / "Shared").is_dir()
        assert (root / "tools.yaml").is_file()

    def test_raises_if_not_found(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Raiz do monorepo"):
            find_monorepo_root(tmp_path / "nonexistent")


class TestTryFindMonorepoRoot:
    def test_returns_none_if_not_found(self, tmp_path: Path):
        assert try_find_monorepo_root(tmp_path / "nowhere") is None

    def test_finds_when_git_and_shared(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "Shared").mkdir()
        pkg = tmp_path / "Rigging3D" / "src" / "rigging3d"
        pkg.mkdir(parents=True)
        assert try_find_monorepo_root(pkg) == tmp_path
