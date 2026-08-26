#!/usr/bin/env python3
"""Regenera os 12 sons do simple-racer com Stable Audio 3 Small (2026-08).

Prompts limpos (os sidecars antigos traziam detritos do enhancer da era Open
— ex. sfx_skid com "footsteps on grass" misturado). BGM usa o pipeline
seamless exacto (kind music_loop via --category humanoid). Durações BGM
múltiplo de 2 s @ 120 BPM = compassos exatos.

Uso:
    cd Text2Sound && .venv/bin/python /path/to/regen_sounds.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

AUDIO_DIR = Path("/home/maikeu/GitClones/AiGameKit/VibeGame/examples/simple-racer/public/assets/audio")
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
SPECS: list[tuple[str, str, str, str, float, int]] = [
    # ── BGM (music_loop via humanoid: seamless exacto, loop final = -d) ──
    (
        "bgm_menu",
        (
            "Cheerful arcade racing menu music, light synth pop, playful and relaxed, "
            "retro racing game lobby, warm synth pads, 120 BPM"
        ),
        "humanoid",
        "music",
        24.0,  # 12 compassos @120bpm
        320,
    ),
    (
        "bgm_race",
        (
            "Upbeat arcade kart racing music, energetic synth rock, fast driving tempo, "
            "pumping bass line, catchy electric guitar melody, exciting race atmosphere, 120 BPM"
        ),
        "humanoid",
        "music",
        32.0,  # 16 compassos @120bpm
        310,
    ),
    # ── SFX ───────────────────────────────────────────────────────────────
    (
        "sfx_coin",
        "Bright coin pickup chime, sparkling collectible jingle, short rewarding sparkle sound",
        "item",
        "effects",
        0.8,
        307,
    ),
    (
        "sfx_countdown",
        "Single short countdown beep, clean digital tone, race start ready signal, brief electronic beep",
        "ui",
        "effects",
        0.6,
        305,
    ),
    (
        "sfx_crash",
        "Kart crash impact, metal scrape and smash, car collision bang with debris rattle, short heavy crash",
        "weapon",
        "effects",
        2.0,
        304,
    ),
    (
        "sfx_engine_rev",
        (
            "Arcade race car engine revving up, high revving kart motor roar, "
            "aggressive throttle blip, mechanical engine growl"
        ),
        "vehicle",
        "effects",
        2.5,
        301,
    ),
    (
        "sfx_finish",
        "Triumphant race finish fanfare, short victory jingle, celebratory brass melody, crossing the finish line",
        "item",
        "effects",
        4.0,
        309,
    ),
    (
        "sfx_go",
        "Loud race start horn blast, GO signal klaxon, powerful air horn, short urgent starting signal",
        "ui",
        "effects",
        1.0,
        306,
    ),
    (
        "sfx_lap",
        "Soft lap completion chime, pleasant two-tone bell, checkpoint confirm sound, gentle success ping",
        "ui",
        "effects",
        1.0,
        308,
    ),
    (
        "sfx_nitro",
        "Nitro boost activation whoosh, powerful rocket-like surge, jet burst hiss with rising pitch, turbo power up",
        "vehicle",
        "effects",
        2.0,
        303,
    ),
    (
        "sfx_skid",
        "Tire skid squeal on asphalt, sharp rubber screech, kart drift brake sound, short sliding friction squeak",
        "vehicle",
        "effects",
        1.5,
        302,
    ),
    # save/load eram cópias do simple-rpg (md5 igual) — passam a ter os seus próprios
    (
        "sfx_save",
        "Quick save confirmation chime, short success blip, game saved jingle, bright digital confirm",
        "ui",
        "effects",
        0.8,
        412,
    ),
    (
        "sfx_load",
        "Game load whoosh, data loading shimmer, short magical loading sparkle, transition swirl",
        "ui",
        "effects",
        0.8,
        413,
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
            "--category",
            category,
        ]

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
