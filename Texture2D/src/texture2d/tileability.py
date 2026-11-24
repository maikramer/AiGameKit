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
    rgb = img if img.mode == "RGB" else img.convert("RGB")
    return numpy.asarray(rgb, dtype=numpy.uint8)


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

    # Diferenças nas bordas reutilizadas para MSE e max_abs_edge_diff (evita recalcular).
    dh = arr_f[:, -1, :] - arr_f[:, 0, :]
    dv = arr_f[-1, :, :] - arr_f[0, :, :]
    max_abs_edge_diff = int(max(float(numpy.abs(dh).max()), float(numpy.abs(dv).max())))
    numpy.square(dh, out=dh)
    numpy.square(dv, out=dv)
    seam_h = float(dh.mean())
    seam_v = float(dv.mean())

    # Gradientes internos com buffer único reutilizável (~70% menos RAM temporária em 1024²).
    buf = numpy.empty((height, width - 1, 3), dtype=numpy.float32)
    numpy.subtract(arr_f[:, 1:, :], arr_f[:, :-1, :], out=buf)
    numpy.square(buf, out=buf)
    adj_h = float(buf.mean())

    n_v = (height - 1) * width * 3
    if n_v <= buf.size:
        buf_v = buf.reshape(-1)[:n_v].reshape(height - 1, width, 3)
    else:
        buf_v = numpy.empty((height - 1, width, 3), dtype=numpy.float32)
    numpy.subtract(arr_f[1:, :, :], arr_f[:-1, :, :], out=buf_v)
    numpy.square(buf_v, out=buf_v)
    adj_v = float(buf_v.mean())

    score = min(_direction_score(seam_h, adj_h), _direction_score(seam_v, adj_v))

    return TileabilityReport(
        score=float(score),
        edge_mse_horizontal=seam_h,
        edge_mse_vertical=seam_v,
        max_abs_edge_diff=max_abs_edge_diff,
        width=int(width),
        height=int(height),
    )


# ---------------------------------------------------------------------------
# Pormenor isolado (repetition metric)
# ---------------------------------------------------------------------------

# Raios do passa-bando para uma imagem de 1024² (escalam com o menor lado).
_SMALL_RADIUS_1024 = 6
_LARGE_RADIUS_1024 = 64
_POOL_RADIUS_1024 = 10


@dataclass(frozen=True)
class IsolatedDetail:
    """Resultado da métrica de pormenor isolado.

    Attributes:
        spot: Quanto o pormenor mais destacado foge aos restantes (0..1; mais
            baixo = melhor): ``band_max - band_p99`` do passa-bando.
        band_max: Maior destaque pontual (média do passa-bando numa vizinhança).
        band_p99: Percentil 99 do mesmo mapa — o "normal" da textura.
    """

    spot: float
    band_max: float
    band_p99: float

    def summary(self) -> str:
        """Resume a métrica numa linha legível."""
        return f"Isolated detail: spot={self.spot:.4f} (max={self.band_max:.4f}, p99={self.band_p99:.4f})"

    def to_dict(self) -> dict[str, float]:
        """Converte o relatório num dicionário (serializável em JSON)."""
        return asdict(self)


def _box_blur_circular(values: numpy.ndarray, radius: int) -> numpy.ndarray:
    """Média em caixa ``(2r+1)`` com fronteira circular, por imagem integral.

    A fronteira é circular porque a textura é um toro: um pormenor junto à
    costura não pode ser penalizado como se fosse um efeito de borda.
    """
    if radius <= 0:
        return values
    pad = ((radius, radius), (radius, radius)) + ((0, 0),) * (values.ndim - 2)
    out = numpy.pad(values, pad, mode="wrap")
    window = 2 * radius + 1
    for axis in (0, 1):
        zeros_shape = list(out.shape)
        zeros_shape[axis] = 1
        integral = numpy.concatenate(
            [numpy.zeros(zeros_shape, dtype=numpy.float64), numpy.cumsum(out, axis=axis, dtype=numpy.float64)],
            axis=axis,
        )
        hi = [slice(None)] * out.ndim
        lo = [slice(None)] * out.ndim
        hi[axis] = slice(window, None)
        lo[axis] = slice(0, -window)
        out = (integral[tuple(hi)] - integral[tuple(lo)]) / window
    return out.astype(numpy.float32)


