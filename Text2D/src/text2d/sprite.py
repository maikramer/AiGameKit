"""Geração de sprite sheets 2D — ciclos de caminhada/corrida por direção.

O FLUX.3 não tem pose conditioning; a estratégia (validada E2E) é:

1. gerar/fixar um **frame de referência** do personagem (identidade);
2. para cada fase do ciclo, gerar um frame com o **prompt de pose** (frase
   explícita sobre pernas e braços) **usando a mesma referência** — o prompt
   muda a pose, a referência mantém o personagem;
3. pós-processar para sprites (fundo fora, downscale, paleta reduzida) e
   montar folha/GIF.

Ver :mod:`text2d.poses` para o vocabulário e
``docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md`` para as medições.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PIL import Image

from .poses import WALK_CYCLE, alpha_difference, build_prompt, canonical_direction, walk_cycle_prompts

STYLE_SUFFIX = "flat colors, crisp pixels, no anti-aliasing, no text, plain white background"

# Callback de render: (prompt, output_path, seed, refs) -> None (escreve o ficheiro).
# Permite delegar cada frame ao vramd (coordenação de VRAM) em vez de correr
# in-process; o CLI fornece o callback com fallback in-process.
RenderFn = Callable[[str, Path, int, list[Path]], None]


def _postprocess(
    image: Image.Image,
    *,
    sprite_size: int | None,
    colors: int,
    transparent: bool,
) -> Image.Image:
    """Corta o fundo (rembg), reduz à grelha de sprite e quantiza a paleta."""
    if transparent:
        from .bg_removal import remove_background

        image = remove_background(image.convert("RGB"))
    if not sprite_size:
        return image
    small = image.convert("RGBA").resize((sprite_size, sprite_size), Image.Resampling.LANCZOS)
    alpha = small.getchannel("A")
    rgb = small.convert("RGB").quantize(colors=colors, method=Image.Quantize.MEDIANCUT).convert("RGBA")
    rgb.putalpha(alpha)
    return rgb


def generate_cycle(
    character: str,
    *,
    direction: str,
    out_dir: str | Path,
    frames: int = 4,
    poses: tuple[str, ...] = WALK_CYCLE,
    reference: str | Path | None = None,
    size: int = 256,
    steps: int = 12,
    seed: int = 100,
    sprite_size: int | None = 96,
    colors: int = 32,
    transparent: bool = True,
    make_sheet: bool = True,
    make_gif: bool = True,
    measure: bool = True,
    generator: Any = None,
    render: RenderFn | None = None,
) -> dict[str, Any]:
    """Gera um ciclo de frames para uma direção e devolve os artefactos.

    Args:
        character: Descrição do personagem (quem é, que estilo).
        direction: Chave de :data:`text2d.poses.DIRECTIONS` (ex.: ``"east"``).
        out_dir: Directório de saída (frames crus + sprites + sheet + gif).
        frames: Nº de frames do ciclo (o ciclo canónico tem 4).
        poses: Fases a usar (repete se forem menos que ``frames``).
        reference: Imagem de referência do personagem (identidade). Se None,
            gera-se um frame base (pose ``idle``) e usa-se esse.
        size: Resolução de geração (múltiplo de 16 recomendado).
        steps: Passos de inferência.
        seed: Seed base (cada frame usa ``seed + i``).
        sprite_size: Lado do sprite final (None = sem downscale).
        colors: Nº de cores da paleta (look 16-bit).
        transparent: Cortar o fundo com rembg.
        make_sheet / make_gif: Gerar folha de sprites e GIF do ciclo.
        measure: Medir a variação de silhueta entre frames consecutivos.
        generator: Instância de :class:`text2d.generator.KleinFluxGenerator`
            (criada se None e se ``render`` não for usado).
        render: Callback que escreve cada frame (ex.: delegar ao vramd). Se
            None, usa o ``generator`` in-process.

    Returns:
        Dict com ``frames`` (paths crus), ``sprites`` (paths processados),
        ``sheet``, ``gif``, ``differences`` (alpha diffs) e ``prompts``.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    if generator is None and render is None:
        from .generator import KleinFluxGenerator

        generator = KleinFluxGenerator(memory_efficient=True)

    if frames < 1:
        raise ValueError("frames tem de ser >= 1")
    phase_list = [poses[i % len(poses)] for i in range(frames)]

    def _render(prompt: str, path: Path, frame_seed: int, refs: list[Path]) -> None:
        """Escreve um frame (via callback externo ou generator in-process)."""
        if render is not None:
            render(prompt, path, frame_seed, refs)
            return
        img, _ = generator.generate(
            prompt,
            width=size,
            height=size,
            refs=refs or None,
            num_inference_steps=steps,
            seed=frame_seed,
        )
        generator.save_image(img, path)

    # 1) Referência (identidade do personagem).
    #
    # Direções espelhadas (W, NW, SW) são geradas a partir da direção canónica
    # e depois espelhadas: a personagem fica **idêntica** nos dois sets (a
    # iluminação espelha-se, o que é o truque clássico de sprite 2D).
    source_direction = canonical_direction(direction)
    mirror = source_direction != direction

    ref_path = Path(reference) if reference else None
    if ref_path is not None and not ref_path.is_file():
        raise FileNotFoundError(f"referência não encontrada: {ref_path}")
    if ref_path is None:
        base_prompt = build_prompt(character, direction=source_direction, pose=poses[0], extra=STYLE_SUFFIX)
        ref_path = out / "00_reference.png"
        t0 = time.time()
        _render(base_prompt, ref_path, seed, [])
        print(f"[sprite] frame de referência: {time.time() - t0:.1f}s → {ref_path}", flush=True)

    # 2) Um frame por fase, com o prompt de pose e a mesma referência.
    prompts = walk_cycle_prompts(character, direction=source_direction, poses=tuple(phase_list), extra=STYLE_SUFFIX)
    raw_paths: list[Path] = []
    for i, (pose, prompt) in enumerate(zip(phase_list, prompts, strict=True)):
        t0 = time.time()
        path = out / f"frame_{i:02d}_{pose}.png"
        _render(prompt, path, seed + 1 + i, [ref_path])
        raw_paths.append(path)
        print(f"[sprite] frame {i + 1}/{frames} ({pose}): {time.time() - t0:.1f}s", flush=True)

    # 3) Pós-processamento para sprites.
    sprite_paths: list[Path] = []
    sprites: list[Image.Image] = []
    source_sprites: list[Image.Image] = []
    for i, path in enumerate(raw_paths):
        spr = _postprocess(
            Image.open(path),
            sprite_size=sprite_size,
            colors=colors,
            transparent=transparent,
        )
        source_sprites.append(spr)
        if mirror:
            spr = spr.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        spr_path = out / f"sprite_{i:02d}_{phase_list[i]}.png"
        spr.save(spr_path)
        sprite_paths.append(spr_path)
        sprites.append(spr)

    # Set espelhado (a direção canónica do par) — consistência por construção.
    if mirror:
        mirrored = out / "mirrored"
        mirrored.mkdir(parents=True, exist_ok=True)
        for i, spr in enumerate(source_sprites):
            m_path = mirrored / f"sprite_{i:02d}_{phase_list[i]}.png"
            spr.save(m_path)
            sprite_paths.append(m_path)
        print(f"[sprite] set espelhado ({source_direction}) → {mirrored}", flush=True)

    # 4) Folha de sprites + GIF.
    sheet_path = None
    if make_sheet and sprites:
        cols = min(len(sprites), 4)
        rows = (len(sprites) + cols - 1) // cols
        side = (sprite_size or size) * 4
        pad = 12
        sheet = Image.new("RGBA", (cols * (side + pad) + pad, rows * (side + pad) + pad), (42, 44, 54, 255))
        for i, spr in enumerate(sprites):
            sheet.alpha_composite(
                spr.resize((side, side), Image.Resampling.NEAREST),
                (pad + (i % cols) * (side + pad), pad + (i // cols) * (side + pad)),
            )
        sheet_path = out / "sheet.png"
        sheet.save(sheet_path)
        print(f"[sprite] folha: {sheet_path}", flush=True)

    gif_path = None
    if make_gif and sprites:
        flat = []
        for spr in sprites:
            canvas = Image.new("RGBA", spr.size, (42, 44, 54, 255))
            canvas.alpha_composite(spr)
            flat.append(canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=64))
        gif_path = out / "cycle.gif"
        flat[0].save(gif_path, save_all=True, append_images=flat[1:], duration=250, loop=0, disposal=2)
        print(f"[sprite] gif: {gif_path}", flush=True)

    # 5) Métrica: os frames são realmente diferentes?
    differences: list[float] = []
    if measure and len(sprites) > 1:
        for a, b in itertools.pairwise(sprites):
            differences.append(alpha_difference(a, b))
        mean = sum(differences) / len(differences)
        print(
            "[sprite] variação de silhueta entre frames consecutivos: "
            + ", ".join(f"{d * 100:.1f}%" for d in differences)
            + f" (média {mean * 100:.1f}%)",
            flush=True,
        )

    return {
        "frames": raw_paths,
        "sprites": sprite_paths,
        "sheet": sheet_path,
        "gif": gif_path,
        "differences": differences,
        "prompts": prompts,
        "reference": ref_path,
    }
