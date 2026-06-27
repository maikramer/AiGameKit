"""Testes para texture2d.tileability (seam-difference metric)."""

from __future__ import annotations

import numpy
import pytest
from PIL import Image

from texture2d.tileability import TileabilityReport, score_tileability


def _make_wrapped_pattern(size: int = 256) -> Image.Image:
    """Cria um padrão perfeitamente tileable (bordas opostas idênticas).

    Base aleatória no interior; as primeiras/últimas N colunas e linhas são
    forçadas a coincidir (N = max(1, min(8, size//64))), garantindo MSE=0.
    """
    rng = numpy.random.default_rng(seed=7)
    arr = rng.integers(0, 256, size=(size, size, 3), dtype=numpy.uint8)
    n = max(1, min(8, size // 64))
    arr[:, -n:] = arr[:, :n]
    arr[-n:, :] = arr[:n, :]
    return Image.fromarray(arr, "RGB")


def _make_random_noise(size: int = 256) -> Image.Image:
    """Cria ruído aleatório (não tileable)."""
    rng = numpy.random.default_rng(seed=42)
    arr = rng.integers(0, 256, size=(size, size, 3), dtype=numpy.uint8)
    return Image.fromarray(arr, "RGB")


def _make_solid(size: int = 128, color: tuple[int, int, int] = (100, 150, 200)) -> Image.Image:
    """Cria uma cor sólida (trivialmente tileable)."""
    arr = numpy.full((size, size, 3), color, dtype=numpy.uint8)
    return Image.fromarray(arr, "RGB")


def _make_seamed(size: int = 128) -> Image.Image:
    """Cria um gradiente linear horizontal com costura forte na borda direita."""
    arr = numpy.zeros((size, size, 3), dtype=numpy.uint8)
    for x in range(size):
        arr[:, x, :] = int(x * 255 / max(size - 1, 1))
    return Image.fromarray(arr, "RGB")


class TestScoreTileableImages:
    def test_solid_color_scores_near_one(self):
        report = score_tileability(_make_solid())
        assert report.score == pytest.approx(1.0, abs=1e-6)
        assert report.edge_mse_horizontal == pytest.approx(0.0, abs=1e-6)
        assert report.edge_mse_vertical == pytest.approx(0.0, abs=1e-6)
        assert report.max_abs_edge_diff == 0

    def test_wrapped_pattern_scores_near_one(self):
        report = score_tileability(_make_wrapped_pattern(size=256))
        assert report.score == pytest.approx(1.0, abs=1e-9)
        assert report.edge_mse_horizontal == pytest.approx(0.0, abs=1e-9)
        assert report.edge_mse_vertical == pytest.approx(0.0, abs=1e-9)

    def test_random_noise_scores_low(self):
        report = score_tileability(_make_random_noise(size=256))
        assert report.score < 0.5

    def test_horizontal_gradient_is_not_tileable(self):
        # Gradiente horizontal: borda esq ~0, dir ~255 => costura forte.
        report = score_tileability(_make_seamed(size=128))
        assert report.score < 0.5
        assert report.max_abs_edge_diff > 100


class TestReportFields:
    def test_dimensions_recorded(self):
        report = score_tileability(_make_solid(size=64))
        assert report.width == 64
        assert report.height == 64

    def test_score_in_unit_range(self):
        for factory in (_make_solid, _make_wrapped_pattern, _make_random_noise, _make_seamed):
            report = score_tileability(factory())
            assert 0.0 <= report.score <= 1.0

    def test_max_abs_edge_diff_bounded(self):
        report = score_tileability(_make_random_noise(size=128))
        assert 0 <= report.max_abs_edge_diff <= 255


class TestInputTypes:
    def test_accepts_pil_image(self):
        report = score_tileability(_make_solid())
        assert isinstance(report, TileabilityReport)

    def test_accepts_path(self, tmp_path):
        p = tmp_path / "solid.png"
        _make_solid(size=64).save(p)
        report = score_tileability(p)
        assert isinstance(report, TileabilityReport)
        assert report.width == 64

    def test_path_and_image_agree(self, tmp_path):
        p = tmp_path / "pattern.png"
        img = _make_wrapped_pattern(size=128)
        img.save(p)
        r_path = score_tileability(p)
        r_img = score_tileability(img)
        assert r_path.score == pytest.approx(r_img.score, abs=1e-9)
        assert r_path.edge_mse_horizontal == pytest.approx(r_img.edge_mse_horizontal, abs=1e-9)

    def test_nonexistent_path_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            score_tileability(tmp_path / "nope.png")

    def test_too_small_raises(self):
        arr = numpy.zeros((1, 1, 3), dtype=numpy.uint8)
        with pytest.raises(ValueError):
            score_tileability(Image.fromarray(arr, "RGB"))


class TestSummaryAndDict:
    def test_summary_is_string_with_score(self):
        report = score_tileability(_make_solid())
        s = report.summary()
        assert isinstance(s, str)
        assert "score=" in s
        assert "PASS" in s

    def test_summary_shows_fail_for_noise(self):
        report = score_tileability(_make_random_noise(size=64))
        assert "FAIL" in report.summary()

    def test_to_dict_has_expected_keys(self):
        report = score_tileability(_make_solid(size=32))
        d = report.to_dict()
        for key in (
            "score",
            "edge_mse_horizontal",
            "edge_mse_vertical",
            "max_abs_edge_diff",
            "width",
            "height",
            "verdict",
        ):
            assert key in d
        assert d["verdict"] == "PASS"

    def test_to_dict_types(self):
        report = score_tileability(_make_solid(size=32))
        d = report.to_dict()
        assert isinstance(d["score"], float)
        assert isinstance(d["max_abs_edge_diff"], int)
        assert isinstance(d["width"], int)
        assert isinstance(d["height"], int)
