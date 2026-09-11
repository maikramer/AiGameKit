"""Ícones no Text2D — categoria `icon`, transparente (rembg) e integração CLI/adapter.

Styling de prompt idempotente + remoção de fundo opcional. Suite CPU-first:
rembg é mockado em sys.modules (padrão unittest.mock), o generator do adapter
é um MagicMock — nada importa torch para além do que a collection já carrega.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from PIL import Image


def _rgb_image(size: int = 8) -> Image.Image:
    return Image.new("RGB", (size, size), color=(200, 30, 30))


def _rgba_png_bytes(size: int = 8) -> bytes:
    import io

    buf = io.BytesIO()
    Image.new("RGBA", (size, size), color=(200, 30, 30, 128)).save(buf, format="PNG")
    return buf.getvalue()


# --- augment_prompt_for_icon ---


class TestAugmentPromptForIcon:
    def test_adds_instructions_when_missing(self) -> None:
        from text2d.icons import BASE_ICON_INSTRUCTIONS, augment_prompt_for_icon

        out = augment_prompt_for_icon("a red sword")
        assert out == f"{BASE_ICON_INSTRUCTIONS}, a red sword"

    def test_strips_prompt_before_check(self) -> None:
        from text2d.icons import BASE_ICON_INSTRUCTIONS, augment_prompt_for_icon

        out = augment_prompt_for_icon("  a red sword  ")
        assert out == f"{BASE_ICON_INSTRUCTIONS}, a red sword"

    def test_empty_prompt_unchanged(self) -> None:
        from text2d.icons import augment_prompt_for_icon

        assert augment_prompt_for_icon("") == ""

    def test_whitespace_only_prompt_unchanged(self) -> None:
        from text2d.icons import augment_prompt_for_icon

        assert augment_prompt_for_icon("   ") == ""

    def test_none_like_prompt_unchanged(self) -> None:
        from text2d.icons import augment_prompt_for_icon

        assert augment_prompt_for_icon("") == ""

    @pytest.mark.parametrize(
        "prompt",
        [
            "icon of a red sword",
            "app icon of a red sword",
            "a red sword logo",
            "emblem of a guild",
            "badge of honor",
            "glyph of fire",
            "ICON of a red sword",  # case-insensitive
            "App Icon of a red sword",
            "a red sword LOGO",
        ],
    )
    def test_idempotent_with_markers(self, prompt: str) -> None:
        from text2d.icons import augment_prompt_for_icon

        assert augment_prompt_for_icon(prompt) == prompt

    @pytest.mark.parametrize("prompt", ["iconic sword", "logical tree", "badger shield"])
    def test_word_boundary_no_false_match(self, prompt: str) -> None:
        """Marcadores só contam como palavra inteira ("iconic" não é "icon")."""
        from text2d.icons import BASE_ICON_INSTRUCTIONS, augment_prompt_for_icon

        out = augment_prompt_for_icon(prompt)
        assert out.startswith(BASE_ICON_INSTRUCTIONS)

    def test_double_augment_is_idempotent(self) -> None:
        from text2d.icons import augment_prompt_for_icon

        once = augment_prompt_for_icon("a red sword")
        assert augment_prompt_for_icon(once) == once

    def test_icon_category_constant(self) -> None:
        from text2d.icons import ICON_CATEGORY

        assert ICON_CATEGORY == "icon"

    def test_base_instructions_exact_text(self) -> None:
        """Contrato de styling: texto exato das instruções de app-icon."""
        from text2d.icons import BASE_ICON_INSTRUCTIONS

        assert BASE_ICON_INSTRUCTIONS == (
            "app icon, simple, centered, bold, clean background, high contrast, "
            "flat design, crisp edges, single subject, readable at small size"
        )


# --- remove_background ---


class TestRemoveBackground:
    def test_removes_background_returns_rgba(self) -> None:
        from text2d.bg_removal import remove_background

        fake_remove = MagicMock(return_value=_rgba_png_bytes())
        fake_rembg = types.ModuleType("rembg")
        fake_rembg.remove = fake_remove
        with patch.dict(sys.modules, {"rembg": fake_rembg}):
            out = remove_background(_rgb_image())
        assert out.mode == "RGBA"

    def test_removes_background_converts_pil_result_to_rgba(self) -> None:
        from text2d.bg_removal import remove_background

        fake_remove = MagicMock(return_value=_rgb_image())  # rembg devolve PIL RGB
        fake_rembg = types.ModuleType("rembg")
        fake_rembg.remove = fake_remove
        with patch.dict(sys.modules, {"rembg": fake_rembg}):
            out = remove_background(_rgb_image())
        assert out.mode == "RGBA"

    def test_session_passthrough(self) -> None:
        from text2d.bg_removal import remove_background

        fake_remove = MagicMock(return_value=_rgba_png_bytes())
        fake_rembg = types.ModuleType("rembg")
        fake_rembg.remove = fake_remove
        session = object()
        with patch.dict(sys.modules, {"rembg": fake_rembg}):
            remove_background(_rgb_image(), session=session)
        fake_remove.assert_called_once()
        assert fake_remove.call_args.kwargs.get("session") is session

    def test_import_error_clear_message_when_rembg_missing(self) -> None:
        from text2d.bg_removal import remove_background

        # sys.modules["rembg"] = None força ImportError no import lazy.
        with (
            patch.dict(sys.modules, {"rembg": None}),
            pytest.raises(ImportError) as exc_info,
        ):
            remove_background(_rgb_image())
        assert "rembg" in str(exc_info.value)
        assert "pip install rembg" in str(exc_info.value)


# --- build_generate_request: category/transparent ---


class TestVramdPayloadCategoryTransparent:
    def test_category_included_when_set(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png", category="icon")
        assert req["category"] == "icon"

    def test_category_omitted_when_none(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png")
        assert "category" not in req

    def test_transparent_true_included(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png", transparent=True)
        assert req["transparent"] is True

    def test_transparent_false_omitted(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png", transparent=False)
        assert "transparent" not in req

    def test_transparent_none_omitted(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png", transparent=None)
        assert "transparent" not in req

    def test_category_and_transparent_together(self) -> None:
        from text2d.vramd_payload import build_generate_request

        req = build_generate_request(prompt="p", output="o.png", category="icon", transparent=True)
        assert req["category"] == "icon"
        assert req["transparent"] is True


# --- worker_serve_adapter.Adapter ---


def _fake_model(image: Image.Image | None = None) -> MagicMock:
    model = MagicMock()
    model.generate.return_value = (image if image is not None else _rgb_image(), {})
    model.memory_efficient = False
    model.group_offload = False
    model.quant_preset = None
    model.model_id = "fake/flux"
    return model


class TestAdapterIconCategory:
    def test_category_icon_augments_prompt(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        model = _fake_model()
        request = {"prompt": "a red sword", "output": str(tmp_path / "icon.png"), "category": "icon", "steps": 2}
        resp = Adapter().generate(model, request)
        assert resp["status"] == "ok"
        sent_prompt = model.generate.call_args.kwargs["prompt"]
        assert sent_prompt.startswith("app icon,")

    def test_prompt_with_marker_not_duplicated(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        model = _fake_model()
        request = {"prompt": "icon of a red sword", "output": str(tmp_path / "icon.png"), "category": "icon"}
        Adapter().generate(model, request)
        assert model.generate.call_args.kwargs["prompt"] == "icon of a red sword"

    def test_no_category_leaves_prompt_alone(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        model = _fake_model()
        request = {"prompt": "a red sword", "output": str(tmp_path / "img.png")}
        Adapter().generate(model, request)
        assert model.generate.call_args.kwargs["prompt"] == "a red sword"

    def test_saves_png_output(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "icon.png"
        resp = Adapter().generate(_fake_model(), {"prompt": "sword icon", "output": str(out), "category": "icon"})
        assert resp["status"] == "ok"
        assert out.is_file()


class TestAdapterTransparent:
    def test_transparent_applies_remove_background_and_saves_png(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from text2d import bg_removal

        stub = MagicMock(return_value=Image.new("RGBA", (8, 8), color=(1, 2, 3, 4)))
        monkeypatch.setattr(bg_removal, "remove_background", stub)

        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "icon.png"
        resp = Adapter().generate(
            _fake_model(),
            {"prompt": "sword icon", "output": str(out), "category": "icon", "transparent": True},
        )
        assert resp["status"] == "ok"
        stub.assert_called_once()
        assert out.is_file()

    def test_transparent_jpg_returns_error(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "icon.jpg"
        resp = Adapter().generate(
            _fake_model(),
            {"prompt": "sword icon", "output": str(out), "category": "icon", "transparent": True},
        )
        assert resp["status"] == "error"
        assert resp["error"] == "--transparent requer saída .png"
        assert not out.exists()

    def test_transparent_jpeg_extension_also_rejected(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "icon.jpeg"
        resp = Adapter().generate(_fake_model(), {"prompt": "sword icon", "output": str(out), "transparent": True})
        assert resp["status"] == "error"
        assert resp["error"] == "--transparent requer saída .png"

    def test_no_transparent_saves_jpg_normally(self, tmp_path: Path) -> None:
        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "img.jpg"
        resp = Adapter().generate(_fake_model(), {"prompt": "a red sword", "output": str(out)})
        assert resp["status"] == "ok"
        assert out.is_file()


# --- QualityEngine: contrato da categoria icon (dados no Shared) ---


class TestQualityCategoryIcon:
    def test_icon_category_resolves_512_2(self) -> None:
        from aigamekit_shared.quality import QualityEngine

        params = QualityEngine().resolve("text2d", quality="medium", category="icon").params
        assert params["width"] == 512
        assert params["height"] == 512
        assert params["steps"] == 2
        assert params["guidance"] == 1.0

    def test_icon_category_beats_tier(self) -> None:
        """A categoria `icon` ganha aos tiers (ex.: highest não sobe steps)."""
        from aigamekit_shared.quality import QualityEngine

        params = QualityEngine().resolve("text2d", quality="highest", category="icon").params
        assert params["width"] == 512
        assert params["steps"] == 2


# --- CLI: --category / --transparent ---


class TestCliCategoryTransparent:
    def _invoke_delegate_capture(self, monkeypatch: pytest.MonkeyPatch, args: list[str]) -> tuple[object, dict]:
        from text2d import cli

        captured: dict = {}

        def _fake_delegate(backend: str, *, payload: dict, **kwargs: object) -> bool:
            captured["backend"] = backend
            captured["payload"] = payload
            return True

        monkeypatch.setattr(cli, "delegate_or_prepare", _fake_delegate)
        runner = CliRunner()
        result = runner.invoke(cli.cli, ["generate", *args])
        return result, captured

    def test_transparent_jpg_fails_before_gpu(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        from text2d import cli

        gen_mock = MagicMock()
        monkeypatch.setattr(cli, "KleinFluxGenerator", gen_mock)
        delegate = MagicMock(return_value=True)
        monkeypatch.setattr(cli, "delegate_or_prepare", delegate)

        runner = CliRunner()
        result = runner.invoke(
            cli.cli,
            ["generate", "espada", "--category", "icon", "--transparent", "-o", str(tmp_path / "icon.jpg")],
        )
        assert result.exit_code != 0
        assert "--transparent requer saída .png" in result.output
        # Falha ANTES de qualquer trabalho de GPU/delegação.
        delegate.assert_not_called()
        gen_mock.assert_not_called()

    def test_transparent_jpeg_extension_also_rejected(self, tmp_path: Path) -> None:
        from text2d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "espada", "--transparent", "-o", str(tmp_path / "icon.jpeg")])
        assert result.exit_code != 0
        assert "--transparent requer saída .png" in result.output

    def test_transparent_png_passes_validation(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        result, captured = self._invoke_delegate_capture(
            monkeypatch,
            ["espada", "--category", "icon", "--transparent", "-o", str(tmp_path / "icon.png")],
        )
        assert result.exit_code == 0, result.output
        assert captured["payload"]["transparent"] is True
        assert captured["payload"]["category"] == "icon"

    def test_category_icon_augments_prompt_in_payload(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        result, captured = self._invoke_delegate_capture(
            monkeypatch, ["espada de fantasia", "--category", "icon", "-o", str(tmp_path / "icon.png")]
        )
        assert result.exit_code == 0, result.output
        assert captured["payload"]["prompt"].startswith("app icon,")
        assert "espada de fantasia" in captured["payload"]["prompt"]
        assert "transparent" not in captured["payload"]

    def test_prompt_with_icon_marker_not_augmented_in_payload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        result, captured = self._invoke_delegate_capture(
            monkeypatch, ["icon of a espada", "--category", "icon", "-o", str(tmp_path / "icon.png")]
        )
        assert result.exit_code == 0, result.output
        assert captured["payload"]["prompt"] == "icon of a espada"

    def test_no_category_no_augment_no_keys(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        result, captured = self._invoke_delegate_capture(monkeypatch, ["uma espada", "-o", str(tmp_path / "img.png")])
        assert result.exit_code == 0, result.output
        assert captured["payload"]["prompt"] == "uma espada"
        assert "category" not in captured["payload"]
        assert "transparent" not in captured["payload"]

    def test_category_icon_resolves_512x512_2_steps(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        """Categoria icon viaja no payload com 512²/2 passos (dados do Shared)."""
        result, captured = self._invoke_delegate_capture(
            monkeypatch, ["espada", "--category", "icon", "-o", str(tmp_path / "icon.png")]
        )
        assert result.exit_code == 0, result.output
        assert captured["payload"]["width"] == 512
        assert captured["payload"]["height"] == 512
        assert captured["payload"]["steps"] == 2
        assert "512x512" in result.output

    def test_generate_help_lists_new_flags(self) -> None:
        from text2d.cli import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["generate", "--help"])
        assert result.exit_code == 0
        assert "--category" in result.output
        assert "--transparent" in result.output
