"""Validador de tileability (seam-continuity metric) para texturas 2D.

Implementa uma métrica rápida, sem dependências pesadas (apenas numpy + PIL),
que pontua o quão "tileable" (repetível sem costura visível) é uma imagem.

A costura de uma textura tileable é **contínua**, não **igual**: a última
coluna deve parecer a vizinha natural da primeira (e idem nas linhas). Por
isso o score compara o salto na costura contra o gradiente interno típico da
própria textura::

    excess = max(0, seam_mse - adjacent_mse)          # costura pior que o normal
    score_dir = 1 - clamp(excess / (3*adjacent_mse + 25), 0, 1)

O piso absoluto (25) evita divisões ~0 em texturas planas. Score 0..1
(mais alto = melhor), ``min`` das duas direções.

Isto é uma aproximação leve do classificador aprendido TexTile
(arXiv:2403.12961), que é deliberadamente **não** integrado aqui por ser
demasiado pesado para um MVP / portão de CI. A métrica seam-continuity é a
escolha pragmática: objectiva, determinística e <50ms para 1024².
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy
from PIL import Image

# Piso absoluto do denominador (MSE): em texturas planas o gradiente interno
# é ~0 e qualquer denominador puro relativo explodiria.
_ABS_FLOOR_MSE = 25.0


@dataclass
class TileabilityReport:
    """Resultado da validação de tileability de uma imagem.

    Attributes:
        score: Score normalizado 0..1 (mais alto = mais tileable) — o quão
            perto o salto na costura está do gradiente interno típico.
        edge_mse_horizontal: MSE entre a última e a primeira colunas (costura
            que aparece ao repetir horizontalmente).
        edge_mse_vertical: MSE entre a última e a primeira linhas.
        max_abs_edge_diff: Maior diferença absoluta de píxel (0..255) entre
            bordas opostas, considerando ambas as costuras.
        width: Largura da imagem avaliada (pixéis).
        height: Altura da imagem avaliada (pixéis).
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


def _direction_score(seam_mse: float, adjacent_mse: float) -> float:
    """Score de uma direção: excesso da costura sobre o gradiente típico."""
    excess = max(0.0, seam_mse - adjacent_mse)
    return 1.0 - min(max(excess / (3.0 * adjacent_mse + _ABS_FLOOR_MSE), 0.0), 1.0)


def score_tileability(image: Path | Image.Image) -> TileabilityReport:
    """Pontua o quão tileable é uma imagem via seam-continuity metric.

    Compara o salto na costura (última↔primeira coluna; última↔primeira linha)
    com o gradiente interno típico da textura (MSE entre vizinhos adjacentes
    na mesma direção). Uma costura só penaliza quando é visivelmente pior do
    que as descontinuidades naturais do material (linhas de argamassa, grão,
    veios) — comparar bordas por igualdade (métrica antiga) pontuava 0 em
    qualquer textura de alta frequência, mesmo perfeitamente tileable.

    Args:
        image: Caminho para um ficheiro de imagem ou um ``PIL.Image``.

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

    arr_f = arr.astype(numpy.float32)

    # Costura horizontal: última coluna vs primeira coluna (vizinhança no wrap).
    seam_h = float(numpy.mean((arr_f[:, -1, :] - arr_f[:, 0, :]) ** 2))
    adj_h = float(numpy.mean((arr_f[:, 1:, :] - arr_f[:, :-1, :]) ** 2))

    # Costura vertical: última linha vs primeira linha.
    seam_v = float(numpy.mean((arr_f[-1, :, :] - arr_f[0, :, :]) ** 2))
    adj_v = float(numpy.mean((arr_f[1:, :, :] - arr_f[:-1, :, :]) ** 2))

    score = min(_direction_score(seam_h, adj_h), _direction_score(seam_v, adj_v))

    h_diff = numpy.abs(arr_f[:, -1, :] - arr_f[:, 0, :])
    v_diff = numpy.abs(arr_f[-1, :, :] - arr_f[0, :, :])
    max_abs_edge_diff = int(max(float(h_diff.max()), float(v_diff.max())))

    return TileabilityReport(
        score=float(score),
        edge_mse_horizontal=seam_h,
        edge_mse_vertical=seam_v,
        max_abs_edge_diff=max_abs_edge_diff,
        width=int(width),
        height=int(height),
    )
