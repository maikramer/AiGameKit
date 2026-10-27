"""Tileset de terreno com autotile (16 tiles de cantos, Tiled/Godot).

Pedir ao modelo um tileset inteiro dá grelhas tortas e transições que não
encaixam. Aqui o modelo só desenha **duas texturas** (terreno base e terreno
de cima: relva e terra, areia e água...) e o resto é determinístico:

1. Cada textura gerada a 512² fica **repetível** (:func:`text2d.kit2d.make_tileable`)
   e desce por área ao tamanho do tile — o tile base encaixa consigo próprio
   em todas as direções.
2. Paleta comum às duas texturas (as transições não trazem cores novas).
3. **16 tiles de transição** pelos 4 cantos (bit ``tl=1, tr=2, bl=4, br=8``;
   1 = terreno de cima): campo bilinear dos cantos + ruído periódico no
   tamanho do tile, com limiar 0.5. O campo numa aresta só depende dos dois
   cantos dessa aresta e o ruído repete-se a cada tile, por isso tiles
   vizinhos com cantos iguais encaixam sem costura. Uma sombra de 1 px no
   terreno base contorna o de cima (leitura do relevo).
4. Entregas: ``tileset.png`` (grelha 4x4, índice = máscara dos cantos),
   ``tileset.tsx`` (Tiled, wangset de cantos — o terrain "Match Corners" do
   Godot 4 importa o mesmo layout), ``tileset.json`` e ``preview.png`` (mapa
   aleatório montado com os tiles).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image

from .kit2d import DEFAULT_STYLE, make_tileable, preview, quantize_shared, resolve_render, slug, write_json
from .sprite import RenderFn

TEXTURE_SIZE = 512
CORNER_BITS = {"tl": 1, "tr": 2, "bl": 4, "br": 8}


def texture_prompt(material: str, *, style: str = DEFAULT_STYLE) -> str:
    return (
        f"top-down view of {material.strip().rstrip('.')} ground, seamless tileable game terrain texture, "
        "bold chunky pattern with large clearly visible details (tufts, pebbles, cracks, ripples), "
        "uniform even coverage across the whole frame, flat lighting, no objects, no border, "
        f"no perspective, {style}"
    )


def boost_detail(image: Image.Image, *, target_std: float = 16.0, max_gain: float = 4.0) -> Image.Image:
    """Repõe o contraste local que o downscale por área apagou.

    A 512² o modelo desenha grão fino; descer 12x ao tile faz a média e o tile
    fica quase liso (relva = verde chapado). Estica o desvio de cada pixel à
    cor média até a luminância ter ``target_std`` (sem passar ``max_gain``).
    """
    import numpy as np

    arr = np.asarray(image.convert("RGB")).astype(np.float32)
    mean = arr.reshape(-1, 3).mean(axis=0)
    std = float((arr @ np.array([0.299, 0.587, 0.114], np.float32)).std())
    gain = min(max_gain, target_std / max(std, 1e-3))
    if gain <= 1:
        return image.convert("RGB")
    return Image.fromarray(np.clip(mean + (arr - mean) * gain + 0.5, 0, 255).astype(np.uint8), "RGB")


def _periodic_noise(size: int, seed: int, *, octaves: int = 3) -> Any:
    """Ruído de valor suave, periódico em ``size`` nos dois eixos (média 0, ~[-1, 1])."""
    import numpy as np

    rng = np.random.default_rng(seed)
    out = np.zeros((size, size), np.float32)
    amp, total = 1.0, 0.0
    for o in range(octaves):
        cells = 2 ** (o + 1)
        grid = rng.uniform(-1, 1, (cells, cells)).astype(np.float32)
        u = np.arange(size, dtype=np.float32) * cells / size
        i0 = np.floor(u).astype(int)
        f = u - i0
        f = f * f * (3 - 2 * f)
        i1 = (i0 + 1) % cells
        i0 %= cells
        rows = grid[i0] * (1 - f)[:, None] + grid[i1] * f[:, None]
        out += amp * (rows[:, i0] * (1 - f)[None, :] + rows[:, i1] * f[None, :])
        total += amp
        amp *= 0.5
    return out / total


def corner_mask(corners: int, tile: int, noise: Any, *, roughness: float = 0.36, pad: int = 0) -> Any:
    """Máscara (bool, ``tile+2*pad``²) do terreno de cima para a máscara de cantos.

    ``pad`` estende o campo para fora do tile (o ruído é periódico, o campo
    continua) — serve para a sombra ver o terreno do vizinho.
    """
    import numpy as np

    v = {k: float(bool(corners & b)) for k, b in CORNER_BITS.items()}
    n = tile + 2 * pad
    u = np.clip((np.arange(n, dtype=np.float32) - pad + 0.5) / tile, 0, 1)
    s = u * u * (3 - 2 * u)
    top = v["tl"] * (1 - s) + v["tr"] * s
    bot = v["bl"] * (1 - s) + v["br"] * s
    field = top[None, :] * (1 - s)[:, None] + bot[None, :] * s[:, None]
    idx = (np.arange(n) - pad) % tile
    field = field + roughness * noise[np.ix_(idx, idx)]
    return field > 0.5


def build_tiles(
    base: Image.Image, top: Image.Image, *, tile: int, seed: int = 0, shadow: float = 0.7
) -> list[Image.Image]:
    """Os 16 tiles (índice = máscara de cantos) a partir dos dois tiles base."""
    import numpy as np
    from scipy import ndimage

    noise = _periodic_noise(tile, seed)
    a = np.asarray(base.convert("RGB")).astype(np.float32)
    b = np.asarray(top.convert("RGB"))
    tiles = []
    for idx in range(16):
        m = corner_mask(idx, tile, noise, pad=1)
        inner = m[1:-1, 1:-1]
        edge = ndimage.binary_dilation(m, structure=np.ones((3, 3), bool))[1:-1, 1:-1] & ~inner
        shaded = np.where(edge[..., None], a * shadow, a)
        out = np.where(inner[..., None], b, shaded)
        tiles.append(Image.fromarray(out.astype(np.uint8), "RGB").convert("RGBA"))
    return tiles


def _tsx(name: str, tile: int, terrains: tuple[str, str], colors: tuple[str, str]) -> str:
    rows = []
    for idx in range(16):
        c = {k: 2 if idx & b else 1 for k, b in CORNER_BITS.items()}
        # Ordem Tiled: top, top-right, right, bottom-right, bottom, bottom-left, left, top-left.
        rows.append(f'   <wangtile tileid="{idx}" wangid="0,{c["tr"]},0,{c["br"]},0,{c["bl"]},0,{c["tl"]}"/>')
    body = "\n".join(rows)
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<tileset version="1.10" name="{name}" tilewidth="{tile}" tileheight="{tile}" tilecount="16" columns="4">\n'
        f' <image source="tileset.png" width="{tile * 4}" height="{tile * 4}"/>\n'
        " <wangsets>\n"
        f'  <wangset name="{name}" type="corner" tile="0">\n'
        f'   <wangcolor name="{terrains[0]}" color="{colors[0]}" tile="0" probability="1"/>\n'
        f'   <wangcolor name="{terrains[1]}" color="{colors[1]}" tile="15" probability="1"/>\n'
        f"{body}\n"
        "  </wangset>\n"
        " </wangsets>\n"
        "</tileset>\n"
    )


def preview_map(tiles: list[Image.Image], *, cols: int = 16, rows: int = 10, seed: int = 0) -> Image.Image:
    """Mapa aleatório em manchas (cantos por ruído suavizado) montado com os tiles."""
    import numpy as np
    from scipy import ndimage

    rng = np.random.default_rng(seed)
    field = ndimage.gaussian_filter(rng.random((rows + 1, cols + 1)), 1.2)
    corners = field > np.median(field)
    t = tiles[0].width
    out = Image.new("RGBA", (cols * t, rows * t))
    for y in range(rows):
        for x in range(cols):
            idx = (
                int(corners[y, x]) * 1
                + int(corners[y, x + 1]) * 2
                + int(corners[y + 1, x]) * 4
                + int(corners[y + 1, x + 1]) * 8
            )
            out.paste(tiles[idx], (x * t, y * t))
    return out


def generate_tileset(
    base: str,
    top: str,
    *,
    out_dir: str | Path,
    tile: int = 32,
    style: str = DEFAULT_STYLE,
    colors: int = 24,
    steps: int = 12,
    seed: int = 100,
    base_texture: str | Path | None = None,
    top_texture: str | Path | None = None,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera o tileset de autotile ``base`` → ``top``.

    Args:
        base / top: Terrenos (ex. ``"lush grass"`` e ``"dirt path"``).
        out_dir: Directório de saída.
        tile: Lado do tile em px (divisor de 384: 16, 24, 32, 48, 64...).
        style: Estilo comum às duas texturas.
        colors: Cores da paleta comum.
        base_texture / top_texture: Texturas já existentes (salta a geração;
            reutilizar a mesma base entre tilesets mantém o terreno comum).

    Returns:
        Dict com ``tileset`` (png), ``tsx``, ``json``, ``preview``, ``tiles``.
    """
    usable = int(TEXTURE_SIZE * 0.75)
    if tile < 8 or usable % tile:
        raise ValueError(f"tile tem de dividir {usable} (ex. 16, 24, 32, 48, 64)")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rendered: RenderFn | None = None
    textures = []
    for i, (material, given) in enumerate(((base, base_texture), (top, top_texture))):
        if given is not None:
            path = Path(given)
            if not path.is_file():
                raise FileNotFoundError(f"textura não encontrada: {path}")
        else:
            rendered = rendered or resolve_render(render, generator, steps)
            path = out / f"texture_{i}_{slug(material)}.png"
            t0 = time.time()
            rendered(texture_prompt(material, style=style), path, seed + i, [], TEXTURE_SIZE, TEXTURE_SIZE)
            print(f"[tileset] textura {material!r}: {time.time() - t0:.1f}s → {path}", flush=True)
        img = Image.open(path).convert("RGB").resize((TEXTURE_SIZE, TEXTURE_SIZE), Image.Resampling.LANCZOS)
        small = make_tileable(img).resize((tile, tile), Image.Resampling.BOX)
        textures.append(boost_detail(small).convert("RGBA"))
    base_tile, top_tile = quantize_shared(textures, colors)
    tiles = build_tiles(base_tile, top_tile, tile=tile, seed=seed)

    sheet = Image.new("RGBA", (tile * 4, tile * 4))
    for idx, t in enumerate(tiles):
        sheet.paste(t, ((idx % 4) * tile, (idx // 4) * tile))
    sheet_path = out / "tileset.png"
    sheet.save(sheet_path)
    name = f"{slug(base)}_{slug(top)}"
    hexes = tuple(
        "#{:02x}{:02x}{:02x}".format(*t.convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0)))
        for t in (base_tile, top_tile)
    )
    tsx_path = out / "tileset.tsx"
    tsx_path.write_text(_tsx(name, tile, (base, top), hexes), encoding="utf-8")  # type: ignore[arg-type]
    meta = {
        "version": 1,
        "kind": "tileset",
        "layout": "corner-16",
        "tile_size": [tile, tile],
        "columns": 4,
        "image": "tileset.png",
        "terrains": [{"name": base, "id": 0, "color": hexes[0]}, {"name": top, "id": 1, "color": hexes[1]}],
        "corner_bits": CORNER_BITS,
        "tiles": [
            {
                "index": idx,
                "x": (idx % 4) * tile,
                "y": (idx // 4) * tile,
                "corners": {k: int(bool(idx & b)) for k, b in CORNER_BITS.items()},
            }
            for idx in range(16)
        ],
        "seed": seed,
    }
    json_path = write_json(out / "tileset.json", meta)
    prev_path = out / "preview.png"
    preview(preview_map(tiles, seed=seed), scale=2).save(prev_path)
    print(f"[tileset] {sheet_path} | tiled: {tsx_path} | preview: {prev_path}", flush=True)
    return {"tileset": sheet_path, "tsx": tsx_path, "json": json_path, "preview": prev_path, "tiles": tiles}
