"""Testes extra Text2Icon: generator, utils, bg_removal."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from text2icon.generator import (
    BASE_ICON_INSTRUCTIONS,
    augment_prompt_for_icon,
    default_model_id,
)
from text2icon.utils import (
    ensure_directory,
    format_bytes,
    format_timestamp,
    generate_seed,
    validate_dimensions,
    validate_params,
    validate_prompt,
)


def test_default_model_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT2ICON_MODEL_ID", "custom/model")
    assert default_model_id() == "custom/model"


def test_augment_empty_returns_empty() -> None:
    assert augment_prompt_for_icon("") == ""
    assert augment_prompt_for_icon("   ") == ""


def test_augment_skips_when_icon_mentioned() -> None:
    p = "A health icon for RPG"
    assert augment_prompt_for_icon(p) == p


def test_augment_skips_logo() -> None:
    p = "Company logo"
    assert augment_prompt_for_icon(p) == p


def test_augment_skips_emblem() -> None:
    p = "Guild emblem"
    assert augment_prompt_for_icon(p) == p


def test_augment_skips_badge() -> None:
    p = "Achievement badge"
    assert augment_prompt_for_icon(p) == p


def test_augment_adds_base_instructions() -> None:
    out = augment_prompt_for_icon("red sword")
    assert BASE_ICON_INSTRUCTIONS.split(",")[0].strip() in out
    assert "red sword" in out


def test_validate_params_width_height_invalid() -> None:
    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 2, "width": 100, "height": 512})
    assert ok is False
    assert err is not None


def test_validate_params_sprint_low_steps_ok() -> None:
    """Sana Sprint allows 1 step (unlike FLUX which needs 10+)."""
    ok, err = validate_params({"guidance_scale": 4.5, "num_inference_steps": 1, "width": 512, "height": 512})
    assert ok is True and err is None


def test_validate_params_zero_steps_invalid() -> None:
    ok, _ = validate_params({"guidance_scale": 4.5, "num_inference_steps": 0, "width": 512, "height": 512})
    assert ok is False


def test_validate_dimensions_square_512() -> None:
    assert validate_dimensions(512, 512)[0] is True


def test_format_timestamp_shape() -> None:
    s = format_timestamp(1_700_000_000.0)
    assert len(s) == 19


def test_ensure_directory_nested(tmp_path: Path) -> None:
    d = tmp_path / "u" / "v"
    ensure_directory(d)
    assert d.is_dir()


def test_format_bytes_tb() -> None:
    s = format_bytes(1024**4)
    assert "TB" in s


def test_format_bytes_pb() -> None:
    s = format_bytes(1024**5)
    assert "PB" in s


def test_generate_seed_range() -> None:
    for _ in range(30):
        s = generate_seed()
        assert 0 <= s < 2**32


def test_cli_help() -> None:
    from click.testing import CliRunner

    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["--help"])
    assert r.exit_code == 0


def test_cli_generate_help() -> None:
    from click.testing import CliRunner

    from text2icon.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--transparent" in r.output


def test_validate_prompt_max_length_ok() -> None:
    ok, err = validate_prompt("x" * 100, max_length=1000)
    assert ok is True and err is None


class TestBgRemoval:
    """Testes do módulo bg_removal (rembg lazy import)."""

    def test_remove_background_returns_rgba(self):
        """remove_background must return an RGBA image (via mocked rembg)."""
        from text2icon import bg_removal

        # Build a fake PNG with alpha that rembg.remove would return
        rgba_out = Image.new("RGBA", (32, 32), (255, 0, 0, 200))

        # Mock BytesIO return from rembg.remove
        import io

        png_bytes = io.BytesIO()
        rgba_out.save(png_bytes, format="PNG")
        png_bytes.seek(0)

        fake_rembg = MagicMock()
        fake_rembg.remove.return_value = png_bytes.getvalue()

        with patch.dict("sys.modules", {"rembg": fake_rembg}):
            result = bg_removal.remove_background(Image.new("RGB", (32, 32), (255, 0, 0)))

        assert result.mode == "RGBA"
        fake_rembg.remove.assert_called_once()

    def test_remove_background_alpha_passthrough(self):
        """If rembg returns an already-converted RGBA PIL image, keep it."""
        from text2icon import bg_removal

        rgba_out = Image.new("RGBA", (32, 32), (0, 255, 0, 128))
        fake_rembg = MagicMock()
        fake_rembg.remove.return_value = rgba_out  # returns PIL directly, not bytes

        with patch.dict("sys.modules", {"rembg": fake_rembg}):
            result = bg_removal.remove_background(Image.new("RGB", (32, 32), (255, 0, 0)))

        assert result.mode == "RGBA"
