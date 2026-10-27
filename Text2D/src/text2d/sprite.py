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
3. **Pós-processamento de sprite** — rembg por célula, alinhamento (tronco no
   mesmo x, pés na mesma linha), downscale por média de área (sem o ringing
   do LANCZOS), alpha binário e **uma paleta para o ciclo todo** (paleta por
   frame fazia as cores cintilar no GIF).

Até :data:`MAX_FRAMES_PER_SHEET` frames saem numa só geração (grelha de
:data:`SHEET_COLS` colunas); acima disso geram-se várias folhas com a mesma
identidade. Se os frames saírem quase iguais (a identidade dominou a pose), a
folha é regenerada com outra seed (``retries``) e fica a de maior variação.
"""

from __future__ import annotations

import itertools
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
    pockets: bool = False,
) -> Image.Image | None:
    """Recorta o fundo liso pela cor da borda (flood fill a partir das margens).

    A cor do fundo é a mediana dos pixels da borda; só sai a região dessa cor
    **ligada à borda**, mais a franja anti-aliased de 1 px. O contorno escuro
    do pixel art trava o preenchimento, por isso uma personagem branca em
    fundo branco não é furada (o rembg furava: cavalo branco com buracos).
    Tolerância 12: a 24 o preenchimento já vazava pelo contorno de um cavalo
    branco.

    Args:
        pockets: Remover também bolsas fechadas (entre pernas) da cor do fundo.
            Só para fundos saturados (cor-chave); com fundo branco apagaria o
            corpo de personagens brancas.

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
    if pockets:
        bg |= dist < tolerance * 0.5
    # Franja: pixels meio-misturados com o fundo encostados à região de fundo.
    grown = np.asarray(Image.fromarray(bg.astype(np.uint8) * 255).filter(ImageFilter.MaxFilter(3))) > 0
    bg |= grown & (dist < tolerance * 2)
    out = image.convert("RGBA")
    out.putalpha(Image.fromarray(np.where(bg, 0, 255).astype(np.uint8), "L"))
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
        frame_ms: Duração de cada frame no GIF.
        generator: Instância de :class:`text2d.generator.KleinFluxGenerator`
            (criada se None e se ``render`` não for usado).
        render: Callback que escreve cada geração (ex.: delegar ao vramd). Se
            None, usa o ``generator`` in-process.

    Returns:
        Dict com ``frames`` (células cruas), ``sprites``, ``sheet`` (preview),
        ``atlas`` (1x, transparente), ``gif``, ``differences``, ``prompts`` e
        ``reference``.
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
    sheet_path = atlas_path = None
    if make_sheet and sprites:
        w, h = sprites[0].size
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

    gif_path = None
    if make_gif and sprites:
        flat = []
        for spr in sprites:
            canvas = Image.new("RGBA", spr.size, (42, 44, 54, 255))
            canvas.alpha_composite(spr)
            # Paleta comum ≤ colors+1 cores → ADAPTIVE é exato (sem cintilar).
            flat.append(canvas.convert("RGB").convert("P", palette=Image.Palette.ADAPTIVE, colors=256))
        gif_path = out / "cycle.gif"
        flat[0].save(gif_path, save_all=True, append_images=flat[1:], duration=frame_ms, loop=0, disposal=2)
        print(f"[sprite] gif: {gif_path}", flush=True)

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

    return {
        "frames": raw_paths,
        "sprites": sprite_paths,
        "sheet": sheet_path,
        "atlas": atlas_path,
        "gif": gif_path,
        "differences": differences,
        "attempts": attempts,
        "prompts": prompts,
        "reference": ref_path,
    }
