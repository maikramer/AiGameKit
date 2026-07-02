"""Testes para texture2d._validate_cli (CLI standalone de validação de tileability).

``_validate_cli`` expõe o comando click ``validate-tileable`` (image + --threshold
+ --json) que pontua quão "tileable" é uma imagem via ``score_tileability`` e sai
com código 0 (PASS) ou 1 (FAIL). São cobertos os caminhos de saída, o threshold, o
output JSON, o helper de impressão e o handling de KeyboardInterrupt em ``main``.
"""

from __future__ import annotations

import io
from pathlib import Path
from unittest.mock import patch

import numpy
from click.testing import CliRunner
from PIL import Image
from rich.console import Console

from texture2d._validate_cli import DEFAULT_THRESHOLD, _print_report, main, validate_tileable_cmd
from texture2d.tileability import TileabilityReport


def _uniform_image(path: Path) -> Path:
    Image.new("RGB", (128, 128), color=(50, 100, 150)).save(path)
    return path


def _nontileable_image(path: Path) -> Path:
    arr = numpy.zeros((128, 128, 3), dtype=numpy.uint8)
    arr[:, 64:] = 255
    Image.fromarray(arr, "RGB").save(path)
    return path


class TestDefaultThreshold:
    def test_value(self):
        assert DEFAULT_THRESHOLD == 0.85


class TestValidateTileableExitCodes:
    def test_tileable_image_exits_zero(self, tmp_path: Path):
        image = _uniform_image(tmp_path / "tile.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image)])
        assert result.exit_code == 0

    def test_nontileable_image_exits_one(self, tmp_path: Path):
        image = _nontileable_image(tmp_path / "split.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image)])
        assert result.exit_code == 1

    def test_high_threshold_fails_even_uniform(self, tmp_path: Path):
        image = _uniform_image(tmp_path / "tile.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image), "--threshold", "1.01"])
        assert result.exit_code == 1

    def test_low_threshold_passes_nontileable(self, tmp_path: Path):
        image = _nontileable_image(tmp_path / "split.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image), "--threshold", "0.0"])
        assert result.exit_code == 0

    def test_threshold_short_flag(self, tmp_path: Path):
        image = _uniform_image(tmp_path / "tile.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image), "-t", "0.5"])
        assert result.exit_code == 0

    def test_missing_image_arg_errors(self):
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [])
        assert result.exit_code != 0


class TestJsonOutput:
    def test_json_flag_emits_score_and_verdict(self, tmp_path: Path, monkeypatch):
        buf = io.StringIO()
        monkeypatch.setattr("texture2d._validate_cli.console", Console(file=buf, width=120))
        image = _uniform_image(tmp_path / "tile.png")
        runner = CliRunner()
        result = runner.invoke(validate_tileable_cmd, [str(image), "--json"])
        out = buf.getvalue()
        assert result.exit_code == 0
        assert "score" in out
        assert "verdict" in out
        assert "PASS" in out


class TestPrintReport:
    def test_prints_without_error(self, tmp_path: Path, monkeypatch):
        buf = io.StringIO()
        fake_console = Console(file=buf, width=120)
        monkeypatch.setattr("texture2d._validate_cli.console", fake_console)
        report = TileabilityReport(
            score=0.9,
            edge_mse_horizontal=1.0,
            edge_mse_vertical=2.0,
            max_abs_edge_diff=10,
            width=128,
            height=128,
        )
        _print_report(report, tmp_path / "img.png", 0.85)
        out = buf.getvalue()
        assert "Tileability Report" in out
        assert "PASS" in out
        assert "128x128" in out


class TestMain:
    def test_keyboard_interrupt_exits_130(self):
        with patch("texture2d._validate_cli.validate_tileable_cmd", side_effect=KeyboardInterrupt):
            try:
                main()
            except SystemExit as exc:
                assert exc.code == 130
            else:
                raise AssertionError("main() devia ter feito sys.exit(130)")
