"""Integração CLI do `generate --svg` e `vectorize` (sem GPU/vramd reais)."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from text2d import cli


def _fake_raster(path: str | Path, size: int = 64) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, size - 6, size - 6), fill=(30, 120, 240, 255))
    img.save(path)


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def patched_delegate(monkeypatch):
    captured: dict[str, dict] = {}

    def _delegate(backend, *, payload, **kwargs):
        captured[backend] = payload
        if backend == "text2d":
            _fake_raster(payload["output"])
        return True

    monkeypatch.setattr(cli, "delegate_or_prepare", _delegate)
    return captured


def test_generate_svg_sidecar_keeps_png(tmp_path, runner, patched_delegate, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "icon.png"
    result = runner.invoke(
        cli.cli,
        ["generate", "a blue round icon", "--svg", "--svg-engine", "vtracer", "-o", str(out), "--no-hw-auto"],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert (tmp_path / "icon.svg").is_file()
    assert "flat vector illustration" in patched_delegate["text2d"]["prompt"]


def test_generate_svg_only_deletes_temp_raster(tmp_path, runner, patched_delegate, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = tmp_path / "icon.svg"
    result = runner.invoke(
        cli.cli,
        ["generate", "a blue round icon", "-o", str(out), "--svg-engine", "vtracer", "--no-hw-auto"],
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert not (tmp_path / "icon.png").exists()


def test_generate_svg_output_override(tmp_path, runner, patched_delegate, monkeypatch):
    monkeypatch.chdir(tmp_path)
    png = tmp_path / "a.png"
    svg = tmp_path / "b.svg"
    result = runner.invoke(
        cli.cli,
        [
            "generate",
            "a blue round icon",
            "--svg",
            "--svg-engine",
            "vtracer",
            "--svg-output",
            str(svg),
            "-o",
            str(png),
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert png.is_file()
    assert svg.is_file()


def test_generate_no_svg_enhance_keeps_the_prompt(tmp_path, runner, patched_delegate, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.cli,
        [
            "generate",
            "a blue round icon",
            "--svg",
            "--no-svg-enhance",
            "--svg-engine",
            "vtracer",
            "-o",
            str(tmp_path / "icon.png"),
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert patched_delegate["text2d"]["prompt"] == "a blue round icon"


def test_generate_default_output_is_resolved_before_vramd(tmp_path, runner, patched_delegate, monkeypatch):
    # Regressão: sem -o o antigo payload fazia Path(None).resolve().
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(
        cli.cli,
        ["generate", "a blue round icon", "--svg", "--svg-engine", "vtracer", "--no-hw-auto"],
    )
    assert result.exit_code == 0, result.output
    images = list((tmp_path / "outputs" / "images").glob("*.png"))
    svgs = list((tmp_path / "outputs" / "images").glob("*.svg"))
    assert len(images) == 1
    assert len(svgs) == 1


def test_generate_starvector_sends_a_vectorize_payload(tmp_path, runner, patched_delegate, monkeypatch):
    import aigamekit_shared.cli_helpers as helpers

    captured: dict[str, dict] = {}

    def _try(backend, payload, **kwargs):
        captured["backend"] = backend
        captured["payload"] = payload
        Path(payload["output"]).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
            '<path d="M0 0 L64 64" fill="#1e78f0"/></svg>'
        )
        return True

    monkeypatch.setattr(helpers, "try_vramd_delegation", _try)
    result = runner.invoke(
        cli.cli,
        [
            "generate",
            "a blue round icon",
            "--svg",
            "--svg-engine",
            "starvector",
            "-o",
            str(tmp_path / "icon.png"),
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["backend"] == "text2svg"
    payload = captured["payload"]
    assert payload["task"] == "vectorize"
    assert payload["engine"] == "starvector"
    assert payload["image"].endswith("icon.png")
    assert payload["output"].endswith("icon.svg")


def test_vectorize_command_vtracer(tmp_path, runner):
    source = tmp_path / "icon.png"
    _fake_raster(source, 48)
    output = tmp_path / "icon.svg"
    result = runner.invoke(
        cli.cli,
        ["vectorize", str(source), "-o", str(output), "--engine", "vtracer", "--colors", "4"],
    )
    assert result.exit_code == 0, result.output
    assert output.is_file()


def test_vectorize_starvector_sends_a_payload(tmp_path, runner, monkeypatch):
    import aigamekit_shared.cli_helpers as helpers

    source = tmp_path / "icon.png"
    _fake_raster(source, 48)
    output = tmp_path / "icon.svg"
    captured: dict[str, dict] = {}

    def _try(backend, payload, **kwargs):
        captured["backend"] = backend
        captured["payload"] = payload
        Path(payload["output"]).write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48">'
            '<path d="M0 0 L48 48" fill="#1e78f0"/></svg>'
        )
        return True

    monkeypatch.setattr(helpers, "try_vramd_delegation", _try)
    result = runner.invoke(
        cli.cli,
        ["vectorize", str(source), "-o", str(output), "--engine", "starvector"],
    )
    assert result.exit_code == 0, result.output
    assert captured["backend"] == "text2svg"
    assert captured["payload"]["task"] == "vectorize"
    assert captured["payload"]["engine"] == "starvector"
    assert captured["payload"]["image"] == str(source.resolve())


def test_generate_starvector_inprocess_fallback(tmp_path, runner, patched_delegate, monkeypatch):
    import aigamekit_shared.cli_helpers as helpers
    import text2d.svg.engines as engines

    class _FakeEngine:
        name = "starvector"
        model_id = "fake/starvector"
        model = object()

        def __init__(self) -> None:
            self.unloaded = False

        def warmup(self) -> None:
            pass

        def vectorize(self, image, opts) -> str:
            return (
                '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
                '<circle cx="32" cy="32" r="24" fill="#1e78f0"/></svg>'
            )

        def unload(self) -> None:
            self.unloaded = True

    fake = _FakeEngine()
    monkeypatch.setattr(helpers, "try_vramd_delegation", lambda *a, **kw: False)
    monkeypatch.setattr(helpers, "prepare_gpu_exclusive", lambda **kw: None)
    monkeypatch.setattr(engines, "get_engine", lambda *a, **kw: fake)

    result = runner.invoke(
        cli.cli,
        [
            "generate",
            "a blue round icon",
            "--svg",
            "--svg-engine",
            "starvector",
            "-o",
            str(tmp_path / "icon.png"),
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "icon.svg").is_file()
    assert fake.unloaded is True
