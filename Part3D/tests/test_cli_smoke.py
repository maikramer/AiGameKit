"""Smoke tests do CLI Part3D (sem GPU nem pesos)."""

from __future__ import annotations

from click.testing import CliRunner
from part3d.cli import main


def test_help_shows_group_and_decompose() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Part3D" in result.output or "part3d" in result.output.lower()
    assert "decompose" in result.output


def test_version() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert "0.1.0" in result.output


def test_decompose_help_lists_quality_and_gpu_flags() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["decompose", "--help"])
    assert result.exit_code == 0
    assert "--quality" in result.output
    assert "--quantization" in result.output
    assert "--hw-auto" in result.output
    assert "--allow-shared-gpu" in result.output
    assert "--gpu-kill-others" in result.output
    assert "--volume-decoder" in result.output
    assert "--kernel-modern" in result.output
    assert "--channels-last" in result.output
    assert "--mc-algo" in result.output
    assert "--compile-mode" in result.output
    assert "--point-num" in result.output
    assert "--postprocess" in result.output
    assert "--fine-parts" in result.output
    assert "--threshold" in result.output
