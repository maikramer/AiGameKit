"""Flags novas do CLI (revisão 2026-10-30) — contrato com o GameAssets."""

from __future__ import annotations

from click.testing import CliRunner

from text3d.cli import cli


def _help(args: list[str]) -> str:
    result = CliRunner().invoke(cli, [*args, "--help"])
    assert result.exit_code == 0, result.output
    return result.output


def test_refine_help_tem_remove_background() -> None:
    assert "--remove-background" in _help(["refine"])


def test_generate_batch_aceita_t2d_full_gpu() -> None:
    # O GameAssets passa esta flag ao generate-batch; sem a opção o click
    # falhava com "No such option" no fallback sem vramd.
    assert "--t2d-full-gpu" in _help(["generate-batch"])


def test_lod_help_tem_alvos_explicitos() -> None:
    out = _help(["lod"])
    assert "--lod1-faces" in out
    assert "--lod2-faces" in out
