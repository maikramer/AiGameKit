"""Animações de efeitos (VFX): explosão, fogo, fumo, magia, corte, raio...

Mesma ideia das animações de personagem: todos os frames numa só geração
(grelha 4x2, células quadradas), guiada por texto por frame **e** por uma
folha-guia desenhada (:func:`draw_guide`) com a forma/tamanho/brilho de cada
fase — sem ela o modelo desenhava 8 explosões quase iguais.

O fundo pedido é **preto**: o efeito é luz, e o alpha sai da luminância
(``a = max(r, g, b)`` acima do preto do fundo, cor dividida por ``a``) — o
fumo escuro fica semitransparente e o núcleo branco opaco, como um sprite
de partículas. Sem recorte por flood fill (brilhos não têm contorno).
Entregas: atlas, preview, GIF e ``anim.json`` (pivô ao centro, ``blend``
sugerido: ``additive`` para luz, ``alpha`` para fumo).
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFilter

from .kit2d import DEFAULT_STYLE, quantize_shared, resolve_render, split_grid, write_json
from .sprite import RenderFn, _write_deliverables

CELL = 256
COLS = 4


@dataclass(frozen=True)
class Effect:
    """Efeito: fases por extenso, forma-guia e metadados de reprodução."""

    subject: str
    phases: tuple[str, ...]
    shape: str  # burst | flame | puff | arc | bolt | ring | rise | star
    loop: bool = False
    blend: str = "additive"
    holds: tuple[float, ...] = ()


EFFECTS: dict[str, Effect] = {
    "explosion": Effect(
        "fiery explosion",
        (
            "tiny bright white-yellow flash",
            "small fireball bursting outward",
            "large orange fireball at full size",
            "huge fireball with dark edges breaking apart",
            "fire turning into black smoke clouds",
            "smoke clouds expanding with embers",
            "thin grey smoke dissipating",
            "faint wisps of smoke almost gone",
        ),
        "burst",
        blend="additive",
    ),
    "fire": Effect(
        "burning campfire flame",
        (
            "flame leaning left",
            "flame tall and straight",
            "flame leaning right",
            "flame short and wide",
            "flame leaning left with a spark",
            "flame tall and thin",
            "flame leaning right with a spark",
            "flame short and flickering",
        ),
        "flame",
        loop=True,
    ),
    "smoke": Effect(
        "grey smoke puff",
        (
            "small dense smoke puff",
            "smoke puff growing",
            "round smoke cloud",
            "large smoke cloud rising",
            "smoke cloud spreading and thinning",
            "loose smoke drifting upward",
            "thin transparent smoke",
            "last faint wisps",
        ),
        "puff",
        blend="alpha",
    ),
    "magic": Effect(
        "magic spell burst of glowing blue and purple energy",
        (
            "small glowing orb",
            "orb pulsing with sparkles",
            "energy ring expanding",
            "large ring of runes and sparkles",
            "ring at full size with bright stars",
            "ring fading, sparkles flying out",
            "scattered fading sparkles",
            "last tiny glints",
        ),
        "ring",
    ),
    "slash": Effect(
        "white sword slash trail",
        (
            "thin start of a curved slash at the top",
            "slash arc growing downward",
            "bright full crescent slash arc",
            "slash arc thinning at the tail",
            "fading crescent trail",
            "faint thin arc",
            "last fragments of the trail",
            "almost invisible glint",
        ),
        "arc",
        holds=(0.6, 0.6, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    ),
    "lightning": Effect(
        "electric lightning bolt",
        (
            "lightning bolt striking down, zigzag",
            "branching lightning bolt, brightest",
            "lightning bolt with sparks at the bottom",
            "lightning bolt flickering thinner",
            "different zigzag lightning bolt",
            "branching bolt with crackling sparks",
            "thin fading bolt",
            "electric sparks fading",
        ),
        "bolt",
        loop=True,
    ),
    "heal": Effect(
        "green healing light with rising sparkles and plus signs",
        (
            "soft green glow at the bottom",
            "sparkles starting to rise",
            "column of green light with sparkles",
            "bright sparkles and plus signs rising",
            "sparkles high up, glow fading",
            "few sparkles at the top",
            "last faint sparkles",
            "almost gone",
        ),
        "rise",
    ),
    "impact": Effect(
        "hit impact spark",
        (
            "bright star-shaped impact flash",
            "impact star with spikes at full size",
            "spikes breaking into sparks",
            "sparks flying outward",
            "small sparks scattered",
            "few fading sparks",
            "tiny glints",
            "nothing but a faint dot",
        ),
        "star",
        holds=(0.6, 0.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
    ),
}


def get_effect(name: str) -> Effect:
    try:
        return EFFECTS[name]
    except KeyError:
        raise ValueError(f"efeito desconhecido: {name} (válidos: {', '.join(EFFECTS)})") from None


def _phase(eff: Effect, i: int, n: int) -> str:
    return eff.phases[min(len(eff.phases) - 1, i * len(eff.phases) // n)]


def sheet_prompt(eff: Effect, frames: int, *, subject: str | None = None, style: str = DEFAULT_STYLE) -> str:
    rows = math.ceil(frames / COLS)
    cells = "; ".join(f"frame {i + 1}: {_phase(eff, i, frames)}" for i in range(frames))
    return (
        f"game VFX sprite sheet, {frames} animation frames of a {subject or eff.subject} in a "
        f"{min(frames, COLS)}x{rows} grid, each frame centered in its own square cell, same scale, "
        f"read left to right, top to bottom: {cells}. Pure black background, glowing light, "
        f"no text, no grid lines, {style}"
    )


def _guide_cell(eff: Effect, i: int, n: int, cell: int) -> Image.Image:
    """Forma-guia de uma fase (cinzento = brilho/tamanho, sobre preto)."""
    img = Image.new("L", (cell, cell), 0)
    d = ImageDraw.Draw(img)
    t = i / max(n - 1, 1)
    c = cell / 2
    life = 1 - t
    if eff.shape in {"burst", "puff", "ring"}:
        grow = math.sin(min(1.0, t * 1.6) * math.pi / 2)
        r = cell * (0.08 + 0.34 * grow)
        val = int(255 * (0.35 + 0.65 * life))
        cy = c - (cell * 0.12 * t if eff.shape == "puff" else 0)
        if eff.shape == "ring":
            d.ellipse((c - r, cy - r, c + r, cy + r), outline=val, width=max(3, int(cell * 0.05 * life) + 2))
        else:
            d.ellipse((c - r, cy - r, c + r, cy + r), fill=val)
    elif eff.shape == "flame":
        sway = math.sin(i / n * 2 * math.pi) * cell * 0.08
        hgt = cell * (0.55 + 0.1 * math.cos(i / n * 4 * math.pi))
        base = cell * 0.85
        d.polygon(
            [(c - cell * 0.18, base), (c + sway, base - hgt), (c + cell * 0.18, base)],
            fill=230,
        )
        d.ellipse((c - cell * 0.18, base - cell * 0.18, c + cell * 0.18, base + cell * 0.05), fill=230)
    elif eff.shape == "arc":
        sweep = 40 + 200 * min(1.0, t * 2.5)
        w = max(2, int(cell * 0.09 * life) + 1)
        d.arc((cell * 0.12, cell * 0.12, cell * 0.88, cell * 0.88), -120, -120 + sweep, fill=int(255 * life), width=w)
    elif eff.shape == "bolt":
        import random

        rnd = random.Random(i)
        x, y, pts = c, cell * 0.05, []
        while y < cell * 0.92:
            pts.append((x, y))
            y += cell * 0.12
            x = c + rnd.uniform(-0.2, 0.2) * cell
        d.line(pts, fill=int(255 * (0.5 + 0.5 * life)), width=max(2, int(cell * 0.03)))
    elif eff.shape == "rise":
        import random

        rnd = random.Random(7)
        for _ in range(9):
            x0, y0 = rnd.uniform(0.25, 0.75) * cell, rnd.uniform(0.55, 0.95) * cell
            y = y0 - t * cell * 0.5
            r = cell * 0.03
            if y > 0:
                d.ellipse((x0 - r, y - r, x0 + r, y + r), fill=int(255 * life))
        d.rectangle(
            (c - cell * 0.15, cell * 0.92 - cell * 0.5 * (1 - t), c + cell * 0.15, cell * 0.92), fill=int(90 * life)
        )
    elif eff.shape == "star":
        r = cell * (0.18 + 0.25 * min(1.0, t * 2))
        spikes = []
        for k in range(16):
            ang = k * math.pi / 8
            rr = r if k % 2 == 0 else r * 0.35
            spikes.append((c + rr * math.cos(ang), c + rr * math.sin(ang)))
        d.polygon(spikes, fill=int(255 * life))
    return img.filter(ImageFilter.GaussianBlur(cell * 0.02))


def draw_guide(eff: Effect, frames: int, *, cell: int = CELL) -> Image.Image:
    rows = math.ceil(frames / COLS)
    sheet = Image.new("RGB", (min(frames, COLS) * cell, rows * cell), (0, 0, 0))
    for i in range(frames):
        sheet.paste(_guide_cell(eff, i, frames, cell).convert("RGB"), ((i % COLS) * cell, (i // COLS) * cell))
    return sheet


def light_to_alpha(cell: Image.Image, *, floor: float = 0.06) -> Image.Image:
    """Fundo preto → alpha pela luminância (``a = max(rgb)`` acima do preto do fundo)."""
    import numpy as np

    rgb = np.asarray(cell.convert("RGB")).astype(np.float32)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    bg = float(np.percentile(border.max(axis=1), 50))
    peak = rgb.max(axis=2)
    a = np.clip((peak - bg) / max(255.0 - bg, 1.0), 0, 1)
    a = np.where(a < floor, 0, a)
    color = np.where(a[..., None] > 0, np.clip((rgb - bg) / np.maximum(a[..., None], 1e-3), 0, 255), 0)
    out = np.dstack([color, a * 255]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def generate_vfx(
    effect: str,
    *,
    out_dir: str | Path,
    frames: int = 8,
    subject: str | None = None,
    style: str = DEFAULT_STYLE,
    sprite_size: int | None = 64,
    colors: int = 24,
    alpha_levels: int = 4,
    frame_ms: int = 70,
    steps: int = 12,
    seed: int = 100,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera a animação de efeito ``effect``.

    Args:
        effect: Chave de :data:`EFFECTS`.
        frames: 2-8 (uma geração, grelha 4 colunas).
        subject: Substitui o assunto do efeito (ex. ``"green acid explosion"``)
            mantendo fases e guia.
        sprite_size: Lado do sprite final (None = célula inteira).
        alpha_levels: Níveis de alpha (2 = binário, 0 = suave).
        frame_ms: Duração base de cada frame (x ``holds``).

    Returns:
        Dict com ``sprites``, ``atlas``, ``sheet``, ``gif``, ``meta``, ``prompt``.
    """
    eff = get_effect(effect)
    if not 2 <= frames <= 8:
        raise ValueError("frames tem de estar entre 2 e 8")
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rendered = resolve_render(render, generator, steps)
    guide = draw_guide(eff, frames)
    guide_path = out / "00_guide.png"
    guide.save(guide_path)
    prompt = sheet_prompt(eff, frames, subject=subject, style=style)
    raw = out / "01_sheet_raw.png"
    t0 = time.time()
    rendered(prompt, raw, seed, [guide_path], guide.width, guide.height)
    print(f"[vfx] folha {effect}: {time.time() - t0:.1f}s", flush=True)
    sheet = Image.open(raw).convert("RGB").resize(guide.size, Image.Resampling.LANCZOS)
    cells = split_grid(sheet, min(frames, COLS), math.ceil(frames / COLS))[:frames]
    cut = [light_to_alpha(c) for c in cells]
    if sprite_size:
        cut = [c.resize((sprite_size, sprite_size), Image.Resampling.BOX) for c in cut]
    sprites = quantize_shared(cut, colors, alpha_levels=alpha_levels)
    holds = eff.holds or (1.0,)
    durations = [max(20, round(frame_ms * holds[min(i, len(holds) - 1)] / 10) * 10) for i in range(frames)]
    frame_dir = out / "frames"
    frame_dir.mkdir(exist_ok=True)
    for i, spr in enumerate(sprites):
        spr.save(frame_dir / f"{i:02d}.png")
    atlas, sheet_prev, gif = _write_deliverables(out, sprites, durations, make_sheet=True, make_gif=True)
    w, h = sprites[0].size
    meta = {
        "version": 1,
        "kind": "vfx",
        "effect": effect,
        "subject": subject or eff.subject,
        "loop": eff.loop,
        "blend": eff.blend,
        "frame_size": [w, h],
        "pivot": [w // 2, h // 2],
        "atlas": "atlas.png",
        "frames": [
            {"index": i, "x": i * w, "y": 0, "duration_ms": d, "file": f"frames/{i:02d}.png"}
            for i, d in enumerate(durations)
        ],
        "total_ms": sum(durations),
        "seed": seed,
    }
    meta_path = write_json(out / "anim.json", meta)
    return {
        "sprites": sprites,
        "atlas": atlas,
        "sheet": sheet_prev,
        "gif": gif,
        "meta": meta_path,
        "prompt": prompt,
        "guide": guide_path,
    }
