"""Testes para gamedev_shared.skill_install."""

import pytest

from gamedev_shared.installer.monorepo import try_find_monorepo_root
from gamedev_shared.skill_install import (
    install_agent_skill,
    resolve_skill_source,
)


class TestFindMonorepoRoot:
    def test_with_git_and_shared(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "Shared").mkdir()
        pkg = tmp_path / "Tool" / "src" / "pkg"
        pkg.mkdir(parents=True)
        result = try_find_monorepo_root(pkg)
        assert result == tmp_path

    def test_shared_only_returns_none(self, tmp_path):
        (tmp_path / "Shared").mkdir()
        pkg = tmp_path / "Tool" / "src" / "pkg"
        pkg.mkdir(parents=True)
        assert try_find_monorepo_root(pkg) is None

    def test_not_found(self, tmp_path):
        deep = tmp_path / "a" / "b" / "c" / "d" / "e" / "f" / "g" / "h"
        deep.mkdir(parents=True)
        assert try_find_monorepo_root(deep) is None


class TestResolveSkillSource:
    def test_monorepo_source(self, tmp_path):
        gamedev = tmp_path / "GameDev"
        gamedev.mkdir()
        (gamedev / ".git").mkdir()
        (gamedev / "Shared").mkdir()
        skill_dir = gamedev / ".cursor" / "create-skill" / "mytool"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text("# Skill")

        pkg_dir = gamedev / "MyTool" / "src" / "mytool"
        pkg_dir.mkdir(parents=True)

        result = resolve_skill_source("mytool", pkg_dir)
        assert result == skill_dir

    def test_package_fallback(self, tmp_path):
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        cursor_skill = pkg_dir / "cursor_skill"
        cursor_skill.mkdir()
        (cursor_skill / "SKILL.md").write_text("# Skill")

        result = resolve_skill_source("mytool", pkg_dir)
        assert result == cursor_skill

    def test_not_found(self, tmp_path):
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="Skill mytool não encontrada"):
            resolve_skill_source("mytool", pkg_dir)


class TestInstallAgentSkill:
    def test_install(self, tmp_path):
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        cursor_skill = pkg_dir / "cursor_skill"
        cursor_skill.mkdir()
        (cursor_skill / "SKILL.md").write_text("# Skill content")

        target = tmp_path / "project"
        target.mkdir()

        result = install_agent_skill("mytool", pkg_dir, target)
        assert result.exists()
        assert result.read_text() == "# Skill content"
        assert result.parent.name == "mytool"

    def test_already_exists(self, tmp_path):
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        cursor_skill = pkg_dir / "cursor_skill"
        cursor_skill.mkdir()
        (cursor_skill / "SKILL.md").write_text("# Skill")

        target = tmp_path / "project"
        dest = target / ".cursor" / "skills" / "mytool"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("# Old")

        with pytest.raises(FileExistsError):
            install_agent_skill("mytool", pkg_dir, target)

    def test_force_overwrite(self, tmp_path):
        pkg_dir = tmp_path / "pkg"
        pkg_dir.mkdir()
        cursor_skill = pkg_dir / "cursor_skill"
        cursor_skill.mkdir()
        (cursor_skill / "SKILL.md").write_text("# New")

        target = tmp_path / "project"
        dest = target / ".cursor" / "skills" / "mytool"
        dest.mkdir(parents=True)
        (dest / "SKILL.md").write_text("# Old")

        result = install_agent_skill("mytool", pkg_dir, target, force=True)
        assert result.read_text() == "# New"
