"""Testes do orçamento de malha pré-paint."""

from __future__ import annotations

from gamedev_shared.paint_budget import (
    PAINT_FACES_MAX,
    PAINT_FACES_MIN,
    paint_target_faces,
    paint_target_vertices,
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
        assert paint_target_faces(256) == PAINT_FACES_MIN

    def test_verts_scale(self) -> None:
        f = paint_target_faces(2048)
        v = paint_target_vertices(2048)
        assert v == round(f * 0.55)

    def test_chapel_clean_starved(self) -> None:
        # 2.27M faces @ 2k ≈ 1 texel/tri — orçamento correcto ~14 tpt no cap
        assert texels_per_face(2048, 2_270_000) < 2.0
        assert texels_per_face(2048, paint_target_faces(2048)) >= 10.0
