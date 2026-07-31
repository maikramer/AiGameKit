"""QualityEngine resolve for part3d tiers."""

from __future__ import annotations

from aigamekit_shared.quality import QualityEngine


def test_part3d_quality_tiers_have_steps_octree_chunks() -> None:
    engine = QualityEngine()
    expected = {
        "fast": (15, 128, 10000),
        "low": (20, 192, 15000),
        "medium": (30, 256, 20000),
        "high": (40, 320, 25000),
        "highest": (50, 320, 28000),
    }
    for quality, (steps, octree, chunks) in expected.items():
        r = engine.resolve("part3d", quality=quality)
        assert r.params["steps"] == steps, quality
        assert r.params["octree"] == octree, quality
        assert r.params["chunks"] == chunks, quality


def test_part3d_quality_kernel_knobs() -> None:
    engine = QualityEngine()
    high = engine.resolve("part3d", quality="high")
    assert high.params["volume_decoder"] == "auto"
    assert high.params["point_num"] == 64000
    assert high.params["prompt_num"] == 160
    assert high.params["mc_algo"] == "dmc"

    highest = engine.resolve("part3d", quality="highest")
    assert highest.params["volume_decoder"] == "auto"
    assert highest.params["octree"] == 320
    assert highest.params["point_num"] == 72000

    fast = engine.resolve("part3d", quality="fast")
    assert fast.params["volume_decoder"] == "flashvdm"
    assert fast.params["point_num"] == 32000
