from __future__ import annotations

import os
import shutil
import tempfile

import numpy as np
import tifffile
from whitebox import WhiteboxTools

# D8 flow direction offsets (whitebox non-ESRI encoding).
# Direction code → (delta_row, delta_col)
# Grid convention: row 0 = north, col 0 = west.
#   64(NW)  128(N)  1(NE)
#   32(W)    x      2(E)
#   16(SW)   8(S)   4(SE)
_D8_OFFSETS: dict[int, tuple[int, int]] = {
    64: (-1, -1),
    128: (-1, 0),
    1: (-1, 1),
    32: (0, -1),
    2: (0, 1),
    16: (1, -1),
    8: (1, 0),
    4: (1, 1),
}

# Reverse mapping: (delta_row, delta_col) -> direction code
# Used to check which neighbors drain INTO the current cell.
_DIR_TO_CODE: dict[tuple[int, int], int] = {v: k for k, v in _D8_OFFSETS.items()}

_MIN_RIVER_LENGTH = 10


def _save_as_asc(arr: np.ndarray, path: str) -> None:
    """Save a 2D float64 array as an ArcInfo ASCII grid (.asc).

    Args:
        arr: 2D numpy array (row-major, float64).
        path: Output file path.
    """
    rows, cols = arr.shape
    with open(path, "w") as f:
        f.write(f"ncols {cols}\n")
        f.write(f"nrows {rows}\n")
        f.write("xllcorner 0\n")
        f.write("yllcorner 0\n")
        f.write("cellsize 1\n")
        f.write("NODATA_value -9999\n")
        for r in range(rows):
            f.write(" ".join(f"{v:.10f}" for v in arr[r, :]) + "\n")


def _read_tif(path: str) -> np.ndarray:
    """Read a GeoTIFF raster as a numpy float64 array.

    Args:
        path: Path to GeoTIFF file.

    Returns:
        2D numpy float64 array.
    """
    data = tifffile.imread(path)
    return data.astype(np.float64)


def _trace_upstream(
    outlet_row: int,
    outlet_col: int,
    pointer: np.ndarray,
    flow_accum: np.ndarray,
    river_mask: np.ndarray,
    visited: np.ndarray,
) -> np.ndarray:
    """Trace a river channel upstream from an outlet, always picking the
    highest-flow tributary at each junction.

    Args:
        outlet_row: Row of the outlet cell.
        outlet_col: Col of the outlet cell.
        pointer: 2D D8 flow direction raster.
        flow_accum: 2D flow accumulation raster.
        river_mask: Boolean mask of river-threshold cells.
        visited: Boolean mask of already-traced cells.

    Returns:
        Nx2 array of (row, col) from source to outlet.
    """
    rows, cols = pointer.shape
    path: list[tuple[int, int]] = [(outlet_row, outlet_col)]
    visited[outlet_row, outlet_col] = True
    r, c = outlet_row, outlet_col

    while True:
        best_flow = -1.0
        best_pos: tuple[int, int] | None = None

        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                nr, nc = r + dr, c + dc
                if not (0 <= nr < rows and 0 <= nc < cols):
                    continue
                if visited[nr, nc] or not river_mask[nr, nc]:
                    continue
                # Does this neighbor drain into (r, c)?
                code = int(pointer[nr, nc])
                if code not in _D8_OFFSETS:
                    continue
                exp_dr, exp_dc = _D8_OFFSETS[code]
                if nr + exp_dr == r and nc + exp_dc == c:
                    flow = flow_accum[nr, nc]
                    if flow > best_flow:
                        best_flow = flow
                        best_pos = (nr, nc)

        if best_pos is None:
            break

        r, c = best_pos
        visited[r, c] = True
        path.append((r, c))

    # Reverse so the path goes source -> outlet
    path.reverse()
    return np.array(path, dtype=np.intp)


