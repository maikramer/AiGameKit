"""Validador de tileability (seam-difference metric) para texturas 2D.

Implementa uma métrica rápida, sem dependências pesadas (apenas numpy + PIL),
que pontua o quão "tileable" (repetível sem costura visível) é uma imagem.

A métrica compara as bordas opostas da imagem (esquerda↔direita e topo↕base):
se as bordas coincidirem, a imagem ladrilha sem costura. Retorna um score 0..1
(mais alto = melhor) e o MSE por costura.

Isto é uma aproximação leve do classificador aprendido TexTile
(arXiv:2403.12961), que é deliberadamente **não** integrado aqui por ser
demasiado pesado para um MVP / portão de CI. A métrica de seam-MSE é a escolha
pragmática: objectiva, determinística e <50ms para 1024².
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy
from PIL import Image


@dataclass
class TileabilityReport:
    """Resultado da validação de tileability de uma imagem.

    Attributes:
        score: Score normalizado 0..1 (mais alto = mais tileable).
            ``1 - clamp(avg_mse / 255, 0, 1)`` onde ``avg_mse`` é a média do MSE
            das duas costuras (horizontal + vertical).
        edge_mse_horizontal: MSE entre as colunas da esquerda e da direita
            (costura que aparece ao repetir horizontalmente).
        edge_mse_vertical: MSE entre as linhas do topo e da base
            (costura que aparece ao repetir verticalmente).
        max_abs_edge_diff: Maior diferença absoluta de píxel (0..255) entre
            bordas opostas, considerando ambas as costuras.
        width: Largura da imagem avaliada (píxeis).
        height: Altura da imagem avaliada (píxeis).
    """

    score: float
    edge_mse_horizontal: float
    edge_mse_vertical: float
    max_abs_edge_diff: int
    width: int
    height: int

    def summary(self) -> str:
        """Resume o relatório numa string legível (uma linha por métrica)."""
        verdict = "PASS" if self.score >= 0.85 else "FAIL"
        return (
            f"Tileability: {verdict} (score={self.score:.4f})\n"
            f"  edge_mse_horizontal: {self.edge_mse_horizontal:.4f}\n"
            f"  edge_mse_vertical:   {self.edge_mse_vertical:.4f}\n"
            f"  max_abs_edge_diff:   {self.max_abs_edge_diff}\n"
            f"  size:                {self.width}x{self.height}"
        )

    def to_dict(self) -> dict[str, float | int]:
        """Converte o relatório num dicionário (serializável em JSON)."""
        d = asdict(self)
        d["verdict"] = "PASS" if self.score >= 0.85 else "FAIL"
        return d


def _to_rgb_array(image: Path | Image.Image) -> numpy.ndarray:
    """Carrega/converte a imagem para um array numpy uint8 (H, W, 3) em RGB.

    Args:
        image: Caminho para o ficheiro de imagem ou um ``PIL.Image`` aberto.

    Returns:
        Array numpy com shape ``(height, width, 3)`` e dtype ``uint8``.

    Raises:
        FileNotFoundError: Caminho não existe.
    """
    img = image if isinstance(image, Image.Image) else Image.open(image)
    return numpy.asarray(img.convert("RGB"), dtype=numpy.uint8)


def score_tileability(image: Path | Image.Image) -> TileabilityReport:
    """Pontua o quão tileable é uma imagem via seam-difference metric.

    Compara as N colunas mais à esquerda com as N mais à direita (costura
    horizontal) e as N linhas do topo com as N da base (costura vertical), onde
    ``N = max(1, min(8, width // 64))``. Calcula o MSE por costura e um score
    normalizado::

        score = 1 - clamp(avg_mse / 255, 0, 1)

    em que ``avg_mse`` é a média dos MSE das duas costuras. Também calcula o
    ``max_abs_edge_diff`` (maior diferença de píxel entre bordas opostas).

    Args:
        image: Caminho para o ficheiro de imagem ou um ``PIL.Image``.

    Returns:
        :class:`TileabilityReport` com o score e detalhes por costura.

    Raises:
        FileNotFoundError: ``image`` é um caminho que não existe.
        ValueError: Imagem demasiado pequena (largura ou altura < 2).
    """
    arr = _to_rgb_array(image)
    height, width = arr.shape[0], arr.shape[1]
    if width < 2 or height < 2:
        raise ValueError(f"Imagem demasiado pequena para avaliar tileability: {width}x{height}")

    n = max(1, min(8, width // 64))
    arr_f = arr.astype(numpy.float32)

    # Costura horizontal: colunas da esquerda vs colunas da direita.
    left = arr_f[:, :n, :]
    right = arr_f[:, -n:, :]
    h_diff = left - right
    mse_horizontal = float(numpy.mean(h_diff * h_diff))

    # Costura vertical: linhas do topo vs linhas da base.
    top = arr_f[:n, :, :]
    bottom = arr_f[-n:, :, :]
    v_diff = top - bottom
    mse_vertical = float(numpy.mean(v_diff * v_diff))

    avg_mse = (mse_horizontal + mse_vertical) / 2.0
    normalized = min(max(avg_mse / 255.0, 0.0), 1.0)
    score = 1.0 - normalized

    max_abs_edge_diff = int(max(float(numpy.max(numpy.abs(h_diff))), float(numpy.max(numpy.abs(v_diff)))))

    return TileabilityReport(
        score=score,
        edge_mse_horizontal=mse_horizontal,
        edge_mse_vertical=mse_vertical,
        max_abs_edge_diff=max_abs_edge_diff,
        width=int(width),
        height=int(height),
    )
