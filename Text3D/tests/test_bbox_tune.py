"""Tests for Text3D bbox → Hunyuan octree autotune."""

from __future__ import annotations

from text3d.bbox_tune import _OCTREE_CEILING, _OCTREE_FLOOR, _OCTREE_STEP, _snap_octree, tune_hunyuan_for_bbox


class TestSnapOctree:
    def test_below_floor_clamps_to_160(self) -> None:
        assert _snap_octree(0) == 160
        assert _snap_octree(128) == 160
        assert _snap_octree(159) == 160

    def test_steps_of_32_up_to_512(self) -> None:
        assert _snap_octree(160) == 160
        assert _snap_octree(176) == 160  # nearer 160 than 192
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
    def test_small_humanoid_floors_at_160(self) -> None:
        """Bandit-scale: desired < base tier → snap piso 160 (sem soft-down)."""
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
        assert r.char_m == 1.65
        assert r.octree == 160
        assert r.voxel_m > 1.65 / 256

    def test_large_building_steps_up(self) -> None:
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
        assert r.char_m == 10.0
        assert r.octree > 256
        assert r.octree <= _OCTREE_CEILING
        assert (r.octree - _OCTREE_FLOOR) % _OCTREE_STEP == 0

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
