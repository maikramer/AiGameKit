"""Testes para texture2d.generator (SD1.5 + circular padding)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import torch
from PIL import Image

from texture2d.generator import (
    DEFAULT_GUIDANCE,
    DEFAULT_MODEL_ID,
    DEFAULT_STEPS,
    SD_BASE_NEGATIVE,
    TextureGenerator,
    default_model_id,
    merge_negative_prompt,
    patch_conv2d_circular,
)

# ---------------------------------------------------------------------------
# patch_conv2d_circular
# ---------------------------------------------------------------------------


class TestPatchConv2dCircular:
    def test_patches_all_nested_convs(self):
        model = torch.nn.Sequential(
            torch.nn.Conv2d(3, 8, 3, padding=1),
            torch.nn.Sequential(torch.nn.Conv2d(8, 8, 3, padding=1), torch.nn.ReLU()),
        )
        count = patch_conv2d_circular(model)
        assert count == 2
        for m in model.modules():
            if isinstance(m, torch.nn.Conv2d):
                assert m.padding_mode == "circular"

    def test_output_wraps_at_borders(self):
        """Com padding circular, a conv de uma imagem constante por colunas
        produz bordas esquerda/direita contínuas (sem efeito de borda zero)."""
        conv = torch.nn.Conv2d(1, 1, 3, padding=1, bias=False)
        torch.nn.init.constant_(conv.weight, 1.0)
        x = torch.ones(1, 1, 8, 8)
        y_zero = conv(x)
        patch_conv2d_circular(conv)
        y_circ = conv(x)
        # Zero padding atenua as bordas; circular mantém o valor uniforme.
        assert y_zero[0, 0, 0, 0] < y_circ[0, 0, 0, 0]
        assert torch.allclose(y_circ, torch.full_like(y_circ, 9.0))

    def test_ignores_non_conv_layers(self):
        model = torch.nn.Sequential(torch.nn.Linear(4, 4), torch.nn.ReLU())
        assert patch_conv2d_circular(model) == 0


# ---------------------------------------------------------------------------
# Helpers puros
# ---------------------------------------------------------------------------


class TestMergeNegativePrompt:
    def test_only_preset(self):
        assert merge_negative_prompt("blurry", "") == "blurry"

    def test_only_user(self):
        assert merge_negative_prompt("", "low quality") == "low quality"

    def test_both_different(self):
        m = merge_negative_prompt("a", "b")
        assert "a" in m and "b" in m

    def test_subset_dedup(self):
        assert merge_negative_prompt("blur", "no blur please") == "no blur please"


class TestDefaultModelId:
    def test_default(self):
        assert default_model_id() == DEFAULT_MODEL_ID

    @patch.dict("os.environ", {"TEXTURE2D_MODEL_ID": "custom/model"})
    def test_env_override(self):
        assert default_model_id() == "custom/model"


# ---------------------------------------------------------------------------
# TextureGenerator.generate (com pipeline mocked)
# ---------------------------------------------------------------------------


class _FakePipeOutput:
    def __init__(self) -> None:
        self.images = [Image.new("RGB", (64, 64), (120, 130, 90))]


class _FakePipe:
    def __init__(self) -> None:
        self.last_kwargs: dict | None = None

    def __call__(self, **kwargs):
        self.last_kwargs = kwargs
        return _FakePipeOutput()


def _make_gen() -> tuple[TextureGenerator, _FakePipe]:
    gen = TextureGenerator(device="cpu")
    fake = _FakePipe()
    return gen, fake


class TestGenerate:
    def test_generate_returns_image_and_metadata(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            image, meta = gen.generate("red woven fabric", seed=42, width=256, height=256, ground="off")
        assert image.size == (64, 64)
        assert meta["backend"] == "sd-circular"
        assert meta["seed"] == 42
        assert "seamless texture" in meta["prompt_final"]

    def test_base_negative_always_applied(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("brick wall texture", seed=1, ground="off")
        assert SD_BASE_NEGATIVE in meta["negative_prompt"]

    def test_ground_mode_enhances_prompt_and_negative(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("green grass", seed=1, ground="on")
        assert "top-down" in meta["prompt_final"]
        assert "isometric" in meta["negative_prompt"]

    def test_legacy_kwargs_ignored(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate(
                "wood planks texture",
                seed=7,
                lora_strength=1.0,
                true_cfg_scale=2.0,
                ground="off",
            )
        assert meta["seed"] == 7
        assert "lora_strength" not in fake.last_kwargs

    def test_random_seed_when_none(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("sand texture", seed=None, ground="off")
        assert isinstance(meta["seed"], int)
        assert meta["seed"] >= 0

    def test_preset_applied(self):
        gen, fake = _make_gen()
        with patch.object(gen, "_load_pipeline", return_value=fake):
            _, meta = gen.generate("test", seed=1, preset="Wood", ground="off")
        # O preset Wood prefixa o prompt base.
        assert "seamless wood texture" in meta["prompt_final"]
        assert meta["guidance_scale"] == 7.5

    def test_default_guidance_is_sd(self):
        """Default guidance deve ser 7.0 (CFG real do SD1.5), não 3.5 (FLUX)."""
        assert DEFAULT_GUIDANCE == 7.0
        assert DEFAULT_STEPS == 30

    @patch.object(TextureGenerator, "_load_pipeline")
    def test_generate_returns_image_via_mocked_pipe(self, mock_load):
        mock_image = Image.new("RGB", (64, 64), color="red")
        fake_out = MagicMock()
        fake_out.images = [mock_image]
        fake_pipe = MagicMock(return_value=fake_out)
        mock_load.return_value = fake_pipe

        gen = TextureGenerator(device="cpu")
        image, metadata = gen.generate(
            prompt="test stone",
            width=256,
            height=256,
            num_inference_steps=10,
            seed=1,
            ground="off",
        )
        assert isinstance(image, Image.Image)
        assert metadata["seed"] == 1
        fake_pipe.assert_called_once()


# ---------------------------------------------------------------------------
# generate_batch (herdado da base — error continuation + seed increment)
# ---------------------------------------------------------------------------


class TestGenerateBatch:
    def test_generate_batch_continues_on_error(self):
        class FlakyPipe:
            calls = 0

            def __call__(self, **kwargs):
                FlakyPipe.calls += 1
                if FlakyPipe.calls == 1:
                    raise RuntimeError("boom")
                return _FakePipeOutput()

        gen = TextureGenerator(device="cpu")
        with patch.object(gen, "_load_pipeline", return_value=FlakyPipe()):
            results = list(gen.generate_batch(["a texture", "b texture"], ground="off"))
        assert len(results) == 2
        assert results[0][0] is None
        assert "boom" in results[0][1]["error"]
        assert results[1][0] is not None


# ---------------------------------------------------------------------------
# Init / construction
# ---------------------------------------------------------------------------


class TestInit:
    def test_init_defaults(self):
        gen = TextureGenerator(device="cpu")
        assert gen.model_id == DEFAULT_MODEL_ID
        assert gen.device == "cpu"
        # CPU usa float32 (base default), CUDA usa float16 (override do SD).
        assert gen.torch_dtype == torch.float32

    def test_init_dtype_fp16_on_cuda(self):
        """Mesmo que a base devolva bfloat16 em CUDA, o SD força float16.

        Sem CUDA real a base faz fallback ``cuda``→``cpu`` (float32) — skip.
        """
        if not torch.cuda.is_available():
            import pytest

            pytest.skip("CUDA required for fp16 dtype assertion")
        gen = TextureGenerator(device="cuda")
        assert gen.device.startswith("cuda")
        assert gen.torch_dtype == torch.float16
