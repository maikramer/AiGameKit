"""
Upscaling de texturas com Real-ESRGAN (via spandrel + huggingface_hub).

Escala a textura baseColor de 1024→2048 ou 4096, adicionando detalhe via IA
sem alterar a identidade visual. Processamento por tiles para VRAM limitada.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import torch
import trimesh
from PIL import Image

from gamedev_shared.logging import Logger

_logger = Logger("paint3d.upscale")

_MODEL_REPO = "ai-forever/Real-ESRGAN"
_MODEL_FILENAME = "RealESRGAN_x4.pth"
_SCALE = 4

_HINT = "Upscaling requer spandrel e huggingface_hub.\n  pip install spandrel huggingface-hub"


def _download_model() -> Path:
    from huggingface_hub import hf_hub_download

    return Path(
        hf_hub_download(
            repo_id=_MODEL_REPO,
            filename=_MODEL_FILENAME,
        )
    )


def _load_model(device: torch.device | str = "cpu") -> torch.nn.Module:
    import spandrel

    path = _download_model()
    model_descriptor = spandrel.ModelLoader(device=device).load_from_file(str(path))
    model = model_descriptor.model.eval()
    return model


def _img_to_tensor(img: Image.Image, device: torch.device | str) -> torch.Tensor:
    arr = np.array(img.convert("RGB")).astype(np.float32) / 255.0
    return torch.from_numpy(arr).permute(2, 0, 1).unsqueeze(0).to(device)


def _tensor_to_img(t: torch.Tensor) -> Image.Image:
    arr = t.squeeze(0).permute(1, 2, 0).cpu().clamp(0, 1).numpy()
    return Image.fromarray((arr * 255).astype(np.uint8))


def upscale_image(
    image: Image.Image,
    *,
    scale: int = 4,
    tile_size: int = 512,
    tile_pad: int = 16,
    device: str | None = None,
    half: bool = True,
    verbose: bool = False,
) -> Image.Image:
    """
    Upscale image using Real-ESRGAN (4x model, tiled for low VRAM).

    Parameters
    ----------
    image : PIL.Image
        Input (typically 1024x1024 texture atlas).
    scale : int
        Output scale factor (2 or 4). If 2, downsamples the 4x result.
    tile_size : int
        Tile size for processing (lower = less VRAM).
    tile_pad : int
        Overlap between tiles to avoid seam artifacts.
    device : str
        "cuda" or "cpu". Auto-detects if None.
    half : bool
        Use FP16 (faster, less VRAM on CUDA).
    verbose : bool
        Print progress.
    """
    try:
        import spandrel  # noqa: F401
    except ImportError as e:
        raise RuntimeError(_HINT) from e

    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"

    if verbose:
        _logger.info(f"device={device}, scale={scale}, tile={tile_size}, half={half}")
        _logger.info(f"Input: {image.size[0]}x{image.size[1]}")

    model = _load_model(device)
    if half and device != "cpu":
        model = model.half()

    img_tensor = _img_to_tensor(image, device)
    if half and device != "cpu":
        img_tensor = img_tensor.half()

    h, w = img_tensor.shape[2], img_tensor.shape[3]

    if h <= tile_size and w <= tile_size:
        with torch.no_grad():
            output = model(img_tensor)
    else:
        output = _tiled_inference(model, img_tensor, tile_size, tile_pad, _SCALE, verbose)

    result = _tensor_to_img(output.float())

    if scale == 2:
        target_w = image.size[0] * 2
        target_h = image.size[1] * 2
        result = result.resize((target_w, target_h), Image.LANCZOS)
    elif scale != 4:
        target_w = image.size[0] * scale
        target_h = image.size[1] * scale
        result = result.resize((target_w, target_h), Image.LANCZOS)

    if verbose:
        _logger.info(f"Output: {result.size[0]}x{result.size[1]}")

    del model, img_tensor, output
    if device != "cpu":
        torch.cuda.empty_cache()

    return result


def _tiled_inference(
    model: torch.nn.Module,
    img: torch.Tensor,
    tile_size: int,
    tile_pad: int,
    scale: int,
    verbose: bool,
) -> torch.Tensor:
    """Process image in overlapping tiles to fit in VRAM."""
    _, c, h, w = img.shape
    out_h, out_w = h * scale, w * scale
    output = torch.zeros((1, c, out_h, out_w), dtype=img.dtype, device=img.device)
    weights = torch.zeros_like(output)

    tiles_x = math.ceil(w / tile_size)
    tiles_y = math.ceil(h / tile_size)
    total = tiles_x * tiles_y

    for yi in range(tiles_y):
        for xi in range(tiles_x):
            idx = yi * tiles_x + xi + 1
            if verbose and idx % 4 == 1:
                _logger.dim(f"tile {idx}/{total}")

            x_start = xi * tile_size
            y_start = yi * tile_size
            x_end = min(x_start + tile_size, w)
            y_end = min(y_start + tile_size, h)

            x_start_pad = max(x_start - tile_pad, 0)
            y_start_pad = max(y_start - tile_pad, 0)
            x_end_pad = min(x_end + tile_pad, w)
            y_end_pad = min(y_end + tile_pad, h)

            tile_input = img[:, :, y_start_pad:y_end_pad, x_start_pad:x_end_pad]

            with torch.no_grad():
                tile_output = model(tile_input)

            out_x_start = (x_start - x_start_pad) * scale
            out_y_start = (y_start - y_start_pad) * scale
            out_x_end = out_x_start + (x_end - x_start) * scale
            out_y_end = out_y_start + (y_end - y_start) * scale

            ox = x_start * scale
            oy = y_start * scale
            ow = (x_end - x_start) * scale
            oh = (y_end - y_start) * scale

            output[:, :, oy : oy + oh, ox : ox + ow] += tile_output[:, :, out_y_start:out_y_end, out_x_start:out_x_end]
            weights[:, :, oy : oy + oh, ox : ox + ow] += 1.0

    return output / weights.clamp(min=1.0)


def upscale_trimesh_texture(
    mesh: trimesh.Trimesh,
    *,
    scale: int = 4,
    tile_size: int = 512,
    device: str | None = None,
    half: bool = True,
    verbose: bool = False,
) -> trimesh.Trimesh:
    """
    Upscale the baseColor texture of a textured trimesh in-place.
    Returns the same mesh with higher-resolution texture.
    """
    import trimesh

    if not isinstance(mesh, trimesh.Trimesh):
        raise TypeError(f"Expected Trimesh, got {type(mesh)}")

    vis = mesh.visual
    if not hasattr(vis, "material"):
        if verbose:
            _logger.dim("Mesh sem material — nada a fazer.")
        return mesh

    mat = vis.material
    texture = None

    if hasattr(mat, "baseColorTexture") and mat.baseColorTexture is not None:
        texture = mat.baseColorTexture
    elif hasattr(mat, "image") and mat.image is not None:
        texture = mat.image

    if texture is None:
        if verbose:
            _logger.dim("Mesh sem textura baseColor — nada a fazer.")
        return mesh

    if verbose:
        _logger.info(f"Textura original: {texture.size[0]}x{texture.size[1]}")

    upscaled = upscale_image(
        texture,
        scale=scale,
        tile_size=tile_size,
        device=device,
        half=half,
        verbose=verbose,
    )

    if hasattr(mat, "baseColorTexture"):
        mat.baseColorTexture = upscaled
    elif hasattr(mat, "image"):
        mat.image = upscaled

    if verbose:
        _logger.info(f"Textura upscaled: {upscaled.size[0]}x{upscaled.size[1]}")

    return mesh
