"""Fundo em camadas para parallax (céu, longe, meio, perto), repetível em x.

Receita:

1. Cada camada é uma geração larga (1280x512). O céu é opaco; as outras
   pedem-se **assentes na borda de baixo, sobre magenta liso** — magenta não
   aparece em cenários e separa-se de céu claro, neve ou nevoeiro, onde um
   fundo branco se confundia com o conteúdo.
2. Recorte pelo magenta medido na linha de cima (o conteúdo toca a borda de
   baixo e as laterais, por isso a mediana da borda toda já não é fundo),
   sem bolsas de céu entre folhas/ramos e sem franja rosada.
3. Repetível em x: a faixa final funde-se com a inicial (:func:`make_tileable`,
   alpha pré-multiplicado) — a camada encaixa consigo própria ao rolar.
4. Downscale por área à altura final, paleta comum a todas as camadas.
5. ``parallax.json`` (ordem, fator de scroll, ficheiro) e ``preview.gif`` com
   as camadas a rolar a velocidades diferentes.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from PIL import Image

from .kit2d import DEFAULT_STYLE, MAGENTA, make_tileable, quantize_shared, resolve_render, write_json
from .sprite import RenderFn, chroma_key

GEN_SIZE = (1280, 512)

# name → (o que desenhar, fator de scroll). Fator 0 = parado, 1 = acompanha a câmara.
LAYERS: dict[str, tuple[str, float]] = {
    "sky": (
        "an empty sky filling the entire image: color gradient, clouds, sun or moon only; "
        "no trees, no mountains, no buildings, no horizon line, no ground",
        0.05,
    ),
    "far": ("only the most distant silhouettes of the scene (mountains, hills or skyline), hazy, low contrast", 0.2),
    "mid": ("only the midground scenery of the scene (trees, rocks, buildings), medium detail", 0.5),
    "near": ("only the closest foreground elements of the scene (grass, bushes, rocks, roots), dark, detailed", 0.85),
}
DEFAULT_LAYERS = ("sky", "far", "mid", "near")


def layer_prompt(scene: str, layer: str, *, style: str = DEFAULT_STYLE) -> str:
    what, _ = LAYERS[layer]
    scene = scene.strip().rstrip(".")
    if layer == "sky":
        # A cena entra só como clima/cor: com ela por extenso ("haunted forest")
        # o modelo desenhava as árvores no céu.
        return f"wide panoramic side-scrolling game background layer, {what}, sky mood and colors of: {scene}, {style}"
    return (
        f"wide panoramic side-scrolling game background layer, {scene}, {what}, the shapes rise from the bottom "
        "edge and span the full width, isolated on a flat solid pure magenta (#ff00ff) background, "
        f"no sky, no gradient behind them, {style}"
    )


def key_layer(image: Image.Image) -> Image.Image:
    """Recorta o magenta (cor medida na linha de cima) e tira o tom rosado das bordas."""
    import numpy as np

    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    key = tuple(int(v) for v in np.median(rgb[0], axis=0))
    if abs(key[0] - MAGENTA[0]) + abs(key[1] - MAGENTA[1]) + abs(key[2] - MAGENTA[2]) > 180:
        key = MAGENTA
    keyed = chroma_key(image, key=key, tolerance=48, fringe=110, peel=2, pockets=True)
    assert keyed is not None
    arr = np.asarray(keyed).copy()
    # Despill: o verde não pode ficar muito abaixo do mínimo de vermelho e azul.
    r, g, b = (arr[..., i].astype(np.int16) for i in range(3))
    cap = np.minimum(r, b)
    spill = (cap - g) > 60
    arr[..., 0] = np.where(spill, np.minimum(r, g + 60), r)
    arr[..., 2] = np.where(spill, np.minimum(b, g + 60), b)
    return Image.fromarray(arr, "RGBA")


def compose(layers: list[Image.Image], offsets: list[int]) -> Image.Image:
    """Camadas de trás para a frente, cada uma rolada ``offset`` px (repetição em x)."""
    w, h = layers[0].size
    out = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    for img, off in zip(layers, offsets, strict=True):
        off %= img.width
        strip = Image.new("RGBA", (w, h))
        x = -off
        while x < w:
            strip.paste(img, (x, h - img.height))
            x += img.width
        out.alpha_composite(strip)
    return out


def generate_parallax(
    scene: str,
    *,
    out_dir: str | Path,
    layers: tuple[str, ...] | list[str] = DEFAULT_LAYERS,
    height: int = 256,
    style: str = DEFAULT_STYLE,
    colors: int = 48,
    steps: int = 12,
    seed: int = 100,
    gif_frames: int = 48,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera as camadas de parallax de ``scene``.

    Args:
        scene: Cenário (ex. ``"haunted forest at dusk"``).
        layers: Subconjunto ordenado de :data:`LAYERS` (de trás para a frente).
        height: Altura final de cada camada em px (largura = 2x).
        colors: Cores da paleta comum (0 = sem quantizar).
        gif_frames: Frames do ``preview.gif`` (0 = sem GIF).

    Returns:
        Dict com ``layers`` (paths), ``json``, ``preview`` e ``gif``.
    """
    bad = [n for n in layers if n not in LAYERS]
    if bad or not layers:
        raise ValueError(f"camadas inválidas: {', '.join(bad) or '(vazio)'} (válidas: {', '.join(LAYERS)})")
    if height < 32:
        raise ValueError("height tem de ser >= 32")
    out = Path(out_dir)
    raw_dir = out / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    rendered = resolve_render(render, generator, steps)
    gw, gh = GEN_SIZE
    width = height * 2
    imgs = []
    for i, name in enumerate(layers):
        raw = raw_dir / f"{i}_{name}.png"
        t0 = time.time()
        rendered(layer_prompt(scene, name, style=style), raw, seed + i, [], gw, gh)
        print(f"[parallax] camada {name}: {time.time() - t0:.1f}s", flush=True)
        img = Image.open(raw).convert("RGB").resize(GEN_SIZE, Image.Resampling.LANCZOS)
        img = img.convert("RGBA") if name == "sky" else key_layer(img)
        img = make_tileable(img, band=0.2, axes="x")
        imgs.append(img.resize((width, height), Image.Resampling.BOX))
    imgs = quantize_shared(imgs, colors) if colors else imgs

    entries, paths = [], []
    for z, (name, img) in enumerate(zip(layers, imgs, strict=True)):
        path = out / f"{z}_{name}.png"
        img.save(path)
        paths.append(path)
        entries.append({"name": name, "file": path.name, "z": z, "scroll": LAYERS[name][1], "opaque": name == "sky"})
    meta = {
        "version": 1,
        "kind": "parallax",
        "scene": scene,
        "size": [width, height],
        "repeat": "x",
        "layers": entries,
        "seed": seed,
    }
    json_path = write_json(out / "parallax.json", meta)
    preview_path = out / "preview.png"
    compose(imgs, [0] * len(imgs)).save(preview_path)
    gif_path = None
    if gif_frames:
        frames = []
        for f in range(gif_frames):
            # A camada mais rápida dá uma volta inteira (loop sem salto); as lentas
            # andam em proporção (saltam no loop — é só pré-visualização).
            t = f / gif_frames
            offsets = [round(t * width * LAYERS[n][1] / max(LAYERS[x][1] for x in layers)) for n in layers]
            frames.append(compose(imgs, offsets).convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE))
        gif_path = out / "preview.gif"
        frames[0].save(gif_path, save_all=True, append_images=frames[1:], duration=60, loop=0)
    print(f"[parallax] {len(paths)} camadas → {json_path}", flush=True)
    return {"layers": paths, "json": json_path, "preview": preview_path, "gif": gif_path}
