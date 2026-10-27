"""Pack de ícones de itens num estilo único (inventário, loja, loot).

Ícones gerados um a um saem com estilos, molduras e escalas diferentes. Aqui
até 8 itens saem **numa só geração** (grelha 4x2, uma célula por item) — o
modelo mantém traço, paleta e luz dentro da mesma imagem. Mais de 8 = várias
folhas, cada uma com a primeira como referência de estilo.

O recorte é da folha inteira (flood fill da borda, sem bolsas/halo — ver
:func:`text2d.sprite.chroma_key`), e cada peça vai para a célula onde está o
seu centro: um item que passe um pouco da célula não é cortado ao meio, e
brilhos soltos ficam com o item. Cada ícone é enquadrado num quadrado com
margem, desce por área ao tamanho final e partilha a paleta do pack.
Entregas: ``icons/<nome>.png``, ``atlas.png``, ``items.json`` (rects) e
``preview.png``.
"""

from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any

from PIL import Image

from .kit2d import DEFAULT_STYLE, preview, quantize_shared, resolve_render, slug, write_json
from .sprite import RenderFn, _remove_background

CELL = 256
COLS = 4
PER_SHEET = 8


def sheet_prompt(names: list[str], *, style: str = DEFAULT_STYLE, theme: str | None = None) -> str:
    rows = math.ceil(len(names) / COLS)
    cells = "; ".join(f"cell {i + 1}: {n}" for i, n in enumerate(names))
    theme_txt = f"{theme.strip().rstrip('.')} theme, " if theme else ""
    return (
        f"game item icon sprite sheet, {len(names)} different inventory items in a {min(len(names), COLS)}x{rows} "
        f"grid, one item centered in each square cell with empty space around it, all items in the same art "
        f"style, same scale and same lighting, {theme_txt}read left to right, top to bottom: {cells}. "
        f"Each item is a single object with a dark outline, plain white background, no text, no grid lines, "
        f"no frames, {style}"
    )


def assign_cells(alpha: Any, cols: int, rows: int, *, min_frac: float = 0.0005) -> list[Any]:
    """Máscara por célula: cada peça do recorte vai para a célula do seu centro."""
    import numpy as np
    from scipy import ndimage

    h, w = alpha.shape
    labels, n = ndimage.label(alpha)
    masks = [np.zeros_like(alpha, bool) for _ in range(cols * rows)]
    if not n:
        return masks
    sizes = np.bincount(labels.ravel())
    for i, com in enumerate(ndimage.center_of_mass(alpha, labels, range(1, n + 1)), 1):
        if sizes[i] < min_frac * h * w:
            continue
        cy, cx = com
        cell = min(int(cy * rows / h), rows - 1) * cols + min(int(cx * cols / w), cols - 1)
        masks[cell] |= labels == i
    return masks


