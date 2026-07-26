#!/usr/bin/env python3
"""Regenera TODOS os LOD0/1/2 do simple-rpg (sem re-paint / re-rig).

Bridge de intermediários legados (``*_lod0_animated`` → ``*_rigged_animated``)
para o DAG Round 3, apaga ``*_lod{0,1,2}.glb`` e corre só a stage LOD
(``run_master_pipeline`` salta rig/animate quando a fonte existe).
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # GameDev/
MANIFEST_DIR = Path(__file__).resolve().parent
MESHES = (MANIFEST_DIR / "../public/assets/meshes").resolve()
LOG = MANIFEST_DIR / "regen_all_lods.log.jsonl"
LOG_ANIMATED = MANIFEST_DIR / "regen_animated_lods.log.jsonl"


def _bridge_legacy_skin_sources(mesh_final: Path, asset_id: str) -> list[str]:
    """Copia legado → paths Round 3 se faltarem."""
    from gameassets.paths import _animated_path, _intermediate_dir, _rigged_path
    from gameassets.pipeline import _glb_has_skin

    notes: list[str] = []
    inter = _intermediate_dir(mesh_final)
    anim_dst = _animated_path(mesh_final)
    rig_dst = _rigged_path(mesh_final)

    anim_candidates = [
        inter / f"{asset_id}_rigged_animated.glb",
        inter / f"{asset_id}_lod0_animated.glb",
        mesh_final.parent / f"{asset_id}_lod0_animated.glb",
    ]
    if not anim_dst.is_file():
        for cand in anim_candidates:
            if cand.is_file() and cand.resolve() != anim_dst.resolve() and _glb_has_skin(cand):
                shutil.copy2(cand, anim_dst)
                notes.append(f"bridge {cand.name}→{anim_dst.name}")
                break

    rig_candidates = [
        inter / f"{asset_id}_rigged.glb",
        inter / f"{asset_id}_lod0_rigged.glb",
        mesh_final.parent / f"{asset_id}_lod0_rigged.glb",
    ]
    if not rig_dst.is_file():
        for cand in rig_candidates:
            if cand.is_file() and cand.resolve() != rig_dst.resolve() and _glb_has_skin(cand):
                shutil.copy2(cand, rig_dst)
                notes.append(f"bridge {cand.name}→{rig_dst.name}")
                break
    return notes


def _delete_lods(mesh_final: Path, asset_id: str) -> list[str]:
    """Apaga lod0/1/2 entregáveis + dirs stump/top_lod (árvores partidas)."""
    removed: list[str] = []
    parent = mesh_final.parent
    for n in (0, 1, 2):
        p = parent / f"{asset_id}_lod{n}.glb"
        if p.is_file():
            p.unlink()
            removed.append(p.name)
    inter = parent / "_intermediate"
    for dname in (f"{asset_id}_stump_lod", f"{asset_id}_top_lod"):
        d = inter / dname
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            removed.append(dname + "/")
    return removed


def _purge_stale_animated(mesh_final: Path, asset_id: str) -> list[str]:
    """Apaga animated intermediários (força re-game-pack com retarget actual)."""
    removed: list[str] = []
    inter = mesh_final.parent / "_intermediate"
    parent = mesh_final.parent
    candidates = [
        inter / f"{asset_id}_rigged_animated.glb",
        inter / f"{asset_id}_lod0_animated.glb",
        inter / f"{asset_id}_lod1_animated.glb",
        inter / f"{asset_id}_lod2_animated.glb",
        parent / f"{asset_id}_lod0_animated.glb",
    ]
    for p in candidates:
        if p.is_file():
            p.unlink()
            removed.append(p.name)
    return removed


def main() -> int:
    sys.path.insert(0, str(ROOT / "GameAssets" / "src"))
    from gameassets.manifest import load_manifest
    from gameassets.paths import _paths_for_row_manifest
    from gameassets.pipeline import _bin_or_none, _post_text3d_mesh_extras
    from gameassets.profile import load_profile
    from gameassets.resume_cmd import _row_wants_animate, _row_wants_rig
    from gameassets.ums_coord import apply_ums_child_env
    from gamedev_shared.env import subprocess_gpu_env
    from gamedev_shared.model_server import ensure_ums_running

    animated_only = "--animated-only" in sys.argv or "--only-animated" in sys.argv

    profile = load_profile(MANIFEST_DIR / "game.yaml")
    rows = [r for r in load_manifest(MANIFEST_DIR / "manifest.yaml") if r.generate_3d]
    has_rig_prof = profile.rigging3d is not None
    if animated_only:
        rows = [
            r
            for r in rows
            if _row_wants_rig(r, has_rig_prof) or _row_wants_animate(r, _row_wants_rig(r, has_rig_prof), has_rig_prof)
        ]
        print(f"filter --animated-only → {len(rows)} assets", flush=True)

    os.environ.setdefault("TEXT3D_BIN", str(ROOT / "Text3D" / ".venv" / "bin" / "text3d"))
    os.environ.setdefault("RIGGING3D_BIN", str(ROOT / "Rigging3D" / ".venv" / "bin" / "rigging3d"))
    os.environ.setdefault("ANIMATOR3D_BIN", str(ROOT / "Animator3D" / ".venv" / "bin" / "animator3d"))

    child_env = dict(subprocess_gpu_env())
    apply_ums_child_env(child_env, ums_stream=False, no_ums=False)
    try:
        ensure_ums_running()
    except Exception as exc:
        print(f"UMS warn: {exc}", flush=True)

    text3d = _bin_or_none("TEXT3D_BIN", "text3d")
    rig_bin = _bin_or_none("RIGGING3D_BIN", "rigging3d")
    anim_bin = _bin_or_none("ANIMATOR3D_BIN", "animator3d")
    log_path = LOG_ANIMATED if animated_only else LOG
    print(f"text3d={text3d}", flush=True)
    print(f"assets={len(rows)} meshes={MESHES} log={log_path.name}", flush=True)
    print("ids:", ", ".join(r.id for r in rows), flush=True)

    ok_n = fail_n = 0
    t0 = time.time()
    with log_path.open("w", encoding="utf-8") as logf:
        for i, row in enumerate(rows, 1):
            _img, mesh_final = _paths_for_row_manifest(profile, MANIFEST_DIR, row)
            wr = _row_wants_rig(row, has_rig_prof)
            wa = _row_wants_animate(row, wr, has_rig_prof)
            # Animated stale (root -90°X / loc_conv) NÃO pode ser bridged — purge primeiro.
            purged = _purge_stale_animated(mesh_final, row.id) if wa else []
            bridged = _bridge_legacy_skin_sources(mesh_final, row.id) if wr else []
            removed = _delete_lods(mesh_final, row.id)
            print(
                f"[{i}/{len(rows)}] {row.id} del={removed or '-'} purge_anim={purged or '-'} "
                f"bridge={bridged or '-'} rig={wr} anim={wa} …",
                flush=True,
            )
            rec: dict = {"id": row.id, "deleted": removed, "purged_anim": purged, "bridged": bridged}
            t1 = time.time()
            try:
                failed = _post_text3d_mesh_extras(
                    profile,
                    row,
                    mesh_final,
                    rec,
                    MANIFEST_DIR,
                    child_env,
                    rig_bin,
                    with_rig=wr,
                    with_animate=wa,
                    animator3d_bin=anim_bin,
                    has_rigging_profile=has_rig_prof,
                    with_lod=bool(row.generate_lod),
                    with_collision=bool(row.generate_collision),
                    with_validate=False,
                )
            except Exception as exc:
                failed = True
                rec["status"] = "error"
                rec["error"] = f"{exc}\n{traceback.format_exc()[-800:]}"
            dt = time.time() - t1
            rec["elapsed_s"] = round(dt, 1)
            # Detect accidental re-rig (should be skip if bridge worked).
            stages = rec.get("stages") or []
            if isinstance(stages, list):
                for s in stages:
                    if (
                        isinstance(s, dict)
                        and s.get("name") == "rigging3d"
                        and "skipped" not in str(s.get("detail") or s.get("error") or "")
                        and s.get("ok")
                        and float(s.get("elapsed_s") or 0) > 5
                    ):
                        print(f"  WARN {row.id}: rigging3d ran ({s.get('elapsed_s')}s)", flush=True)
            if failed:
                fail_n += 1
                print(f"  FAIL {row.id}: {str(rec.get('error', ''))[:220]}", flush=True)
            else:
                ok_n += 1
                print(f"  OK {row.id} ({dt:.0f}s)", flush=True)
            logf.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            logf.flush()

    print(
        f"DONE ok={ok_n} fail={fail_n} total_s={time.time() - t0:.0f} log={log_path}",
        flush=True,
    )
    return 1 if fail_n else 0


if __name__ == "__main__":
    raise SystemExit(main())
