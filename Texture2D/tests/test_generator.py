"""Testes para texture2d.generator."""

from unittest.mock import MagicMock, patch

from PIL import Image

from texture2d.generator import (
    DEFAULT_MODEL_ID,
    augment_prompt_for_seamless,
    default_model_id,
    merge_negative_prompt,
)


class TestAugmentPrompt:
    def test_adds_seamless_prefix(self):
        result = augment_prompt_for_seamless("stone wall")
        assert "seamless" in result.lower()
        assert "stone wall" in result

    def test_skips_if_already_seamless(self):
        original = "seamless brick texture"
        result = augment_prompt_for_seamless(original)
        assert result == original

    def test_skips_if_tileable(self):
        original = "tileable marble floor"
        result = augment_prompt_for_seamless(original)
        assert result == original

    def test_empty_prompt(self):
        assert augment_prompt_for_seamless("") == ""
        assert augment_prompt_for_seamless("   ") == ""


class TestMergeNegativePrompt:
    def test_only_preset(self):
        result = merge_negative_prompt("blurry", "")
        assert result == "blurry"

    def test_only_user(self):
        result = merge_negative_prompt("", "low quality")
        assert result == "low quality"

    def test_both_different(self):
        result = merge_negative_prompt("blurry", "low quality")
        assert "blurry" in result
        assert "low quality" in result

    def test_subset_dedup(self):
        result = merge_negative_prompt("blurry", "blurry, low quality")
        assert result == "blurry, low quality"


class TestDefaultModelId:
    def test_default(self):
        assert default_model_id() == DEFAULT_MODEL_ID

    @patch.dict("os.environ", {"TEXTURE2D_MODEL_ID": "custom/model"})
    def test_env_override(self):
        assert default_model_id() == "custom/model"


class TestTextureGenerator:
    @patch("texture2d.generator.TextureGenerator._init_client")
    def test_init(self, mock_client):
        from texture2d.generator import TextureGenerator

        mock_client.return_value = MagicMock()
        gen = TextureGenerator()
        assert gen.model_id == DEFAULT_MODEL_ID

    @patch("texture2d.generator.TextureGenerator._init_client")
    def test_generate_returns_image(self, mock_init):
        from texture2d.generator import TextureGenerator

        mock_image = Image.new("RGB", (64, 64), color="red")
        mock_client = MagicMock()
        mock_client.text_to_image.return_value = mock_image
        mock_init.return_value = mock_client

        gen = TextureGenerator()
        image, metadata = gen.generate(
            prompt="test stone",
            width=256,
            height=256,
            num_inference_steps=10,
        )
        assert isinstance(image, Image.Image)
        assert "seed" in metadata
        mock_client.text_to_image.assert_called_once()
