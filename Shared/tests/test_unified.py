"""Testes da ponte GameDev → Clified."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from gamedev_shared.installer.unified import (
    ensure_clified_env,
    install_tool,
    list_available_tools,
    main,
)


class TestEnsureClifiedEnv:
    def test_sets_tools_yaml(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
        (tmp_path / "tools.yaml").write_text("workspace:\n  root: .\n tools: {}\n")
        (tmp_path / "Shared").mkdir()
        (tmp_path / ".git").mkdir()
        monkeypatch.setenv("CLIFIED_ROOT", str(tmp_path / "clified"))
        root = ensure_clified_env(tmp_path)
        assert root == tmp_path.resolve()
        import os

        assert os.environ["CLIFIED_TOOLS"] == str((tmp_path / "tools.yaml").resolve())


class TestInstallToolBridge:
    @patch("clified.installer.unified.install_tool")
    @patch("clified.installer.registry.load_registry")
    def test_delegates_to_clified(self, _load, mock_install, monkeypatch):
        mock_install.return_value = True
        monkeypatch.setenv("CLIFIED_ROOT", "/tmp/clified")
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
    @patch("subprocess.call", return_value=0)
    def test_linux_calls_install_sh(self, mock_call, monkeypatch):
        monkeypatch.setenv("CLIFIED_ROOT", str(Path.home() / "AI" / "clified"))
        code = main(["--list"])
        assert code == 0
        mock_call.assert_called_once()
        cmd = mock_call.call_args.args[0]
        assert "install.sh" in cmd[0]
        assert "--list" in cmd
