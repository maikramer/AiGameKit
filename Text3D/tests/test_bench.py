"""Testes puros do bench-decode (sem GPU)."""

from __future__ import annotations

from text3d.bench import recommend_latent_ceiling, summarize_report


def _row(
    octree: int,
    *,
    decoder: str = "flashvdm",
    mc_level: str = "auto",
    bounds_mode: str = "auto",
    pre_internal: float,
    boundary: int,
    post_internal: float = 0.0,
) -> dict:
    return {
        "case_id": f"o{octree}_{decoder}",
        "octree": octree,
        "decoder": decoder,
        "mc_level": mc_level,
        "bounds_mode": bounds_mode,
        "pre_drop": {
            "internal_volume_ratio": pre_internal,
            "n_internal": int(pre_internal > 0) * 10,
        },
        "metrics": {
            "boundary_edges": boundary,
            "internal_volume_ratio": post_internal,
            "internal_components": 0,
        },
    }


class TestRecommendLatentCeiling:
    def test_uses_pre_drop_not_post(self) -> None:
        # Pós-drop zera internals — sem PRE, ceiling iria a 512; com PRE, para em 256.
        results = [
            _row(256, pre_internal=0.005, boundary=100),
            _row(384, pre_internal=0.25, boundary=120),
            _row(512, pre_internal=0.40, boundary=140),
        ]
        assert recommend_latent_ceiling(results) == 256

    def test_boundary_growth_caps(self) -> None:
        results = [
            _row(256, pre_internal=0.001, boundary=100),
            _row(384, pre_internal=0.001, boundary=400),  # 4x > 2.0 default
        ]
        assert recommend_latent_ceiling(results) == 256

    def test_all_clean_returns_max(self) -> None:
        results = [
            _row(256, pre_internal=0.001, boundary=100),
            _row(448, pre_internal=0.01, boundary=110),
        ]
        assert recommend_latent_ceiling(results) == 448

    def test_summarize_includes_ceiling(self) -> None:
        results = [_row(320, pre_internal=0.001, boundary=50)]
        s = summarize_report(results)
        assert s["recommended_latent_ceiling"] == 320
        assert s["cases"] == 1
