"""Métricas de imagem (MAE, RMSE, SSIM simplificado) para pares PNG."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image


def _to_float_rgb_a(arr: np.ndarray) -> np.ndarray:
    """H x W x C, float32 0..1, RGB."""
    if arr.ndim == 2:
        arr = np.stack([arr, arr, arr], axis=-1)
    if arr.shape[-1] == 4:
        arr = arr[..., :3]
    return arr.astype(np.float32) / 255.0


def load_pair_same_size(
    path_a: Path,
    path_b: Path,
) -> tuple[np.ndarray, np.ndarray]:
    """Carrega dois PNG, redimensiona ao menor lado comum (center crop ou resize)."""
    ia = Image.open(path_a).convert("RGBA")
    ib = Image.open(path_b).convert("RGBA")
    wa, ha = ia.size
    wb, hb = ib.size
    w = min(wa, wb)
    h = min(ha, hb)

    def crop_resize(im: Image.Image) -> Image.Image:
        iw, ih = im.size
        if iw != w or ih != h:
            left = (iw - w) // 2
            top = (ih - h) // 2
            im = im.crop((left, top, left + w, top + h))
        return im

    ia2 = crop_resize(ia)
    ib2 = crop_resize(ib)
    aa = np.array(ia2)
    bb = np.array(ib2)
    fa = _to_float_rgb_a(aa)
    fb = _to_float_rgb_a(bb)
    return fa, fb


def metrics_mae_rmse_ssim(a: np.ndarray, b: np.ndarray) -> dict[str, float]:
    """a, b: H x W x 3 float 0..1."""
    diff = a - b
    mae = float(np.mean(np.abs(diff)))
    rmse = float(np.sqrt(np.mean(diff**2)))

    # SSIM simplificado (janela global, canal a canal, depois média)
    c1, c2 = 0.01**2, 0.03**2
    ssim_ch = []
    for c in range(3):
        x, y = a[..., c], b[..., c]
        mu_x, mu_y = float(np.mean(x)), float(np.mean(y))
        sig_x = float(np.var(x))
        sig_y = float(np.var(y))
        sig_xy = float(np.mean((x - mu_x) * (y - mu_y)))
        num = (2 * mu_x * mu_y + c1) * (2 * sig_xy + c2)
        den = (mu_x**2 + mu_y**2 + c1) * (sig_x + sig_y + c2)
        if den > 0:
            ssim_ch.append(num / den)
        else:
            ssim_ch.append(1.0)
    ssim = float(np.mean(ssim_ch))

    return {"mae": mae, "rmse": rmse, "ssim": ssim}


def compare_view_pair(path_a: Path, path_b: Path) -> dict[str, Any]:
    fa, fb = load_pair_same_size(path_a, path_b)
    m = metrics_mae_rmse_ssim(fa, fb)
    return {"path_a": str(path_a), "path_b": str(path_b), **m}
