"""BiRefNetBGRemover — unit tests (mocked model, no GPU required)."""

from __future__ import annotations

from unittest.mock import MagicMock

from PIL import Image


def _rgb_image(size: int = 64) -> Image.Image:
    return Image.new("RGB", (size, size), color=(128, 64, 32))


def test_init_default_device() -> None:
    from text3d.utils.bg_removal import BiRefNetBGRemover

    remover = BiRefNetBGRemover(device="cpu")
    assert remover._device == "cpu"
    assert remover._model is None


def test_unload_without_load() -> None:
    from text3d.utils.bg_removal import BiRefNetBGRemover

    remover = BiRefNetBGRemover(device="cpu")
    remover.unload()
    assert remover._model is None


def test_unload_clears_model() -> None:
    from text3d.utils.bg_removal import BiRefNetBGRemover

    remover = BiRefNetBGRemover(device="cpu")
    remover._model = MagicMock()
    remover.unload()
    assert remover._model is None


def test_remove_background_converts_rgba() -> None:
    from text3d.utils.bg_removal import BiRefNetBGRemover

    import torch

    fake_mask = torch.rand(1, 1, 1024, 1024)

    fake_model = MagicMock()
    fake_model.dtype = torch.float32
    fake_model.return_value = [None, None, None, fake_mask]
    remover = BiRefNetBGRemover(device="cpu")
    remover._model = fake_model

    img = _rgb_image()
    result = remover.remove_background(img)

    assert result.mode == "RGBA"
    assert result.size == img.size


def test_remove_background_handles_grayscale_input() -> None:
    import torch

    from text3d.utils.bg_removal import BiRefNetBGRemover

    fake_mask = torch.rand(1, 1, 1024, 1024)
    fake_model = MagicMock()
    fake_model.dtype = torch.float32
    fake_model.return_value = [None, None, None, fake_mask]

    remover = BiRefNetBGRemover(device="cpu")
    remover._model = fake_model

    img = Image.new("L", (32, 32), color=128)
    result = remover.remove_background(img)

    assert result.mode == "RGBA"
