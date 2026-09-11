#!/usr/bin/env python3
"""Regenera todas as texturas do simple-rpg via vramd (texture2d) + Materialize (PBR).

9 texturas:
  Ground/Terrain: vale_grass, forest_floor, desert_sand, swamp_mud, snow_peak, mountain_stone
  Props/Materiais: wood_planks, wall_plaster, roof_tiles

Cada textura vive em `textures/<nome>/` (albedo + mapas + gen.json):
  1. Difuso gerado via vramd (texture2d com group_offload auto na RTX 4050)
  2. Pós-processamento determinístico (POST)
  3. PBR maps gerados via Materialize (height, normal, metallic, smoothness, edge, AO)
  4. Finalização: PNGs de trabalho convertidos para WebP (albedo q92, mapas q95)
     — o Materialize só escreve png/jpg, pelo que a conversão corre aqui.
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from aigamekit_shared.vramd_client import delegate_to_vramd

# O pool passou a ser partilhado pelos exemplos (shared-assets), servido pelo
# plugin `sharedAssets` do Vite — as texturas do simple-rpg vivem lá.
TEXTURES_DIR = Path(__file__).resolve().parents[1] / "shared-assets" / "public" / "assets" / "textures"

# (filename, prompt, negative_prompt, materialize_preset)
SPECS: list[tuple[str, str, str, str]] = [
    # --- Ground / Terrain ---
    (
        "vale_grass",
        "seamless tileable texture, lush green grass meadow, fine uniform grass cover, small wildflowers scattered, "
        "vibrant healthy grass, top-down flat view, game asset",
        "macro, brown, dead, mud, 3d render, blurry, low quality, seams, borders, frame",
        "default",
    ),
    (
        "forest_floor",
        # A versão anterior saía com folhas de outono vermelhas/amarelas: numa
        # "Floresta Sombria" o chão lia-se como confetti. Musgo + húmus, sem cor forte.
        "seamless tileable texture, dark damp forest floor, deep green moss over brown humus soil, "
        "scattered pine needles and small twigs, muted desaturated earthy palette, soft even lighting, "
        "top-down orthographic flat view, stylized game ground texture",
        "autumn leaves, red leaves, orange, yellow, colorful, saturated, confetti, flowers, grass lawn, "
        "high contrast, harsh shadows, seams, borders, frame, watermark",
        "default",
    ),
    (
        "desert_sand",
        # Antes saía laranja-néon. Areia clara com ondulação; o realce de
        # contraste LOCAL (POST abaixo) é o que dá as marcas de vento.
        "seamless tileable texture, light golden yellow sand dunes seen from above, rippled sand waves, "
        "bright sunny desert floor, clean sand, top-down orthographic view, stylized game ground",
        "brown, mud, dark, soil, dirt, rocks, grass, water, seams, borders, vignette",
        "default",
    ),
    (
        "swamp_mud",
        # O brejo lia-se como praia clara; o escurecimento vive no POST.
        "seamless tileable texture, murky dark swamp mud with algae patches, wet slippery ground, "
        "decaying vegetation, top-down flat view, game asset",
        "grass, clean, polished, sand, seams, borders",
        "default",
    ),
    (
        "snow_peak",
        # Antes: cascalho cinzento-azulado (lia-se como betão sujo).
        "seamless tileable texture, pure white fresh snow surface, soft powder drifts, gentle undulations, "
        "bright snowfield seen from above, top-down orthographic view, stylized game ground",
        "blue, cyan, speckles, dots, granite, rock, gravel, dirt, grey, noise, stars, seams, borders",
        "default",
    ),
    (
        "mountain_stone",
        "seamless tileable texture, grey mountain rock stone surface with cracks and moss patches, "
        "rugged cliff face, top-down flat view, game asset",
        "smooth, polished, sand, wood, seams, borders",
        "stone",
    ),
    # --- Props / Building Materials ---
    (
        "wood_planks",
        "seamless tileable texture, weathered pine wood planks, natural wood grain, medieval cottage flooring, "
        "aged brown wood boards, top-down flat view, game asset",
        "blurry, low quality, distorted, metal, stone, seams, borders",
        "wood",
    ),
    (
        "wall_plaster",
        "seamless tileable texture, rough white plaster wall, medieval cottage weathered stucco, "
        "cracked whitewash, old painted surface, top-down flat view, game asset",
        "wood, metal, modern, clean, seams, borders",
        "default",
    ),
    (
        "roof_tiles",
        "seamless tileable texture, red clay roof tiles, terracotta shingles overlapping, "
        "medieval cottage rooftop, weathered ceramic, top-down flat view, game asset",
        "wood, metal, modern, flat roof, seams, borders",
        "stone",
    ),
]


def generate_texture(name: str, prompt: str, negative: str) -> bool:
    """Gera uma textura via vramd (texture2d). Retorna True se OK."""
    mat_dir = TEXTURES_DIR / name
    mat_dir.mkdir(parents=True, exist_ok=True)
    # PNG de trabalho; o finalize converte para albedo.webp no fim.
    output = mat_dir / "albedo.png"
    print(f"  [vramd] A gerar {name}/albedo.png...", flush=True)

    t0 = time.time()
    result = delegate_to_vramd(
        "texture2d",
        {
            "prompt": prompt,
            "negative_prompt": negative,
            "output": str(output.resolve()),
            "width": 1024,
            "height": 1024,
            "steps": 28,
            "guidance": 7.0,
        },
        timeout_sec=600,
    )
    elapsed = time.time() - t0

    if result and result.get("status") == "ok":
        print(f"  [vramd] ✓ {name}/albedo.png em {elapsed:.1f}s", flush=True)
        # Guardar sidecar JSON com metadados.
        meta = {
            "prompt": prompt,
            "negative_prompt": negative,
            "width": 1024,
            "height": 1024,
            "steps": 28,
            "guidance": 7.0,
            "seed": result.get("seed"),
            "regenerated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (mat_dir / "gen.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        return True
    else:
        error = result.get("error", "?") if result else "vramd não respondeu"
        print(f"  [vramd] ✗ {name}: {error}", flush=True)
        return False


# --- Pós-processamento por textura -------------------------------------------
# O difuso do texture2d chega correto na cor mas nem sempre no contraste/valor;
# estes passes são determinísticos e correm sobre o PNG já gerado, antes do
# Materialize. Sem eles: a areia é um lençol liso (std ≈ 7) e o brejo lê-se
# como praia clara. Ver public/world/context.md ("Chão e materiais").
POST: dict[str, str] = {
    # Realce de contraste LOCAL (ondulações), mantendo a média/cor da areia.
    "desert_sand": "local_contrast:3.2",
    # Escurecer e puxar para verde-lodo.
    "swamp_mud": "scale_rgb:0.56,0.62,0.50",
    # Cobble cinzento-neutro lia-se AZUL sob o IBL do céu (praça "de gelo").
    "cobblestone_road": "warm_stone",
}


def postprocess(name: str) -> None:
    """Aplica o passe de POST (se houver) ao difuso já gerado."""
    recipe = POST.get(name)
    if not recipe:
        return
    import numpy as np
    from PIL import Image, ImageFilter

    path = TEXTURES_DIR / name / "albedo.png"
    im = Image.open(path).convert("RGB")
    a = np.asarray(im).astype(np.float32)
    if recipe.startswith("local_contrast:"):
        amount = float(recipe.split(":", 1)[1])
        blur = np.asarray(im.filter(ImageFilter.GaussianBlur(12))).astype(np.float32)
        out = blur + (a - blur) * amount
    elif recipe.startswith("scale_rgb:"):
        factors = [float(v) for v in recipe.split(":", 1)[1].split(",")]
        out = a * np.array(factors, dtype=np.float32)
    elif recipe == "warm_stone":
        lum = a @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        warm = np.stack([lum * 1.06 + 6, lum * 0.99, lum * 0.88 - 4], axis=-1)
        out = warm * 0.82 + a * 0.18
    else:
        raise ValueError(f"POST desconhecido: {recipe}")
    Image.fromarray(np.clip(out, 0, 255).astype("uint8")).save(path)
    print(f"  [POST] {name}: {recipe}", flush=True)


def generate_pbr(name: str, preset: str) -> bool:
    """Gera PBR maps via Materialize. Retorna True se OK."""
    mat_dir = TEXTURES_DIR / name
    diffuse = mat_dir / "albedo.png"
    # O Materialize escreve `<stem>_<map>.png` ao lado do input: com o difuso
    # chamado `albedo.png`, os mapas saem `albedo_normal.png`, `albedo_ao.png`, …
    pbr_dir = mat_dir

    if not diffuse.exists():
        print(f"  [MAT] ✗ {name}: diffuse não encontrado", flush=True)
        return False

    # Limpar PBR antigo (o difuso de trabalho fica).
    for old in pbr_dir.glob("albedo_*.png"):
        old.unlink()

    print(f"  [MAT] A gerar PBR maps ({preset})...", flush=True)
    t0 = time.time()
    r = subprocess.run(
        ["materialize", str(diffuse), "-o", str(pbr_dir), "-p", preset, "--format", "png"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    elapsed = time.time() - t0

    if r.returncode == 0:
        maps = list(pbr_dir.glob("albedo_*.png"))
        print(f"  [MAT] ✓ {len(maps)} PBR maps em {elapsed:.1f}s", flush=True)
        return True
    else:
        print(f"  [MAT] ✗ {name}: {r.stderr[:200] if r.stderr else r.stdout[:200]}", flush=True)
        return False


def finalize(name: str) -> None:
    """Converte os PNGs de trabalho para WebP (esquema final por pasta).

    `albedo.png` → `albedo.webp` (q92, sRGB); `albedo_<map>.png` → `<map>.webp`
    (q95 — lossless em normais ruidosas quase não comprime, q95 é transparente).
    """
    from PIL import Image

    mat_dir = TEXTURES_DIR / name
    for png in sorted(mat_dir.glob("*.png")):
        if png.stem == "albedo":
            dst, quality = mat_dir / "albedo.webp", 92
        else:
            dst, quality = mat_dir / f"{png.stem.removeprefix('albedo_')}.webp", 95
        Image.open(png).save(dst, "WEBP", quality=quality, method=6)
        png.unlink()
        print(f"  [WEBP] {dst.relative_to(TEXTURES_DIR)}", flush=True)


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"Regeneração de texturas do simple-rpg ({len(SPECS)} texturas)")
    print(f"{'=' * 60}\n")

    ok_count = 0
    fail_count = 0

    for idx, (name, prompt, negative, preset) in enumerate(SPECS, 1):
        print(f"\n[{idx}/{len(SPECS)}] {name}", flush=True)

        # 1. Gerar diffuse via vramd.
        if not generate_texture(name, prompt, negative):
            fail_count += 1
            continue

        # 2. Pós-processamento determinístico (contraste/valor), se houver.
        postprocess(name)

        # 3. Gerar PBR maps via Materialize (a partir do difuso já tratado)
        #    e converter tudo para WebP.
        if generate_pbr(name, preset):
            finalize(name)
            ok_count += 1
        else:
            fail_count += 1

    print(f"\n{'=' * 60}")
    print(f"Concluído: {ok_count} OK, {fail_count} falhas")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