def extract_rivers(
    heightmap: np.ndarray,
    accumulation_threshold: float = 1000,
    seed: int = 42,
) -> list[np.ndarray]:
    """Extract river paths from a heightmap using hydrological flow analysis.

    Uses WhiteboxTools to fill depressions, compute D8 flow accumulation,
    and trace river paths from high-flow cells downhill.

    Args:
        heightmap: 2D float64 heightmap array (values in 0-1 range).
        accumulation_threshold: Minimum flow accumulation to classify a
            cell as a river pixel.
        seed: Random seed for deterministic ordering of equal-flow cells.

    Returns:
        List of Nx2 integer arrays, each containing (row, col) coordinates
        of a single river path, sorted by descending accumulation at the
        source. Paths shorter than 10 cells are filtered out.
    """
    work_dir = tempfile.mkdtemp(prefix="wbt_rivers_")

    try:
        asc_path = os.path.join(work_dir, "heightmap.asc")
        _save_as_asc(heightmap, asc_path)

        filled_path = os.path.join(work_dir, "filled.tif")
        flow_path = os.path.join(work_dir, "flow.tif")
        pntr_path = os.path.join(work_dir, "pntr.tif")

        wbt = WhiteboxTools()
        wbt.work_dir = work_dir
        wbt.verbose = False

        wbt.fill_depressions_planchon_and_darboux(asc_path, filled_path, fix_flats=True)
        wbt.d8_flow_accumulation(filled_path, flow_path, out_type="specific contributing area")
        wbt.d8_pointer(filled_path, pntr_path)

        flow_accum = _read_tif(flow_path)
        pointer = _read_tif(pntr_path)

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    river_mask = flow_accum > accumulation_threshold
    if not river_mask.any():
        return []

    rows, cols = heightmap.shape
    visited = np.zeros((rows, cols), dtype=bool)

    edge_cells: list[tuple[int, int]] = []
    for r in range(rows):
        if river_mask[r, 0]:
            edge_cells.append((r, 0))
        if river_mask[r, cols - 1]:
            edge_cells.append((r, cols - 1))
    for c in range(1, cols - 1):
        if river_mask[0, c]:
            edge_cells.append((0, c))
        if river_mask[rows - 1, c]:
            edge_cells.append((rows - 1, c))

    if not edge_cells:
        return []

    edge_cells.sort(key=lambda rc: flow_accum[rc[0], rc[1]], reverse=True)

    rivers: list[np.ndarray] = []
    for r, c in edge_cells:
        if visited[r, c]:
            continue

        path = _trace_upstream(r, c, pointer, flow_accum, river_mask, visited)
        if len(path) >= _MIN_RIVER_LENGTH:
            rivers.append(path)

    return rivers


def carve_river_valleys(
    heightmap: np.ndarray,
    river_paths: list[np.ndarray],
    depth: float = 0.05,
    width: int = 3,
) -> np.ndarray:
    """Carve river valleys into a heightmap along the given river paths.

    Each river path is carved with a cosine cross-sectional profile.  The
    carving depth follows a gentle taper at both ends of the river so the
    valley blends smoothly into the surrounding terrain.

    Args:
        heightmap: 2D float64 heightmap array.
        river_paths: List of Nx2 (row, col) coordinate arrays from
            ``extract_rivers``.
        depth: Maximum valley depth in heightmap units (0-1 range).
        width: Half-width of the valley in pixels.  Total valley width
            is ``2 * width + 1`` pixels.

    Returns:
        New heightmap array with valleys carved (original is not modified).
    """
    result = heightmap.copy()
    rows, cols = result.shape

    for path in river_paths:
        n_points = len(path)
        if n_points == 0:
            continue

        for idx in range(n_points):
            pr, pc = int(path[idx, 0]), int(path[idx, 1])

            taper_len = max(1, n_points // 10)
            if idx < taper_len:
                taper = idx / taper_len
            elif idx >= n_points - taper_len:
                taper = (n_points - 1 - idx) / taper_len
            else:
                taper = 1.0

            local_depth = depth * taper

            for dw in range(-width, width + 1):
                cc = pc + dw
                if cc < 0 or cc >= cols:
                    continue

                t = abs(dw) / max(width, 1)
                # Cosine profile: 1 at centre, 0 at edges
                profile = 0.5 * (1.0 + np.cos(t * np.pi))

                carve_amount = local_depth * profile
                result[pr, cc] -= carve_amount

            for dw in range(-width, width + 1):
                rr = pr + dw
                if rr < 0 or rr >= rows or dw == 0:
                    continue

                t = abs(dw) / max(width, 1)
                profile = 0.5 * (1.0 + np.cos(t * np.pi))
                result[rr, pc] = min(result[rr, pc], result[pr, pc] + local_depth * profile * 0.5)

    np.clip(result, 0.0, None, out=result)
    return result
