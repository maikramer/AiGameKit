"""Testes para texture2d.generator (pattern-diffusion, SD2-base, Apache-2.0).

API pública sob teste:

    TextureGenerator(device, low_vram, verbose, model_id, cache_dir, gpu_ids,
                     seamless_method="late", quant="none", compile_flag=False)
    generate(...) -> (PIL.Image, metadata: dict)

Os testes mockam ``StableDiffusionPipeline.from_pretrained`` /
``DDPMScheduler.from_config`` — não carregam modelos nem usam GPU.
"""

from __future__ import annotations

import builtins
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import torch
from PIL import Image

from texture2d.generator import (
    BASE_TEXTURE_INSTRUCTIONS,
    DEFAULT_MODEL_ID,
    QUANT_MODES,
    SEAMLESS_METHODS,
    TextureGenerator,
    _build_quantization_config,
    _disable_seamless,
    _make_seamless,
    _set_conv_padding_mode,
    augment_prompt_for_seamless,
    default_model_id,
    merge_negative_prompt,
)


def _fake_pipeline_image() -> Image.Image:
    return Image.new("RGB", (512, 512), color=(123, 45, 67))


@pytest.fixture
def mock_diffusers() -> Any:
    """Mocka from_pretrained + DDPMScheduler.from_config e devolve [pipe, fp, sched].

    ``pipe(**kwargs)`` devolve um objeto com ``.images = [<PIL.Image 512x512>]``.
    """
    fake_output = MagicMock()
    fake_output.images = [_fake_pipeline_image()]
    fake_pipe = MagicMock()
    fake_pipe.return_value = fake_output
    fake_pipe.scheduler = MagicMock()
    fake_pipe.scheduler.config = MagicMock()

    with (
        patch("diffusers.StableDiffusionPipeline.from_pretrained", return_value=fake_pipe) as mock_fp,
        patch("diffusers.DDPMScheduler.from_config", return_value=MagicMock()) as mock_sched,
    ):
        yield [fake_pipe, mock_fp, mock_sched]


def _block_imports(monkeypatch: pytest.MonkeyPatch, *names: str) -> None:
    """Bloqueia a importação dos módulos em ``names`` para simular deps em falta."""
    blocked = set(names)
    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        root = name.split(".")[0]
        if root in blocked:
            raise ImportError(f"No module named '{name}' (blocked for test)")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)


class TestModuleConstants:
    def test_default_model_id_is_pattern_diffusion(self) -> None:
        assert DEFAULT_MODEL_ID == "Arrexel/pattern-diffusion"

    def test_seamless_methods(self) -> None:
        assert SEAMLESS_METHODS == ("late", "roll", "full", "none")

    def test_quant_modes(self) -> None:
        assert QUANT_MODES == ("none", "fp8", "nf4")


