"""Testes do orçamento de malha pré-paint."""

from __future__ import annotations

from gamedev_shared.paint_budget import (
    PAINT_FACES_MAX,
    PAINT_FACES_MIN,
    paint_target_faces,
    paint_target_vertices,
    paint_texture_for_char,
    texels_per_face,
)


class TestPaintTargetFaces:
    def test_medium_2048_caps_at_max(self) -> None:
        # raw ≈ 230k → clamp MAX (160k, orçamento 2x)
        assert PAINT_FACES_MAX == 160_000
        assert paint_target_faces(2048) == PAINT_FACES_MAX

    def test_1024_in_band(self) -> None:
        f = paint_target_faces(1024)
        assert PAINT_FACES_MIN <= f <= PAINT_FACES_MAX
        assert f == 57_671  # 1024^2 * 0.55 / 10

    def test_min_floor(self) -> None:
        assert PAINT_FACES_MIN == 6_000
        assert paint_target_faces(256) == PAINT_FACES_MIN

    def test_verts_scale(self) -> None:
        f = paint_target_faces(2048)
        v = paint_target_vertices(2048)
        assert v == round(f * 0.55)

    def test_chapel_clean_starved(self) -> None:
        # 2.27M faces @ 2k ≈ 1 texel/tri — orçamento correcto ~14 tpt no cap
        assert texels_per_face(2048, 2_270_000) < 2.0
        assert texels_per_face(2048, paint_target_faces(2048)) >= 10.0


class TestPaintTextureForChar:
    def test_bucket_vs_house_anchor(self) -> None:
        """Balde ~512/~14k faces; casa medium 2048/160k; highest casa 4096."""
        bucket_tex = paint_texture_for_char(0.37, quality_cap=2048)
        house_tex = paint_texture_for_char(5.0, quality_cap=2048)
        house_hi = paint_texture_for_char(5.0, quality_cap=4096)
        assert bucket_tex == 512
        assert house_tex == 2048
        assert house_hi == 4096
        bucket_faces = paint_target_faces(bucket_tex)
        house_faces = paint_target_faces(house_tex)
        assert 6_000 <= bucket_faces <= 20_000
        assert bucket_faces == 14_417  # 512^2 * 0.55 / 10
        assert house_faces == PAINT_FACES_MAX

    def test_prop_band_1024(self) -> None:
        assert paint_texture_for_char(0.69, quality_cap=2048) == 1024
        assert paint_texture_for_char(1.2, quality_cap=2048) == 1024

    def test_never_above_quality_cap(self) -> None:
        assert paint_texture_for_char(10.0, quality_cap=1024) == 1024
        assert paint_texture_for_char(0.2, quality_cap=256) == 256
