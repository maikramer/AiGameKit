#!/usr/bin/env python3
"""Regenera todos os 24 sons do simple-rpg com enhance + mastering + negative prompt.

Os 9 sons com metadata reusam seed/prompt original; os 15 sem metadata recebem
prompts curados derivados do contexto de uso no jogo (sounds.ts + call sites).

Uso:
    cd Text2Sound && .venv/bin/python /path/to/regen_sounds.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

AUDIO_DIR = Path("/home/maikeu/GitClones/GameDev/VibeGame/examples/simple-rpg/public/assets/audio")
T2S = [".venv/bin/python", "-m", "text2sound", "generate"]

# Cada spec: (filename, prompt, category, profile, duration, seed, extra_flags)
# category → audio_kind → negative prompt + compressor preset automáticos.
# quality high → LUFS -15, enhance ON, mastering chain.
SPECS: list[tuple[str, str, str, str, float, int, list[str]]] = [
    # ── BGM (profile music, seamless loop, long) ──────────────────────────
    (
        "bgm_battle",
        "intense fantasy battle music, driving orchestral combat theme, dramatic action rhythm",
        "humanoid", "music", 47.0, 220,
        ["--cfg-scale", "6.0", "--seamless-loop", "--loop-edge-trim", "8.0", "--crossfade-ms", "2000"],
    ),
    (
        "bgm_explore",
        "peaceful fantasy village exploration music, gentle acoustic guitar and flute, warm ambient adventure melody, calm countryside",
        "humanoid", "music", 47.0, 300,
        ["--cfg-scale", "7.0", "--seamless-loop", "--loop-edge-trim", "4.0", "--crossfade-ms", "2000"],
    ),
    # ── Combat SFX (profile effects) ──────────────────────────────────────
    (
        "sfx_hit",
        "sharp metal sword clash, bright metallic ring, short combat hit impact",
        "weapon", "effects", 2.0, 201,
        ["--steps", "32", "--cfg-scale", "6.0"],
    ),
    (
        "sfx_swing",
        "whoosh sword swing through air, quick blade swish, fast weapon swing",
        "weapon", "effects", 0.5, 401,
        ["--steps", "8", "--cfg-scale", "1.0", "--crop", "--fade-out", "0.06"],
    ),
    # ── Gathering SFX ─────────────────────────────────────────────────────
    (
        "sfx_chop_hit",
        "sharp axe chop into wood tree trunk, heavy blade impact on timber",
        "weapon", "effects", 1.0, 302,
        ["--steps", "8", "--cfg-scale", "1.0", "--crop", "--fade-out", "0.06"],
    ),
    (
        "sfx_chop_break",
        "large tree falling crashing down, timber cracking and splintering",
        "weapon", "effects", 2.0, 303,
        ["--steps", "8", "--cfg-scale", "1.0", "--crop"],
    ),
    (
        "sfx_mine_hit",
        "pickaxe striking stone rock, hard mineral impact, mining chisel hit on ore, sharp crack",
        "weapon", "effects", 1.0, 301,
        ["--steps", "8", "--cfg-scale", "1.0", "--crop", "--fade-out", "0.06"],
    ),
    (
        "sfx_mine_break",
        "rock crumbling apart, stone debris falling, boulder breaking into fragments",
        "weapon", "effects", 1.5, 304,
        ["--steps", "8", "--cfg-scale", "1.0", "--crop"],
    ),
    # ── Level up / progression ────────────────────────────────────────────
    (
        "sfx_levelup",
        "triumphant level up fanfare, bright ascending victory chime, cheerful RPG success jingle",
        "item", "effects", 2.0, 400,
        ["--steps", "80", "--cfg-scale", "1.0", "--crop"],
    ),
    # ── SFX sem metadata — prompts curados do contexto do jogo ────────────
    (
        "sfx_player_hurt",
        "painful grunt, male warrior taking damage, sharp exhale of pain, short hurt vocalization",
        "humanoid", "effects", 1.0, 501,
        ["--steps", "12", "--crop"],
    ),
    (
        "sfx_enemy_hurt",
        "creature taking damage yelp, short monster pain cry, wounded beast whimper",
        "creature", "effects", 0.8, 502,
        ["--steps", "12", "--crop"],
    ),
    (
        "sfx_enemy_death",
        "creature death wail, fading monster cry, dying beast collapse, long final groan",
        "creature", "effects", 2.0, 503,
        ["--steps", "12", "--crop"],
    ),
    (
        "sfx_boss_roar",
        "massive boss monster roar, deep terrifying beast bellow, giant creature threatening growl, powerful boss entrance",
        "creature", "effects", 3.0, 504,
        ["--steps", "20", "--cfg-scale", "3.0", "--crop"],
    ),
    (
        "sfx_heal",
        "warm healing magic spell, gentle restoration chime, soft glowing energy swell, soothing recovery aura",
        "effects", "effects", 2.0, 505,
        ["--steps", "20", "--crop"],
    ),
    (
        "sfx_shop_open",
        "friendly shop door opening chime, welcoming merchant bell, cozy tavern door creak, warm marketplace greeting",
        "ui", "effects", 1.5, 506,
        ["--steps", "16", "--crop"],
    ),
    (
        "sfx_buy",
        "satisfying purchase confirmation, coin transaction chime, merchant deal struck, bright purchase success tone",
        "ui", "effects", 1.0, 507,
        ["--steps", "16", "--crop"],
    ),
    (
        "sfx_error",
        "negative error buzz, declining tone, low rejected action sound, descending failure blip, interface denial",
        "ui", "effects", 0.8, 508,
        ["--steps", "16", "--crop"],
    ),
    (
        "sfx_coin",
        "sparkling coin pickup chime, bright golden coin collect, rewarding currency jingle, short treasure acquire",
        "item", "effects", 1.0, 509,
        ["--steps", "16", "--crop"],
    ),
    (
        "sfx_item_drop",
        "item dropping on ground, object landing on stone floor, dull thud with bounce, inventory item fall",
        "weapon", "effects", 1.0, 510,
        ["--steps", "12", "--crop"],
    ),
    (
        "sfx_bomb_drop",
        "bomb fuse igniting, sizzling explosive timer, crackling fuse burn, tense countdown hiss",
        "prop", "effects", 1.0, 511,
        ["--steps", "12", "--crop"],
    ),
    (
        "sfx_save",
        "quick save confirmation chime, short success blip, game saved jingle, bright digital confirm",
        "ui", "effects", 0.8, 512,
        ["--steps", "16", "--crop"],
    ),
    (
        "sfx_load",
        "game load whoosh, data loading shimmer, short magical loading sparkle, transition swirl",
        "ui", "effects", 0.8, 513,
        ["--steps", "16", "--crop"],
    ),
]


def main() -> int:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    total = len(SPECS)
    start_all = time.time()

    for idx, (name, prompt, category, profile, duration, seed, extra) in enumerate(SPECS, 1):
        out = AUDIO_DIR / f"{name}.ogg"
        print(f"\n[{idx}/{total}] {name} ({profile}/{category}, {duration}s, seed {seed})")
        print(f"  prompt: {prompt[:80]}...")

        cmd = T2S + [
            prompt,
            "--profile", profile,
            "--quality", "high",
            "--category", category,
            "--duration", str(duration),
            "--seed", str(seed),
            "-f", "ogg",
            "-o", str(out),
        ] + extra

        t0 = time.time()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        elapsed = time.time() - t0

        if result.returncode == 0:
            ok += 1
            size = out.stat().st_size if out.exists() else 0
            print(f"  ✓ {size} bytes in {elapsed:.1f}s")
        else:
            fail += 1
            print(f"  ✗ FAILED ({elapsed:.1f}s)")
            print(f"    stderr: {result.stderr[-300:]}")

    total_time = time.time() - start_all
    print(f"\n{'═' * 60}")
    print(f"Regeneração completa: {ok}/{total} OK, {fail} falhas em {total_time:.0f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
