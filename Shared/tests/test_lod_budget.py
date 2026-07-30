"""Testes do orçamento LOD (faces + texturas por volume)."""

from __future__ import annotations

from gamedev_shared.lod_budget import (
    LOD_FACE_SCALE_CEIL,
    LOD_FACE_SCALE_FLOOR,
    LOD_FACES_ABS_MIN,
    lod_face_scale,
    lod_texture_ladder,
    lod_texture_size_for_char,
    snap_tex_64,
)


class TestLodFaceScale:
    def test_horseshoe_floor(self) -> None:
        # char≈0.37 → (0.37/2)^2 ≈ 0.034 → floor 0.12
        assert abs(lod_face_scale(0.37) - LOD_FACE_SCALE_FLOOR) < 1e-9

    def test_full_at_ref(self) -> None:
        assert lod_face_scale(2.0) == LOD_FACE_SCALE_CEIL

    def test_ceil_large(self) -> None:
        assert lod_face_scale(10.0) == LOD_FACE_SCALE_CEIL

    def test_one_meter(self) -> None:
        assert abs(lod_face_scale(1.0) - 0.25) < 1e-9


class TestSnapTex64:
    def test_power2_noop(self) -> None:
        assert snap_tex_64(512) == 512
        assert snap_tex_64(1024, cap=2048) == 1024

    def test_round_and_floor(self) -> None:
        assert snap_tex_64(780) == 768
        assert snap_tex_64(30) == 64

    def test_cap(self) -> None:
        assert snap_tex_64(4096, cap=2048) == 2048


class TestLodTextureLadder:
    def test_512_chain(self) -> None:
        assert lod_texture_ladder(512) == (512, 256, 128)

    def test_768_chain(self) -> None:
        assert lod_texture_ladder(768) == (768, 384, 192)

    def test_for_char_bucket(self) -> None:
        assert lod_texture_size_for_char(0.37, quality_cap=2048) == 512
        assert lod_texture_size_for_char(5.0, quality_cap=2048) == 2048


class TestLodFacesAbsMin:
    def test_constant(self) -> None:
        assert LOD_FACES_ABS_MIN == 800
