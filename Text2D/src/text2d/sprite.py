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

Ciclos com mais de :data:`MAX_FRAMES_PER_SHEET` frames geram várias folhas
com a mesma identidade e seed.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .poses import (
    WALK_CYCLE,
    alpha_difference,
    canonical_direction,
    direction_prompt,
    direction_view,
    draw_pose_sheet,
    facing_prompt,
    pose_prompt,
)

STYLE_SUFFIX = "flat colors, crisp pixels, no anti-aliasing, no text, plain white background"

# Frames por geração: 4 células de 256 px = 1024 de largura (resolução nativa).
MAX_FRAMES_PER_SHEET = 4

# Resolução da imagem de identidade (abaixo de ~512 o FLUX.3 perde detalhe e o
# "pixel art" sai sujo — era a causa dos frames borrados a 256²).
IDENTITY_SIZE = 512

# Abaixo disto (variação média entre frames consecutivos, após alinhar) os
# frames são cópias uns dos outros. Medido a 96 px na 4050 — perfil: cópia 2.8%,
# ciclo bom 6-11%; de frente (passada naturalmente subtil): cópia 1.5%, bom 4.6%.
MIN_POSE_VARIATION: dict[str, float] = {"side": 0.04, "front": 0.03}

# Callback de render: (prompt, output_path, seed, refs, width, height) -> None
# (escreve o ficheiro). Permite delegar cada geração ao vramd em vez de correr
# in-process; o CLI fornece o callback com fallback in-process.
RenderFn = Callable[[str, Path, int, list[Path], int, int], None]


