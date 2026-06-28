"""Testes da rasterização de cores por vértice → textura (numpy/cv2) — sem bpy."""

from __future__ import annotations

import numpy as np

from paint3d.vertex_pbr import _rasterize_vertex_colors_to_texture


def _single_triangle_faces() -> np.ndarray:
    return np.array([[0, 1, 2]], dtype=np.int64)


class TestRasterizeShape:
    def test_output_shape_and_dtype(self) -> None:
        size = 16
        uvs = np.array([[0.0, 0.0], [1.0, 0.0], [0.5, 1.0]], dtype=np.float64)
        rgb = np.array([[1.0, 1.0, 1.0]] * 3, dtype=np.float64)
        out = _rasterize_vertex_colors_to_texture(uvs, _single_triangle_faces(), rgb, size)
        assert out.shape == (size, size, 3)
        assert out.dtype == np.uint8


class TestRasterizeSolidTriangle:
    def test_white_triangle_fills_interior_uniformly(self) -> None:
        size = 32
        # uv (0,0)→pixel(0,31); uv(1,0)→pixel(31,31); uv(1,1)→pixel(31,0)
        uvs = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], dtype=np.float64)
        rgb = np.array([[1.0, 1.0, 1.0]] * 3, dtype=np.float64)
        out = _rasterize_vertex_colors_to_texture(uvs, _single_triangle_faces(), rgb, size)
        # Equal vertex colors → barycentric blend is flat → interior pixels are uniform.
        samples = [tuple(out[py, px]) for py, px in ((20, 20), (25, 25), (15, 25), (22, 18))]
        assert len(set(samples)) == 1
        # Linear 1.0 encodes to a near-maximal sRGB byte; the encoder float-rounds
        # 1.055*1 - 0.055 to 0.9999..., so the byte lands at 254 rather than 255.
        assert samples[0][0] >= 250
        assert np.any(out > 0)


class TestRasterizeBarycentric:
    def test_vertex_color_dominates_near_each_corner(self) -> None:
        size = 32
        uvs = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]], dtype=np.float64)
        rgb = np.array(
            [
                [1.0, 0.0, 0.0],  # v0 red → pixel (0, 31)
                [0.0, 1.0, 0.0],  # v1 green → pixel (31, 31)
                [0.0, 0.0, 1.0],  # v2 blue → pixel (31, 0)
            ],
            dtype=np.float64,
        )
        out = _rasterize_vertex_colors_to_texture(uvs, _single_triangle_faces(), rgb, size)
        # out is indexed [py, px]; near v0 (px=2, py=29) red dominates.
        r, g, b = out[29, 2]
        assert r > g
        assert r > b
        # Near v2 (px=29, py=2) blue dominates.
        r, g, b = out[2, 29]
        assert b > r
        assert b > g


class TestRasterizeDegenerate:
    def test_zero_area_triangle_does_not_crash(self) -> None:
        size = 8
        # Coincident UVs → denom ~ 0 → triangle skipped; no crash.
        uvs = np.array([[0.5, 0.5], [0.5, 0.5], [0.5, 0.5]], dtype=np.float64)
        rgb = np.array([[1.0, 0.0, 0.0]] * 3, dtype=np.float64)
        out = _rasterize_vertex_colors_to_texture(uvs, _single_triangle_faces(), rgb, size)
        assert out.shape == (size, size, 3)
        assert out.dtype == np.uint8
        assert np.all(np.isfinite(out))
