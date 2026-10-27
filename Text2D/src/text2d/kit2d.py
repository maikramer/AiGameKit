"""Peças comuns das ferramentas de jogo 2D (tileset, parallax, vfx, items).

Todas seguem a receita do :mod:`text2d.sprite`: uma geração por folha (o
modelo mantém estilo e escala dentro da mesma imagem), recorte determinístico
e pós-processamento de pixel art (downscale por área + paleta comum) — o modelo
só desenha; formato, alinhamento e metadados são código.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from PIL import Image

from .sprite import RenderFn

DEFAULT_STYLE = "pixel art, 16-bit, crisp pixels, flat colors"

# Fundo pedido nas camadas/folhas que tocam a borda: magenta não aparece em
# cenários e separa-se de céu claro, neve ou nevoeiro (o branco não).
MAGENTA = (255, 0, 255)


def resolve_render(render: RenderFn | None, generator: Any, steps: int) -> RenderFn:
    """Callback de render: o dado, ou um que corre o ``generator`` in-process."""
    if render is not None:
        return render
    if generator is None:
        from .generator import KleinFluxGenerator

        generator = KleinFluxGenerator(memory_efficient=True)

    def _render(prompt: str, path: Path, seed: int, refs: list[Path], width: int, height: int) -> None:
        img, _ = generator.generate(
            prompt, width=width, height=height, refs=refs or None, num_inference_steps=steps, seed=seed
        )
        generator.save_image(img, path)

    return _render


def slug(text: str) -> str:
    """Nome de ficheiro a partir de um rótulo (``"Health Potion"`` → ``health_potion``)."""
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "item"


def make_tileable(image: Image.Image, *, band: float = 0.25, axes: str = "xy") -> Image.Image:
    """Torna a imagem repetível cruzando a borda final com a inicial.

    Numa faixa de ``band`` da largura o início da imagem funde-se com o fim; o
    resultado perde essa faixa e a última coluna encosta na primeira sem
    costura. Em RGBA a mistura é pré-multiplicada (sem halo de cor do fundo).
    """
    import numpy as np

    rgba = image.mode == "RGBA"
    arr = np.asarray(image.convert("RGBA" if rgba else "RGB")).astype(np.float32)
    if rgba:
        arr[..., :3] *= arr[..., 3:] / 255.0
    for axis in (1, 0):
        if "xy"[1 - axis] not in axes:
            continue
        n = arr.shape[axis]
        b = int(n * band)
        if b < 1:
            continue
        head = np.take(arr, range(b), axis=axis)
        tail = np.take(arr, range(n - b, n), axis=axis)
        t = np.linspace(0.0, 1.0, b, dtype=np.float32)
        t = t[None, :, None] if axis == 1 else t[:, None, None]
        arr = np.take(arr, range(n - b), axis=axis).copy()
        idx = [slice(None)] * 3
        idx[axis] = slice(0, b)
        arr[tuple(idx)] = tail * (1 - t) + head * t
    if rgba:
        a = arr[..., 3:]
        arr[..., :3] = np.where(a > 0, arr[..., :3] * 255.0 / np.maximum(a, 1e-3), 0)
    return Image.fromarray(np.clip(arr + 0.5, 0, 255).astype(np.uint8), "RGBA" if rgba else "RGB")


def split_grid(image: Image.Image, cols: int, rows: int) -> list[Image.Image]:
    """Células da grelha, linha a linha."""
    w, h = image.size
    cw, ch = w // cols, h // rows
    return [image.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)) for r in range(rows) for c in range(cols)]


def quantize_shared(images: list[Image.Image], colors: int, *, alpha_levels: int = 2) -> list[Image.Image]:
    """Paleta única (pixels visíveis de todas as imagens) + alpha em ``alpha_levels`` níveis.

    ``alpha_levels`` 2 = binário (pixel art clássico); 0 = alpha intacto (VFX
    suaves).
    """
    import numpy as np

    from .sprite import _shared_palette

    palette = _shared_palette(images, colors) if colors else None
    out = []
    for img in images:
        rgba = img.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A")).astype(np.float32)
        if alpha_levels >= 2:
            step = 255.0 / (alpha_levels - 1)
            alpha = np.round(alpha / step) * step
        rgb = rgba.convert("RGB")
        if palette is not None:
            rgb = rgb.quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")
        res = rgb.convert("RGBA")
        res.putalpha(Image.fromarray(np.clip(alpha, 0, 255).astype(np.uint8), "L"))
        out.append(res)
    return out


def preview(image: Image.Image, *, scale: int = 4, bg: tuple[int, int, int] = (42, 44, 54)) -> Image.Image:
    """Ampliação nearest sobre fundo escuro (pré-visualização de pixel art)."""
    canvas = Image.new("RGBA", image.size, (*bg, 255))
    canvas.alpha_composite(image.convert("RGBA"))
    return canvas.resize((image.width * scale, image.height * scale), Image.Resampling.NEAREST)


def write_json(path: Path, data: dict[str, Any]) -> Path:
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
