"""Tests for Text3D bbox → Hunyuan octree autotune."""

from __future__ import annotations

from typing import ClassVar

import pytest

from text3d.bbox_tune import _OCTREE_CEILING, _OCTREE_FLOOR, _OCTREE_STEP, _snap_octree, tune_hunyuan_for_bbox


class TestSnapOctree:
    def test_below_floor_clamps_to_128(self) -> None:
        assert _snap_octree(0) == 128
        assert _snap_octree(96) == 128
        assert _snap_octree(127) == 128
        assert _snap_octree(128) == 128

    def test_steps_of_32_up_to_512(self) -> None:
        # Snap = floor + round((v-floor)/step)*step (half-even).
        assert _snap_octree(144) == 128  # (144-128)/32 = 0.5 → 0
        assert _snap_octree(145) == 160
        assert _snap_octree(160) == 160
        assert _snap_octree(176) == 192  # (176-128)/32 = 1.5 → 2
        assert _snap_octree(177) == 192
        assert _snap_octree(256) == 256
        assert _snap_octree(500) == 512
        assert _snap_octree(999) == 512

    def test_ladder_is_floor_step_ceiling(self) -> None:
        from text3d.bbox_tune import _OCTREE_LADDER

        assert _OCTREE_LADDER[0] == _OCTREE_FLOOR
        assert _OCTREE_LADDER[-1] == _OCTREE_CEILING
        assert all((_OCTREE_LADDER[i + 1] - _OCTREE_LADDER[i]) == _OCTREE_STEP for i in range(len(_OCTREE_LADDER) - 1))


class TestResolveFastDecode:
    def test_flashvdm_below_256_falls_back(self) -> None:
        from text3d.decode_tune import resolve_fast_decode

        assert resolve_fast_decode("flashvdm", 160) is False
        assert resolve_fast_decode("flashvdm", 256) is True
        assert resolve_fast_decode("hierarchical", 160) is False
        assert resolve_fast_decode("hierarchical", 448) is True


class TestTuneHunyuanForBbox:
    def test_char_m_is_volume_equivalent_not_max_axis(self) -> None:
        from text3d.bbox_tune import characteristic_meters, volume_equivalent_meters

        size = [0.55, 1.65, 0.4]
        char, src = characteristic_meters(size)
        assert src == "size_m"
        assert char == volume_equivalent_meters(size)
        assert char is not None
        assert char < max(size)  # volume-eq < altura

    def test_small_humanoid_respects_face_budget_cap(self) -> None:
        """Bandit-scale: face-budget cap impede soft-boost de subir a 224+."""
        r = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[0.55, 1.65, 0.4],
            category="creature",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
            volume_decoder="flashvdm",
        )
        # char = (0.55·1.65·0.4)^(1/3) ≈ 0.71 — não o eixo 1.65.
        assert 0.6 < r.char_m < 0.8
        assert r.octree >= _OCTREE_FLOOR
        assert r.octree <= 192
        assert (r.octree - _OCTREE_FLOOR) % _OCTREE_STEP == 0

    def test_large_building_steps_up_unchanged_by_soft_floor(self) -> None:
        r = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[8.0, 10.0, 6.0],
            category="building",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        # Volume-eq ≈ 7.83 m — soft-floor ~0; octree na zona alta.
        assert 7.0 < r.char_m < 8.5
        assert r.octree > 256
        assert r.octree <= _OCTREE_CEILING
        assert (r.octree - _OCTREE_FLOOR) % _OCTREE_STEP == 0

    def test_bucket_vs_house_octree_anchor(self) -> None:
        """Âncoras: balde no piso/160; casa ≥256 e acima do balde."""
        bucket = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[0.35, 0.4, 0.35],
            category="prop",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        house = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[5.0, 4.2, 6.0],
            category="building",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        assert 0.3 < bucket.char_m < 0.45
        assert bucket.octree in {128, 160}
        assert 4.5 < house.char_m < 5.5
        assert house.octree >= 256
        assert house.octree > bucket.octree

    def test_new_prop_batch_face_budget(self) -> None:
        """Props simple-rpg 2026-07-28: horseshoe/anvil não voltam a 256."""
        from text3d.bbox_tune import octree_face_budget_cap

        horseshoe = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[0.45, 0.25, 0.45],
            category="prop",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        anvil = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[0.7, 0.9, 0.45],
            category="prop",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        assert horseshoe.octree <= 160
        assert anvil.octree <= 160
        assert octree_face_budget_cap(horseshoe.char_m, "prop") is not None
        assert octree_face_budget_cap(5.0) is None  # building: sem cap

    def test_pancake_prop_breaks_face_cap_by_anisotropy(self) -> None:
        """Fogueira 1.5x0.5x1.5: volume-eq+cap davam 160; eixo fino pede +64."""
        from text3d.bbox_tune import anisotropy_octree_floor

        pit = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[1.5, 0.5, 1.5],
            category="prop",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        assert anisotropy_octree_floor([1.5, 0.5, 1.5], "prop") == 224
        assert pit.octree >= 224
        assert (pit.octree - _OCTREE_FLOOR) % _OCTREE_STEP == 0
        # Cubo / ferradura: sem boost (ratio ≤ 2).
        assert anisotropy_octree_floor([0.8, 0.8, 0.8], "prop") is None
        assert anisotropy_octree_floor([0.45, 0.25, 0.45], "prop") is None
        assert anisotropy_octree_floor([0.12, 1.0, 0.04], "weapon") is None

    def test_terrain_hole_prone_matches_former_manual_overrides(self) -> None:
        """Nest/cliff/outcrop: auto ≥ overrides manuais (256/288) sem manifesto."""
        nest = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[2.5, 1.2, 2.5],
            category="terrain",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        cliff = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[2.1, 3.0, 2.16],
            category="terrain",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        outcrop = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=[2.4, 1.6, 1.8],
            category="terrain",
            quality="medium",
            total_vram_gib=6.0,
            group_offload=True,
        )
        # manuais: nest 256, cliff/outcrop 288
        assert nest.octree >= 256
        assert cliff.octree >= 288
        assert outcrop.octree >= 256

    def test_no_size_signal_keeps_base(self) -> None:
        r = tune_hunyuan_for_bbox(
            base_steps=30,
            base_octree=256,
            base_chunks=4,
            size_m=None,
            category=None,
            quality="medium",
        )
        assert r.applied is False
        assert r.octree == 256

    def test_soft_floor_boost_decays_with_size(self) -> None:
        from text3d.bbox_tune import small_asset_octree_boost

        b_small = small_asset_octree_boost(1.4)
        b_mid = small_asset_octree_boost(2.5)
        b_large = small_asset_octree_boost(10.0)
        assert b_small > b_mid > b_large
        assert b_large < 5.0  # chapel: boost desprezável


