"""Testes para aigamekit_shared.logging."""

from __future__ import annotations

import logging as stdlib_logging
from pathlib import Path

import pytest

from aigamekit_shared.logging import (
    Logger,
    configure_logging,
    detect_tool_name,
    file_logging_enabled,
    reset_file_logging_for_tests,
    resolve_log_path,
)


@pytest.fixture(autouse=True)
def _clean_file_logging(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Cada teste: dir temporário + enable explícito + reset sink."""
    monkeypatch.setenv("AIGAMEKIT_FILE_LOG", "1")
    monkeypatch.setenv("AIGAMEKIT_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("AIGAMEKIT_LOG_FILE", raising=False)
    monkeypatch.delenv("AIGAMEKIT_LOG_TOOL", raising=False)
    monkeypatch.delenv("AIGAMEKIT_NO_FILE_LOG", raising=False)
    reset_file_logging_for_tests()
    yield
    reset_file_logging_for_tests()


class TestLoggerAnsi:
    """Testes com fallback ANSI (sem Rich)."""

    def _make_logger_no_rich(self) -> Logger:
        logger = Logger.__new__(Logger)
        logger._console = None
        logger._tool = None
        logger._file_logging = False  # só consola nestes asserts
        return logger

    def test_info(self, capsys):
        logger = self._make_logger_no_rich()
        logger.info("teste info")
        captured = capsys.readouterr()
        assert "[INFO]" in captured.out
        assert "teste info" in captured.out

    def test_warn(self, capsys):
        logger = self._make_logger_no_rich()
        logger.warn("aviso")
        captured = capsys.readouterr()
        assert "[WARN]" in captured.out
        assert "aviso" in captured.out

    def test_error(self, capsys):
        logger = self._make_logger_no_rich()
        logger.error("falha")
        captured = capsys.readouterr()
        assert "[ERROR]" in captured.out
        assert "falha" in captured.out

    def test_step(self, capsys):
        logger = self._make_logger_no_rich()
        logger.step("passo 1")
        captured = capsys.readouterr()
        assert "[STEP]" in captured.out
        assert "passo 1" in captured.out

    def test_success(self, capsys):
        logger = self._make_logger_no_rich()
        logger.success("feito")
        captured = capsys.readouterr()
        assert "feito" in captured.out

    def test_header(self, capsys):
        logger = self._make_logger_no_rich()
        logger.header("Secção")
        captured = capsys.readouterr()
        assert "Secção" in captured.out

    def test_panel_ansi(self, capsys):
        logger = self._make_logger_no_rich()
        logger.panel("conteúdo", title="Título")
        captured = capsys.readouterr()
        assert "Título" in captured.out
        assert "conteúdo" in captured.out

    def test_table_ansi(self, capsys):
        logger = self._make_logger_no_rich()
        logger.table([("chave", "valor")], title="Info")
        captured = capsys.readouterr()
        assert "chave" in captured.out
        assert "valor" in captured.out


class TestLoggerRich:
    """Testes com Rich (se disponível)."""

    def test_rich_available(self):
        logger = Logger(file_logging=False)
        assert isinstance(logger.rich_available, bool)

    def test_console_property(self):
        logger = Logger(file_logging=False)
        if logger.rich_available:
            assert logger.console is not None

    def test_info_rich(self):
        logger = Logger(file_logging=False)
        if logger.rich_available:
            logger.info("teste rich")


class TestFileLogging:
    def test_file_logging_enabled_with_env(self):
        assert file_logging_enabled() is True

    def test_disabled_by_no_file_log(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AIGAMEKIT_NO_FILE_LOG", "1")
        assert file_logging_enabled() is False

    def test_configure_and_write(self, tmp_path: Path):
        path = configure_logging("text2d", log_dir=tmp_path / "logs", bridge_stdlib=False)
        assert path is not None
        assert path.name.startswith("text2d-")
        assert path.suffix == ".log"

        log = Logger(tool="text2d", file_logging=True)
        log.info("hello file", console=False)
        text = path.read_text(encoding="utf-8")
        assert "log start tool=text2d" in text
        assert "hello file" in text
        assert "[INFO" in text

    def test_console_false_still_files(self, tmp_path: Path, capsys):
        path = configure_logging("ums", log_dir=tmp_path / "logs", bridge_stdlib=False)
        assert path is not None
        Logger(file_logging=True).info("ums silent console", console=False)
        out = capsys.readouterr().out
        assert "ums silent console" not in out
        assert "ums silent console" in path.read_text(encoding="utf-8")

    def test_stdlib_bridge(self, tmp_path: Path):
        path = configure_logging("gameassets", log_dir=tmp_path / "logs", bridge_stdlib=True)
        assert path is not None
        stdlib_logging.getLogger("gameassets.pipeline").warning("stdlib warn")
        text = path.read_text(encoding="utf-8")
        assert "stdlib warn" in text
        assert "gameassets.pipeline" in text

    def test_detect_tool_ums_alias(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("sys.argv", ["vramd", "start"])
        monkeypatch.delenv("AIGAMEKIT_LOG_TOOL", raising=False)
        assert detect_tool_name() == "ums"

    def test_resolve_log_file_override(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        target = tmp_path / "custom.log"
        monkeypatch.setenv("AIGAMEKIT_LOG_FILE", str(target))
        assert resolve_log_path("ignored") == target
