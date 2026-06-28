"""Testes para skymap2d.generator."""

from unittest.mock import MagicMock, patch

from PIL import Image

from skymap2d.generator import (
    DEFAULT_MODEL_ID,
    augment_prompt_for_equirectangular,
    default_model_id,
    merge_negative_prompt,
)


class TestAugmentPrompt:
    def test_adds_equirectangular_prefix(self):
        result = augment_prompt_for_equirectangular("sunset sky")
        assert "equirectangular" in result.lower()
        assert "sunset sky" in result

    def test_skips_if_already_equirectangular(self):
        original = "equirectangular sunset panorama"
        result = augment_prompt_for_equirectangular(original)
        assert result == original

    def test_skips_if_panorama(self):
        original = "panorama of a mountain range"
        result = augment_prompt_for_equirectangular(original)
        assert result == original

    def test_skips_if_360(self):
        original = "360 degree view of ocean"
        result = augment_prompt_for_equirectangular(original)
        assert result == original

    def test_skips_if_hdri(self):
        original = "hdri sky environment"
        result = augment_prompt_for_equirectangular(original)
        assert result == original

    def test_empty_prompt(self):
        assert augment_prompt_for_equirectangular("") == ""
        assert augment_prompt_for_equirectangular("   ") == ""


class TestMergeNegativePrompt:
    def test_only_preset(self):
        result = merge_negative_prompt("indoor", "")
        assert result == "indoor"

    def test_only_user(self):
        result = merge_negative_prompt("", "low quality")
        assert result == "low quality"

    def test_both_different(self):
        result = merge_negative_prompt("indoor", "low quality")
        assert "indoor" in result
        assert "low quality" in result

    def test_subset_dedup(self):
        result = merge_negative_prompt("indoor", "indoor, low quality")
        assert result == "indoor, low quality"


class TestDefaultModelId:
    def test_default(self):
        assert default_model_id() == DEFAULT_MODEL_ID

    @patch.dict("os.environ", {"SKYMAP2D_MODEL_ID": "custom/model"})
    def test_env_override(self):
        assert default_model_id() == "custom/model"


class TestSkymapGenerator:
    def test_init(self):
        from skymap2d.generator import SkymapGenerator

        gen = SkymapGenerator(device="cpu")
        assert gen.model_id == DEFAULT_MODEL_ID

    def test_generate_returns_image(self):
        from skymap2d.generator import SkymapGenerator

        gen = SkymapGenerator(device="cpu")
        with patch.object(gen, "_load_pipeline") as mock_load:
            mock_pipe = MagicMock()
            mock_pipe.return_value = (Image.new("RGB", (128, 64)), {"sample": None})
            mock_load.return_value = mock_pipe

            image, metadata = gen.generate(
                prompt="test sunset sky",
                width=1024,
                height=512,
                num_inference_steps=10,
            )
            assert isinstance(image, Image.Image)
            assert "seed" in metadata
