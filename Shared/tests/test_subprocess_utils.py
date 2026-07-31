"""Testes para aigamekit_shared.subprocess_utils (resolve_binary + monorepo)."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from aigamekit_shared.env import (
    TOOL_BINS,
    apply_monorepo_tool_bins,
    discover_monorepo_tool_bin,
    prefer_monorepo_tools,
)
from aigamekit_shared.subprocess_utils import resolve_binary


def _make_tool_venv(root: Path, folder: str, cli: str) -> Path:
    scripts = root / folder / ".venv" / "bin"
    scripts.mkdir(parents=True)
    bin_path = scripts / cli
    bin_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    bin_path.chmod(0o755)
    (root / "Shared").mkdir(exist_ok=True)
    (root / ".git").mkdir(exist_ok=True)
    return bin_path


class TestPreferMonorepoTools:
    def test_default_on(self):
        with patch.dict(os.environ, {}, clear=True):
            assert prefer_monorepo_tools() is True

    def test_off(self):
        with patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "0"}, clear=True):
            assert prefer_monorepo_tools() is False


class TestDiscoverMonorepoToolBin:
    def test_finds_venv_cli(self, tmp_path: Path):
        expected = _make_tool_venv(tmp_path, "Text3D", "text3d")
        found = discover_monorepo_tool_bin("text3d", monorepo=tmp_path)
        assert found == str(expected.resolve())

    def test_missing_venv(self, tmp_path: Path):
        (tmp_path / "Shared").mkdir()
        (tmp_path / ".git").mkdir()
        assert discover_monorepo_tool_bin("text3d", monorepo=tmp_path) is None

    def test_unknown_tool(self, tmp_path: Path):
        assert discover_monorepo_tool_bin("naoexiste", monorepo=tmp_path) is None


class TestApplyMonorepoToolBins:
    def test_fills_missing(self, tmp_path: Path):
        expected = _make_tool_venv(tmp_path, "Text3D", "text3d")
        with (
            patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "1"}, clear=True),
            patch(
                "aigamekit_shared.monorepo.try_find_monorepo_root",
                return_value=tmp_path,
            ),
        ):
            env: dict[str, str] = {}
            apply_monorepo_tool_bins(env)
            assert env[TOOL_BINS["text3d"]] == str(expected.resolve())

    def test_does_not_override(self, tmp_path: Path):
        _make_tool_venv(tmp_path, "Text3D", "text3d")
        with (
            patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "1"}, clear=True),
            patch(
                "aigamekit_shared.monorepo.try_find_monorepo_root",
                return_value=tmp_path,
            ),
        ):
            env = {TOOL_BINS["text3d"]: "/custom/text3d"}
            apply_monorepo_tool_bins(env)
            assert env[TOOL_BINS["text3d"]] == "/custom/text3d"

    def test_disabled(self, tmp_path: Path):
        _make_tool_venv(tmp_path, "Text3D", "text3d")
        with patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "0"}, clear=True):
            env: dict[str, str] = {}
            apply_monorepo_tool_bins(env)
            assert TOOL_BINS["text3d"] not in env


class TestResolveBinaryMonorepo:
    def test_env_wins(self, tmp_path: Path):
        _make_tool_venv(tmp_path, "Text3D", "text3d")
        with (
            patch.dict(
                os.environ,
                {"TEXT3D_BIN": "/env/text3d", "AIGAMEKIT_PREFER_MONOREPO": "1"},
                clear=True,
            ),
            patch(
                "aigamekit_shared.monorepo.try_find_monorepo_root",
                return_value=tmp_path,
            ),
        ):
            assert resolve_binary("TEXT3D_BIN", "text3d") == "/env/text3d"

    def test_prefers_monorepo_over_path(self, tmp_path: Path):
        expected = _make_tool_venv(tmp_path, "Text3D", "text3d")
        with (
            patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "1"}, clear=True),
            patch(
                "aigamekit_shared.monorepo.try_find_monorepo_root",
                return_value=tmp_path,
            ),
            patch("aigamekit_shared.subprocess_utils.shutil.which", return_value="/usr/bin/text3d"),
        ):
            assert resolve_binary("TEXT3D_BIN", "text3d") == str(expected.resolve())

    def test_falls_back_to_path_when_disabled(self):
        with (
            patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "0"}, clear=True),
            patch("aigamekit_shared.subprocess_utils.shutil.which", return_value="/usr/bin/text3d"),
        ):
            assert resolve_binary("TEXT3D_BIN", "text3d") == "/usr/bin/text3d"

    def test_raises_when_missing(self):
        with (
            patch.dict(os.environ, {"AIGAMEKIT_PREFER_MONOREPO": "0"}, clear=True),
            patch("aigamekit_shared.subprocess_utils.shutil.which", return_value=None),
            pytest.raises(FileNotFoundError, match="text3d"),
        ):
            resolve_binary("TEXT3D_BIN", "text3d")
