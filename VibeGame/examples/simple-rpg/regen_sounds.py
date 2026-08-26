#!/usr/bin/env python3
"""Regenera os 22 sons do simple-rpg com Stable Audio 3 Small (2026-08).

Era SA3: sem flags de steps/cfg/crop da era Open (o quality tier `high` +
ModelSpec resolvem steps 16, cfg 1.0, pingpong + mastering). BGM usa o
pipeline seamless exacto (kind music_loop via --category humanoid: crossfade
equal-power, edge trim adaptativo, comprimento final = -d, mastering em
buffer dobrado). Durações BGM múltiplo de 2 s @ 120 BPM = compassos exatos.

Uso:
    cd Text2Sound && .venv/bin/python /path/to/regen_sounds.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

AUDIO_DIR = Path("/home/maikeu/GitClones/AiGameKit/VibeGame/examples/simple-rpg/public/assets/audio")
T2S = [".venv/bin/python", "-m", "text2sound", "generate"]


def _probe_duration(path: Path) -> float | None:
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        return float(r.stdout.strip()) if r.returncode == 0 else None
    except (OSError, ValueError):
        return None


# (filename, prompt, category, profile, duration, seed)
# category → audio_kind → trim/negative/compressor automáticos.
# sfx_player_hurt SEM category: "humanoid" mapeia para music_loop (modelo
# música) — grunts pertencem ao modelo sfx (bug latente da era Open).
SPECS: list[tuple[str, str, str | None, str, float, int]] = [
    # ── BGM (music_loop via humanoid: seamless exacto, loop final = -d) ──
    (
        "bgm_battle",
        "Intense fantasy battle music, driving orchestral combat theme, dramatic action rhythm, 120 BPM",
        "humanoid",
        "music",
        32.0,  # 16 compassos @120bpm
        220,
    ),
    (
        "bgm_explore",
        (
            "Peaceful fantasy village exploration music, gentle acoustic guitar and flute, "
            "warm ambient adventure melody, calm countryside, 120 BPM"
        ),
        "humanoid",
        "music",
        40.0,  # 20 compassos @120bpm
        300,
    ),
    # ── Combat SFX ────────────────────────────────────────────────────────
    (
        "sfx_hit",
        "Sharp metal sword clash, bright metallic ring, short combat hit impact",
        "weapon",
        "effects",
        2.0,
        201,
    ),
    (
        "sfx_swing",
        "Whoosh sword swing through air, quick blade swish, fast weapon swing",
        "weapon",
        "effects",
        0.5,
        401,
    ),
    # ── Gathering SFX ─────────────────────────────────────────────────────
    (
        "sfx_chop_hit",
        "Sharp axe chop into wood tree trunk, heavy blade impact on timber",
        "weapon",
        "effects",
        1.0,
        302,
    ),
    (
        "sfx_chop_break",
        "Large tree falling crashing down, timber cracking and splintering",
        "weapon",
        "effects",
        2.0,
        303,
    ),
    (
        "sfx_mine_hit",
        "Pickaxe striking stone rock, hard mineral impact, mining chisel hit on ore, sharp crack",
        "weapon",
        "effects",
        1.0,
        301,
    ),
    (
        "sfx_mine_break",
        "Rock crumbling apart, stone debris falling, boulder breaking into fragments",
        "weapon",
        "effects",
        1.5,
        304,
    ),
    # ── Level up / progression ───────────────────────────────────────────
    (
        "sfx_levelup",
        "Triumphant level up fanfare, bright ascending victory chime, cheerful RPG success jingle",
        "item",
        "effects",
        2.0,
        400,
    ),
    # ── Vocais / criaturas ───────────────────────────────────────────────
    (
        "sfx_player_hurt",
        "Painful grunt, male warrior taking damage, sharp exhale of pain, short hurt vocalization",
        None,
        "effects",
        1.0,
        501,
    ),
    (
        "sfx_enemy_hurt",
        "Creature taking damage yelp, short monster pain cry, wounded beast whimper",
        "creature",
        "effects",
        0.8,
        502,
    ),
    (
        "sfx_enemy_death",
        "Creature death wail, fading monster cry, dying beast collapse, long final groan",
        "creature",
        "effects",
        2.0,
        503,
    ),
    (
        "sfx_boss_roar",
        (
            "Massive boss monster roar, deep terrifying beast bellow, "
            "giant creature threatening growl, powerful boss entrance"
        ),
        "creature",
        "effects",
        3.0,
        504,
    ),
    # ── Magia / UI / itens ───────────────────────────────────────────────
    (
        "sfx_heal",
        "Warm healing magic spell, gentle restoration chime, soft glowing energy swell, soothing recovery aura",
        "effects",
        "effects",
        2.0,
        505,
    ),
    (
        "sfx_shop_open",
        "Friendly shop door opening chime, welcoming merchant bell, cozy tavern door creak, warm marketplace greeting",
        "ui",
        "effects",
        1.5,
        506,
    ),
    (
        "sfx_buy",
        "Satisfying purchase confirmation, coin transaction chime, merchant deal struck, bright purchase success tone",
        "ui",
        "effects",
        1.0,
        507,
    ),
    (
        "sfx_error",
        "Negative error buzz, declining tone, low rejected action sound, descending failure blip, interface denial",
        "ui",
        "effects",
        0.8,
        508,
    ),
    (
        "sfx_coin",
        "Sparkling coin pickup chime, bright golden coin collect, rewarding currency jingle, short treasure acquire",
        "item",
        "effects",
        1.0,
        509,
    ),
    (
        "sfx_item_drop",
        "Item dropping on ground, object landing on stone floor, dull thud with bounce, inventory item fall",
        "weapon",
        "effects",
        1.0,
        510,
    ),
    (
        "sfx_bomb_drop",
        "Bomb fuse igniting, sizzling explosive timer, crackling fuse burn, tense countdown hiss",
        "prop",
        "effects",
        1.0,
        511,
    ),
    (
        "sfx_save",
        "Quick save confirmation chime, short success blip, game saved jingle, bright digital confirm",
        "ui",
        "effects",
        0.8,
        512,
    ),
    (
        "sfx_load",
        "Game load whoosh, data loading shimmer, short magical loading sparkle, transition swirl",
        "ui",
        "effects",
        0.8,
        513,
    ),
]


def _tail_head_pct(path: Path) -> tuple[float, float] | None:
    """(RMS cauda 500ms, RMS início 500ms) em % da mediana do corpo."""
    try:
        import numpy as np
        import soundfile as sf

        data, sr = sf.read(str(path))
        m = data.mean(axis=1)
        w = int(sr * 0.1)
        prof = np.sqrt((m[: len(m) // w * w].reshape(-1, w) ** 2).mean(axis=1))
        body = float(np.median(prof))
        if body <= 0:
            return None
        return float(prof[-5:].mean() / body * 100), float(prof[:5].mean() / body * 100)
    except Exception:
        return None


def main() -> int:
    # argv[1] opcional: "sfx" (só efeitos) ou "bgm" (só música) — p.ex. para
    # re-passar só os SFX depois de afinar thresholds de trim por kind.
    only = sys.argv[1] if len(sys.argv) > 1 else None
    specs = [s for s in SPECS if only is None or (only == "sfx") == (s[3] != "music")]

    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    ok, fail = 0, 0
    total = len(specs)
    start_all = time.time()

    for idx, (name, prompt, category, profile, duration, seed) in enumerate(specs, 1):
        out = AUDIO_DIR / f"{name}.ogg"
        cat_label = category or "-"
        print(f"\n[{idx}/{total}] {name} ({profile}/{cat_label}, {duration}s, seed {seed})")

        cmd = [
            *T2S,
            prompt,
            "--profile",
            profile,
            "--quality",
            "high",
            "--no-enhance",  # prompts curados
            # prioridade interactiva (default): o scheduler vramd intercala
            # estes jobs curtos (~10s) à frente do batch por design (cuts<=3),
            # sem partir a wave text3d em curso.
            "--vramd-priority",
            "interactive",
            "--duration",
            str(duration),
            "--seed",
            str(seed),
            "-f",
            "ogg",
            "-o",
            str(out),
        ]
        if category:
            cmd.extend(["--category", category])

        # BGM: gate de qualidade — outro musical fundo (cauda <70% do corpo)
        # re-rola com seed+1000 (o outro varia por geração; reserve fixo não converge).
        attempts = 3 if profile == "music" else 1
        gen_seed = seed
        accepted = False
        for _attempt in range(attempts):
            cmd[cmd.index("--seed") + 1] = str(gen_seed)
            t0 = time.time()
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            elapsed = time.time() - t0
            if result.returncode != 0:
                break
            th = _tail_head_pct(out) if profile == "music" else None
            if profile != "music" or (th and th[0] >= 70.0 and th[1] >= 60.0):
                accepted = True
                break
            print(f"  ↻ seed {gen_seed}: cauda={th[0]:.0f}% início={th[1]:.0f}% — re-roll")
            gen_seed += 1000

        if accepted or (result.returncode == 0 and profile != "music"):
            dur = _probe_duration(out)
            # SA3 respeita -d: SFX (trim ON) pode vir mais curto; BGM seamless
            # aterra exacto em -d. Falhar alto se algo estiver grotescamente off.
            if profile == "music":
                good = dur is not None and abs(dur - duration) <= 0.25
            else:
                good = dur is not None and dur <= duration + 0.5
            if good:
                ok += 1
                print(f"  ✓ {out.stat().st_size} bytes in {elapsed:.1f}s ({dur:.2f}s)")
            else:
                fail += 1
                print(f"  ✗ duração fora do esperado: {dur}s (pedido {duration}s)")
        elif result.returncode != 0:
            fail += 1
            print(f"  ✗ FAILED ({elapsed:.1f}s)")
            print(f"    stderr: {result.stderr[-300:]}")
        else:
            fail += 1
            th = _tail_head_pct(out)
            print(f"  ✗ gate de loop: cauda/início abaixo do limiar após {attempts} seeds ({th})")

    total_time = time.time() - start_all
    print(f"\n{'═' * 60}")
    print(f"Regeneração completa: {ok}/{total} OK, {fail} falhas em {total_time:.0f}s")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