def _cell_size(size: int) -> tuple[int, int]:
    """Célula ``(largura, altura)`` múltipla de 16 com proporção 4:5 (corpo inteiro)."""
    height = max(64, (size // 16) * 16)
    return max(48, round(height * 0.8 / 16) * 16), height


def sheet_prompt(character: str, *, direction: str, poses: tuple[str, ...] | list[str]) -> str:
    """Prompt da folha: N cópias da personagem, cada uma na pose da silhueta.

    A pose de cada célula também vai por extenso (``frame 1: ...``): sozinha, a
    silhueta frontal (diferenças subtis de comprimento das pernas) não chegava
    para o modelo variar a passada.
    """
    n = len(poses)
    view = direction_view(direction)
    frames_text = "; ".join(f"frame {i + 1}: {pose_prompt(p, view=view)}" for i, p in enumerate(poses))
    return (
        f"a horizontal sprite sheet of a {n}-frame walk cycle animation of "
        f"{character.strip().rstrip('.')}, {n} copies of the same character from left to right, each copy "
        f"{direction_prompt(direction)}, posed exactly like the white silhouette at the same position in the "
        "second image (the grey limbs are the far side of the body), the character looks exactly like the "
        "first image, identical outfit, colors, size and proportions in every frame, full body, feet on the "
        f"same ground line. {frames_text}. {STYLE_SUFFIX}"
    )


def identity_prompt(character: str, *, direction: str) -> str:
    """Prompt da identidade: corpo inteiro, **parado** (ver :func:`facing_prompt`)."""
    view = direction_view(direction)
    return (
        f"{character.strip().rstrip('.')}, full body, {facing_prompt(direction)}, "
        f"{pose_prompt('idle', view=view)}, {STYLE_SUFFIX}"
    )


def _remove_background(image: Image.Image) -> Image.Image:
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


def align_frames(frames: list[Image.Image], *, margin: float = 0.06) -> list[Image.Image]:
    """Alinha frames RGBA numa tela quadrada comum: tronco no centro, pés no chão.

    Mantém a escala relativa entre frames (uma só tela para todos) — só
    translada. Frames sem conteúdo ficam vazios.
    """
    boxes = [f.getchannel("A").point(lambda a: 255 if a > 127 else 0).getbbox() for f in frames]
    valid = [(f, b) for f, b in zip(frames, boxes, strict=True) if b]
    if not valid:
        return [f.copy() for f in frames]
    anchors = [_anchor_x(f.getchannel("A"), b) for f, b in valid]
    half_w = max(max(a - b[0], b[2] - a) for (_, b), a in zip(valid, anchors, strict=True))
    height = max(b[3] - b[1] for _, b in valid)
    side = int(max(2 * half_w, height) * (1 + 2 * margin)) + 1
    ground = side - int(side * margin)
    out: list[Image.Image] = []
    it = iter(zip(valid, anchors, strict=True))
    for box in boxes:
        canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        if box:
            (frame, b), anchor = next(it)
            canvas.alpha_composite(frame, (round(side / 2 - anchor), ground - b[3]))
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
    poses: tuple[str, ...] = WALK_CYCLE,
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
        poses: Fases a usar (repete se forem menos que ``frames``).
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
    out.mkdir(parents=True, exist_ok=True)

    if frames < 1:
        raise ValueError("frames tem de ser >= 1")
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
        base_prompt = identity_prompt(character, direction=source_direction)
        ref_path = out / "00_reference.png"
        t0 = time.time()
        _render(base_prompt, ref_path, seed, [], IDENTITY_SIZE, IDENTITY_SIZE)
        print(f"[sprite] identidade: {time.time() - t0:.1f}s → {ref_path}", flush=True)

    # 2) Folhas: cada uma com até MAX_FRAMES_PER_SHEET células.
    cell_w, cell_h = _cell_size(size)
    raw_paths: list[Path] = []
    cells: list[Image.Image] = []
    prompts: list[str] = []
    for start in range(0, frames, MAX_FRAMES_PER_SHEET):
        chunk = phase_list[start : start + MAX_FRAMES_PER_SHEET]
        k = start // MAX_FRAMES_PER_SHEET
        guide = out / f"poses_{k:02d}.png"
        draw_pose_sheet(chunk, view=view, cell=(cell_w, cell_h)).save(guide)
        prompt = sheet_prompt(character, direction=source_direction, poses=chunk)
        prompts.append(prompt)
        raw = out / f"sheet_raw_{k:02d}.png"
        t0 = time.time()
        _render(prompt, raw, seed + 1, [ref_path, guide], cell_w * len(chunk), cell_h)
        print(f"[sprite] folha {k + 1} ({len(chunk)} frames: {', '.join(chunk)}): {time.time() - t0:.1f}s", flush=True)
        # A imagem pode vir noutro tamanho (callbacks/testes): fatiar proporcionalmente.
        sheet_img = Image.open(raw).convert("RGB")
        step = sheet_img.width / len(chunk)
        for j, pose in enumerate(chunk):
            cell = sheet_img.crop((round(j * step), 0, round((j + 1) * step), sheet_img.height))
            path = out / f"frame_{start + j:02d}_{pose}.png"
            cell.save(path)
            raw_paths.append(path)
            cells.append(cell)

    # 3) Sprites: fundo fora → alinhar → downscale + paleta comum.
    rgba = [_remove_background(c) if transparent else c.convert("RGBA") for c in cells]
    if transparent:
        rgba = align_frames(rgba)
    source_sprites = to_sprites(rgba, sprite_size=sprite_size, colors=colors)
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
    differences: list[float] = []
    if measure and len(sprites) > 1:
        for a, b in itertools.pairwise([*sprites, sprites[0]]):
            differences.append(alpha_difference(a, b))
        mean = sum(differences) / len(differences)
        print(
            "[sprite] variação de silhueta entre frames consecutivos (com o loop): "
            + ", ".join(f"{d * 100:.1f}%" for d in differences)
            + f" (média {mean * 100:.1f}%)",
            flush=True,
        )
        floor = MIN_POSE_VARIATION.get(view, MIN_POSE_VARIATION["side"])
        if mean < floor:
            print(
                f"[sprite] AVISO: frames quase iguais (média {mean * 100:.1f}% < {floor * 100:.0f}%) — "
                "a pose da identidade dominou; tenta outra --seed ou um --ref com a personagem parada.",
                flush=True,
            )

    return {
        "frames": raw_paths,
        "sprites": sprite_paths,
        "sheet": sheet_path,
        "atlas": atlas_path,
        "gif": gif_path,
        "differences": differences,
        "prompts": prompts,
        "reference": ref_path,
    }
