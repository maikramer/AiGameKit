"""Worker do unwrap xatlas (subprocesso com timeout — ver ``uvwrap_utils``).

Corre num processo limpo, sem bpy/CUDA: lê ``verts``/``faces`` (e opções
opcionais de chart/pack) de um ``.npz`` e grava ``vmapping``/``indices``/``uvs``
no ficheiro de saída.

Opções opcionais no npz (floats):
    normal_seam_weight, max_cost, resolution, padding
"""

from __future__ import annotations

import sys

import numpy as np
import xatlas


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("uso: -m paint3d.hy3dpaint.utils._xatlas_worker IN.npz OUT.npz", file=sys.stderr)
        return 2
    data = np.load(argv[1])
    opts = {k: float(data[k]) for k in data.files if k not in ("verts", "faces")}

    atlas = xatlas.Atlas()
    atlas.add_mesh(data["verts"], data["faces"])

    chart_options = xatlas.ChartOptions()
    if "normal_seam_weight" in opts:
        chart_options.normal_seam_weight = opts["normal_seam_weight"]
    if "max_cost" in opts:
        chart_options.max_cost = opts["max_cost"]

    pack_options = xatlas.PackOptions()
    if opts.get("resolution", 0) > 0:
        pack_options.resolution = int(opts["resolution"])
    if opts.get("padding", 0) > 0:
        pack_options.padding = int(opts["padding"])

    atlas.generate(chart_options, pack_options)
    vmapping, indices, uvs = atlas.get_mesh(0)
    np.savez(argv[2], vmapping=vmapping, indices=indices, uvs=uvs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
