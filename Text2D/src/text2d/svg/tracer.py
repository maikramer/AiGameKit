"""Motor VTracer (Rust, CPU) — colour tracing determinístico."""

from __future__ import annotations

import io
import re
from typing import Any

from PIL import Image

from .options import DETAIL_PRESETS, SvgEngineUnavailable, SvgOptions

_HEX_RE = re.compile(r"fill\s*=\s*[\"'](#[0-9a-fA-F]{6})[\"']")
_D_RE = re.compile(r"\bd\s*=\s*[\"']([^\"']*)[\"']")


def _import_vtracer():
    try:
        import vtracer  # type: ignore
    except ImportError as exc:  # pragma: no cover - exercised on machines without it
        raise SvgEngineUnavailable(
            "vtracer não está instalado — instala com `pip install vtracer` "
            "ou usa `--svg-engine starvector`."
        ) from exc
    return vtracer


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


def _near(a: tuple[int, int, int], b: tuple[int, int, int], tolerance: int = 24) -> bool:
    return sum((x - y) ** 2 for x, y in zip(a, b, strict=True)) <= tolerance**2


def _pick_sentinel(image: Image.Image) -> tuple[int, int, int]:
    """Cor de fundo que não existe na paleta do ícone (para remover depois)."""

    colors = image.convert("RGB").getcolors(maxcolors=1 << 24) or []
    existing = [color for _count, color in colors]
    candidates = [
        (255, 0, 255),
        (0, 255, 0),
        (255, 255, 0),
        (0, 255, 255),
        (255, 0, 0),
        (0, 0, 255),
        (128, 0, 128),
        (255, 128, 0),
    ]
    for candidate in candidates:
        if all(not _near(candidate, color, tolerance=32) for color in existing):
            return candidate
    return (255, 0, 255)


def prepare_raster(
    image: Image.Image, opts: SvgOptions
) -> tuple[Image.Image, tuple[int, int, int] | None, bool]:
    """Prepara o raster e devolve ``(imagem, cor_de_fundo, exigir_canvas)``.

    Para imagens com alpha, o VTracer achata a transparência numa cor. Em vez
    de deixar preto/branco no SVG final, compõe-se sobre um sentinela ausente
    da paleta; os paths dessa cor são removidos depois do tracing. Em
    ``line`` usa-se branco e só se remove um path que cubra a tela inteira.
    """

    img = image.convert("RGBA")
    has_alpha = img.getchannel("A").getextrema()[0] < 255
    if not opts.transparent and not has_alpha:
        return img.convert("RGB"), None, False

    alpha = img.getchannel("A")
    if opts.style == "line":
        background = Image.new("RGB", img.size, (255, 255, 255))
        background.paste(img.convert("RGB"), mask=alpha)
        return background, (255, 255, 255), True

    rgb = img.convert("RGB")
    quantized = rgb.quantize(
        colors=int(opts.colors),
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.NONE,
    ).convert("RGB")
    sentinel = _pick_sentinel(quantized)
    background = Image.new("RGB", img.size, sentinel)
    background.paste(quantized, mask=alpha)
    return background, sentinel, False


def trace_params(opts: SvgOptions) -> dict[str, Any]:
    """Mapeia estilo/detalhe para os parâmetros do VTracer."""

    params: dict[str, Any] = dict(DETAIL_PRESETS[opts.detail])
    params.update(
        {
            "colormode": "binary" if opts.style == "line" else "color",
            # ``cutout`` preserva buracos/furos de ícones transparentes;
            # ``stacked`` é mais fiel em ilustrações com sobreposição.
            "hierarchical": "cutout" if opts.transparent else "stacked",
            "mode": "spline",
            "corner_threshold": 60,
            "max_iterations": 10,
            "splice_threshold": 45,
        }
    )
    return params


def _path_covers_canvas(d: str, canvas: tuple[int, int]) -> bool:
    width, height = canvas
    head = d[:240].replace(",", " ")
    return head.lstrip().startswith("M0 0") and (
        f"{width} {height}" in head or f"{height} {width}" in head
    )


def strip_background_paths(
    svg: str,
    color: tuple[int, int, int],
    *,
    canvas: tuple[int, int],
    require_canvas: bool,
) -> str:
    """Remove os paths do fundo (sentinela ou branco de line art)."""

    def _drop(match: re.Match[str]) -> str:
        tag = match.group(0)
        fill_match = _HEX_RE.search(tag)
        if not fill_match:
            return tag
        if not _near(_hex_to_rgb(fill_match.group(1)), color, tolerance=24):
            return tag
        if require_canvas:
            d_match = _D_RE.search(tag)
            if not d_match or not _path_covers_canvas(d_match.group(1), canvas):
                return tag
        return ""

    return re.sub(r"<path\b[^>]*>", _drop, svg, flags=re.IGNORECASE)


def trace_image(image: Image.Image, opts: SvgOptions) -> str:
    """Vectoriza uma imagem PIL e devolve o SVG (sem validar/sanitizar)."""

    vtracer = _import_vtracer()
    prepared, background, require_canvas = prepare_raster(image, opts)
    buffer = io.BytesIO()
    prepared.save(buffer, format="PNG")
    params = trace_params(opts)
    try:
        svg = vtracer.convert_raw_image_to_svg(
            buffer.getvalue(), img_format="png", **params
        )
    except TypeError:
        # Wheels antigos não aceitam ``hierarchical``/``path_precision``.
        params.pop("hierarchical", None)
        params.pop("path_precision", None)
        svg = vtracer.convert_raw_image_to_svg(
            buffer.getvalue(), img_format="png", **params
        )

    if background is not None:
        svg = strip_background_paths(
            svg,
            background,
            canvas=prepared.size,
            require_canvas=require_canvas,
        )
    return svg


class VtracerEngine:
    """Adaptador com a mesma interface do StarVectorEngine (worker/CLI)."""

    name = "vtracer"

    def __init__(self, **_kwargs: Any) -> None:
        # Falha cedo se o wheel não estiver instalado.
        _import_vtracer()

    def warmup(self) -> None:
        _import_vtracer()

    def vectorize(self, image: Image.Image, opts: SvgOptions) -> str:
        return trace_image(image, opts)

    def unload(self) -> None:
        return None
