from __future__ import annotations

import shutil
import struct
import tempfile
from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass
class LakeData:
    """Represents a single lake found in the terrain."""

    center_x: float
    center_z: float
    surface_level: float
    width: float
    depth: float
    area: int
    pixels: list[tuple[int, int]]
    shape_points: list[tuple[int, int]]  # (row, col) boundary points


@dataclass
class LakePlaneData:
    """A rectangular water plane for VibeGame rendering."""

    lake_id: int
    pos_x: float
    pos_y: float
    pos_z: float
    size_x: float
    size_z: float


def _write_geotiff(arr: np.ndarray, path: str) -> None:
    """Save a 2D float64 array as a minimal GeoTIFF file that whitebox can read."""
    rows, cols = arr.shape
    data = arr.astype(np.float32)

    # Minimal GeoTIFF: IFD with 13 entries + GeoKeys + Tiepoint + PixelScale + strip data
    num_ifd_entries = 13
    header_size = 8
    ifd_size = 2 + num_ifd_entries * 12 + 4

    # GeoKeyDirectory: GTModelTypeGeoKey=2 (Geographic), GTRasterTypeGeoKey=1 (PixelIsArea)
    geokey_data = struct.pack("<4H4H4H", 1, 1, 0, 2, 1024, 0, 1, 2, 1025, 0, 1, 1)
    geokey_offset = header_size + ifd_size
    geokey_count = len(geokey_data) // 2  # number of SHORT values

    # ModelTiepointTag (33922): pixel (0,0,0) → geo (0,0,0)
    tiepoint_data = struct.pack("<6d", 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    tiepoint_offset = geokey_offset + len(geokey_data)

    # ModelPixelScaleTag (33550): ScaleX=1, ScaleY=1, ScaleZ=0
    pixel_scale_data = struct.pack("<3d", 1.0, 1.0, 0.0)
    pixel_scale_offset = tiepoint_offset + len(tiepoint_data)

    strip_offset = pixel_scale_offset + len(pixel_scale_data)
    strip_size = rows * cols * 4  # float32

    with open(path, "wb") as f:
        # TIFF header (little-endian)
        f.write(b"II")
        f.write(struct.pack("<H", 42))
        f.write(struct.pack("<I", header_size))

        f.write(struct.pack("<H", num_ifd_entries))
        entries = [
            (256, 3, 1, cols),  # ImageWidth
            (257, 3, 1, rows),  # ImageLength
            (258, 3, 1, 32),  # BitsPerSample
            (259, 3, 1, 1),  # Compression: none
            (262, 3, 1, 1),  # PhotometricInterpretation: MinIsBlack
            (273, 4, 1, strip_offset),  # StripOffsets
            (277, 3, 1, 1),  # SamplesPerPixel
            (278, 3, 1, rows),  # RowsPerStrip
            (279, 4, 1, strip_size),  # StripByteCounts
            (339, 3, 1, 3),  # SampleFormat: IEEEFP
            (34735, 3, geokey_count, geokey_offset),  # GeoKeyDirectoryTag
            (33922, 12, 6, tiepoint_offset),  # ModelTiepointTag
            (33550, 12, 3, pixel_scale_offset),  # ModelPixelScaleTag
        ]
        for tag, typ, count, value in entries:
            f.write(struct.pack("<HHII", tag, typ, count, value))

        f.write(struct.pack("<I", 0))

        f.write(geokey_data)
        f.write(tiepoint_data)
        f.write(pixel_scale_data)
        f.write(data.tobytes())


def _read_geotiff(path: str) -> np.ndarray:
    """Read a GeoTIFF file produced by whitebox back as a float64 numpy array."""
    with open(path, "rb") as f:
        content = f.read()

    if content[:2] != b"II":
        msg = "Only little-endian TIFF supported"
        raise ValueError(msg)

    ifd_off = struct.unpack("<I", content[4:8])[0]
    n_entries = struct.unpack("<H", content[ifd_off : ifd_off + 2])[0]

    width = height = bits_per_sample = None
    strip_offsets: list[int] = []
    strip_byte_counts: list[int] = []

    for i in range(n_entries):
        e = ifd_off + 2 + i * 12
        tag = struct.unpack("<H", content[e : e + 2])[0]
        count = struct.unpack("<I", content[e + 4 : e + 8])[0]
        value = struct.unpack("<I", content[e + 8 : e + 12])[0]

        if tag == 256:
            width = value
        elif tag == 257:
            height = value
        elif tag == 258:
            bits_per_sample = value
        elif tag == 273:  # StripOffsets
            if count == 1:
                strip_offsets = [value]
            else:
                for j in range(count):
                    strip_offsets.append(struct.unpack("<I", content[value + 4 * j : value + 4 * j + 4])[0])
        elif tag == 279:  # StripByteCounts
            if count == 1:
                strip_byte_counts = [value]
            else:
                for j in range(count):
                    strip_byte_counts.append(struct.unpack("<I", content[value + 4 * j : value + 4 * j + 4])[0])

    assert width is not None and height is not None and bits_per_sample is not None
    dtype = np.float64 if bits_per_sample == 64 else np.float32
    bpp = bits_per_sample // 8

    result = np.empty((height, width), dtype=dtype)
    row = 0
    for off, bc in zip(strip_offsets, strip_byte_counts, strict=True):
        n_pixels = bc // bpp
        n_rows = n_pixels // width
        strip_data = np.frombuffer(content[off : off + bc], dtype=dtype)
        result[row : row + n_rows, :] = strip_data.reshape(n_rows, width)
        row += n_rows

    return result.astype(np.float64)


def _connected_components(mask: np.ndarray) -> list[np.ndarray]:
    """Find connected components in a boolean mask using BFS flood fill."""
    visited = np.zeros_like(mask, dtype=bool)
    components: list[np.ndarray] = []
    rows, cols = mask.shape

    for i in range(rows):
        for j in range(cols):
            if mask[i, j] and not visited[i, j]:
                component: list[tuple[int, int]] = []
                queue: deque[tuple[int, int]] = deque([(i, j)])
                visited[i, j] = True
                while queue:
                    x, y = queue.popleft()
                    component.append((x, y))
                    for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nx, ny = x + dx, y + dy
                        if 0 <= nx < rows and 0 <= ny < cols and mask[nx, ny] and not visited[nx, ny]:
                            visited[nx, ny] = True
                            queue.append((nx, ny))
                components.append(np.array(component))
    return components


def _find_boundary_pixels(component: np.ndarray, shape: tuple[int, int]) -> list[tuple[int, int]]:
    """Find boundary pixels of a component (pixels adjacent to non-component or edge)."""
    rows, cols = shape
    component_set = set(map(tuple, component))
    boundary: list[tuple[int, int]] = []

    for x, y in component:
        is_boundary = False
        for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
            nx, ny = x + dx, y + dy
            if nx < 0 or nx >= rows or ny < 0 or ny >= cols or (nx, ny) not in component_set:
                is_boundary = True
                break
        if is_boundary:
            boundary.append((x, y))

    return boundary


def identify_lakes(heightmap: np.ndarray, min_area: int = 100, max_depth: float = 0.1) -> list[LakeData]:
    """Identify natural depressions in a heightmap that could form lakes.

    Uses whitebox FillDepressions to find where water would accumulate, then
    filters by area and depth to identify lake candidates.

    Args:
        heightmap: 2D float64 array of terrain elevations.
        min_area: Minimum number of pixels for a valid lake.
        max_depth: Maximum fill depth to consider (deeper = canyon, not lake).

    Returns:
        List of LakeData for each qualifying depression.
    """
    from whitebox import WhiteboxTools

    tmpdir = tempfile.mkdtemp(prefix="wbt_lakes_")
    try:
        inp_path = f"{tmpdir}/input.tif"
        out_path = f"{tmpdir}/filled.tif"

        _write_geotiff(heightmap, inp_path)

        wbt = WhiteboxTools()
        wbt.working_directory = tmpdir
        wbt.verbose = False
        # whitebox requires absolute paths for file I/O
        wbt.fill_depressions(dem=inp_path, output=out_path)

        filled = _read_geotiff(out_path)

        # Where filled > original → water would pool (depression filled)
        diff = filled - heightmap
        lake_mask = (diff > 1e-6) & (diff <= max_depth)

        if not lake_mask.any():
            return []

        components = _connected_components(lake_mask)
        lakes: list[LakeData] = []

        for component in components:
            area = len(component)
            if area < min_area:
                continue

            rows_idx = component[:, 0]
            cols_idx = component[:, 1]

            surface_level = float(filled[rows_idx, cols_idx].max())

            mean_original = float(heightmap[rows_idx, cols_idx].mean())
            depth = surface_level - mean_original

            # center_x = col direction, center_z = row direction
            center_x = float(cols_idx.mean())
            center_z = float(rows_idx.mean())

            width = float(max(cols_idx.max() - cols_idx.min(), rows_idx.max() - rows_idx.min()))

            shape_points = _find_boundary_pixels(component, heightmap.shape)

            lakes.append(
                LakeData(
                    center_x=center_x,
                    center_z=center_z,
                    surface_level=surface_level,
                    width=width,
                    depth=depth,
                    area=area,
                    pixels=[(int(r), int(c)) for r, c in component],
                    shape_points=shape_points,
                )
            )

        return lakes

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def excavate_lakes(heightmap: np.ndarray, lakes: list[LakeData]) -> np.ndarray:
    """Carve flat-bottomed depressions into the heightmap for each lake.

    Lowers terrain under each lake to ``surface_level - depth``, creating a
    flat-bottomed depression that matches the lake shape.

    Args:
        heightmap: Original 2D float64 elevation array.
        lakes: List of LakeData identifying lake locations.

    Returns:
        New heightmap array with lake depressions carved.
    """
    result = heightmap.copy()

    for lake in lakes:
        target_level = lake.surface_level - lake.depth
        for row, col in lake.pixels:
            result[row, col] = target_level

    return result


def generate_lake_planes(lakes: list[LakeData]) -> list[LakePlaneData]:
    """One axis-aligned bounding box per lake (single ``<Water>`` in VibeGame).

    Avoids overlapping strips and keeps entity count equal to the number of
    lakes kept by the pipeline.

    Args:
        lakes: Lake regions (pixel coordinates; ``surface_level`` is 0..1).

    Returns:
        One :class:`LakePlaneData` per lake, ``lake_id`` = index in *lakes*.
    """
    if not lakes:
        return []

    planes: list[LakePlaneData] = []

    for lake_idx, lake in enumerate(lakes):
        if lake.area <= 0:
            continue

        if lake.shape_points:
            pts = np.array(lake.shape_points)
            min_row, max_row = float(pts[:, 0].min()), float(pts[:, 0].max())
            min_col, max_col = float(pts[:, 1].min()), float(pts[:, 1].max())
        else:
            half_w = lake.width / 2
            min_row = lake.center_z - half_w
            max_row = lake.center_z + half_w
            min_col = lake.center_x - half_w
            max_col = lake.center_x + half_w

        bbox_h = max_row - min_row
        bbox_w = max_col - min_col

        planes.append(
            LakePlaneData(
                lake_id=lake_idx,
                pos_x=(min_col + max_col) / 2,
                pos_y=lake.surface_level,
                pos_z=(min_row + max_row) / 2,
                size_x=max(bbox_w, 1.0),
                size_z=max(bbox_h, 1.0),
            )
        )

    return planes
