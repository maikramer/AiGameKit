"""Testes para text2icon.generator."""

from unittest.mock import MagicMock, patch

from PIL import Image

from text2icon.generator import (
    DEFAULT_MODEL_ID,
    augment_prompt_for_icon,
    default_model_id,
)


class TestAugmentPrompt:
    def test_adds_icon_prefix(self):
        result = augment_prompt_for_icon("health potion")
        assert "app icon" in result.lower() or "icon" in result.lower()
        assert "health potion" in result

    def test_skips_if_already_icon(self):
        original = "health potion icon"
        result = augment_prompt_for_icon(original)
        assert result == original

    def test_skips_if_logo(self):
        original = "company logo"
        result = augment_prompt_for_icon(original)
        assert result == original

    def test_skips_if_emblem(self):
        original = "guild emblem"
        result = augment_prompt_for_icon(original)
        assert result == original

    def test_empty_prompt(self):
        assert augment_prompt_for_icon("") == ""
        assert augment_prompt_for_icon("   ") == ""


class TestDefaultModelId:
    def test_default(self):
        assert default_model_id() == DEFAULT_MODEL_ID

    @patch.dict("os.environ", {"TEXT2ICON_MODEL_ID": "custom/model"})
    def test_env_override(self):
        assert default_model_id() == "custom/model"


class TestSanaIconGenerator:
    def test_init(self):
        from text2icon.generator import SanaIconGenerator

        gen = SanaIconGenerator(device="cpu")
        assert gen.transformer_id == DEFAULT_MODEL_ID

    @patch("text2icon.generator.SanaIconGenerator._load_pipeline")
    def test_generate_returns_image(self, mock_load):
        from text2icon.generator import SanaIconGenerator

        mock_image = Image.new("RGB", (64, 64), color="red")
        fake_out = MagicMock()
        fake_out.images = [mock_image]
        fake_pipe = MagicMock(return_value=fake_out)
        mock_load.return_value = fake_pipe

        gen = SanaIconGenerator(device="cpu")
        image, metadata = gen.generate(
            prompt="test icon",
            width=256,
            height=256,
            num_inference_steps=2,
            seed=1,
        )
        assert isinstance(image, Image.Image)
        assert "seed" in metadata
        assert metadata["remove_background"] is False
        fake_pipe.assert_called_once()

    @patch("text2icon.generator.SanaIconGenerator._load_pipeline")
    def test_generate_with_transparent_calls_rembg(self, mock_load):
        from text2icon.generator import SanaIconGenerator

        mock_image = Image.new("RGB", (64, 64), color="red")
        fake_out = MagicMock()
        fake_out.images = [mock_image]
        fake_pipe = MagicMock(return_value=fake_out)
        mock_load.return_value = fake_pipe

        gen = SanaIconGenerator(device="cpu")

        # Mock bg_removal.remove_background to return an RGBA image
        rgba_image = Image.new("RGBA", (64, 64), color=(255, 0, 0, 128))
        with patch("text2icon.bg_removal.remove_background", return_value=rgba_image) as mock_rbg:
            image, metadata = gen.generate(
                prompt="sword icon",
                width=256,
                height=256,
                num_inference_steps=2,
                seed=1,
                remove_background=True,
            )

        mock_rbg.assert_called_once()
        assert image.mode == "RGBA"
        assert metadata["remove_background"] is True
