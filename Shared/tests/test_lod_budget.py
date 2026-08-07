"""Testes do orçamento LOD (faces + texturas por silhueta)."""

from __future__ import annotations

from aigamekit_shared.lod_budget import (
    LOD_FACE_SCALE_CEIL,
    LOD_FACE_SCALE_FLOOR,
    LOD_FACES_ABS_MIN,
    lod_face_scale,
    lod_texture_ladder,
    lod_texture_size_for_char,
    silhouette_equivalent_meters,
    snap_tex_64,
)

# Herói simple-rpg: o caso que expôs a métrica volumétrica.
HERO_SIZE_M = [0.55, 1.55, 0.4]


class TestSilhouetteEquivalentMeters:
    def test_hero_uses_two_largest_axes(self) -> None:
        # sqrt(1.55 x 0.55) ≈ 0.923 — não (0.55·1.55·0.4)^(1/3) ≈ 0.699.
        sil = silhouette_equivalent_meters(HERO_SIZE_M)
        assert sil is not None
        assert abs(sil - 0.9232) < 1e-3
        vol = (0.55 * 1.55 * 0.4) ** (1.0 / 3.0)
        assert sil > vol

    def test_cube_agrees_with_volume(self) -> None:
        # Props compactos: silhueta == volume-equivalente (sem regressão).
        sil = silhouette_equivalent_meters([0.8, 0.8, 0.8])
        assert sil is not None
        assert abs(sil - 0.8) < 1e-9

    def test_ignores_zero_and_missing_axes(self) -> None:
        assert silhouette_equivalent_meters(None) is None
        assert silhouette_equivalent_meters([0.0, 0.0, 0.0]) is None
        assert silhouette_equivalent_meters([2.0, 0.0, 0.0]) == 2.0


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

    def test_category_ref_lifts_characters_off_the_floor(self) -> None:
        # Métrica antiga (volume-equivalente 0.70 m) deixava o herói a raspar no
        # FLOOR: 0.122 vs 0.12 → 12% do orçamento humanoid.
        vol = (0.55 * 1.55 * 0.4) ** (1.0 / 3.0)
        assert lod_face_scale(vol) < LOD_FACE_SCALE_FLOOR * 1.05
        # Silhueta sozinha já ajuda, mas só a ref da categoria dá orçamento útil.
        sil = silhouette_equivalent_meters(HERO_SIZE_M)
        assert sil is not None
        assert lod_face_scale(sil) > LOD_FACE_SCALE_FLOOR
        assert lod_face_scale(sil, ref_m=1.0) > 0.75

    def test_ref_zero_falls_back_to_default(self) -> None:
        assert lod_face_scale(1.0, ref_m=0.0) == lod_face_scale(1.0)


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
