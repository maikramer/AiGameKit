"""Testes do parser de cor hex para textura rápida (cor sólida/Perlin)."""

from __future__ import annotations

import pytest

from paint3d.quick_bake import parse_hex_rgb


class TestParseHexRgb:
    def test_full_hex_white(self) -> None:
        assert parse_hex_rgb("#ffffff") == (1.0, 1.0, 1.0)

    def test_full_hex_black(self) -> None:
        assert parse_hex_rgb("#000000") == (0.0, 0.0, 0.0)

    def test_normalized_to_unit_floats(self) -> None:
        r, g, b = parse_hex_rgb("#ff8800")
        assert r == pytest.approx(1.0)
        assert g == pytest.approx(0x88 / 255.0)
        assert b == pytest.approx(0.0)
        assert 0.0 <= r <= 1.0
        assert 0.0 <= g <= 1.0
        assert 0.0 <= b <= 1.0

    def test_shorthand_three_chars_expand(self) -> None:
        r, g, b = parse_hex_rgb("#f0a")
        assert r == pytest.approx(1.0)
        assert g == pytest.approx(0.0)
        assert b == pytest.approx(0xAA / 255.0)

    def test_no_hash_prefix_accepted(self) -> None:
        assert parse_hex_rgb("ff8800") == parse_hex_rgb("#ff8800")

    def test_uppercase_accepted(self) -> None:
        assert parse_hex_rgb("#FFFFFF") == (1.0, 1.0, 1.0)
        assert parse_hex_rgb("FFF") == (1.0, 1.0, 1.0)

    def test_whitespace_stripped(self) -> None:
        assert parse_hex_rgb("   #ffffff   ") == (1.0, 1.0, 1.0)

    @pytest.mark.parametrize("value", ["#xyz", "#12", "#gggggg", "", "#", "abcde"])
    def test_invalid_raises_value_error(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_hex_rgb(value)
