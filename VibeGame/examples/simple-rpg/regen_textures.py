#!/usr/bin/env python3
"""Regenera todas as texturas do simple-rpg via UMS (texture2d) + Materialize (PBR).

9 texturas:
  Ground/Terrain: vale_grass, forest_floor, desert_sand, swamp_mud, snow_peak, mountain_stone
  Props/Materiais: wood_planks, wall_plaster, roof_tiles

Cada textura:
  1. Gerada via UMS (texture2d com group_offload auto na RTX 4050)
  2. PBR maps gerados via Materialize (height, normal, metallic, smoothness, edge, AO)
"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from aigamekit_shared.model_server import delegate_to_ums

TEXTURES_DIR = Path(__file__).parent / "public" / "assets" / "textures"

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
        "seamless tileable texture, dark mossy forest floor with fallen leaves, twigs and roots, "
        "rich organic soil, damp earth, top-down flat view, game asset",
        "grass, clean, polished, sand, desert, seams, borders",
        "default",
    ),
    (
        "desert_sand",
        "seamless tileable texture, golden desert sand with small rocks and cracks, wind-rippled dune sand, "
        "dry arid ground, top-down flat view, game asset",
        "rocks, grass, water, snow, seams, borders",
        "stone",
    ),
    (
        "swamp_mud",
        "seamless tileable texture, murky dark swamp mud with algae patches, wet slippery ground, "
        "decaying vegetation, top-down flat view, game asset",
        "grass, clean, polished, sand, seams, borders",
        "default",
    ),
    (
        "snow_peak",
        "seamless tileable texture, white snow with ice crystals and small rocks, mountain peak surface, "
        "frozen ground, frost, top-down flat view, game asset",
        "smooth, polished, grass, green, seams, borders",
        "stone",
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
    """Gera uma textura via UMS (texture2d). Retorna True se OK."""
    output = TEXTURES_DIR / f"{name}.png"
    print(f"  [UMS] A gerar {name}.png...", flush=True)

    t0 = time.time()
    result = delegate_to_ums(
        "texture2d",
        {
            "prompt": prompt,
            "negative_prompt": negative,
            "output": str(output.resolve()),
            "width": 1024,
            "height": 1024,
            "steps": 28,
            "guidance": 3.5,
        },
        timeout_sec=600,
    )
    elapsed = time.time() - t0

    if result and result.get("status") == "ok":
        print(f"  [UMS] ✓ {name}.png em {elapsed:.1f}s", flush=True)
        # Guardar sidecar JSON com metadados.
        meta = {
            "prompt": prompt,
            "negative_prompt": negative,
            "width": 1024,
            "height": 1024,
            "steps": 28,
            "guidance": 3.5,
            "seed": result.get("seed"),
            "regenerated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        (TEXTURES_DIR / f"{name}.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        return True
    else:
        error = result.get("error", "?") if result else "UMS não respondeu"
        print(f"  [UMS] ✗ {name}: {error}", flush=True)
        return False


def generate_pbr(name: str, preset: str) -> bool:
    """Gera PBR maps via Materialize. Retorna True se OK."""
    diffuse = TEXTURES_DIR / f"{name}.png"
    pbr_dir = TEXTURES_DIR / f"pbr_{name}"

    if not diffuse.exists():
        print(f"  [MAT] ✗ {name}: diffuse não encontrado", flush=True)
        return False

    # Limpar PBR antigo.
    if pbr_dir.exists():
        for old in pbr_dir.glob("*.png"):
            old.unlink()
    else:
        pbr_dir.mkdir(parents=True, exist_ok=True)

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
        maps = list(pbr_dir.glob("*.png"))
        print(f"  [MAT] ✓ {len(maps)} PBR maps em {elapsed:.1f}s", flush=True)
        return True
    else:
        print(f"  [MAT] ✗ {name}: {r.stderr[:200] if r.stderr else r.stdout[:200]}", flush=True)
        return False


def main() -> None:
    print(f"\n{'=' * 60}")
    print(f"Regeneração de texturas do simple-rpg ({len(SPECS)} texturas)")
    print(f"{'=' * 60}\n")

    ok_count = 0
    fail_count = 0

    for idx, (name, prompt, negative, preset) in enumerate(SPECS, 1):
        print(f"\n[{idx}/{len(SPECS)}] {name}", flush=True)

        # 1. Gerar diffuse via UMS.
        if not generate_texture(name, prompt, negative):
            fail_count += 1
            continue

        # 2. Gerar PBR maps via Materialize.
        if generate_pbr(name, preset):
            ok_count += 1
        else:
            fail_count += 1

    print(f"\n{'=' * 60}")
    print(f"Concluído: {ok_count} OK, {fail_count} falhas")
    print(f"{'=' * 60}\n")


if __name__ == "__main__":
    main()
