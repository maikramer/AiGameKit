"""Testes unitários do runner (subprocess e resolução de binários)."""

from __future__ import annotations

import sys

import pytest

from gameassets.runner import merge_subprocess_output, resolve_binary, run_cmd


def test_resolve_binary_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT2D_BIN", "/opt/text2d/bin")
    assert resolve_binary("TEXT2D_BIN", "text2d") == "/opt/text2d/bin"


def test_resolve_binary_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NONEXISTENT_TOOL_X", raising=False)

    def _no_which(_name: str) -> None:
        return None

    monkeypatch.setattr("aigamekit_shared.subprocess_utils.shutil.which", _no_which)
    with pytest.raises(FileNotFoundError, match="Comando não encontrado"):
        resolve_binary("NONEXISTENT_TOOL_X", "nonexistent-command-xyz")


def test_run_cmd_python_echo() -> None:
    r = run_cmd([sys.executable, "-c", "print('ok')"])
    assert r.returncode == 0
    assert "ok" in r.stdout
    assert r.stderr == ""


def test_run_cmd_failure() -> None:
    r = run_cmd([sys.executable, "-c", "import sys; sys.exit(2)"])
    assert r.returncode == 2


def test_run_cmd_extra_env() -> None:
    r = run_cmd(
        [sys.executable, "-c", "import os; print(os.environ.get('GAMEASSETS_TEST_X', ''))"],
        extra_env={"GAMEASSETS_TEST_X": "ok"},
    )
    assert r.returncode == 0
    assert "ok" in r.stdout


def test_merge_subprocess_output_stderr_only() -> None:
    r = run_cmd([sys.executable, "-c", "import sys; print('e', file=sys.stderr); sys.exit(1)"])
    assert "e" in merge_subprocess_output(r)


def test_merge_subprocess_output_stdout_when_stderr_empty() -> None:
    r = run_cmd([sys.executable, "-c", "print('o'); import sys; sys.exit(1)"])
    assert "o" in merge_subprocess_output(r)


def test_merge_subprocess_output_both_streams() -> None:
    r = run_cmd(
        [
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(1)",
        ]
    )
    m = merge_subprocess_output(r)
    assert "err" in m and "out" in m and "--- stdout ---" in m