def score_isolated_detail(image: Path | Image.Image) -> IsolatedDetail:
    """Mede o pormenor isolado que o tiling repetiria em cada célula.

    A costura pode ser contínua (:func:`score_tileability`) não basta: um nó
    na madeira, uma mancha ou um objeto solto repetem-se à mesma posição em
    todos os tiles e denunciam o padrão numa superfície comprida. O
    passa-bando (blur pequeno menos blur grande) isola o que é pequeno e se
    destaca da vizinhança; uma vizinhança maior o agrega e compara-se o máximo
    com o percentil 99. Numa textura densa (grão, pedra, telha) o máximo é
    quase o resto e ``spot`` fica abaixo de ~0,10 nas texturas do pool; um nó
    isolado com halo dá 0,17-0,30. A referência é o percentil 99 e não o 99,9:
    um nó com halo ocupa mais de 0,1% da imagem e o 99,9 caía dentro dele.

    Cerca de 0,4 s para 1024².

    Args:
        image: Caminho para um ficheiro de imagem ou um ``PIL.Image``.

    Returns:
        :class:`IsolatedDetail` com o destaque pontual e a referência.

    Raises:
        FileNotFoundError: ``image`` é um caminho que não existe.
        ValueError: Imagem demasiado pequena (menor lado < 16).
    """
    arr = _to_rgb_array(image).astype(numpy.float32) / 255.0
    height, width = arr.shape[0], arr.shape[1]
    shortest = min(width, height)
    if shortest < 16:
        raise ValueError(f"Imagem demasiado pequena para avaliar pormenor isolado: {width}x{height}")

    scale = shortest / 1024.0

    def radius(base: int) -> int:
        return max(1, round(base * scale))

    small = _box_blur_circular(arr, radius(_SMALL_RADIUS_1024))
    large = _box_blur_circular(arr, radius(_LARGE_RADIUS_1024))
    band = numpy.abs(small - large).max(axis=2)
    pooled = _box_blur_circular(band, radius(_POOL_RADIUS_1024))

    band_max = float(pooled.max())
    band_p99 = float(numpy.percentile(pooled, 99.0))
    return IsolatedDetail(
        spot=max(0.0, band_max - band_p99),
        band_max=band_max,
        band_p99=band_p99,
    )


# ---------------------------------------------------------------------------
# Estrutura: bandas por eixo e cauda pesada
# ---------------------------------------------------------------------------

# Piso do percentil de referência (imagem plana): evita dividir por ~0.
_BAND_FLOOR = 1e-4


@dataclass(frozen=True)
class AxisBanding:
    """Resultado da métrica de bandas por eixo.

    Mede o quanto uma linha (ou coluna) **inteira** se destaca no passa-bando,
    em unidades do percentil 99 do módulo do mesmo mapa.

    Attributes:
        rows: Máximo do valor absoluto da média de cada linha. Alto quando há
            estruturas coerentes na horizontal: veio deitado, tábuas com frestas.
        cols: O mesmo por coluna: veio direito, ripas verticais.
    """

    rows: float
    cols: float

    @property
    def worst(self) -> float:
        """O eixo pior: o valor a comparar com um limiar."""
        return max(self.rows, self.cols)

    def summary(self) -> str:
        """Resume a métrica numa linha legível."""
        return f"Axis banding: rows={self.rows:.3f}, cols={self.cols:.3f}"

    def to_dict(self) -> dict[str, float]:
        """Converte o relatório num dicionário (serializável em JSON)."""
        return asdict(self)


@dataclass(frozen=True)
class TailRatio:
    """Resultado da métrica de cauda pesada.

    Quanto a cauda (percentil 99,9) foge da mediana, em unidades do "normal
    brilhante" (percentil 95) — e o simétrico no lado escuro. Uma textura
    densa tem uma distribuição em sino e dá ~1,5-3; flocos ou pontos claros
    espalhados sobre um fundo liso são uma segunda população e dão >10.

    Attributes:
        high: Cauda clara: ``(p99.9 - mediana) / (p95 - mediana)``.
        low: Cauda escura: ``(mediana - p0.1) / (mediana - p5)``.
    """

    high: float
    low: float

    @property
    def worst(self) -> float:
        """O lado pior: o valor a comparar com um limiar."""
        return max(self.high, self.low)

    def summary(self) -> str:
        """Resume a métrica numa linha legível."""
        return f"Tail ratio: high={self.high:.2f}, low={self.low:.2f}"

    def to_dict(self) -> dict[str, float]:
        """Converte o relatório num dicionário (serializável em JSON)."""
        return asdict(self)


