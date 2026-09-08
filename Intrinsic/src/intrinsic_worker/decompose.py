"""Core da decomposição intrínseca — pós-processamento do compphoto/Intrinsic.

Os imports pesados (torch, intrinsic.pipeline) são lazy: o módulo importa
sem GPU para os testes CPU-first (o modelo é mockado/injetado).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


@dataclass
class DecomposeOutputs:
    """Caminhos dos três PNGs gerados (display-referred, 8-bit)."""

    albedo: Path
    shading: Path
    specular: Path


def invert(x: np.ndarray, eps: float = 1e-4) -> np.ndarray:
    """Inversão de brilho do chrislib (1/x) com guarda numérica.

    O shading colorido do pipeline v2 vive em espaço inverso; a versão
    display é ``1 - invert(dif_shd)``.
    """
    return 1.0 / np.clip(x, eps, None)


def load_image_rgb(path: str | Path) -> np.ndarray:
    """Carrega uma imagem como RGB float32 em [0, 1] (sem canal alpha)."""
    from PIL import Image

    img = Image.open(path).convert("RGB")
    return np.asarray(img, dtype=np.float32) / 255.0


def to_u8(x: np.ndarray) -> np.ndarray:
    """Quantiza um array float [0,1] (ou clipado) para u8 com round-to-nearest."""
    return (np.clip(x, 0.0, 1.0) * 255.0 + 0.5).astype(np.uint8)


def save_rgb(x: np.ndarray, path: str | Path) -> Path:
    """Salva um array HxWx3 float [0,1] como PNG 8-bit."""
    from PIL import Image

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(to_u8(x)).save(path)
    return path


def upscale_to(x: np.ndarray, height: int, width: int) -> np.ndarray:
    """Resize bilinear HxWx3 para (height, width) — torch se disponível,
    PIL como fallback (CPU, testes)."""
    if x.shape[0] == height and x.shape[1] == width:
        return x
    try:
        import torch

        t = torch.from_numpy(np.transpose(x, (2, 0, 1)))[None].float()
        t = torch.nn.functional.interpolate(t, size=(height, width), mode="bilinear", align_corners=False)
        return np.transpose(t[0].numpy(), (1, 2, 0))
    except Exception:
        from PIL import Image

        img = Image.fromarray(to_u8(x)).resize((width, height), Image.BILINEAR)
        return np.asarray(img, dtype=np.float32) / 255.0


def output_paths(image_path: str | Path, output_dir: str | Path) -> DecomposeOutputs:
    """Nomes canónicos: <stem>_albedo/_shading/_specular.png no output_dir."""
    stem = Path(image_path).stem or "image"
    out = Path(output_dir)
    return DecomposeOutputs(
        albedo=out / f"{stem}_albedo.png",
        shading=out / f"{stem}_shading.png",
        specular=out / f"{stem}_specular.png",
    )


def run_decompose(models: Any, image_path: str | Path, output_dir: str | Path) -> DecomposeOutputs:
    """Corre o pipeline v2 e salva os três PNGs (display-referred).

    Args:
        models: dict devolvido por ``intrinsic.pipeline.load_models``.
        image_path: imagem de entrada (RGB).
        output_dir: diretório de saída (criado se necessário).

    Returns:
        Caminhos dos PNGs gerados.
    """
    from intrinsic.pipeline import run_pipeline

    img = load_image_rgb(image_path)
    results = run_pipeline(models, img, device="cuda")

    h, w = img.shape[:2]

    # Albedo: já em [0,1] (sigmoid) — só upscale + gamma-linear? O modelo
    # devolve albedo linear; a visualização oficial aplica view() (gamma).
    # Para consumo PBR guardamos o linear quantizado (o materialize aplica
    # as suas transformações).
    albedo = upscale_to(np.asarray(results["hr_alb"], dtype=np.float32), h, w)

    # Shading colorido: espaço inverso → tonemap display 1 - invert(x).
    shd_inv = np.asarray(results["dif_shd"], dtype=np.float32)
    shading = upscale_to(1.0 - invert(shd_inv), h, w)

    # Especular: residual positivo (highlights / fontes de luz).
    specular = upscale_to(np.asarray(results.get("pos_res", results["residual"]), dtype=np.float32), h, w)

    paths = output_paths(image_path, output_dir)
    save_rgb(albedo, paths.albedo)
    save_rgb(shading, paths.shading)
    save_rgb(specular, paths.specular)
    return paths
