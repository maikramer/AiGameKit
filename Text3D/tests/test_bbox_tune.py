"""Tests for Text3D bbox → Hunyuan octree autotune."""

from __future__ import annotations

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