def frame_icon(rgba: Image.Image, mask: Any, *, size: int, margin: float = 0.08) -> Image.Image | None:
    """Recorta a máscara, centra num quadrado com ``margin`` e desce a ``size``."""
    import numpy as np

    ys, xs = np.nonzero(mask)
    if not len(xs):
        return None
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    arr = np.asarray(rgba).copy()
    arr[..., 3] = np.where(mask, arr[..., 3], 0)
    crop = Image.fromarray(arr, "RGBA").crop((int(x0), int(y0), int(x1), int(y1)))
    side = int(max(crop.size) * (1 + 2 * margin))
    canvas = Image.new("RGBA", (side, side))
    canvas.alpha_composite(crop, ((side - crop.width) // 2, (side - crop.height) // 2))
    return canvas.resize((size, size), Image.Resampling.BOX)


def generate_items(
    names: list[str],
    *,
    out_dir: str | Path,
    style: str = DEFAULT_STYLE,
    theme: str | None = None,
    icon_size: int = 32,
    colors: int = 48,
    steps: int = 12,
    seed: int = 100,
    reference: str | Path | None = None,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera um ícone por nome, num estilo comum.

    Args:
        names: Itens (ex. ``["health potion", "iron sword"]``); nomes repetidos
            são recusados (o ficheiro é o slug do nome).
        theme: Tema comum (ex. ``"dark fantasy"``).
        icon_size: Lado do ícone final.
        reference: Imagem de estilo para todas as folhas (sem ela, a 1.ª folha
            serve de referência às seguintes).

    Returns:
        Dict com ``icons`` (nome → path), ``atlas``, ``json``, ``preview``,
        ``missing`` (itens que o modelo não desenhou).
    """
    import numpy as np

    names = [n.strip() for n in names if n.strip()]
    if not names:
        raise ValueError("lista de itens vazia")
    slugs = [slug(n) for n in names]
    dup = sorted({s for s in slugs if slugs.count(s) > 1})
    if dup:
        raise ValueError(f"itens repetidos: {', '.join(dup)}")
    ref = Path(reference) if reference else None
    if ref is not None and not ref.is_file():
        raise FileNotFoundError(f"referência não encontrada: {ref}")
    out = Path(out_dir)
    icon_dir = out / "icons"
    icon_dir.mkdir(parents=True, exist_ok=True)
    rendered = resolve_render(render, generator, steps)

    icons: dict[str, Image.Image] = {}
    missing: list[str] = []
    for s, start in enumerate(range(0, len(names), PER_SHEET)):
        chunk = names[start : start + PER_SHEET]
        cols, rows = min(len(chunk), COLS), math.ceil(len(chunk) / COLS)
        raw = out / f"sheet_{s}_raw.png"
        t0 = time.time()
        prompt = sheet_prompt(chunk, style=style, theme=theme)
        if ref is not None:
            prompt += ", same art style as the reference image"
        rendered(prompt, raw, seed + s, [ref] if ref else [], cols * CELL, rows * CELL)
        print(f"[items] folha {s}: {len(chunk)} itens em {time.time() - t0:.1f}s", flush=True)
        if ref is None:
            ref = raw
        sheet = Image.open(raw).convert("RGB").resize((cols * CELL, rows * CELL), Image.Resampling.LANCZOS)
        cut = _remove_background(sheet)
        masks = assign_cells(np.asarray(cut.getchannel("A")) > 127, cols, rows)
        for name, mask in zip(chunk, masks, strict=False):
            icon = frame_icon(cut, mask, size=icon_size)
            if icon is None:
                missing.append(name)
            else:
                icons[name] = icon
    if not icons:
        raise RuntimeError("o modelo não desenhou nenhum item reconhecível")

    done = [n for n in names if n in icons]
    final = dict(zip(done, quantize_shared([icons[n] for n in done], colors), strict=True))
    cols = min(len(done), 8)
    rows = math.ceil(len(done) / cols)
    atlas = Image.new("RGBA", (cols * icon_size, rows * icon_size))
    entries: dict[str, Any] = {}
    paths: dict[str, Path] = {}
    for i, name in enumerate(done):
        x, y = (i % cols) * icon_size, (i // cols) * icon_size
        atlas.alpha_composite(final[name], (x, y))
        path = icon_dir / f"{slug(name)}.png"
        final[name].save(path)
        paths[name] = path
        entries[slug(name)] = {
            "name": name,
            "x": x,
            "y": y,
            "w": icon_size,
            "h": icon_size,
            "file": f"icons/{path.name}",
        }
    atlas_path = out / "atlas.png"
    atlas.save(atlas_path)
    preview_path = out / "preview.png"
    preview(atlas, scale=4).save(preview_path)
    meta = {
        "version": 1,
        "kind": "items",
        "icon_size": [icon_size, icon_size],
        "atlas": "atlas.png",
        "items": entries,
        "missing": missing,
        "seed": seed,
    }
    json_path = write_json(out / "items.json", meta)
    if missing:
        print(f"[items] sem ícone (célula vazia): {', '.join(missing)}", flush=True)
    print(f"[items] {len(done)} ícones → {json_path}", flush=True)
    return {"icons": paths, "atlas": atlas_path, "json": json_path, "preview": preview_path, "missing": missing}
