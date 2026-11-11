"""Blend por vista das referências do Paint3D (sem GPU)."""

from __future__ import annotations

import numpy as np
from PIL import Image

from paint3d.hy3dpaint.textureGenPipeline import _blend_multiview_runs, _default_ref_azims


def _img(value: int) -> Image.Image:
    return Image.new("RGB", (4, 4), (value, value, value))


def _channel(image: Image.Image) -> int:
    return int(np.asarray(image)[0, 0, 0])


def test_default_ref_azims() -> None:
    assert _default_ref_azims(1) == [0.0]
    assert _default_ref_azims(2) == [0.0, 180.0]
    assert _default_ref_azims(4) == [0.0, 180.0, 90.0, 270.0]


def test_two_refs_select_front_and_back() -> None:
    """front/back: as vistas cardinais usam a ref do seu azimute; as laterais 50/50."""
    runs = [
        {"albedo": [_img(10), _img(20), _img(30), _img(40)]},
        {"albedo": [_img(110), _img(120), _img(130), _img(140)]},
    ]
    out = _blend_multiview_runs(runs, [0.0, 90.0, 180.0, 270.0], [0.0, 180.0])["albedo"]
    assert [_channel(i) for i in out] == [10, 70, 130, 90]


def test_four_refs_select_each_cardinal() -> None:
    refs = [0.0, 180.0, 90.0, 270.0]
    runs = [
        {"albedo": [_img(10), _img(20), _img(30), _img(40)]},
        {"albedo": [_img(60), _img(70), _img(80), _img(90)]},
        {"albedo": [_img(110), _img(120), _img(130), _img(140)]},
        {"albedo": [_img(160), _img(170), _img(180), _img(190)]},
    ]
    out = _blend_multiview_runs(runs, [0.0, 90.0, 180.0, 270.0], refs)["albedo"]
    # vista frontal → run da ref frontal; esquerda → ref esquerda; trás → trás; direita → direita
    assert [_channel(i) for i in out] == [10, 120, 80, 190]
