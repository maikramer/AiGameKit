"""Geração de sprite sheets 2D — ciclos de caminhada/corrida por direção.

O FLUX.3 não tem pose conditioning; a receita (validada E2E na 4050, ver
``docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md``) é:

1. **Identidade** — um frame da personagem em ``idle`` a 512² (ou ``--ref``).
2. **Folha numa só geração** — todos os frames saem numa imagem só
   (``n x 256 x 320``) com duas referências: a identidade e uma **folha de
   silhuetas** (:func:`text2d.poses.draw_pose_sheet`) com a pose de cada
   célula. Dentro da mesma imagem o modelo mantém personagem, escala e linha
   do chão; a silhueta impõe a pose. Frames gerados em separado saíam com
   escala/enquadramento diferentes e, com a referência como única guia,
   repetiam a pose da referência.
3. **Pós-processamento de sprite** — recorte por célula (flood fill da borda),
   alinhamento (tronco no
   mesmo x, pés na mesma linha), downscale por média de área (sem o ringing
   do LANCZOS), alpha binário e **uma paleta para o ciclo todo** (paleta por
   frame fazia as cores cintilar no GIF).
4. **Entrega** — atlas 1x, preview 4x, GIF com a duração de cada fase
   (``Animation.holds``) e ``anim.json`` (frames, durações, pivô nos pés,
   loop) para o motor consumir sem adivinhar.

Até :data:`MAX_FRAMES_PER_SHEET` frames saem numa só geração (grelha de
:data:`SHEET_COLS` colunas); acima disso geram-se várias folhas com a mesma
identidade. Se os frames saírem quase iguais (a identidade dominou a pose), a
folha é regenerada com outra seed (``retries``) e fica a de maior variação.
"""

from __future__ import annotations

import itertools
import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .anim import Animation, cell_size, check_direction, draw_guide, get_animation, phase_lift, phase_prompt
from .poses import (
    alpha_difference,
    canonical_direction,
    direction_prompt,
    direction_view,
    facing_prompt,
    pose_prompt,
)

STYLE_SUFFIX = "flat colors, crisp pixels, no anti-aliasing, no text, plain white background"

# Frames por geração: grelha de 4 colunas x até 2 linhas (8 frames bípedes =
# 1024x640). Mais frames numa geração = mais consistência; acima de 8 a imagem
# passa de ~0.65 MP e a 6 GB o tempo cresce sem ganho visível.
MAX_FRAMES_PER_SHEET = 8
SHEET_COLS = 4

# Resolução da imagem de identidade (abaixo de ~512 o FLUX.3 perde detalhe e o
# "pixel art" sai sujo — era a causa dos frames borrados a 256²).
IDENTITY_SIZE = 512

# Callback de render: (prompt, output_path, seed, refs, width, height) -> None
# (escreve o ficheiro). Permite delegar cada geração ao vramd em vez de correr
# in-process; o CLI fornece o callback com fallback in-process.
RenderFn = Callable[[str, Path, int, list[Path], int, int], None]


def sheet_prompt(
    character: str,
    *,
    direction: str,
    poses: tuple[str, ...] | list[str],
    animation: str | Animation = "walk",
    cols: int | None = None,
) -> str:
    """Prompt da folha: N cópias da personagem, cada uma na pose da silhueta.

    A pose de cada célula também vai por extenso (``frame 1: ...``): sozinha, a
    silhueta frontal (diferenças subtis de comprimento das pernas) não chegava
    para o modelo variar a passada.
    """
    anim = get_animation(animation) if isinstance(animation, str) else animation
    n = len(poses)
    view = direction_view(direction)
    frames_text = "; ".join(f"frame {i + 1}: {phase_prompt(anim, p, view=view)}" for i, p in enumerate(poses))
    direction_text = direction_prompt(direction)
    if not anim.name.startswith("walk"):
        # "walking to the right" contradiz salto/ataque/idle; fica só a vista.
        direction_text = facing_prompt(direction)
    # Fases aéreas: a silhueta mostra a altura de cada frame (não "mesmo chão").
    ground = (
        "each copy at the same height as its silhouette"
        if any(phase_lift(anim, p) for p in poses)
        else "feet on the same ground line"
    )
    limbs = "legs" if anim.body == "quadruped" else "limbs"
    cols = cols or n
    rows = (n + cols - 1) // cols
    layout = (
        f"a horizontal sprite sheet of a {n}-frame {anim.label} animation of "
        f"{character.strip().rstrip('.')}, {n} copies of the same character from left to right"
        if rows == 1
        else f"a sprite sheet grid of a {n}-frame {anim.label} animation of {character.strip().rstrip('.')}, "
        f"{n} copies of the same character in {rows} rows of {cols}, read left to right then top to bottom"
    )
    return (
        f"{layout}, each copy "
        f"{direction_text}, posed exactly like the white silhouette at the same position in the "
        f"second image (the grey {limbs} are the far side of the body), the character looks exactly like the "
        f"first image, identical outfit, colors, size and proportions in every frame, full body, {ground}. "
        f"{frames_text}. {STYLE_SUFFIX}"
    )