# --- Voxel-alvo de categorias de parede fina (regressão) -------------------
#
# 13 dos 19 assets que tiveram de ser regenerados eram ``building``: o alvo
# perceptual escala com a distância de inspeção, portanto edifícios grandes
# recebiam o voxel MAIS GROSSO de todos (0.0375 m) e uma parede de 10 cm ficava
# com 2.7 voxels — casca perfurada no marching cubes.


class TestThinWallTargetVoxel:
    def test_building_capped_below_perceptual_target(self) -> None:
        from text3d.bbox_tune import target_voxel_for

        tv = target_voxel_for("building", None, "medium")
        assert tv <= 0.015, f"edifícios não podem levar voxel grosso: {tv}"

    def test_building_not_coarser_than_props(self) -> None:
        """O bug era exactamente este: edifícios mais grosseiros que props."""
        from text3d.bbox_tune import target_voxel_for

        assert target_voxel_for("building", None, "medium") < target_voxel_for("prop", None, "medium")

    def test_quality_tier_still_tightens(self) -> None:
        from text3d.bbox_tune import target_voxel_for

        hi = target_voxel_for("building", None, "highest")
        med = target_voxel_for("building", None, "medium")
        assert hi < med

    def test_low_tier_does_not_loosen_past_cap(self) -> None:
        from text3d.bbox_tune import target_voxel_for

        assert target_voxel_for("building", None, "fast") <= 0.015

    def test_other_categories_unchanged(self) -> None:
        from text3d.bbox_tune import target_voxel_for

        assert target_voxel_for("furniture", None, "medium") == pytest.approx(0.025)
        assert target_voxel_for("weapon", None, "medium") == pytest.approx(0.0125)


class TestBuildingOctreeTargets:
    """Octrees validados à mão nos assets que falharam."""

    CASES: ClassVar[list[tuple[str, list[float], int]]] = [
        ("shepherd_cottage", [6.7, 3.94, 7.5], 480),
        ("village_barn", [8.48, 6.06, 11.0], 480),
        ("village_longhouse", [5.73, 5.61, 10.0], 480),
        ("witch_hut", [5.0, 3.99, 4.94], 416),
        ("village_house", [4.73, 3.18, 6.0], 416),
        ("swamp_shack", [5.0, 4.09, 4.94], 416),
        ("village_forge", [6.0, 5.75, 4.88], 448),
        ("city_wall_seg_b", [6.5, 5.0, 1.2], 224),
        ("forge_furnace", [2.2, 2.6, 1.5], 192),
        ("fireplace_hearth", [1.8, 2.0, 1.2], 160),
    ]

    @pytest.mark.parametrize(("asset", "size_m", "min_octree"), CASES)
    def test_reaches_validated_octree(self, asset: str, size_m: list[float], min_octree: int) -> None:
        from text3d.bbox_tune import tune_hunyuan_for_bbox

        r = tune_hunyuan_for_bbox(
            base_steps=50,
            base_octree=256,
            base_chunks=20000,
            size_m=size_m,
            category="building",
            total_vram_gib=6.0,
            group_offload=True,
            quality="medium",
        )
        assert r.octree >= min_octree, f"{asset}: octree {r.octree} < {min_octree}"

    def test_wall_resolved_by_several_voxels(self) -> None:
        """Parede típica de 10 cm tem de caber em ≥5 voxels."""
        from text3d.bbox_tune import tune_hunyuan_for_bbox

        for _asset, size_m, _o in self.CASES:
            r = tune_hunyuan_for_bbox(
                base_steps=50,
                base_octree=256,
                base_chunks=20000,
                size_m=size_m,
                category="building",
                total_vram_gib=6.0,
                group_offload=True,
                quality="medium",
            )
            assert 0.10 / r.voxel_m >= 5.0, f"voxel {r.voxel_m:.4f} grosso demais para parede de 10 cm"
