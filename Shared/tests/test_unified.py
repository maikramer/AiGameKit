"""Testes da ponte GameDev → Clified."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from gamedev_shared.installer.unified import (
    ensure_clified_env,
    install_tool,
    list_available_tools,
    main,
)


class TestEnsureClifiedEnv:
    def test_sets_tools_yaml(self, tmp_path: Path):
        (tmp_path / "tools.yaml").write_text("workspace:\n  root: .\n tools: {}\n")
        (tmp_path / "Shared").mkdir()
        root = ensure_clified_env(tmp_path)
        assert root == tmp_path.resolve()
        assert os.environ["CLIFIED_TOOLS"] == str((tmp_path / "tools.yaml").resolve())


class TestInstallToolBridge:
    @patch("clified.installer.unified.install_tool")
    @patch("clified.installer.registry.load_registry")
    def test_delegates_to_clified(self, _load, mock_install):
        mock_install.return_value = True
        ok = install_tool("materialize", force=True)
        assert ok is True
        mock_install.assert_called_once()
        assert mock_install.call_args.args[0] == "materialize"
        assert mock_install.call_args.kwargs["force"] is True


class TestListAvailableTools:
    def test_includes_materialize(self):
        tools = list_available_tools()
        names = {spec.cli_name for spec in tools}
        assert "materialize" in names
        assert "text2d" in names


class TestMain:
    @patch("clified.installer.bootstrap.run", return_value=0)
    @patch("gamedev_shared.installer.unified.find_monorepo_root")
    def test_delegates_to_clified(self, mock_root, mock_run, tmp_path: Path):
        (tmp_path / "tools.yaml").write_text("workspace:\n  root: .\n tools: {}\n")
        mock_root.return_value = tmp_path
        code = main(["--list"])
        assert code == 0
        mock_run.assert_called_once_with(["--list"], cwd=tmp_path)
