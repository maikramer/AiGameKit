"""Opções e resultados do pipeline text2svg do Text2D."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

SVG_ENGINES = ("auto", "vtracer", "starvector")
SVG_STYLES = ("flat", "line", "detailed")
SVG_DETAILS = ("low", "medium", "high")


class SvgEngineUnavailable(RuntimeError):
    """O motor pedido não está instalado/cacheado neste ambiente."""

# Hints de prompt por estilo (ver ``svg.prompt``).
STYLE_HINTS: dict[str, str] = {
    "flat": (
        "flat vector illustration, solid flat colors, clean geometric shapes, "
        "bold simple silhouette, limited palette of {colors} colors, crisp edges, "
        "no gradients, no textures, no shading, no shadows, plain background"
    ),
    "line": (
        "clean monochrome line art, bold uniform outlines, white background, "
        "no fill, no gradients, no shading, high contrast, simple shapes"
    ),
    "detailed": (
        "stylized flat illustration, limited palette of {colors} colors, clean shapes, "
        "subtle cel shading, no gradients, no textures, crisp edges"
    ),
}

STYLE_MARKERS: dict[str, str] = {
    "flat": "flat vector illustration",
    "line": "monochrome line art",
    "detailed": "stylized flat illustration",
}

# Parâmetros do VTracer por nível de detalhe. O ``layer_difference`` baixo
# produz mais camadas de cor (SVG maior); o ``filter_speckle`` alto remove
# manchas pequenas.
DETAIL_PRESETS: dict[str, dict[str, float | int]] = {
    "low": {
        "filter_speckle": 8,
        "length_threshold": 6.0,
        "path_precision": 2,
        "layer_difference": 24,
        "color_precision": 5,
    },
    "medium": {
        "filter_speckle": 4,
        "length_threshold": 4.0,
        "path_precision": 3,
        "layer_difference": 16,
        "color_precision": 6,
    },
    "high": {
        "filter_speckle": 2,
        "length_threshold": 3.5,
        "path_precision": 4,
        "layer_difference": 8,
        "color_precision": 8,
    },
}


@dataclass
class SvgOptions:
    """Opções comuns a ``generate --svg``, ``vectorize`` e ao backend vramd."""

    engine: str = "auto"
    style: str = "flat"
    colors: int = 8
    detail: str = "medium"
    max_tokens: int = 4000
    seed: int | None = None
    device: str = "auto"
    preview: bool = False
    transparent: bool = False
    enhance: bool = True
    meta: bool = True
    strict: bool = False
    max_kb: int = 2048
    keep_raster: bool = False
    model_id: str | None = None
    gpu_ids: list[int] | None = None

    def validate(self) -> None:
        if self.engine not in SVG_ENGINES:
            raise ValueError(f"--svg-engine inválido: {self.engine!r} (use {', '.join(SVG_ENGINES)})")
        if self.style not in SVG_STYLES:
            raise ValueError(f"--svg-style inválido: {self.style!r} (use {', '.join(SVG_STYLES)})")
        if self.detail not in SVG_DETAILS:
            raise ValueError(f"--svg-detail inválido: {self.detail!r} (use {', '.join(SVG_DETAILS)})")
        if not 2 <= int(self.colors) <= 64:
            raise ValueError("--svg-colors tem de estar entre 2 e 64")
        if int(self.max_tokens) < 64:
            raise ValueError("--svg-max-tokens tem de ser >= 64")
        if int(self.max_kb) < 1:
            raise ValueError("--svg-max-kb tem de ser >= 1")
        if self.device not in ("auto", "cuda", "cpu"):
            raise ValueError(f"--svg-device inválido: {self.device!r}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> SvgOptions:
        """Constrói a partir de um request vramd, ignorando chaves alheias."""

        allowed = {item.name for item in fields(cls)}
        return cls(**{key: value for key, value in data.items() if key in allowed and value is not None})


@dataclass
class SvgResult:
    """Resultado de uma vectorização (sem o texto SVG, que pode ser grande)."""

    path: Path
    engine: str
    path_count: int = 0
    bytes: int = 0
    style: str = "flat"
    colors: int = 8
    detail: str = "medium"
    source_image: str | None = None
    prompt: str | None = None
    seed: int | None = None
    model_id: str | None = None
    params: dict[str, Any] = field(default_factory=dict)
    warning: str | None = None
    seconds: float | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["path"] = str(self.path)
        return data


def resolve_engine(
    engine: str,
    *,
    category: str | None = None,
    starvector_cached: bool | None = None,
) -> str:
    """Resolve ``auto`` sem surpresas de download.

    ``auto`` só escolhe StarVector quando o pedido é explicitamente um ícone
    (``--category icon``) e os pesos já estão no cache HF; caso contrário usa
    VTracer (CPU, determinístico). Um ``--svg-engine starvector`` explícito
    permite o download e falha com hint se o modelo não carregar.
    """

    if engine != "auto":
        return engine
    if category == "icon":
        if starvector_cached is None:
            from .engines import starvector_available

            starvector_cached = starvector_available(require_cached=True)
        if starvector_cached:
            return "starvector"
    return "vtracer"
