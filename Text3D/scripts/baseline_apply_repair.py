"""A partir de um GLB baseline cru (sem repair), gera variantes para comparação.

Escreve no mesmo diretório:
  - ``{stem}_repaired.glb`` — :func:`repair_mesh` com defaults do CLI (incl. remesh).
  - ``{stem}_full.glb`` — ``repaired`` + :func:`remove_backing_plates` (equivalente ao
    fluxo ``generate`` sem ``--no-mesh-repair`` / ``--no-remove-plates``).

Uso: ``python scripts/baseline_apply_repair.py <out_dir> <stem>``
Ex.: ``python scripts/baseline_apply_repair.py testdata/baseline_meshes_raw baseline_01_rock``
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import trimesh

from text3d import defaults as t3d_defaults
from text3d.utils.mesh_repair import remove_backing_plates, repair_mesh


def _load_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(str(path))
    if isinstance(loaded, trimesh.Scene):
        return loaded.to_geometry()
    if isinstance(loaded, trimesh.Trimesh):
        return loaded
    raise TypeError(f"Tipo de mesh não suportado: {type(loaded)}")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Aplica repair_mesh + remove_backing_plates a um GLB baseline cru.",
    )
    ap.add_argument(
        "out_dir",
        type=Path,
        help="Pasta de saída (ex.: testdata/baseline_meshes_raw)",
    )
    ap.add_argument(
        "stem",
        help="Nome base sem extensão (ex.: baseline_01_rock); lê {stem}.glb",
    )
    args = ap.parse_args()
    out_dir = args.out_dir.resolve()
    stem = args.stem
    raw_path = out_dir / f"{stem}.glb"
    if not raw_path.is_file():
        print(f"Ficheiro em falta: {raw_path}", file=sys.stderr)
        sys.exit(1)

    mesh = _load_mesh(raw_path)
    repaired = repair_mesh(
        mesh,
        remesh=t3d_defaults.DEFAULT_REMESH,
        remesh_resolution=t3d_defaults.DEFAULT_REMESH_RESOLUTION,
        remesh_iterations=t3d_defaults.DEFAULT_REMESH_ITERATIONS,
        remesh_max_surf_dist_factor=t3d_defaults.DEFAULT_REMESH_MAX_SURF_DIST_FACTOR,
    )
    repaired_path = out_dir / f"{stem}_repaired.glb"
    repaired.export(str(repaired_path), file_type="glb")

    full, info = remove_backing_plates(repaired)
    full_path = out_dir / f"{stem}_full.glb"
    full.export(str(full_path), file_type="glb")
    print(f"  → {repaired_path.name}  (repair_mesh, defaults CLI)")
    print(
        f"  → {full_path.name}  (+ remove_backing_plates; "
        f"placas_removidas={info['plates_removed']}, componentes={info['components_removed']})",
    )


if __name__ == "__main__":
    main()
