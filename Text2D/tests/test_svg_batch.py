"""Fase SVG do `generate-batch` (skip, force, per-item)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from text2d import cli


def _icon(path: Path, size: int = 48, color=(30, 120, 240, 255)) -> None:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((4, 4, size - 4, size - 4), fill=color)
    img.save(path)


def _manifest(tmp_path: Path, items: list[dict]) -> Path:
    path = tmp_path / "batch.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def _lines(output: str) -> list[dict]:
    return [json.loads(line) for line in output.splitlines() if line.strip().startswith("{")]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_batch_existing_png_only_vectorizes(tmp_path, runner):
    _icon(tmp_path / "a.png")
    _icon(tmp_path / "b.png")
    manifest = _manifest(
        tmp_path,
        [
            {"id": "a", "prompt": "a blue icon", "output": "a.png"},
            {"id": "b", "prompt": "a blue icon", "output": "b.png"},
        ],
    )
    result = runner.invoke(
        cli.cli,
        [
            "generate-batch",
            str(manifest),
            "-O",
            str(tmp_path),
            "--svg",
            "--svg-engine",
            "vtracer",
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    lines = _lines(result.output)
    raster = [line for line in lines if line.get("phase") != "svg"]
    svg = [line for line in lines if line.get("phase") == "svg"]
    assert len(raster) == 2 and all(line["status"] == "skipped" for line in raster)
    assert len(svg) == 2 and all(line["status"] == "ok" for line in svg)
    assert (tmp_path / "a.svg").is_file()
    assert (tmp_path / "b.svg").is_file()


def test_batch_skips_existing_svg_and_regenerates_missing(tmp_path, runner):
    _icon(tmp_path / "a.png")
    _icon(tmp_path / "b.png")
    manifest = _manifest(
        tmp_path,
        [
            {"id": "a", "prompt": "a blue icon", "output": "a.png"},
            {"id": "b", "prompt": "a blue icon", "output": "b.png"},
        ],
    )
    args = [
        "generate-batch",
        str(manifest),
        "-O",
        str(tmp_path),
        "--svg",
        "--svg-engine",
        "vtracer",
        "--no-hw-auto",
    ]
    assert runner.invoke(cli.cli, args).exit_code == 0
    (tmp_path / "b.svg").unlink()

    result = runner.invoke(cli.cli, args)
    assert result.exit_code == 0, result.output
    svg = [line for line in _lines(result.output) if line.get("phase") == "svg"]
    by_id = {line["id"]: line for line in svg}
    assert by_id["a"]["status"] == "skipped"
    assert by_id["b"]["status"] == "ok"
    assert (tmp_path / "b.svg").is_file()


def test_batch_per_item_svg_false_overrides_global(tmp_path, runner):
    _icon(tmp_path / "a.png")
    _icon(tmp_path / "b.png")
    manifest = _manifest(
        tmp_path,
        [
            {"id": "a", "prompt": "a blue icon", "output": "a.png", "svg": False},
            {"id": "b", "prompt": "a blue icon", "output": "b.png"},
        ],
    )
    result = runner.invoke(
        cli.cli,
        [
            "generate-batch",
            str(manifest),
            "-O",
            str(tmp_path),
            "--svg",
            "--svg-engine",
            "vtracer",
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "a.svg").exists()
    assert (tmp_path / "b.svg").is_file()


def test_batch_per_item_svg_output(tmp_path, runner):
    _icon(tmp_path / "a.png")
    custom = tmp_path / "custom" / "a.svg"
    manifest = _manifest(
        tmp_path,
        [
            {
                "id": "a",
                "prompt": "a blue icon",
                "output": "a.png",
                "svg_output": str(custom),
            }
        ],
    )
    result = runner.invoke(
        cli.cli,
        [
            "generate-batch",
            str(manifest),
            "-O",
            str(tmp_path),
            "--svg",
            "--svg-engine",
            "vtracer",
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert custom.is_file()


def test_batch_force_regenerates_raster_and_svg(tmp_path, runner, monkeypatch):
    manifest = _manifest(
        tmp_path,
        [{"id": "a", "prompt": "a blue icon", "output": "a.png"}],
    )
    calls: list[str] = []

    def _delegate(backend, *, payload, **kwargs):
        calls.append(backend)
        _icon(Path(payload["output"]))
        return True

    monkeypatch.setattr(cli, "delegate_or_prepare", _delegate)
    result = runner.invoke(
        cli.cli,
        [
            "generate-batch",
            str(manifest),
            "-O",
            str(tmp_path),
            "--svg",
            "--svg-engine",
            "vtracer",
            "--force",
            "--no-hw-auto",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == ["text2d"]
    assert (tmp_path / "a.png").is_file()
    assert (tmp_path / "a.svg").is_file()
