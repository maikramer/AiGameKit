"""Testes extra do Text2D (generator/CLI helpers) sem carregar pesos HF.

Imports são locais para não carregar torch na fase de collection do pytest.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image


def test_default_model_id_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from text2d.generator import default_model_id

    monkeypatch.setenv("TEXT2D_MODEL_ID", "x/y-z")
    assert default_model_id() == "x/y-z"


def test_torch_dtype_cpu() -> None:
    from text2d.generator import _torch_dtype_for

    assert _torch_dtype_for("CPU") == __import__("torch").float32


def test_torch_dtype_cuda_unavailable_uses_float32(monkeypatch: pytest.MonkeyPatch) -> None:
    import torch

    from text2d.generator import _torch_dtype_for

    monkeypatch.setattr(torch, "cuda", MagicMock(is_available=lambda: False))
    assert _torch_dtype_for("cuda") == torch.float32


def test_maybe_apply_quantized_matmul_no_triton() -> None:
    from text2d.generator import _maybe_apply_quantized_matmul

    pipe = MagicMock()
    _maybe_apply_quantized_matmul(pipe, False)


def test_maybe_apply_quantized_matmul_no_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    import torch

    from text2d.generator import _maybe_apply_quantized_matmul

    monkeypatch.setattr(torch, "cuda", MagicMock(is_available=lambda: False))
    pipe = MagicMock()
    _maybe_apply_quantized_matmul(pipe, True)


def test_maybe_apply_quantized_matmul_applies_to_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    import sdnq.loader
    import torch

    from text2d.generator import _maybe_apply_quantized_matmul

    monkeypatch.setattr(torch, "cuda", MagicMock(is_available=lambda: True))
    apply_loader = MagicMock(side_effect=lambda m, **kw: m)
    monkeypatch.setattr(sdnq.loader, "apply_sdnq_options_to_model", apply_loader)
    tr = MagicMock()
    te = MagicMock()
    pipe = MagicMock()
    pipe.transformer = tr
    pipe.text_encoder = te
    pipe.text_encoder_2 = None
    _maybe_apply_quantized_matmul(pipe, True)
    assert apply_loader.call_count >= 2


def test_klein_init_forces_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    import torch

    from text2d.generator import KleinFluxGenerator

    monkeypatch.setattr(torch, "cuda", MagicMock(is_available=lambda: True))
    g = KleinFluxGenerator(device="cpu", low_vram=False, verbose=False)
    assert g.device == "cpu"


def test_klein_unload_clears_pipe() -> None:
    from text2d.generator import KleinFluxGenerator

    g = KleinFluxGenerator(device="cpu", verbose=False)
    g._pipe = MagicMock()
    g.unload()
    assert g._pipe is None


def test_klein_set_status_callback() -> None:
    from text2d.generator import KleinFluxGenerator

    g = KleinFluxGenerator(device="cpu", verbose=False)
    calls: list[str] = []

    def cb(msg: str) -> None:
        calls.append(msg)

    g.set_status_callback(cb)
    g._status("hello")
    assert calls == ["hello"]


def test_save_image_creates_parent_and_writes_png(tmp_path: Path) -> None:
    from text2d.generator import KleinFluxGenerator

    img = Image.new("RGB", (2, 2), color=(255, 0, 0))
    out = tmp_path / "nested" / "a.png"
    ret = KleinFluxGenerator.save_image(img, out, image_format="PNG")
    assert ret == out
    assert out.is_file()


def test_save_image_jpeg(tmp_path: Path) -> None:
    from text2d.generator import KleinFluxGenerator

    img = Image.new("RGB", (4, 4), color=(0, 255, 0))
    out = tmp_path / "b.jpg"
    KleinFluxGenerator.save_image(img, out, image_format="JPEG")
    assert out.is_file()


def test_cli_group_verbose_flag() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["-v", "--help"])
    assert r.exit_code == 0


def test_cli_generate_help() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    assert "--output" in r.output or "-o" in r.output


def test_cli_models_help() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["models", "--help"])
    assert r.exit_code == 0


def test_cli_info_help() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["info", "--help"])
    assert r.exit_code == 0


def test_skill_install_help() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["skill", "install", "--help"])
    assert r.exit_code == 0
    assert "--target" in r.output or "-t" in r.output


def test_version_option() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["--version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.output


def test_memory_format_bytes_import() -> None:
    from text2d.utils.memory import format_bytes

    assert "MB" in format_bytes(3 * 1024 * 1024)


def test_hf_home_display_import() -> None:
    from gamedev_shared.hf import hf_home_display_rich

    assert hf_home_display_rich() is not None


def test_klein_log_verbose_only(capsys: pytest.CaptureFixture[str]) -> None:
    from text2d.generator import KleinFluxGenerator

    g = KleinFluxGenerator(device="cpu", verbose=False)
    g._log("nope")
    assert capsys.readouterr().out == ""

    g2 = KleinFluxGenerator(device="cpu", verbose=True)
    g2._log("yep")
    assert "yep" in capsys.readouterr().out
