#!/usr/bin/env python3
"""Re-game-pack + LOD ladder for simple-rpg enemies (clips → lod0/1/2).

Humanoids: Quaternius retarget (clean clip names).
Creatures: procedural pack + rename-clips to idle/walk/run/….
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MESHES = (Path(__file__).resolve().parent / "../public/assets/meshes").resolve()
INTER = MESHES / "_intermediate"
LOG = Path(__file__).resolve().parent / "regen_enemy_anims.log.jsonl"

ANIM = ROOT / "Animator3D" / ".venv" / "bin" / "animator3d"
TEXT3D = ROOT / "Text3D" / ".venv" / "bin" / "text3d"

# (id, game-pack mode)
ENEMIES: list[tuple[str, str]] = [
    ("goblin", "humanoid"),
    ("bandit", "humanoid"),
    ("bogling", "humanoid"),
    ("slime", "humanoid"),  # Quaternius (anão/chibi), not procedural creature
    ("wolf", "creature"),
    ("scorpion", "creature"),
]

CREATURE_RENAME = (
    "Animator3D_BreatheIdle:idle,"
    "Animator3D_Walk:walk,"
    "Animator3D_Run:run,"
    "Animator3D_Jump:jump,"
    "Animator3D_Attack:attack,"
    "Animator3D_Roar:roar,"
    "Animator3D_Hit:hit,"
    "Animator3D_Death:death"
)


def _run(argv: list[str], timeout: int = 900) -> tuple[bool, str]:
    print("  $", " ".join(argv), flush=True)
    try:
        p = subprocess.run(
            argv,
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return False, f"timeout: {exc}"
    out = ((p.stdout or "") + (p.stderr or "")).strip()
    if p.returncode != 0:
        return False, out[-2000:]
    return True, out[-500:]


def _glb_clip_names(path: Path) -> list[str]:
    import struct

    data = path.read_bytes()
    off = 12
    chunk_len, chunk_type = struct.unpack_from("<I4s", data, off)
    assert chunk_type == b"JSON", path
    js = json.loads(data[off + 8 : off + 8 + chunk_len])
    return [a.get("name") or "" for a in js.get("animations") or []]


def process_one(asset_id: str, mode: str) -> dict:
    rec: dict = {"id": asset_id, "mode": mode}
    rigged = INTER / f"{asset_id}_rigged.glb"
    animated = INTER / f"{asset_id}_rigged_animated.glb"
    if not rigged.is_file():
        rec["status"] = "error"
        rec["error"] = f"missing {rigged.name}"
        return rec

    # Fresh game-pack (overwrite animated).
    if animated.is_file():
        animated.unlink()

    if mode == "humanoid":
        ok, detail = _run(
            [
                str(ANIM),
                "game-pack",
                str(rigged),
                str(animated),
                "--preset",
                "humanoid",
                "--force-preset",
                "--clips",
                "idle,walk,run,jump,attack,hit,death",
            ]
        )
    else:
        ok, detail = _run(
            [
                str(ANIM),
                "game-pack",
                str(rigged),
                str(animated),
                "--preset",
                "creature",
                "--force-preset",
                "--procedural",
                "--clips",
                "idle,walk,run,jump,attack,hit,death,roar",
            ]
        )
        if ok and animated.is_file():
            renamed = animated.with_name(f"{asset_id}_rigged_animated_renamed.glb")
            ok2, detail2 = _run(
                [
                    str(ANIM),
                    "rename-clips",
                    str(animated),
                    str(renamed),
                    "--map",
                    CREATURE_RENAME,
                ]
            )
            detail = detail + "\n" + detail2
            if ok2 and renamed.is_file():
                shutil.move(str(renamed), str(animated))
            else:
                ok = False

    rec["game_pack_ok"] = ok
    rec["game_pack_detail"] = detail[-800:]
    if not ok or not animated.is_file():
        rec["status"] = "error"
        rec["error"] = "game-pack failed"
        return rec

    clips = _glb_clip_names(animated)
    rec["animated_clips"] = clips
    need = ("idle", "walk", "run", "attack", "hit", "death")
    missing = [c for c in need if c not in clips]
    if missing:
        rec["status"] = "error"
        rec["error"] = f"missing clips {missing}: {clips}"
        return rec

    # Rebuild LOD ladder from animated (preserves skins/clips).
    for n in (0, 1, 2):
        p = MESHES / f"{asset_id}_lod{n}.glb"
        if p.is_file():
            p.unlink()

    ok_lod, lod_detail = _run(
        [
            str(TEXT3D),
            "lod",
            str(animated),
            "-o",
            str(MESHES),
            "--basename",
            asset_id,
            "--lod1-ratio",
            "0.40",
            "--lod2-ratio",
            "0.22",
            "--min-faces-lod1",
            "500",
            "--min-faces-lod2",
            "150",
        ],
        timeout=1200,
    )
    rec["lod_ok"] = ok_lod
    rec["lod_detail"] = lod_detail[-800:]
    if not ok_lod:
        rec["status"] = "error"
        rec["error"] = "lod failed"
        return rec

    lod_clips = {}
    for n in (0, 1, 2):
        p = MESHES / f"{asset_id}_lod{n}.glb"
        lod_clips[f"lod{n}"] = _glb_clip_names(p) if p.is_file() else []
    rec["lod_clips"] = lod_clips
    if "idle" not in lod_clips.get("lod0", []):
        rec["status"] = "error"
        rec["error"] = f"lod0 missing idle: {lod_clips}"
        return rec

    rec["status"] = "ok"
    return rec


def main() -> int:
    if not ANIM.is_file():
        print(f"missing animator3d: {ANIM}", file=sys.stderr)
        return 1
    if not TEXT3D.is_file():
        print(f"missing text3d: {TEXT3D}", file=sys.stderr)
        return 1

    ids = [a for a in sys.argv[1:] if not a.startswith("-")]
    jobs = [(i, m) for i, m in ENEMIES if not ids or i in ids]
    print(f"enemies={len(jobs)} meshes={MESHES}", flush=True)

    ok_n = fail_n = 0
    t0 = time.time()
    with LOG.open("w", encoding="utf-8") as logf:
        for i, (asset_id, mode) in enumerate(jobs, 1):
            print(f"[{i}/{len(jobs)}] {asset_id} ({mode}) …", flush=True)
            t1 = time.time()
            rec = process_one(asset_id, mode)
            rec["elapsed_s"] = round(time.time() - t1, 1)
            if rec.get("status") == "ok":
                ok_n += 1
                print(
                    f"  OK {asset_id} clips={rec.get('animated_clips')} ({rec['elapsed_s']}s)",
                    flush=True,
                )
            else:
                fail_n += 1
                print(f"  FAIL {asset_id}: {rec.get('error')}", flush=True)
            logf.write(json.dumps(rec, ensure_ascii=False) + "\n")
            logf.flush()

    print(f"DONE ok={ok_n} fail={fail_n} total_s={time.time() - t0:.0f} log={LOG}", flush=True)
    return 1 if fail_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
