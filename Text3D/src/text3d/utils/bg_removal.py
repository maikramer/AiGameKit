"""Lazy-loaded BiRefNet background removal (ZhengPeng7/BiRefNet)."""

from __future__ import annotations

import contextlib

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


def _preferred_cuda_dtype() -> torch.dtype:
    """Use fp16 on CUDA (torchvision's CUDA deform_conv2d lacks BFloat16 kernels)."""
    return torch.float16


class BiRefNetBGRemover:
    """Removes image backgrounds using BiRefNet.

    The model is loaded lazily on the first call to ``remove_background()``.
    Call ``offload_to_cpu()`` between inferences to reclaim VRAM while keeping
    weights warm in RAM, or ``unload()`` to drop the model completely.
    """

    def __init__(self, device: str | None = None) -> None:
        self._device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self._model: AutoModelForImageSegmentation | None = None
        self._on_cpu: bool = False

    def _is_cuda_device(self) -> bool:
        return str(self._device).startswith("cuda") and torch.cuda.is_available()

    def _ensure_loaded(self) -> None:
        is_cuda = self._is_cuda_device()
        if self._model is not None:
            if self._on_cpu and is_cuda:
                self._model.to(self._device, non_blocking=True)
                self._on_cpu = False
            return
        self._model = AutoModelForImageSegmentation.from_pretrained(_MODEL_ID, trust_remote_code=True)
        if is_cuda:
            target_dtype = _preferred_cuda_dtype()
            self._model.to(device=self._device, dtype=target_dtype)
            with contextlib.suppress(Exception):
                self._model.to(memory_format=torch.channels_last)
        else:
            self._model.to(self._device)
        self._model.eval()
        self._on_cpu = not is_cuda

    @torch.inference_mode()
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
        is_cuda = self._is_cuda_device()
        model_dtype = getattr(self._model, "dtype", torch.float32)
        input_tensor = _TRANSFORM(image).unsqueeze(0).to(self._device, dtype=model_dtype, non_blocking=is_cuda)
        if is_cuda:
            with contextlib.suppress(Exception):
                input_tensor = input_tensor.contiguous(memory_format=torch.channels_last)

        preds = self._model(input_tensor)[-1].sigmoid().float().cpu()
        mask = transforms.ToPILImage()(preds[0].squeeze()).resize(image_size)

        image.putalpha(mask)
        return image

    def offload_to_cpu(self) -> None:
        """Move weights to CPU and clear CUDA cache, keeping BiRefNet warm in RAM."""
        if self._model is not None and not self._on_cpu and self._is_cuda_device():
            self._model.to("cpu")
            self._on_cpu = True
            clear_cuda_memory()

    def unload(self) -> None:
        """Free VRAM: delete the model and clear CUDA cache."""
        if self._model is not None:
            del self._model
            self._model = None
            self._on_cpu = False
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