def identity_prompt(character: str, *, direction: str, body: str = "biped") -> str:
    """Prompt da identidade: corpo inteiro, **parado** (ver :func:`facing_prompt`)."""
    view = direction_view(direction)
    if body == "quadruped":
        from .quadruped import quad_pose_prompt

        stance = quad_pose_prompt("quad_idle")
    else:
        stance = pose_prompt("idle", view=view)
    return f"{character.strip().rstrip('.')}, full body, {facing_prompt(direction)}, {stance}, {STYLE_SUFFIX}"


def chroma_key(
    image: Image.Image,
    *,
    tolerance: float = 12.0,
    max_border_spread: float = 24.0,
    pockets: bool | None = None,
    fringe: float = 72.0,
    peel: int = 2,
) -> Image.Image | None:
    """Recorta o fundo liso pela cor da borda (flood fill a partir das margens).

    A cor do fundo é a mediana dos pixels da borda; só sai a região dessa cor
    **ligada à borda**, mais a franja anti-aliased de 1 px. O contorno escuro
    do pixel art trava o preenchimento, por isso uma personagem branca em
    fundo branco não é furada (o rembg furava: cavalo branco com buracos).
    Tolerância 12: a 24 o preenchimento já vazava pelo contorno de um cavalo
    branco.

    Depois do flood fill há dois filtros contra o "pixel branco" que sobrava:

    - **Bolsas** (entre braço e tronco, entre pernas): componentes fechadas
      da cor do fundo, não minúsculas, até 18% da figura (o vão entre arco e
      corda chega a 13%), e **rodeadas de pixels escuros/saturados** (anel
      com distância mediana > 60). O anel separa o buraco real do brilho de
      um orbe mágico (rodeado de claro). Desligado sozinho quando a figura é
      clara — maior peça cor-de-fundo > 20% da figura (corpo do cavalo branco:
      25%) ou > 40% de pixels claros — aí as manchas brancas são corpo.
    - **Franja**: descasca ``peel`` px de borda clara (distância ao fundo <
      ``fringe``) — o anti-aliasing do modelo deixava um halo branco que o
      downscale virava contorno claro. O contorno escuro do pixel art trava.

    Args:
        pockets: ``None`` = automático (acima); ``True``/``False`` força.
        fringe: Distância máxima ao fundo de um pixel de franja.
        peel: Px de franja a descascar (0 = só a franja de 1 px antiga).

    Returns:
        RGBA recortado, ou ``None`` se a borda não for uniforme (o modelo não
        desenhou um fundo liso) — o chamador cai para o rembg.
    """
    import numpy as np
    from PIL import ImageDraw, ImageFilter

    rgb = np.asarray(image.convert("RGB")).astype(np.int16)
    border = np.concatenate([rgb[0], rgb[-1], rgb[:, 0], rgb[:, -1]])
    key = np.median(border, axis=0)
    if float(np.median(np.abs(border - key).max(axis=1))) > max_border_spread:
        return None
    dist = np.abs(rgb - key).max(axis=2)
    # .copy(): com Pillow 12 a imagem de fromarray partilha o buffer read-only do
    # numpy e o floodfill não faz nada (sem erro).
    near = Image.fromarray(np.where(dist < tolerance, 255, 0).astype(np.uint8), "L").copy()
    h, w = dist.shape
    seeds = [(x, 0) for x in range(w)] + [(x, h - 1) for x in range(w)]
    seeds += [(0, y) for y in range(h)] + [(w - 1, y) for y in range(h)]
    for xy in seeds:
        if near.getpixel(xy) == 255:
            ImageDraw.floodfill(near, xy, 128)
    bg = np.asarray(near) == 128
    fg_area = int((~bg).sum())
    if pockets is None:
        pockets = bool(fg_area) and int(((~bg) & (dist < tolerance * 2)).sum()) < 0.4 * fg_area
    if pockets:
        bg |= _enclosed_pockets((~bg) & (dist < tolerance), dist, fg_area, auto=True)
    # Franja: pixels meio-misturados com o fundo encostados à região de fundo.
    grown = np.asarray(Image.fromarray(bg.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    bg |= grown & (dist < tolerance * 2)
    for _ in range(peel):
        grown = np.asarray(Image.fromarray(bg.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
        bg |= grown & (dist < fringe)
    out = image.convert("RGBA")
    out.putalpha(Image.fromarray(np.where(bg, 0, 255).astype(np.uint8), "L"))
    return out


def _enclosed_pockets(
    cand: Any, dist: Any, fg_area: int, *, min_px: int = 12, max_frac: float = 0.18, auto: bool = False
) -> Any:
    """Máscara das bolsas de fundo fechadas dentro da figura (ver :func:`chroma_key`).

    ``auto``: devolve vazio se a maior peça cor-de-fundo passar 20% da figura
    (personagem branca — a peça é o corpo, não um buraco).
    """
    import numpy as np
    from scipy import ndimage

    out = np.zeros(cand.shape, bool)
    labels, n = ndimage.label(cand)
    if auto and n and float(np.bincount(labels.ravel())[1:].max()) > 0.2 * fg_area:
        return out
    for i, sl in enumerate(ndimage.find_objects(labels), 1):
        if sl is None:
            continue
        # Janela com 2 px de folga para o anel (não dilatar a imagem inteira).
        y0, x0 = max(sl[0].start - 2, 0), max(sl[1].start - 2, 0)
        win = (slice(y0, sl[0].stop + 2), slice(x0, sl[1].stop + 2))
        comp = labels[win] == i
        size = int(comp.sum())
        if size < min_px or size > max_frac * fg_area:
            continue
        ring = ndimage.binary_dilation(comp, iterations=2) & ~comp
        if ring.any() and float(np.median(dist[win][ring])) > 60:
            out[win] |= comp
    return out


def _remove_background(image: Image.Image) -> Image.Image:
    keyed = chroma_key(image)
    if keyed is not None:
        return keyed
    from .bg_removal import remove_background

    return remove_background(image.convert("RGB")).convert("RGBA")


def _anchor_x(alpha: Image.Image, bbox: tuple[int, int, int, int]) -> float:
    """Centro horizontal da cabeça/tronco (metade de cima do bbox).

    As pernas e braços mexem-se entre frames; o tronco não — ancorar no bbox
    inteiro fazia a personagem deslizar para a frente/trás no loop.
    """
    import numpy as np

    left, top, right, bottom = bbox
    upper = np.asarray(alpha.crop((left, top, right, top + max(1, (bottom - top) // 2)))) > 127
    hist = upper.sum(axis=0)
    total = int(hist.sum())
    if not total:
        return (left + right) / 2.0
    return left + float((np.arange(hist.size) * hist).sum()) / total


def align_frames(
    frames: list[Image.Image],
    *,
    margin: float = 0.06,
    lifts: list[float] | None = None,
) -> list[Image.Image]:
    """Alinha frames RGBA numa tela quadrada comum: tronco no centro, pés no chão.

    Mantém a escala relativa entre frames (uma só tela para todos) — só
    translada. ``lifts`` (fração da altura da personagem, por frame) repõe a
    altura das fases aéreas depois de assentar os pés: o modelo desenha o
    salto no ar, o recorte perde essa altura, e sem a repor o salto ficava
    colado ao chão. Frames sem conteúdo ficam vazios.
    """
    boxes = [f.getchannel("A").point(lambda a: 255 if a > 127 else 0).getbbox() for f in frames]
    valid = [(f, b) for f, b in zip(frames, boxes, strict=True) if b]
    if not valid:
        return [f.copy() for f in frames]
    anchors = [_anchor_x(f.getchannel("A"), b) for f, b in valid]
    half_w = max(max(a - b[0], b[2] - a) for (_, b), a in zip(valid, anchors, strict=True))
    height = max(b[3] - b[1] for _, b in valid)
    lifts = lifts or [0.0] * len(frames)
    max_lift = max(lifts) if lifts else 0.0
    side = int(max(2 * half_w, height * (1 + max_lift)) * (1 + 2 * margin)) + 1
    ground = side - int(side * margin)
    out: list[Image.Image] = []
    it = iter(zip(valid, anchors, strict=True))
    for box, lift in zip(boxes, lifts, strict=True):
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        if box:
            (frame, b), anchor = next(it)
            canvas.alpha_composite(frame, (round(side / 2 - anchor), ground - b[3] - round(lift * height)))
        out.append(canvas)
    return out


def _shared_palette(frames: list[Image.Image], colors: int) -> Image.Image | None:
    """Paleta única (MEDIANCUT) a partir dos pixels opacos de todos os frames."""
    import numpy as np

    opaque = [arr[arr[..., 3] > 127, :3] for arr in (np.asarray(f.convert("RGBA")) for f in frames)]
    pixels = np.concatenate(opaque) if opaque else np.empty((0, 3), np.uint8)
    if not len(pixels):
        return None
    strip = Image.fromarray(np.ascontiguousarray(pixels.reshape(1, -1, 3)), "RGB")
    return strip.quantize(colors=max(2, colors), method=Image.Quantize.MEDIANCUT)


def to_sprites(frames: list[Image.Image], *, sprite_size: int | None, colors: int) -> list[Image.Image]:
    """Frames alinhados (RGBA) → sprites: downscale por área, alpha binário, paleta comum."""
    if sprite_size:
        frames = [f.resize((sprite_size, sprite_size), Image.Resampling.BOX) for f in frames]
    palette = _shared_palette(frames, colors)
    sprites: list[Image.Image] = []
    for f in frames:
        alpha = f.getchannel("A").point(lambda a: 255 if a > 127 else 0)
        rgb = f.convert("RGB")
        if palette is not None:
            rgb = rgb.quantize(palette=palette, dither=Image.Dither.NONE).convert("RGB")
        spr = rgb.convert("RGBA")
        spr.putalpha(alpha)
        sprites.append(spr)
    return sprites


def _write_deliverables(
    out: Path, sprites: list[Image.Image], durations: list[int], *, make_sheet: bool, make_gif: bool
) -> tuple[Path | None, Path | None, Path | None]:
    """Atlas 1x (frames em x), preview 4x e GIF com a duração de cada frame."""
    atlas_path = sheet_path = gif_path = None
    if not sprites:
        return atlas_path, sheet_path, gif_path
    w, h = sprites[0].size
    if make_sheet:
        atlas = Image.new("RGBA", (w * len(sprites), h), (0, 0, 0, 0))
        for i, spr in enumerate(sprites):
            atlas.alpha_composite(spr, (i * w, 0))
        atlas_path = out / "atlas.png"
        atlas.save(atlas_path)

        cols = min(len(sprites), 4)
        rows = (len(sprites) + cols - 1) // cols
        side = w * 4
        pad = 12
        sheet = Image.new("RGBA", (cols * (side + pad) + pad, rows * (side + pad) + pad), (42, 44, 54, 255))
        for i, spr in enumerate(sprites):
            sheet.alpha_composite(
                spr.resize((side, side * h // w), Image.Resampling.NEAREST),
                (pad + (i % cols) * (side + pad), pad + (i // cols) * (side + pad)),
            )
        sheet_path = out / "sheet.png"
        sheet.save(sheet_path)
        print(f"[sprite] atlas: {atlas_path} | preview: {sheet_path}", flush=True)
    if make_gif:
        flat = []
        for spr in sprites:
            canvas = Image.new("RGBA", spr.size, (42, 44, 54, 255))
            canvas.alpha_composite(spr)
            # Paleta comum ≤ colors+1 cores → ADAPTIVE é exato (sem cintilar).
            flat.append(canvas.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        gif_path = out / "cycle.gif"
        flat[0].save(gif_path, save_all=True, append_images=flat[1:], duration=durations, loop=0, disposal=2)
        print(f"[sprite] gif: {gif_path}", flush=True)
    return atlas_path, sheet_path, gif_path


def mirror_cycle(src_dir: str | Path, out_dir: str | Path, *, direction: str) -> dict[str, Any]:
    """Espelha um ciclo já gerado (``anim.json`` + sprites) para a direção oposta.

    Sem GPU: num set com ``east`` e ``west`` a segunda direção é a primeira
    espelhada (truque clássico de sprite — personagem idêntica nos dois).

    Raises:
        FileNotFoundError: ``src_dir`` sem ``anim.json``.
    """
    src, out = Path(src_dir), Path(out_dir)
    meta = json.loads((src / "anim.json").read_text(encoding="utf-8"))
    out.mkdir(parents=True, exist_ok=True)
    sprites = [
        Image.open(src / f["file"]).convert("RGBA").transpose(Image.Transpose.FLIP_LEFT_RIGHT) for f in meta["frames"]
    ]
    paths = []
    for f, spr in zip(meta["frames"], sprites, strict=True):
        spr.save(out / f["file"])
        paths.append(out / f["file"])
    durations = [f["duration_ms"] for f in meta["frames"]]
    atlas, sheet, gif = _write_deliverables(out, sprites, durations, make_sheet=True, make_gif=True)
    w = meta["frame_size"][0]
    meta.update(
        direction=direction,
        mirrored_from=meta["direction"],
        pivot=[w - meta["pivot"][0], meta["pivot"][1]],
        atlas=atlas.name if atlas else None,
    )
    meta_path = out / "anim.json"
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"sprites": paths, "atlas": atlas, "sheet": sheet, "gif": gif, "meta": meta_path, "durations": durations}


def _pivot(sprites: list[Image.Image]) -> tuple[int, int]:
    """Pivô dos sprites alinhados: centro horizontal, linha do chão (pés).

    Os frames saem de :func:`align_frames` com o tronco no centro e os pés na
    mesma linha; a linha é a base mais baixa entre os frames (fases aéreas
    ficam acima dela).
    """
    w, h = sprites[0].size
    bottoms = [b[3] for b in (s.getchannel("A").getbbox() for s in sprites) if b]
    return w // 2, max(bottoms) if bottoms else h


def _write_meta(
    out: Path,
    *,
    character: str,
    anim: Animation,
    direction: str,
    source_direction: str,
    phases: list[str],
    sprites: list[Image.Image],
    sprite_paths: list[Path],
    durations: list[int],
    atlas: Path | None,
    seed: int,
    attempts: int,
    differences: list[float],
) -> Path | None:
    """Escreve ``anim.json`` — o contrato do atlas para o motor (frames em x)."""
    if not sprites:
        return None
    w, h = sprites[0].size
    px, py = _pivot(sprites)
    meta = {
        "version": 1,
        "character": character,
        "animation": anim.name,
        "body": anim.body,
        "direction": direction,
        "mirrored_from": source_direction if source_direction != direction else None,
        "loop": anim.loop,
        "frame_size": [w, h],
        "pivot": [px, py],
        "atlas": atlas.name if atlas else None,
        "frames": [
            {"index": i, "pose": pose, "x": i * w, "y": 0, "duration_ms": ms, "file": path.name}
            for i, (pose, ms, path) in enumerate(zip(phases, durations, sprite_paths, strict=True))
        ],
        "total_ms": sum(durations),
        "seed": seed,
        "attempts": attempts,
        "variation": [round(d, 4) for d in differences],
    }
    path = out / "anim.json"
    path.write_text(json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def generate_cycle(
    character: str,
    *,
    direction: str,
    out_dir: str | Path,
    frames: int = 4,
    animation: str = "walk",
    poses: tuple[str, ...] | None = None,
    reference: str | Path | None = None,
    size: int = 320,
    steps: int = 12,
    seed: int = 100,
    sprite_size: int | None = 96,
    colors: int = 32,
    transparent: bool = True,
    make_sheet: bool = True,
    make_gif: bool = True,
    measure: bool = True,
    retries: int = 1,
    frame_ms: int = 160,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera um ciclo de frames para uma direção e devolve os artefactos.

    Args:
        character: Descrição do personagem (quem é, que estilo).
        direction: Chave de :data:`text2d.poses.DIRECTIONS` (ex.: ``"east"``).
        out_dir: Directório de saída (folha crua, frames, sprites, atlas, gif).
        frames: Nº de frames do ciclo (o ciclo canónico tem 4).
        animation: Chave de :data:`text2d.anim.ANIMATIONS` (``walk``, ``run``,
            ``idle``, ``jump``, ``attack``, ``quad-walk``, ``quad-gallop``...).
        poses: Fases a usar (default: as da animação; repete se forem menos
            que ``frames``).
        reference: Imagem de referência do personagem (identidade). Se None,
            gera-se um frame ``idle`` a :data:`IDENTITY_SIZE` e usa-se esse.
        size: Altura de cada célula da folha gerada (largura = 0.8x).
        steps: Passos de inferência.
        seed: Seed (identidade usa ``seed``; as folhas ``seed + 1``).
        sprite_size: Lado do sprite final (None = sem downscale).
        colors: Nº de cores da paleta comum ao ciclo (look 16-bit).
        transparent: Cortar o fundo com rembg.
        make_sheet / make_gif: Gerar folha de pré-visualização / atlas e GIF.
        measure: Medir a variação de silhueta entre frames consecutivos.
        retries: Tentativas extra (outra seed) quando a variação fica abaixo do
            limiar da animação; fica a melhor. 0 = desligado.
        frame_ms: Duração base de cada frame (x ``holds`` da animação).
        generator: Instância de :class:`text2d.generator.KleinFluxGenerator`
            (criada se None e se ``render`` não for usado).
        render: Callback que escreve cada geração (ex.: delegar ao vramd). Se
            None, usa o ``generator`` in-process.

    Returns:
        Dict com ``frames`` (células cruas), ``sprites``, ``sheet`` (preview),
        ``atlas`` (1x, transparente), ``gif``, ``meta`` (``anim.json``),
        ``durations``, ``differences``, ``prompts`` e ``reference``.
    """
    out = Path(out_dir)
    if frames < 1:
        raise ValueError("frames tem de ser >= 1")
    try:
        anim = get_animation(animation)
    except KeyError as e:
        raise ValueError(str(e)) from None
    check_direction(anim, canonical_direction(direction))
    poses = poses or anim.poses
    out.mkdir(parents=True, exist_ok=True)
    if generator is None and render is None:
        from .generator import KleinFluxGenerator

        generator = KleinFluxGenerator(memory_efficient=True)

    phase_list = [poses[i % len(poses)] for i in range(frames)]

    def _render(prompt: str, path: Path, frame_seed: int, refs: list[Path], width: int, height: int) -> None:
        """Escreve uma geração (via callback externo ou generator in-process)."""
        if render is not None:
            render(prompt, path, frame_seed, refs, width, height)
            return
        img, _ = generator.generate(
            prompt,
            width=width,
            height=height,
            refs=refs or None,
            num_inference_steps=steps,
            seed=frame_seed,
        )
        generator.save_image(img, path)

    # 1) Identidade. Direções espelhadas (W, NW, SW) geram-se na canónica e
    # espelham-se: personagem idêntica nos dois sets (truque clássico de sprite).
    source_direction = canonical_direction(direction)
    mirror = source_direction != direction
    view = direction_view(source_direction)

    ref_path = Path(reference) if reference else None
    if ref_path is not None and not ref_path.is_file():
        raise FileNotFoundError(f"referência não encontrada: {ref_path}")
    if ref_path is None:
        base_prompt = identity_prompt(character, direction=source_direction, body=anim.body)
        ref_path = out / "00_reference.png"
        t0 = time.time()
        _render(base_prompt, ref_path, seed, [], IDENTITY_SIZE, IDENTITY_SIZE)
        print(f"[sprite] identidade: {time.time() - t0:.1f}s → {ref_path}", flush=True)

    # 2) Folhas em grelha (até MAX_FRAMES_PER_SHEET células, SHEET_COLS por
    # linha): uma geração por folha → mesma personagem/escala/chão em todas.
    cell_w, cell_h = cell_size(anim, size)
    lifts = [phase_lift(anim, p) for p in phase_list]
    floor = anim.min_variation.get(view, 0.0) if measure else 0.0

    def _attempt(attempt: int) -> tuple[list[Path], list[Image.Image], list[Image.Image], list[str]]:
        """Gera todas as folhas com a seed da tentativa → (raws, células, sprites, prompts)."""
        tag = f"_try{attempt}" if attempt else ""
        raw_paths: list[Path] = []
        cells: list[Image.Image] = []
        prompts: list[str] = []
        for start in range(0, frames, MAX_FRAMES_PER_SHEET):
            chunk = phase_list[start : start + MAX_FRAMES_PER_SHEET]
            k = start // MAX_FRAMES_PER_SHEET
            cols = min(SHEET_COLS, len(chunk))
            rows = (len(chunk) + cols - 1) // cols
            guide = out / f"poses_{k:02d}.png"
            draw_guide(anim, chunk, view=view, cell=(cell_w, cell_h), cols=cols).save(guide)
            prompt = sheet_prompt(character, direction=source_direction, poses=chunk, animation=anim, cols=cols)
            prompts.append(prompt)
            raw = out / f"sheet_raw_{k:02d}{tag}.png"
            t0 = time.time()
            _render(prompt, raw, seed + 1 + attempt, [ref_path, guide], cell_w * cols, cell_h * rows)
            print(
                f"[sprite] folha {k + 1}{f' (tentativa {attempt + 1})' if attempt else ''} "
                f"({len(chunk)} frames: {', '.join(chunk)}): {time.time() - t0:.1f}s",
                flush=True,
            )
            # A imagem pode vir noutro tamanho (callbacks/testes): fatiar proporcionalmente.
            sheet_img = Image.open(raw).convert("RGB")
            step_x, step_y = sheet_img.width / cols, sheet_img.height / rows
            for j, pose in enumerate(chunk):
                cx, cy = j % cols, j // cols
                box = (round(cx * step_x), round(cy * step_y), round((cx + 1) * step_x), round((cy + 1) * step_y))
                cell = sheet_img.crop(box)
                path = out / f"frame_{start + j:02d}_{pose}{tag}.png"
                cell.save(path)
                raw_paths.append(path)
                cells.append(cell)
        # 3) Sprites: fundo fora → alinhar → downscale + paleta comum.
        rgba = [_remove_background(c) if transparent else c.convert("RGBA") for c in cells]
        if transparent:
            rgba = align_frames(rgba, lifts=lifts)
        return raw_paths, cells, to_sprites(rgba, sprite_size=sprite_size, colors=colors), prompts

    def _variation(sprites: list[Image.Image]) -> list[float]:
        if len(sprites) < 2:
            return []
        seq = [*sprites, sprites[0]] if anim.loop else sprites
        return [alpha_difference(a, b) for a, b in itertools.pairwise(seq)]

    # Auto-retry: se os frames saírem cópias (a identidade dominou a pose), tenta
    # outra seed e fica com a tentativa de maior variação — o utilizador já não
    # tem de adivinhar seeds (o aviso da v2 só dizia "tenta outra --seed").
    best = _attempt(0)
    differences = _variation(best[2])
    attempts = 1
    while floor and differences and sum(differences) / len(differences) < floor and attempts <= retries:
        mean = sum(differences) / len(differences)
        print(f"[sprite] variação {mean * 100:.1f}% < {floor * 100:.0f}% — nova tentativa com outra seed", flush=True)
        candidate = _attempt(attempts)
        cand_diff = _variation(candidate[2])
        attempts += 1
        if sum(cand_diff) > sum(differences):
            best, differences = candidate, cand_diff
    raw_paths, _cells, source_sprites, prompts = best
    sprites = [s.transpose(Image.Transpose.FLIP_LEFT_RIGHT) for s in source_sprites] if mirror else source_sprites

    sprite_paths: list[Path] = []
    for i, spr in enumerate(sprites):
        spr_path = out / f"sprite_{i:02d}_{phase_list[i]}.png"
        spr.save(spr_path)
        sprite_paths.append(spr_path)

    # Set espelhado (a direção canónica do par) — consistência por construção.
    if mirror:
        mirrored = out / "mirrored"
        mirrored.mkdir(parents=True, exist_ok=True)
        for i, spr in enumerate(source_sprites):
            m_path = mirrored / f"sprite_{i:02d}_{phase_list[i]}.png"
            spr.save(m_path)
            sprite_paths.append(m_path)
        print(f"[sprite] set espelhado ({source_direction}) → {mirrored}", flush=True)

    # 4) Atlas (1x, transparente — o que o jogo consome), preview 4x e GIF.
    durations = anim.durations(len(sprites), frame_ms)
    atlas_path, sheet_path, gif_path = _write_deliverables(
        out, sprites, durations, make_sheet=make_sheet, make_gif=make_gif
    )

    # 5) Métrica: os frames são realmente diferentes? (após alinhar → só pose)
    if measure and differences:
        mean = sum(differences) / len(differences)
        print(
            f"[sprite] variação de silhueta entre frames consecutivos{' (com o loop)' if anim.loop else ''}: "
            + ", ".join(f"{d * 100:.1f}%" for d in differences)
            + f" (média {mean * 100:.1f}%"
            + (f", {attempts} tentativas)" if attempts > 1 else ")"),
            flush=True,
        )
        if floor and mean < floor:
            print(
                f"[sprite] AVISO: frames quase iguais (média {mean * 100:.1f}% < {floor * 100:.0f}%) mesmo após "
                f"{attempts} tentativa(s) — tenta um --ref com a personagem parada ou --retries maior.",
                flush=True,
            )
    elif not measure:
        differences = []

    meta_path = _write_meta(
        out,
        character=character,
        anim=anim,
        direction=direction,
        source_direction=source_direction,
        phases=phase_list,
        sprites=sprites,
        sprite_paths=sprite_paths[: len(sprites)],
        durations=durations,
        atlas=atlas_path,
        seed=seed,
        attempts=attempts,
        differences=differences,
    )

    return {
        "frames": raw_paths,
        "sprites": sprite_paths,
        "sheet": sheet_path,
        "atlas": atlas_path,
        "gif": gif_path,
        "meta": meta_path,
        "durations": durations,
        "differences": differences,
        "attempts": attempts,
        "prompts": prompts,
        "reference": ref_path,
    }


# ---------------------------------------------------------------------------
# Set completo: várias animações x direções de uma personagem
# ---------------------------------------------------------------------------

DEFAULT_SET: dict[str, tuple[str, ...]] = {
    "biped": ("idle", "walk", "run", "jump", "attack", "hurt", "death"),
    "quadruped": ("quad-idle", "quad-walk", "quad-trot", "quad-gallop", "quad-attack", "quad-hurt"),
}


def generate_set(
    character: str,
    *,
    out_dir: str | Path,
    animations: list[str] | tuple[str, ...] | None = None,
    directions: list[str] | tuple[str, ...] = ("east",),
    body: str | None = None,
    reference: str | Path | None = None,
    seed: int = 100,
    generator: Any = None,
    render: RenderFn | None = None,
    **cycle_kwargs: Any,
) -> dict[str, Any]:
    """Gera o set de sprites de uma personagem (animações x direções) de uma vez.

    - **Uma identidade para tudo**, gerada uma vez (ou ``reference``) e
      passada a todas as animações e vistas. Quem impõe a vista é a folha de
      silhuetas: medido E2E, uma identidade de frente dá folhas de perfil e
      de costas corretas. Gerar uma identidade por vista com a primeira como
      referência não serve — o modelo copia a referência (3 identidades
      iguais, ~85 s cada, desperdiçados).
    - Direções espelhadas (``west``, ``north_west``, ``south_west``) saem da
      canónica espelhada, sem GPU (:func:`mirror_cycle`).
    - ``set.json`` indexa tudo: identidade e ``anim.json`` por animação/direção.

    Args:
        animations: Chaves de :data:`text2d.anim.ANIMATIONS` (default: o set
            standard do corpo — :data:`DEFAULT_SET`). Todas do mesmo corpo.
        directions: Direções pedidas (quadrúpedes: só ``east``/``west``).
        body: ``biped``/``quadruped`` — só decide o set default.
        reference: Identidade da personagem parada (opcional).
        **cycle_kwargs: Passa para :func:`generate_cycle` (``frames``,
            ``sprite_size``, ``retries``...).

    Raises:
        ValueError: Animação desconhecida, corpos misturados ou direção sem vista.
    """
    from .anim import ANIMATIONS

    names = list(animations or DEFAULT_SET[body or "biped"])
    unknown = [a for a in names if a not in ANIMATIONS]
    if unknown:
        raise ValueError(f"animações desconhecidas: {', '.join(unknown)} (disponíveis: {', '.join(ANIMATIONS)})")
    bodies = {ANIMATIONS[a].body for a in names}
    if len(bodies) > 1:
        raise ValueError("um set não mistura bípedes e quadrúpedes (identidades diferentes)")
    set_body = bodies.pop()
    dirs = list(dict.fromkeys(directions))
    for d in dirs:
        check_direction(ANIMATIONS[names[0]], canonical_direction(d))
    canon = list(dict.fromkeys(canonical_direction(d) for d in dirs))

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    if generator is None and render is None:
        from .generator import KleinFluxGenerator

        generator = KleinFluxGenerator(memory_efficient=True)
    steps = int(cycle_kwargs.get("steps", 12))
    if render is None:

        def render(prompt: str, path: Path, frame_seed: int, refs: list[Path], width: int, height: int) -> None:
            img, _ = generator.generate(
                prompt, width=width, height=height, refs=refs or None, num_inference_steps=steps, seed=frame_seed
            )
            generator.save_image(img, path)

    # 1) Identidade única (ou --ref), na 1.ª vista canónica pedida.
    if reference:
        identity = Path(reference)
    else:
        identity = out / "identity.png"
        t0 = time.time()
        render(
            identity_prompt(character, direction=canon[0], body=set_body),
            identity,
            seed,
            [],
            IDENTITY_SIZE,
            IDENTITY_SIZE,
        )
        print(f"[set] identidade: {time.time() - t0:.1f}s → {identity}", flush=True)

    # 2) Cada animação na vista canónica; espelhadas sem GPU.
    index: dict[str, dict[str, str]] = {}
    for name in names:
        index[name] = {}
        for d in canon:
            res = generate_cycle(
                character,
                direction=d,
                out_dir=out / name / d,
                animation=name,
                reference=identity,
                seed=seed,
                render=render,
                **cycle_kwargs,
            )
            if d in dirs and res["meta"]:
                index[name][d] = str(Path(res["meta"]).relative_to(out))
        for d in dirs:
            src = canonical_direction(d)
            if src == d:
                continue
            res = mirror_cycle(out / name / src, out / name / d, direction=d)
            index[name][d] = str(Path(res["meta"]).relative_to(out))
            print(f"[set] {name} {d}: espelho de {src}", flush=True)

    manifest = {
        "version": 1,
        "character": character,
        "body": set_body,
        "directions": dirs,
        "identity": str(identity.relative_to(out)) if identity.is_relative_to(out) else str(identity),
        "animations": index,
    }
    set_path = out / "set.json"
    set_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"[set] {len(names)} animações x {len(dirs)} direções → {set_path}", flush=True)
    return {"set": set_path, "identity": identity, "animations": index}
