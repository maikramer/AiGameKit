"""Tests for param_optimizer: tier-based Text3D / Paint3D parameter selection."""

from __future__ import annotations

from gameassets.param_optimizer import (
    optimize_paint_for_target,
    optimize_text3d_for_target,
    should_optimize_paint,
    should_optimize_text3d,
)
from gameassets.profile import Text3DProfile


class TestOptimizeText3D:
    def test_tier1_very_low_targets(self) -> None:
        opts = optimize_text3d_for_target(100)
        assert opts.octree_resolution == 80
        assert opts.steps == 12
        assert opts.num_chunks == 2048

    def test_tier1_boundary(self) -> None:
        opts = optimize_text3d_for_target(1200)
        assert opts.octree_resolution == 80

    def test_tier2_medium_targets(self) -> None:
        opts = optimize_text3d_for_target(1500)
        assert opts.octree_resolution == 128
        assert opts.steps == 18
        assert opts.num_chunks == 4096

    def test_tier2_boundary(self) -> None:
        opts = optimize_text3d_for_target(2500)
        assert opts.octree_resolution == 128

    def test_tier3_complex(self) -> None:
        opts = optimize_text3d_for_target(3000)
        assert opts.octree_resolution == 192
        assert opts.steps == 24
        assert opts.num_chunks == 6000

    def test_tier3_boundary(self) -> None:
        opts = optimize_text3d_for_target(5000)
        assert opts.octree_resolution == 192

    def test_tier4_high(self) -> None:
        opts = optimize_text3d_for_target(8000)
        assert opts.octree_resolution == 256
        assert opts.steps == 30
        assert opts.num_chunks == 8000

    def test_tier4_very_high(self) -> None:
        opts = optimize_text3d_for_target(50000)
        assert opts.octree_resolution == 256


class TestOptimizePaint:
    def test_simple_category_gets_perlin(self) -> None:
        opts = optimize_paint_for_target(800)
        assert opts.paint_style == "perlin"
        assert opts.paint_max_views is None
        assert opts.paint_texture_size is None

    def test_simple_boundary(self) -> None:
        opts = optimize_paint_for_target(1200)
        assert opts.paint_style == "perlin"

    def test_medium_category_keeps_hunyuan(self) -> None:
        opts = optimize_paint_for_target(2000)
        assert opts.paint_style is None
        assert opts.paint_max_views == 2
        assert opts.paint_view_resolution == 384
        assert opts.paint_texture_size == 2048

    def test_medium_boundary(self) -> None:
        opts = optimize_paint_for_target(2500)
        assert opts.paint_style is None
        assert opts.paint_max_views == 2

    def test_high_category_full_quality(self) -> None:
        opts = optimize_paint_for_target(8000)
        assert opts.paint_style is None
        assert opts.paint_max_views == 4
        assert opts.paint_view_resolution == 512
        assert opts.paint_texture_size == 4096


class TestShouldOptimize:
    def test_optimize_text3d_when_no_overrides(self) -> None:
        t3 = Text3DProfile()
        assert should_optimize_text3d(t3) is True

    def test_dont_optimize_text3d_with_preset(self) -> None:
        t3 = Text3DProfile(preset="fast")
        assert should_optimize_text3d(t3) is False

    def test_dont_optimize_text3d_with_explicit_steps(self) -> None:
        t3 = Text3DProfile(steps=30)
        assert should_optimize_text3d(t3) is False

    def test_dont_optimize_text3d_with_explicit_octree(self) -> None:
        t3 = Text3DProfile(octree_resolution=256)
        assert should_optimize_text3d(t3) is False

    def test_dont_optimize_text3d_with_explicit_chunks(self) -> None:
        t3 = Text3DProfile(num_chunks=8000)
        assert should_optimize_text3d(t3) is False

    def test_optimize_paint_when_no_overrides(self) -> None:
        t3 = Text3DProfile()
        assert should_optimize_paint(t3) is True

    def test_dont_optimize_paint_with_max_views(self) -> None:
        t3 = Text3DProfile(paint_max_views=2)
        assert should_optimize_paint(t3) is False

    def test_dont_optimize_paint_with_view_resolution(self) -> None:
        t3 = Text3DProfile(paint_view_resolution=384)
        assert should_optimize_paint(t3) is False

    def test_dont_optimize_paint_with_texture_size(self) -> None:
        t3 = Text3DProfile(paint_texture_size=2048)
        assert should_optimize_paint(t3) is False