class TestDefaultModelId:
    def test_default(self) -> None:
        assert default_model_id() == DEFAULT_MODEL_ID
        assert default_model_id() == "Arrexel/pattern-diffusion"

    def test_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEXTURE2D_MODEL_ID", "custom/sd-model")
        assert default_model_id() == "custom/sd-model"

    def test_env_override_cleared(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEXTURE2D_MODEL_ID", raising=False)
        assert default_model_id() == DEFAULT_MODEL_ID


class TestAugmentPrompt:
    def test_adds_seamless_prefix(self) -> None:
        result = augment_prompt_for_seamless("stone wall")
        assert "seamless" in result.lower()
        assert "stone wall" in result

    def test_prefix_is_base_instructions(self) -> None:
        result = augment_prompt_for_seamless("rusty metal")
        assert BASE_TEXTURE_INSTRUCTIONS.split(",")[0].strip() in result
        assert "rusty metal" in result

    def test_skips_if_already_seamless(self) -> None:
        original = "seamless brick texture"
        assert augment_prompt_for_seamless(original) == original

    def test_skips_if_tileable(self) -> None:
        original = "tileable marble floor"
        assert augment_prompt_for_seamless(original) == original

    def test_skips_if_repeatable(self) -> None:
        original = "repeatable fabric pattern"
        assert augment_prompt_for_seamless(original) == original

    def test_skips_if_tiling(self) -> None:
        original = "tiling grass texture"
        assert augment_prompt_for_seamless(original) == original

    def test_empty_prompt(self) -> None:
        assert augment_prompt_for_seamless("") == ""
        assert augment_prompt_for_seamless("   ") == ""


class TestMergeNegativePrompt:
    def test_only_preset(self) -> None:
        assert merge_negative_prompt("blurry", "") == "blurry"

    def test_only_user(self) -> None:
        assert merge_negative_prompt("", "low quality") == "low quality"

    def test_both_empty(self) -> None:
        assert merge_negative_prompt("", "") == ""

    def test_both_different(self) -> None:
        result = merge_negative_prompt("blurry", "low quality")
        assert "blurry" in result
        assert "low quality" in result

    def test_subset_dedup_user_contains_preset(self) -> None:
        assert merge_negative_prompt("blurry", "blurry, low quality") == "blurry, low quality"

    def test_subset_dedup_preset_contains_user(self) -> None:
        assert merge_negative_prompt("blurry, noise", "noise") == "blurry, noise"


class TestTextureGeneratorConstruction:
    def test_defaults(self) -> None:
        gen = TextureGenerator(device="cpu")
        assert gen.model_id == DEFAULT_MODEL_ID
        assert gen.seamless_method == "late"
        assert gen.quant == "none"
        assert gen.compile_flag is False
        assert gen.device == "cpu"
        assert gen._pipe is None

    def test_custom_params(self) -> None:
        gen = TextureGenerator(
            device="cpu",
            seamless_method="full",
            quant="nf4",
            compile_flag=True,
            model_id="custom/model",
        )
        assert gen.seamless_method == "full"
        assert gen.quant == "nf4"
        assert gen.compile_flag is True
        assert gen.model_id == "custom/model"

    def test_model_id_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEXTURE2D_MODEL_ID", "env/model")
        gen = TextureGenerator(device="cpu")
        assert gen.model_id == "env/model"

    @pytest.mark.parametrize("method", SEAMLESS_METHODS)
    def test_all_seamless_methods_accepted(self, method: str) -> None:
        gen = TextureGenerator(device="cpu", seamless_method=method)
        assert gen.seamless_method == method

    @pytest.mark.parametrize("quant", QUANT_MODES)
    def test_all_quant_modes_accepted(self, quant: str) -> None:
        gen = TextureGenerator(device="cpu", quant=quant)
        assert gen.quant == quant

    def test_invalid_seamless_method_raises(self) -> None:
        with pytest.raises(ValueError, match="seamless_method"):
            TextureGenerator(device="cpu", seamless_method="bogus")

    def test_invalid_quant_raises(self) -> None:
        with pytest.raises(ValueError, match="quant"):
            TextureGenerator(device="cpu", quant="int8")


def _tiny_conv_module() -> torch.nn.Module:
    return torch.nn.Sequential(
        torch.nn.Conv2d(3, 3, 3, padding=1),
        torch.nn.Conv2d(3, 3, 1),
    )


class TestSeamlessHelpers:
    def test_conv_default_padding_mode_is_zeros(self) -> None:
        module = _tiny_conv_module()
        for m in module.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "zeros"

    def test_make_seamless_sets_circular(self) -> None:
        module = _tiny_conv_module()
        changed = _make_seamless(module)
        assert changed == 2
        for m in module.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "circular"

    def test_set_padding_mode_explicit(self) -> None:
        module = _tiny_conv_module()
        count = _set_conv_padding_mode(module, "circular")
        assert count == 2
        for m in module.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "circular"

    def test_disable_seamless_restores_zeros(self) -> None:
        module = _tiny_conv_module()
        _make_seamless(module)
        for m in module.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "circular"
        _disable_seamless(module)
        for m in module.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "zeros"

    def test_make_seamless_returns_zero_on_empty(self) -> None:
        empty = torch.nn.Sequential(torch.nn.Linear(3, 3))
        assert _make_seamless(empty) == 0
        assert _disable_seamless(empty) == 0


class TestBuildQuantizationConfig:
    def test_none_returns_none(self) -> None:
        assert _build_quantization_config("none") is None

    def test_invalid_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Quantização desconhecida"):
            _build_quantization_config("int8")

    def test_fp8_config_or_skip(self) -> None:
        try:
            cfg = _build_quantization_config("fp8")
        except ImportError:
            pytest.skip("torchao float8 support unavailable in this environment")
        assert cfg is not None

    def test_nf4_config_or_skip(self) -> None:
        try:
            cfg = _build_quantization_config("nf4")
        except ImportError:
            pytest.skip("bitsandbytes/transformers unavailable in this environment")
        assert cfg is not None

    def test_fp8_missing_torchao_raises_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _block_imports(monkeypatch, "torchao")
        with pytest.raises(ImportError):
            _build_quantization_config("fp8")

    def test_nf4_missing_bitsandbytes_raises_import_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _block_imports(monkeypatch, "bitsandbytes")
        with pytest.raises(ImportError):
            _build_quantization_config("nf4")


class TestGenerate:
    def test_generate_returns_image_and_metadata(self, mock_diffusers: list) -> None:
        fake_pipe = mock_diffusers[0]
        gen = TextureGenerator(device="cpu")
        image, metadata = gen.generate(prompt="rough stone wall", seed=42)

        assert isinstance(image, Image.Image)
        assert image.mode == "RGB"
        assert image.size == (512, 512)
        assert isinstance(metadata, dict)
        fake_pipe.assert_called_once()

    def test_generate_metadata_required_keys(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu")
        _image, metadata = gen.generate(prompt="mossy cobblestone", seed=7)

        required = {
            "model",
            "seamless_method",
            "quant",
            "seed",
            "prompt_final",
            "width",
            "height",
            "guidance_scale",
            "num_inference_steps",
        }
        assert required.issubset(metadata.keys()), f"keys em falta: {required - set(metadata)}"

    def test_generate_metadata_values(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu", seamless_method="late", quant="none")
        _image, metadata = gen.generate(prompt="red brick", seed=123)

        assert metadata["model"] == DEFAULT_MODEL_ID
        assert metadata["seamless_method"] == "late"
        assert metadata["quant"] == "none"
        assert metadata["seed"] == 123
        assert metadata["width"] == 512
        assert metadata["height"] == 512
        assert metadata["guidance_scale"] == 7.5
        assert metadata["num_inference_steps"] == 50
        assert "seamless" in metadata["prompt_final"].lower()
        assert "red brick" in metadata["prompt_final"]

    def test_generate_auto_seed_when_none(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu")
        _image, metadata = gen.generate(prompt="sand", seed=None)
        assert isinstance(metadata["seed"], int)
        assert 0 <= metadata["seed"] < 2**32

    def test_generate_negative_seed_generates(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu")
        _image, metadata = gen.generate(prompt="dirt", seed=-1)
        assert isinstance(metadata["seed"], int)
        assert metadata["seed"] >= 0

    def test_generate_invalid_params_raises(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu")
        with pytest.raises(ValueError, match="Parâmetros inválidos"):
            gen.generate(prompt="x", guidance_scale=99.0)

    def test_generate_uses_cached_pipeline(self, mock_diffusers: list) -> None:
        mock_fp = mock_diffusers[1]
        gen = TextureGenerator(device="cpu")
        gen.generate(prompt="a", seed=1)
        gen.generate(prompt="b", seed=2)
        assert mock_fp.call_count == 1

    def test_generate_seamless_method_override(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu", seamless_method="late")
        _image, metadata = gen.generate(prompt="stone", seed=1, seamless_method="full")
        assert metadata["seamless_method"] == "full"
        assert gen.seamless_method == "full"

    def test_generate_compile_flag_in_metadata(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu", compile_flag=False)
        _image, metadata = gen.generate(prompt="metal", seed=1)
        assert "compile" in metadata
        assert metadata["compile"] is False

    def test_generate_with_preset(self, mock_diffusers: list) -> None:
        gen = TextureGenerator(device="cpu")
        _image, metadata = gen.generate(prompt="scratched surface", preset="Metal", seed=5)
        assert metadata["guidance_scale"] == 8.0
        assert metadata["num_inference_steps"] == 60
        assert "metal" in metadata["prompt_final"].lower()


class TestGenerateBatch:
    def test_batch_yields_three_tuples(self) -> None:
        gen = TextureGenerator(device="cpu")
        fake_image = Image.new("RGB", (8, 8))
        gen.generate = MagicMock(return_value=(fake_image, {"seed": 1}))  # type: ignore[method-assign]
        results = list(gen.generate_batch(["grass field", "stone wall", "wood plank"]))

        assert len(results) == 3
        for idx, item in enumerate(results):
            assert len(item) == 3
            image, metadata, i = item
            assert i == idx
            assert image is fake_image
            assert metadata == {"seed": 1}

    def test_batch_empty_prompts(self) -> None:
        gen = TextureGenerator(device="cpu")
        gen.generate = MagicMock(return_value=(Image.new("RGB", (8, 8)), {}))  # type: ignore[method-assign]
        assert list(gen.generate_batch([])) == []

    def test_batch_yields_none_on_error(self) -> None:
        gen = TextureGenerator(device="cpu")
        fake_image = Image.new("RGB", (8, 8))
        call_count = {"n": 0}

        def flaky_generate(**kwargs: Any) -> Any:
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("boom")
            return (fake_image, {"seed": 1})

        gen.generate = flaky_generate  # type: ignore[method-assign]
        results = list(gen.generate_batch(["ok1", "boom", "ok3"]))

        assert len(results) == 3
        assert results[0][0] is fake_image
        assert results[1][0] is None
        assert "error" in results[1][1]
        assert results[1][2] == 1
        assert results[2][0] is fake_image


class TestSaveImage:
    def test_save_image_writes_png(self, tmp_path: Any) -> None:
        img = Image.new("RGB", (32, 32), color="green")
        out = TextureGenerator.save_image(img, tmp_path / "out.png")
        assert out.exists()
        assert out.suffix == ".png"

    def test_save_image_creates_parent_dirs(self, tmp_path: Any) -> None:
        img = Image.new("RGB", (16, 16), color="blue")
        target = tmp_path / "nested" / "deep" / "img.png"
        out = TextureGenerator.save_image(img, target)
        assert out.exists()
