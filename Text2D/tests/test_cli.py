"""Smoke tests — CLI sem carregar o modelo."""

from click.testing import CliRunner
from PIL import Image

from text2d.cli import _image_format_for, cli
from text2d.generator import KleinFluxGenerator


def test_root_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "Text2D" in r.output


def test_generate_help():
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "PROMPT" in r.output or "prompt" in r.output.lower()


def test_info():
    runner = CliRunner()
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "PyTorch" in r.output


def test_models():
    runner = CliRunner()
    r = runner.invoke(cli, ["models"])
    assert r.exit_code == 0
    assert "flux-3-image-klein" in r.output


def test_image_format_for_maps_suffixes():
    assert _image_format_for("a.png") == "PNG"
    assert _image_format_for("a.JPG") == "JPEG"
    assert _image_format_for("a.webp") == "WEBP"
    assert _image_format_for("a.bin") == "PNG"


def test_save_image_webp_writes_webp(tmp_path):
    # Regressão: ``save_image`` usa format= explícito; um ``-o x.webp`` não
    # pode gravar bytes PNG dentro do ficheiro .webp.
    out = tmp_path / "x.webp"
    KleinFluxGenerator.save_image(
        Image.new("RGB", (4, 4), "red"),
        out,
        image_format=_image_format_for(out),
    )
    with Image.open(out) as saved:
        assert saved.format == "WEBP"
