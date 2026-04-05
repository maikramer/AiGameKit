"""Testes para gamedev_shared.installer.unified."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.installer.bun_installer import BunProjectInstaller
from gamedev_shared.installer.registry import (
    ToolKind,
    ToolSpec,
    find_monorepo_root,
    get_tool,
)
from gamedev_shared.installer.unified import (
    _ToolPythonInstaller,
    _ToolRustInstaller,
    install_tool,
    main,
)


class TestToolPythonInstaller:
    def test_init_from_spec(self, tmp_path: Path):
        spec = ToolSpec(
            name="TestPy",
            kind=ToolKind.PYTHON,
            folder="TestPy",
            cli_name="testpy",
            description="test",
            python_module="testpy",
            extra_aliases=("tp-gen",),
        )
        (tmp_path / "TestPy").mkdir()
        inst = _ToolPythonInstaller(spec, tmp_path)
        assert inst.project_name == "TestPy"
        assert inst.cli_name == "testpy"
        assert inst.spec is spec

    def test_spec_aliases_passed(self, tmp_path: Path):
        spec = ToolSpec(
            name="TestPy",
            kind=ToolKind.PYTHON,
            folder="TestPy",
            cli_name="testpy",
            description="test",
            extra_aliases=("alias1", "alias2"),
        )
        (tmp_path / "TestPy").mkdir()
        inst = _ToolPythonInstaller(spec, tmp_path)
        assert inst.spec.extra_aliases == ("alias1", "alias2")


class TestToolBunInstaller:
    def test_init_from_spec(self, tmp_path: Path):
        spec = ToolSpec(
            name="TestBun",
            kind=ToolKind.BUN,
            folder="TestBun",
            cli_name="testbun",
            description="test",
        )
        (tmp_path / "TestBun").mkdir()
        inst = BunProjectInstaller(
            project_name=spec.name,
            cli_name=spec.cli_name,
            project_root=spec.project_root(tmp_path),
        )
        assert inst.project_name == "TestBun"
        assert inst.cli_name == "testbun"


class TestToolRustInstaller:
    def test_init_from_spec(self, tmp_path: Path):
        spec = ToolSpec(
            name="TestRust",
            kind=ToolKind.RUST,
            folder="TestRust",
            cli_name="trs",
            description="test",
            cargo_bin_name="trs-cli",
        )
        (tmp_path / "TestRust").mkdir()
        inst = _ToolRustInstaller(spec, tmp_path)
        assert inst.project_name == "TestRust"
        assert inst.cargo_bin_name == "trs-cli"
        assert inst.cli_name == "trs"

    def test_materialize_spec(self):
        monorepo = find_monorepo_root()
        spec = get_tool("materialize")
        inst = _ToolRustInstaller(spec, monorepo)
        assert inst.cargo_bin_name == "materialize-cli"
        assert inst.cli_name == "materialize"
        assert (inst.project_root / "Cargo.toml").is_file()


class TestInstallTool:
    def test_unknown_tool(self):
        with pytest.raises(KeyError, match="Ferramenta desconhecida"):
            install_tool("this_does_not_exist_xyz")

    def test_missing_directory(self, tmp_path: Path):
        ok = install_tool("materialize", monorepo=tmp_path)
        assert ok is False

    @patch("gamedev_shared.installer.unified._ToolRustInstaller")
    def test_rust_install_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("materialize", monorepo=monorepo, action="install")
        assert ok is True
        mock_inst.run.assert_called_once()

    @patch("gamedev_shared.installer.unified._ToolRustInstaller")
    def test_rust_uninstall_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run_uninstall.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("materialize", monorepo=monorepo, action="uninstall")
        assert ok is True
        mock_inst.run_uninstall.assert_called_once()

    @patch("gamedev_shared.installer.unified._ToolRustInstaller")
    def test_rust_reinstall_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run_reinstall.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("materialize", monorepo=monorepo, action="reinstall")
        assert ok is True
        mock_inst.run_reinstall.assert_called_once()

    @patch("gamedev_shared.installer.unified._ToolPythonInstaller")
    def test_python_install_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("text2d", monorepo=monorepo, action="install")
        assert ok is True
        mock_inst.run.assert_called_once()

    @patch("gamedev_shared.installer.unified.BunProjectInstaller")
    def test_bun_install_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("vibegame", monorepo=monorepo, action="install")
        assert ok is True
        mock_inst.run.assert_called_once()

    @patch("gamedev_shared.installer.unified.BunProjectInstaller")
    def test_bun_uninstall_called(self, mock_cls: MagicMock):
        mock_inst = MagicMock()
        mock_inst.run_uninstall.return_value = True
        mock_cls.return_value = mock_inst

        monorepo = find_monorepo_root()
        ok = install_tool("vibegame", monorepo=monorepo, action="uninstall")
        assert ok is True
        mock_inst.run_uninstall.assert_called_once()

    def test_invalid_action(self):
        monorepo = find_monorepo_root()
        ok = install_tool("materialize", monorepo=monorepo, action="invalid_action")
        assert ok is False


class TestMainCLI:
    def test_list_returns_zero(self):
        rc = main(["--list"])
        assert rc == 0

    def test_no_args_returns_zero(self):
        rc = main([])
        assert rc == 0

    def test_help(self):
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0

    @patch("gamedev_shared.installer.unified.install_tool")
    def test_single_tool(self, mock_install: MagicMock):
        mock_install.return_value = True
        rc = main(["materialize"])
        assert rc == 0
        mock_install.assert_called_once()
        call_kwargs = mock_install.call_args
        assert call_kwargs[0][0] == "materialize"

    @patch("gamedev_shared.installer.unified.install_all")
    def test_all_tools(self, mock_all: MagicMock):
        mock_all.return_value = True
        rc = main(["all"])
        assert rc == 0
        mock_all.assert_called_once()

    @patch("gamedev_shared.installer.unified.install_tool")
    def test_action_uninstall(self, mock_install: MagicMock):
        mock_install.return_value = True
        rc = main(["materialize", "--action", "uninstall"])
        assert rc == 0
        kwargs = mock_install.call_args[1]
        assert kwargs["action"] == "uninstall"

    @patch("gamedev_shared.installer.unified.install_tool")
    def test_failure_returns_one(self, mock_install: MagicMock):
        mock_install.return_value = False
        rc = main(["materialize"])
        assert rc == 1
