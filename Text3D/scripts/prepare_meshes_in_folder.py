#!/usr/bin/env python3
"""Aplica ``prepare_mesh_topology`` a todos os ``*.glb`` em cada pasta indicada (in-place).

Uso::
    python scripts/prepare_meshes_in_folder.py /caminho/para/meshes [/outra/pasta ...]

Requer o pacote ``text3d`` instalado (``pip install -e .`` a partir de ``Text3D/``).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from text3d.utils.export import _export_glb_with_normals, _load_as_trimesh  # noqa: E402
from text3d.utils.mesh_repair import prepare_mesh_topology  # noqa: E402


def _process_one(glb: Path, *, dry_run: bool) -> tuple[bool, str]:
    try:
        raw = _load_as_trimesh(glb)
        fixed = prepare_mesh_topology(raw)
    except Exception as e:
        return False, f"erro: {e}"
    if dry_run:
        return True, f"ok (dry-run) faces {len(raw.faces)} -> {len(fixed.faces)}"
    tmp = glb.with_suffix(".glb._tmp")
    try:
        _export_glb_with_normals(fixed, tmp)
        tmp.replace(glb)
    except Exception:
        if tmp.exists():
            tmp.unlink(missing_ok=True)
        raise
    return True, f"faces {len(raw.faces)} -> {len(fixed.faces)}"


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "directories",
        nargs="+",
        type=Path,
        help="Pastas com ficheiros .glb",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Só valida carga e preparação, não escreve ficheiros",
    )
    args = p.parse_args()

    ok = 0
    fail = 0
    for d in args.directories:
        d = d.resolve()
        if not d.is_dir():
            print(f"[skip] não é pasta: {d}", file=sys.stderr)
            fail += 1
            continue
        glbs = sorted(d.glob("*.glb"))
        if not glbs:
            print(f"[skip] sem .glb: {d}", file=sys.stderr)
            continue
        print(f"=== {d} ({len(glbs)} ficheiros) ===")
        for glb in glbs:
            success, msg = _process_one(glb, dry_run=args.dry_run)
            if success:
                ok += 1
                print(f"  [ok] {glb.name} — {msg}")
            else:
                fail += 1
                print(f"  [!!] {glb.name} — {msg}", file=sys.stderr)
    print(f"\nResumo: {ok} ok, {fail} falhas/skips")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
