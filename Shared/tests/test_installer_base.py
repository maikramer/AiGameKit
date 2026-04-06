"""Testes para gamedev_shared.installer.base."""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from gamedev_shared.installer.base import BaseInstaller, path_env_contains_dir


class TestBaseInstallerInit:
    def test_defaults(self, tmp_path):
        installer = BaseInstaller(
            project_name="TestProject",
            cli_name="test",
            project_root=tmp_path,
        )
        assert installer.project_name == "TestProject"
        assert installer.cli_name == "test"
        assert installer.project_root == tmp_path.resolve()
        assert installer.logger is not None
        assert installer.bin_dir == installer.install_prefix / "bin"

    def test_custom_prefix(self, tmp_path):
        prefix = tmp_path / "custom"
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
            install_prefix=prefix,
        )
        assert installer.install_prefix == prefix

    def test_env_prefix(self, tmp_path):
        with patch.dict("os.environ", {"INSTALL_PREFIX": str(tmp_path / "custom_prefix")}):
            installer = BaseInstaller(
                project_name="Test",
                cli_name="test",
                project_root=tmp_path,
            )
            assert installer.install_prefix == Path(str(tmp_path / "custom_prefix"))


class TestBaseInstallerCheckPython:
    def test_check_python_succeeds(self, tmp_path):
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
            python_cmd="python" if __import__("sys").platform == "win32" else "python3",
        )
        assert installer.check_python() is True

    def test_check_python_bad_cmd(self, tmp_path):
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
            python_cmd="python_inexistente_xyz",
        )
        assert installer.check_python() is False


class TestBaseInstallerCreateWrapper:
    def test_create_wrapper_module(self, tmp_path):
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
            install_prefix=tmp_path / "prefix",
        )
        wrapper = installer.create_wrapper(
            "test-cmd",
            python_path="/usr/bin/python3",
            module_name="test_mod",
        )
        assert wrapper.exists()
        if sys.platform == "win32":
            assert wrapper.suffix == ".cmd"
        content = wrapper.read_text()
        assert "/usr/bin/python3" in content
        assert "test_mod" in content

    def test_create_wrapper_binary(self, tmp_path):
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
            install_prefix=tmp_path / "prefix",
        )
        binary = tmp_path / "mybin"
        wrapper = installer.create_wrapper(
            "test-bin",
            target_binary=binary,
        )
        assert wrapper.exists()
        if sys.platform == "win32":
            assert wrapper.suffix == ".cmd"
        content = wrapper.read_text()
        assert str(binary) in content


class TestPathEnvContainsDir:
    def test_windows_normalizes_case(self):
        p = Path(r"C:\Users\test\bin")
        env = r"D:\x;C:\Users\test\bin;."
        assert path_env_contains_dir(env, p, is_windows=True) is True

    def test_windows_not_present(self):
        p = Path(r"C:\Users\test\bin")
        assert path_env_contains_dir(r"C:\Other\bin", p, is_windows=True) is False

    @pytest.mark.skipif(sys.platform == "win32", reason="PATH estilo Unix; resolve() difere no Windows")
    def test_unix(self):
        p = Path("/home/u/.local/bin")
        assert path_env_contains_dir("/a:/home/u/.local/bin:/b", p, is_windows=False) is True


class TestBaseInstallerShowSummary:
    def test_show_summary_no_crash(self, tmp_path):
        installer = BaseInstaller(
            project_name="Test",
            cli_name="test",
            project_root=tmp_path,
        )
        installer.show_summary(["test --help", "test run"])
