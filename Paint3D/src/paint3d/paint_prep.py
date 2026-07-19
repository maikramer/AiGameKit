"""Prep de textura para Hunyuan-Paint — inpaint restrito.

Double-shell / cascas internas resolvem-se no ``topology-fix`` (shape→clean)
antes do paint. Aqui só evitamos bleed de inpaint em ilhas UV nunca baked.
"""

from __future__ import annotations

from typing import Any

import numpy as np

# Dilatação UV (px) à volta de texels com bake trust — só esses buracos
# recebem OpenCV/vertex inpaint. Ilhas nunca vistas ficam cinza neutro.
DEFAULT_INPAINT_DILATE_PX = 16
_NEUTRAL_RGB = (0.45, 0.45, 0.45)


def restrict_inpaint_mask(
    mask: np.ndarray,
    *,
    dilate_px: int = DEFAULT_INPAINT_DILATE_PX,
) -> tuple[np.ndarray, np.ndarray]:
    """Restringe buracos de inpaint à vizinhança de texels com trust.

    Convenção Hunyuan ``uv_inpaint``: ``255`` = keep, ``0`` = inpaint.

    Args:
        mask: Máscara uint8 (H,W) ou (H,W,1).
        dilate_px: Raio de dilatação à volta da região trusted.

    Returns:
        ``(new_mask, far_holes)`` — ``far_holes`` bool onde o buraco está
        longe de qualquer bake (não deve ser inpaintado).
    """
    import cv2

    m = np.asarray(mask)
    if m.ndim == 3:
        m = m.squeeze(-1)
    m = m.astype(np.uint8, copy=False)
    trusted = (m > 0).astype(np.uint8)
    if dilate_px > 0:
        k = 2 * int(dilate_px) + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        near = cv2.dilate(trusted, kernel)
    else:
        near = trusted
    holes = m == 0
    to_inpaint = holes & (near > 0)
    far_holes = holes & (near == 0)
    new_mask = np.where(to_inpaint, np.uint8(0), np.uint8(255))
    return new_mask, far_holes


def fill_far_holes(texture: Any, far_holes: np.ndarray, *, rgb: tuple[float, float, float] = _NEUTRAL_RGB) -> Any:
    """Preenche ilhas UV nunca baked com cor neutra (ou média dos trusted)."""
    if not far_holes.any():
        return texture

    import torch

    if isinstance(texture, torch.Tensor):
        tex = texture.clone()
        keep = ~torch.as_tensor(far_holes, device=tex.device)
        if keep.ndim == 2 and tex.ndim == 3:
            fill = tex[keep].mean(dim=0) if keep.any() else torch.tensor(rgb, dtype=tex.dtype, device=tex.device)
            tex[far_holes] = fill
        return tex

    arr = np.asarray(texture, dtype=np.float32).copy()
    if arr.ndim == 3 and far_holes.ndim == 2:
        keep = ~far_holes
        fill = arr[keep].mean(axis=0) if keep.any() else np.asarray(rgb, dtype=np.float32)
        arr[far_holes] = fill
    return arr


def install_restricted_inpaint(view_processor: Any, *, dilate_px: int = DEFAULT_INPAINT_DILATE_PX) -> None:
    """Monkeypatch ``view_processor.texture_inpaint`` para skip ilhas nunca vistas."""
    orig = view_processor.texture_inpaint

    def _restricted(texture: Any, mask: Any, defualt: Any = None) -> Any:
        if defualt is not None:
            return orig(texture, mask, defualt=defualt)
        mask_np = np.asarray(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np.squeeze(-1)
        new_mask, far = restrict_inpaint_mask(mask_np, dilate_px=dilate_px)
        texture = fill_far_holes(texture, far)
        return orig(texture, new_mask)

    view_processor.texture_inpaint = _restricted  # type: ignore[method-assign]
