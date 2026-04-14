#!/usr/bin/env python3
"""
Correção de origem para GLBs usados no exemplo simple-rpg (motor Y-up, “pés” na base).

Delega no Blender: ``VibeGame/tools/blender_reorigin_glb_feet.py``
(import glTF Y-up → Blender Z-up; por defeito a base da AABB usa o eixo **Z** no Blender).

Uso (Python normal, não o bpy):

  python3 scripts/fix-glb-yup-feet.py path/to/a.glb [outro.glb ...]

  python3 scripts/fix-glb-yup-feet.py \\
    public/assets/models/crystal_blue.glb \\
    public/assets/models/wooden_crate.glb

Se a **base estiver virada para a frente** (+Z) em vez de assentar no chão (−Y),
experimenta **+90° em X** no Blender (e depois assentar em Y no glTF):

  python3 scripts/fix-glb-yup-feet.py public/assets/models/algo.glb --rotate 90 0 0

(são graus nos eixos globais X, Y, Z no Blender, após import glTF e antes de repor a origem.)

Após o Blender, o wrapper **re-centra em XZ** e põe **min Y = 0** no espaço glTF (trimesh).

Requer ``blender`` no PATH (ex.: /snap/bin/blender) e ``trimesh`` para o passo glTF.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def _repo_tools_script() -> Path:
    here = Path(__file__).resolve()
    vibr = here.parents[3]  # .../VibeGame
    return vibr / "tools" / "blender_reorigin_glb_feet.py"


def _align_feet_y_gltf(path: Path) -> None:
    """Base da AABB em Y=0 e centro em XZ (espaço glTF / Three.js Y-up)."""
    import trimesh

    loaded = trimesh.load(str(path), force="scene")
    if isinstance(loaded, trimesh.Scene):
        for geom in loaded.geometry.values():
            b = geom.bounds
            cx = (b[0][0] + b[1][0]) * 0.5
            cy = float(b[0][1])
            cz = (b[0][2] + b[1][2]) * 0.5
            geom.apply_translation([-cx, -cy, -cz])
        loaded.export(str(path))
        return
    if isinstance(loaded, trimesh.Trimesh):
        b = loaded.bounds
        cx = (b[0][0] + b[1][0]) * 0.5
        cy = float(b[0][1])
        cz = (b[0][2] + b[1][2]) * 0.5
        loaded.apply_translation([-cx, -cy, -cz])
        loaded.export(str(path))
        return
    raise TypeError(f"Formato inesperado: {type(loaded)}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "glbs",
        nargs="+",
        type=Path,
        help="Ficheiros .glb a corrigir (in-place).",
    )
    ap.add_argument(
        "--axis",
        choices=("Z", "Y", "X"),
        default="Z",
        help="Eixo vertical no Blender para a base da AABB (glTF import: Z por defeito).",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Apenas import/export em memória no Blender (não substitui os GLB).",
    )
    ap.add_argument(
        "--blender",
        default=os.environ.get("BLENDER_BIN", "blender"),
        help="Executável Blender (ou env BLENDER_BIN).",
    )
    ap.add_argument(
        "--rotate",
        nargs=3,
        type=float,
        default=None,
        metavar=("RX", "RY", "RZ"),
        help="Euler XYZ em graus (Blender), repassado a blender_reorigin_glb_feet.py --rotate.",
    )
    ap.add_argument(
        "--no-gltf-feet",
        action="store_true",
        help="Não aplicar min Y=0 / centro XZ em espaço glTF após o Blender.",
    )
    args = ap.parse_args()

    blender_bin = shutil.which(args.blender) or args.blender
    if not Path(blender_bin).is_file() and not shutil.which(args.blender):
        print(f"Blender não encontrado: {args.blender}", file=sys.stderr)
        return 2

    bpy_script = _repo_tools_script()
    if not bpy_script.is_file():
        print(f"Script Blender em falta: {bpy_script}", file=sys.stderr)
        return 2

    glbs: list[Path] = []
    for g in args.glbs:
        p = g.expanduser().resolve()
        if not p.is_file():
            print(f"Ficheiro inexistente: {p}", file=sys.stderr)
            return 1
        if p.suffix.lower() != ".glb":
            print(f"Esperado .glb: {p}", file=sys.stderr)
            return 1
        glbs.append(p)

    argv = [
        blender_bin,
        "--background",
        "--python",
        str(bpy_script),
        "--",
    ]
    for p in glbs:
        argv.extend(["--only", str(p)])
    argv.extend(["--axis", args.axis])
    if args.rotate is not None:
        argv.extend(
            ["--rotate", str(args.rotate[0]), str(args.rotate[1]), str(args.rotate[2])]
        )
    if args.dry_run:
        argv.append("--dry-run")

    print("[fix-glb-yup-feet]", " ".join(argv), flush=True)
    proc = subprocess.run(argv, check=False)
    if proc.returncode != 0 or args.dry_run or args.no_gltf_feet:
        return proc.returncode

    try:
        import trimesh  # noqa: F401
    except ImportError:
        print(
            "[fix-glb-yup-feet] aviso: trimesh não instalado — a ignorar assentamento glTF (min Y=0).",
            file=sys.stderr,
        )
        return 0

    for p in glbs:
        try:
            _align_feet_y_gltf(p)
            print(f"[fix-glb-yup-feet] glTF: min Y=0 + centro XZ → {p}", flush=True)
        except Exception as e:
            print(f"[fix-glb-yup-feet] erro glTF {p}: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
