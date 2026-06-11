"""Testes do alpha keying anti-placa (fundo uniforme sem BiRefNet)."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from text3d.utils.bg_removal import has_meaningful_alpha, key_uniform_background


def _white_bg_object(size: int = 256) -> Image.Image:
    img = Image.new("RGB", (size, size), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.ellipse((60, 80, 200, 230), fill=(120, 60, 40))
    return img


def test_has_meaningful_alpha() -> None:
    assert has_meaningful_alpha(Image.new("RGB", (8, 8))) is False
    assert has_meaningful_alpha(Image.new("RGBA", (8, 8), (0, 0, 0, 255))) is False  # alpha trivial
    assert has_meaningful_alpha(Image.new("RGBA", (8, 8), (0, 0, 0, 0))) is True


def test_keying_white_background() -> None:
    keyed = key_uniform_background(_white_bg_object())
    assert keyed is not None
    assert keyed.mode == "RGBA"
    a = np.asarray(keyed.getchannel("A"))
    assert a[0, 0] == 0, "canto deve ficar transparente"
    assert a[150, 130] == 255, "objecto deve ficar opaco"
    # Fundo maioritário transparente, objecto minoritário opaco.
    assert 0.05 < (a == 255).mean() < 0.6


def test_keying_rejects_nonuniform_background() -> None:
    rng = np.random.default_rng(0)
    noisy = Image.fromarray(rng.integers(0, 255, (128, 128, 3), dtype=np.uint8), "RGB")
    assert key_uniform_background(noisy) is None


def test_keying_does_not_eat_white_inside_object() -> None:
    """Branco interno (olhos, brilho de espada) não conectado à borda fica opaco."""
    img = _white_bg_object()
    d = ImageDraw.Draw(img)
    d.ellipse((120, 120, 140, 140), fill=(255, 255, 255))  # "olho" branco dentro do objecto
    keyed = key_uniform_background(img)
    assert keyed is not None
    a = np.asarray(keyed.getchannel("A"))
    assert a[130, 130] == 255, "branco interior não pode virar transparente"
