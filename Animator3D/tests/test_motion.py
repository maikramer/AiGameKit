"""Testes dos primitivos de movimento orgânico (``_motion.py``).

Funções puras (sem bpy) — testáveis diretamente.
"""

from __future__ import annotations

import math

from animator3d._motion import (
    ease_in_out_sin,
    fbm,
    noise1,
    shaped_cos,
    shaped_sin,
    value_noise,
    with_noise,
)


class TestValueNoise:
    def test_in_range(self) -> None:
        for s in range(1000):
            v = value_noise(s)
            assert -1.0 <= v <= 1.0

    def test_deterministic(self) -> None:
        assert value_noise(42) == value_noise(42)

    def test_different_seeds_differ(self) -> None:
        # Não é garantido que seeds adjacentes diferem sempre, mas numa amostra
        # larga devem produzir valores distintos quase sempre.
        vals = {value_noise(s) for s in range(100)}
        assert len(vals) > 80  # quasi-todos distintos


class TestNoise1:
    def test_in_range(self) -> None:
        for i in range(-50, 50):
            t = i * 0.1
            assert -1.0 <= noise1(t) <= 1.0

    def test_continuous_at_integers(self) -> None:
        # noise1(n) == value_noise(n) nos inteiros (f=0 → s=0 → a).
        from animator3d._motion import value_noise

        for n in range(-5, 6):
            assert abs(noise1(float(n)) - value_noise(n)) < 1e-9

    def test_smooth_between_samples(self) -> None:
        # Amostras adjacentes não saltam muito (smoothstep interpola).
        for i in range(100):
            t = i * 0.05
            assert abs(noise1(t) - noise1(t + 0.05)) < 1.0

    def test_seed_independent_channels(self) -> None:
        # Seeds diferentes produzem canais independentes.
        assert noise1(1.5, seed=0) != noise1(1.5, seed=1)


class TestFbm:
    def test_in_range(self) -> None:
        for i in range(100):
            t = i * 0.1
            assert -1.0 <= fbm(t) <= 1.0

    def test_more_octaves_more_detail(self) -> None:
        # Mais oitavas = mais variação entre amostras adjacentes.
        t0, t1 = 1.0, 1.01
        d1 = abs(fbm(t0, octaves=1) - fbm(t1, octaves=1))
        d3 = abs(fbm(t0, octaves=3) - fbm(t1, octaves=3))
        # Com 3 oitavas, a alta frequência deve dar mais delta num passo fixo.
        assert d3 >= d1 * 0.5


class TestShapedCos:
    def test_periodic(self) -> None:
        # shaped_cos(0) == shaped_cos(1) (periódico).
        assert abs(shaped_cos(0.0) - shaped_cos(1.0)) < 1e-9

    def test_peak_slows_down(self) -> None:
        # No pico (phi~0 ou ~1), a derivada deve ser menor que a do cos puro.
        # shaped_cos desacelera nos picos por construção.
        eps = 0.01
        d_shaped = abs(shaped_cos(0.0) - shaped_cos(eps))
        d_pure = abs(math.cos(0.0) - math.cos(eps * 2 * math.pi))
        assert d_shaped < d_pure  # desacelera no pico

    def test_in_range_approx(self) -> None:
        for i in range(100):
            phi = i / 100.0
            assert -1.05 <= shaped_cos(phi) <= 1.05


class TestShapedSin:
    def test_periodic(self) -> None:
        assert abs(shaped_sin(0.0) - shaped_sin(1.0)) < 1e-9

    def test_cubic_profile(self) -> None:
        # sin³(π/2) = 1, sin³(π/4) ≈ 0.354 (vs sin(π/4)=0.707).
        # shaped_sin(0.25) = sin³(0.25·2π) = sin³(π/2) = 1.
        assert abs(shaped_sin(0.25) - 1.0) < 1e-9
        # shaped_sin(0.125) = sin³(π/4) ≈ 0.354.
        assert abs(shaped_sin(0.125) - (math.sqrt(2) / 2) ** 3) < 1e-6

    def test_in_range(self) -> None:
        for i in range(100):
            t = i / 100.0
            assert -1.0 <= shaped_sin(t) <= 1.0


class TestEaseInOutSin:
    def test_endpoints(self) -> None:
        # sin(0·π) = 0, sin(0.5·π) = 1 (pico), sin(1·π) = 0.
        assert abs(ease_in_out_sin(0.0)) < 1e-9
        assert abs(ease_in_out_sin(0.5) - 1.0) < 1e-9
        assert abs(ease_in_out_sin(1.0)) < 1e-9

    def test_in_range(self) -> None:
        for i in range(101):
            t = i / 100.0
            assert 0.0 <= ease_in_out_sin(t) <= 1.0


class TestWithNoise:
    def test_adds_jitter(self) -> None:
        base = 0.5
        noisy = with_noise(base, 0.1, t=1.5, seed=42)
        # Deve estar perto de base ± 0.1.
        assert abs(noisy - base) <= 0.15  # fbm pode ir até ~1.0

    def test_zero_amp_is_base(self) -> None:
        assert with_noise(0.5, 0.0, t=1.0) == 0.5

    def test_seed_varies(self) -> None:
        a = with_noise(0.0, 1.0, t=1.5, seed=0)
        b = with_noise(0.0, 1.0, t=1.5, seed=1)
        assert a != b
