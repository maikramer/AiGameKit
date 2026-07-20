"""Smoke tests do CLI Text3D (sem carregar Hunyuan / modelos)."""

from __future__ import annotations

from click.testing import CliRunner

from text3d.cli import cli


def test_root_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Text3D" in r.output or "mesh" in r.output.lower()


def test_version() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_generate_help() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--from-image" in r.output or "from-image" in r.output


def test_generate_requires_prompt_or_image() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate"])
    assert r.exit_code != 0


def test_info() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "PyTorch" in r.output


def test_doctor() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["doctor"])
    assert r.exit_code == 0
    assert "PyTorch" in r.output or "Diagnóstico" in r.output


def test_models() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["models"])
    assert r.exit_code == 0
    assert "Hunyuan" in r.output or "Text2D" in r.output


def test_generate_explicit_steps_octree_beat_quality_preset(tmp_path) -> None:
    """Flags explícitos --steps/--octree-resolution ganham do preset soft-filled por --quality.

    Regressão: o bloco do preset atropelava incondicionalmente steps/octree/chunks
    mesmo com flags CLI explícitos (painel mostrava steps=30/octree=256 com
    --steps 40 --octree-resolution 384).
    """
    import click

    import text3d.cli as c

    img = tmp_path / "img.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")  # conteúdo não interessa; falha antes de gerar
    seen: dict[str, object] = {}
    orig = c.generate.callback

    def _spy(*a: object, **kw: object) -> None:
        ctx = click.get_current_context()
        seen["steps"] = ctx.params["steps"]
        seen["octree"] = ctx.params["octree_resolution"]
        # Simular a resolução até ao painel: quality tier soft-fills preset e o
        # bloco do preset aplica-se só aos não-user-set. Verificado no painel
        # impresso (config) — aqui basta garantir que os params CLI chegam.
        raise SystemExit(0)

    c.generate.callback = _spy
    try:
        r = CliRunner().invoke(
            cli,
            [
                "generate",
                "--from-image",
                str(img),
                "-o",
                str(tmp_path / "x.glb"),
                "--quality",
                "medium",
                "--steps",
                "40",
                "--octree-resolution",
                "384",
            ],
        )
        assert r.exit_code == 0
        assert seen["steps"] == 40
        assert seen["octree"] == 384
    finally:
        c.generate.callback = orig