@dataclass(frozen=True)
class Structure:
    """As duas métricas de estrutura, calculadas no mesmo passa-bando."""

    banding: AxisBanding
    tail: TailRatio

    def to_dict(self) -> dict[str, dict[str, float]]:
        """Converte num dicionário (serializável em JSON)."""
        return {"banding": self.banding.to_dict(), "tail_ratio": self.tail.to_dict()}


def _luminance_band(arr: numpy.ndarray) -> tuple[numpy.ndarray, numpy.ndarray]:
    """Passa-bando **com sinal** da luminância e o seu percentil 99.

    O sinal importa aqui: a média de uma linha só é grande quando a estrutura
    é coerente ao longo dela — um veio ou uma fresta atravessam a linha toda.
    """
    scale = min(arr.shape[0], arr.shape[1]) / 1024.0

    def radius(base: int) -> int:
        return max(1, round(base * scale))

    luminance = arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722
    band = _box_blur_circular(luminance, radius(_SMALL_RADIUS_1024)) - _box_blur_circular(
        luminance, radius(_LARGE_RADIUS_1024)
    )
    return band, max(float(numpy.percentile(numpy.abs(band), 99.0)), _BAND_FLOOR)


def score_structure(image: Path | Image.Image) -> Structure:
    """Mede a estrutura da textura: bandas por eixo e cauda pesada.

    Duas falhas que a costura e o pormenor isolado não vêem, encontradas a
    gerar madeira (SD1.5, 1024²):

    - **tábuas com frestas** e **veio deitado**: o prompt pedia veio vertical e
      o modelo desenhou linhas horizontais. A média de cada linha do
      passa-bando denuncia a direção dominante (medido: tábuas 0,85, veio
      deitado 0,63, veio direito 0,26-0,44);
    - **flocos**: pontos claros espalhados sobre um fundo liso repetem-se em
      cada tile. São muitos, por isso o percentil 99 do pormenor isolado cai
      dentro deles — a cauda pesada dá 24 numa cal com flocos contra 1,5-3,2
      em todas as outras.

    Cerca de 0,5 s para 1024².

    Args:
        image: Caminho para um ficheiro de imagem ou ``PIL.Image``.

    Returns:
        :class:`Structure` com as duas métricas.

    Raises:
        FileNotFoundError: ``image`` é um caminho que não existe.
        ValueError: Imagem demasiado pequena (menor lado < 16).
    """
    arr = _to_rgb_array(image).astype(numpy.float32) / 255.0
    shortest = min(arr.shape[0], arr.shape[1])
    if shortest < 16:
        raise ValueError(f"Imagem demasiado pequena para avaliar a estrutura: {arr.shape[1]}x{arr.shape[0]}")

    band, p99 = _luminance_band(arr)
    rows = float(numpy.abs(band.mean(axis=1, dtype=numpy.float64)).max()) / p99
    cols = float(numpy.abs(band.mean(axis=0, dtype=numpy.float64)).max()) / p99
    median = float(numpy.median(band))

    def q(percentile: float) -> float:
        return float(numpy.percentile(band, percentile))

    tail = TailRatio(
        high=max(0.0, (q(99.9) - median) / (q(95.0) - median + _BAND_FLOOR)),
        low=max(0.0, (median - q(0.1)) / (median - q(5.0) + _BAND_FLOOR)),
    )
    return Structure(banding=AxisBanding(rows=rows, cols=cols), tail=tail)


def score_axis_banding(image: Path | Image.Image) -> AxisBanding:
    """Só as bandas por eixo (ver :func:`score_structure`)."""
    return score_structure(image).banding


def score_tail_ratio(image: Path | Image.Image) -> TailRatio:
    """Só a cauda pesada (ver :func:`score_structure`)."""
    return score_structure(image).tail
