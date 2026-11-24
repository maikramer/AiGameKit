"""Graduação de cor: põe a cor média da textura num alvo, sem tocar na estrutura.

O SD1.5 tem um prior de tom médio: medido a 2026-11-23 em nove prompts de
madeira, **todas** as imagens saíram com luminância média 96-128 de 255 e um
castanho ``#7x5x4x`` — um pinho quase preto (``#232B24``) dava ``#7F6A5C`` e
um branco de cal (``#E6DEC8``) dava ``#937E66``. A paleta não sai do prompt.

A graduação é afim e por canal, com uma escala comum: translate a média para o
alvo e comprime o contraste só pelo necessário para o miolo (percentis 1-99)
caber em 0-255. Uma escala comum preserva a cor das variações — o veio quente
continua quente sobre um alvo escuro — porque a escala de cada canal seria
diferente e mudava os tons.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy
from PIL import Image

# Percentis que definem o miolo: fora deles o corte é aceitável.
_INNER_LO = 1.0
_INNER_HI = 99.0
# Piso dos intervalos: uma imagem plana num canal não divide por zero.
_EPSILON = 1e-6


@dataclass(frozen=True)
class ColorMatch:
    """Resultado de :func:`match_mean_color`.

    Attributes:
        target: A cor pedida, ``#RRGGBB``.
        before: A cor média da imagem de entrada, ``#RRGGBB``.
        after: A cor média da imagem graduada, ``#RRGGBB`` (iguais ao alvo).
        scale: O factor comum aplicado ao contraste (1 = não comprimiu).
    """

    target: str
    before: str
    after: str
    scale: float

    def summary(self) -> str:
        """Resume a graduação numa linha legível."""
        return f"Color match: {self.before} → {self.after} (escala {self.scale:.3f})"

    def to_dict(self) -> dict[str, float | str]:
        """Converte num dicionário (serializável em JSON)."""
        return {"target": self.target, "before": self.before, "after": self.after, "scale": self.scale}


def parse_hex_color(text: str) -> tuple[float, float, float]:
    """Lê uma cor ``#RRGGBB`` (ou ``#RGB``) para ``(r, g, b)`` em 0-255.

    Raises:
        ValueError: Texto que não é uma cor hex de 3 ou 6 dígitos.
    """
    value = text.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(digit * 2 for digit in value)
    if len(value) != 6:
        raise ValueError(f"Cor inválida: {text!r} (esperado #RRGGBB)")
    try:
        return tuple(float(int(value[i : i + 2], 16)) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError as error:
        raise ValueError(f"Cor inválida: {text!r} (esperado #RRGGBB)") from error


def format_hex_color(color: tuple[float, float, float] | numpy.ndarray) -> str:
    """Escreve ``(r, g, b)`` em 0-255 como ``#RRGGBB``."""
    return "#" + "".join(f"{round(float(channel)):02X}" for channel in color)


def match_mean_color(
    image: Image.Image,
    target: str | tuple[float, float, float],
) -> tuple[Image.Image, ColorMatch]:
    """Gradua a imagem para a cor média ``target``, preservando a estrutura.

    Args:
        image: Imagem RGB(A); o resultado é sempre RGB.
        target: ``#RRGGBB`` ou ``(r, g, b)`` em 0-255.

    Returns:
        Tuple (imagem graduada, :class:`ColorMatch`).
    """
    wanted = parse_hex_color(target) if isinstance(target, str) else tuple(float(channel) for channel in target)
    arr = numpy.asarray(image.convert("RGB"), dtype=numpy.float64)
    flat = arr.reshape(-1, 3)
    mean = flat.mean(axis=0)
    lo = numpy.percentile(flat, _INNER_LO, axis=0)
    hi = numpy.percentile(flat, _INNER_HI, axis=0)

    # Escala comum: a maior que mantém o miolo de todos os canais dentro de 0-255.
    goal = numpy.asarray(wanted, dtype=numpy.float64)
    room_hi = (255.0 - goal) / numpy.maximum(hi - mean, _EPSILON)
    room_lo = goal / numpy.maximum(mean - lo, _EPSILON)
    scale = float(min(1.0, room_hi.min(), room_lo.min()))

    graded = numpy.clip(goal + (arr - mean) * scale, 0.0, 255.0)
    out = Image.fromarray(graded.round().astype("uint8"), "RGB")
    after = graded.reshape(-1, 3).mean(axis=0)
    return out, ColorMatch(
        target=format_hex_color(goal),
        before=format_hex_color(tuple(mean)),
        after=format_hex_color(tuple(after)),
        scale=scale,
    )
