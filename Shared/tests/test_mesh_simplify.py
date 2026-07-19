"""Testes do simplificador unificado (Decimate + perfis)."""

from __future__ import annotations

from gamedev_shared.mesh_simplify import clamp_decimate_target


class TestClampDecimateTarget:
    def test_raises_floor_on_aggressive_ratio(self) -> None:
        # 246k → 750 era o caso LOD2 que destruía silhueta
        assert clamp_decimate_target(246_013, 750) >= int(246_013 * 0.008)

    def test_keeps_reasonable_request(self) -> None:
        assert clamp_decimate_target(100_000, 10_000) == 10_000

    def test_allows_request_above_current(self) -> None:
        assert clamp_decimate_target(1000, 2000) == 2000

    def test_min_faces_floor(self) -> None:
        # n pequeno: piso absoluto 150
        assert clamp_decimate_target(5_000, 10) >= 150
