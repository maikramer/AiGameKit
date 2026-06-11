"""Lazy-loaded BiRefNet background removal (ZhengPeng7/BiRefNet)."""

from __future__ import annotations

import torch
from PIL import Image
from torchvision import transforms
from transformers import AutoModelForImageSegmentation

from text3d.utils.memory import clear_cuda_memory

_MODEL_ID = "ZhengPeng7/BiRefNet"

_TRANSFORM = transforms.Compose(
    [
        transforms.Resize((1024, 1024)),
        transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225]),
    ]
)


class BiRefNetBGRemover:
    """Removes image backgrounds using BiRefNet.

    The model is loaded lazily on the first call to ``remove_background()``.
    Call ``unload()`` to free VRAM when done.
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model: AutoModelForImageSegmentation | None = None

    def _ensure_loaded(self) -> None:
        if self._model is not None:
            return
        self._model = AutoModelForImageSegmentation.from_pretrained(_MODEL_ID, trust_remote_code=True)
        self._model.to(self._device)
        self._model.eval()

    @torch.no_grad()
    def remove_background(self, image: Image.Image) -> Image.Image:
        """Remove background from *image*, returning an RGBA PIL Image.

        Args:
            image: Input image (any mode).

        Returns:
            RGBA image with transparent background.
        """
        self._ensure_loaded()
        assert self._model is not None

        if image.mode != "RGB":
            image = image.convert("RGB")

        image_size = image.size
        input_tensor = _TRANSFORM(image).unsqueeze(0).to(self._device, dtype=self._model.dtype)

        preds = self._model(input_tensor)[-1].sigmoid().cpu()
        mask = transforms.ToPILImage()(preds[0].squeeze()).resize(image_size)

        image.putalpha(mask)
        return image

    def unload(self) -> None:
        """Free VRAM: delete the model and clear CUDA cache."""
        if self._model is not None:
            del self._model
            self._model = None
            clear_cuda_memory()


def has_meaningful_alpha(image: Image.Image, threshold: int = 250) -> bool:
    """True se a imagem tem canal alpha com transparência real (silhueta)."""
    if "A" not in image.getbands():
        return False
    import numpy as np

    alpha = np.asarray(image.getchannel("A"))
    return bool((alpha < threshold).any())


def key_uniform_background(image: Image.Image, tolerance: int = 14) -> Image.Image | None:
    """Alpha keying barato para renders com fundo uniforme (branco/cinza liso).

    Flood-fill a partir dos 4 cantos sobre a cor mediana da borda. Sem rede
    neural — serve para inputs ``--no-remove-bg`` que são renders limpos.
    Sem silhueta no conditioning, o preprocessor do Hunyuan trata o frame
    inteiro como objecto e o modelo esculpe placas/pedestais fundidos.

    Returns:
        RGBA com fundo transparente, ou ``None`` se a borda não for uniforme
        (fundo fotográfico → usar BiRefNet).
    """
    import cv2
    import numpy as np

    rgb = np.asarray(image.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    if h < 8 or w < 8:
        return None

    border = np.concatenate([rgb[0, :], rgb[-1, :], rgb[:, 0], rgb[:, -1]]).astype(np.int16)
    median = np.median(border, axis=0)
    deviates = np.abs(border - median).max(axis=1) > tolerance
    if deviates.mean() > 0.02:
        return None  # fundo não-uniforme; keying simples seria destrutivo

    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    src = rgb.copy()
    flags = cv2.FLOODFILL_MASK_ONLY | 8 | (255 << 8)
    for seed in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        cv2.floodFill(src, ff_mask, seed, 0, loDiff=(tolerance,) * 3, upDiff=(tolerance,) * 3, flags=flags)

    background = ff_mask[1:-1, 1:-1] > 0
    if not background.any() or background.all():
        return None

    alpha = np.where(background, 0, 255).astype(np.uint8)
    rgba = np.dstack([rgb, alpha])
    return Image.fromarray(rgba, "RGBA")


def crop_to_content(image: Image.Image, pad_ratio: float = 0.05) -> Image.Image:
    """Crop RGBA image to the bounding box of non-transparent content.

    Adds a small padding (``pad_ratio`` of bbox size) to avoid tight edges.
    If the image is fully transparent (getbbox returns None), returns as-is.

    Args:
        image: RGBA PIL Image (typically from BiRefNet background removal).
        pad_ratio: Fraction of bbox dimensions to pad on each side (default 5%).

    Returns:
        Cropped RGBA PIL Image, or the original if no content found.
    """
    bbox = image.getbbox()
    if bbox is None:
        return image
    left, top, right, bottom = bbox
    w = right - left
    h = bottom - top
    pad_x = int(w * pad_ratio)
    pad_y = int(h * pad_ratio)
    cropped = image.crop(
        (max(0, left - pad_x), max(0, top - pad_y), min(image.width, right + pad_x), min(image.height, bottom + pad_y))
    )
    return cropped
