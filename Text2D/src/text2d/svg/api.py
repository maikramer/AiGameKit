"""API de alto nível: vectorizar para ficheiro + validar/sanitizar/escrever."""

from __future__ import annotations

import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from PIL import Image

from .engines import render_svg
from .meta import write_svg_meta
from .options import SvgOptions, SvgResult
from .validate import SvgValidationError, check_size, normalize_svg


def image_has_alpha(image: Image.Image) -> bool:
    if image.mode not in ("RGBA", "LA", "PA"):
        return False
    alpha = image.getchannel("A")
    return alpha.getextrema()[0] < 255


def render_preview(svg: str, output_path: Path, *, size: int = 512) -> str | None:
    """Rasteriza o SVG para QA. Devolve um warning se o cairosvg faltar."""

    try:
        import cairosvg  # type: ignore
    except ImportError:
        return (
            "preview pedido mas cairosvg não está instalado "
            "(`pip install 'text2d[svg-preview]'`)"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    png = cairosvg.svg2png(
        bytestring=svg.encode("utf-8"),
        output_width=int(size),
        output_height=int(size),
    )
    tmp = output_path.with_name(output_path.name + ".tmp")
    tmp.write_bytes(png)
    tmp.replace(output_path)
    return None


def save_svg(
    raw_svg: str,
    output_path: str | Path,
    opts: SvgOptions,
    *,
    source_image: str | Path | None = None,
    prompt: str | None = None,
    seed: int | None = None,
    engine_meta: dict[str, Any] | None = None,
    width: int | None = None,
    height: int | None = None,
    seconds: float | None = None,
) -> SvgResult:
    """Normaliza, valida, escreve o SVG e (opcionalmente) preview/meta."""

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    if (width is None or height is None) and source_image:
        source_path = Path(source_image)
        if source_path.is_file():
            with Image.open(source_path) as image:
                width, height = image.size

    svg, path_count = normalize_svg(raw_svg, width=width, height=height)
    warning = check_size(svg, max_kb=opts.max_kb, strict=opts.strict)
    if path_count == 0:
        message = "SVG sem paths (geração truncada?)"
        if opts.strict:
            raise SvgValidationError(message)
        warning = f"{warning}; {message}" if warning else message

    tmp = output.with_name(output.name + ".tmp")
    tmp.write_text(svg + "\n", encoding="utf-8")
    tmp.replace(output)

    meta = dict(engine_meta or {})
    result = SvgResult(
        path=output,
        engine=str(meta.get("engine") or opts.engine),
        path_count=path_count,
        bytes=output.stat().st_size,
        style=opts.style,
        colors=int(opts.colors),
        detail=opts.detail,
        source_image=str(source_image) if source_image else None,
        prompt=prompt,
        seed=seed,
        model_id=meta.get("model_id") or opts.model_id,
        params=dict(meta.get("params") or {}),
        warning=warning,
        seconds=seconds,
    )

    if opts.preview:
        size = max(int(width or 512), int(height or 512))
        preview_warning = render_preview(
            svg, output.with_name(f"{output.stem}_preview.png"), size=size
        )
        if preview_warning:
            result.warning = (
                f"{result.warning}; {preview_warning}" if result.warning else preview_warning
            )

    if opts.meta:
        write_svg_meta(output, result)

    return result


def vectorize_file(
    image_path: str | Path,
    output_path: str | Path,
    opts: SvgOptions,
    *,
    engine: Any | None = None,
    prompt: str | None = None,
    seed: int | None = None,
    verbose: bool = False,
) -> SvgResult:
    """Vectoriza um ficheiro de imagem num SVG validado."""

    source = Path(image_path).resolve()
    if not source.is_file():
        raise FileNotFoundError(f"imagem não encontrada: {source}")

    started = time.perf_counter()
    with Image.open(source) as image:
        image.load()
        if not opts.transparent and image_has_alpha(image):
            opts = replace(opts, transparent=True)
        width, height = image.size
        raw, engine_meta = render_svg(
            image, opts, engine=engine, category=None, verbose=verbose
        )

    return save_svg(
        raw,
        output_path,
        opts,
        source_image=source,
        prompt=prompt,
        seed=seed,
        engine_meta=engine_meta,
        width=width,
        height=height,
        seconds=time.perf_counter() - started,
    )
