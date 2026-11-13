"""Extracção, reparação e sanitização de SVG gerado por modelo.

O StarVector pode devolver markdown, texto antes do ``<svg>``, tags por
fechar (limite de tokens) ou até marcas perigosas. Nada disto entra num
ficheiro do jogo sem passar por aqui. O VTracer é determinístico e limpo, mas
passa pelo mesmo caminho para garantir dimensões e um contrato único.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from pathlib import Path

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

# Tags que podem aparecer num ícone/ilustração vectorial. ``script``,
# ``foreignObject``, ``style``, ``image`` e afins ficam de fora.
ALLOWED_TAGS = {
    "svg",
    "g",
    "defs",
    "path",
    "rect",
    "circle",
    "ellipse",
    "polygon",
    "polyline",
    "line",
    "linearGradient",
    "radialGradient",
    "stop",
    "clipPath",
    "mask",
    "use",
    "symbol",
    "title",
    "desc",
}
_SHAPE_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}
_VOID_TAGS = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line", "stop", "use"}

_HTML_ENTITIES = {
    "&nbsp;": " ",
    "&mdash;": "-",
    "&ndash;": "-",
    "&hellip;": "...",
    "&quot;": "&quot;",
}

_TAG_RE = re.compile(r"<\s*(/?)\s*([A-Za-z_][\w:.-]*)([^>]*?)(/?)\s*>", re.DOTALL)
_ENTITY_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]+);")
_SAFE_STYLE_RE = re.compile(r"url\s*\(|expression\s*\(|javascript\s*:", re.IGNORECASE)


class SvgValidationError(ValueError):
    """O texto gerado não é um SVG utilizável."""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _iter_local_names(text: str) -> Iterable[str]:
    for match in _TAG_RE.finditer(text):
        yield match.group(2)


def extract_svg(raw: str) -> str:
    """Extrai o bloco ``<svg>…</svg>`` de markdown/prosa."""

    if not raw or not str(raw).strip():
        raise SvgValidationError("SVG vazio")

    text = str(raw).replace("\ufeff", "").strip()

    fenced = re.search(r"```(?:svg|xml)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()

    start = re.search(r"<svg\b", text, flags=re.IGNORECASE)
    if not start:
        raise SvgValidationError("o texto não contém um elemento <svg>")
    text = text[start.start() :]

    end = text.lower().rfind("</svg>")
    if end != -1:
        text = text[: end + len("</svg>")]

    # XML decl/DOCTYPE/ENTITY não são necessários e abrem a porta a XXE.
    text = re.sub(r"<\?xml[^>]*\?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<!ENTITY[^>]*>", "", text, flags=re.IGNORECASE)
    for entity, replacement in _HTML_ENTITIES.items():
        text = text.replace(entity, replacement)
    return text.strip()


def repair_truncated(svg: str) -> str:
    """Fecha tags abertas quando o modelo bate no limite de tokens."""

    text = svg.strip()
    if not text:
        raise SvgValidationError("SVG vazio")

    lower = text.lower()
    if "</svg>" in lower:
        return text

    # Se a última tag ficou cortada a meio (ex.: <path d="M0 0 L…), descarta
    # o fragmento incompleto antes de fechar a pilha.
    last_close = text.rfind(">")
    if last_close != -1 and "<" in text[last_close + 1 :]:
        text = text[: last_close + 1]

    stack: list[str] = []
    for match in _TAG_RE.finditer(text):
        closing, name, _attrs, self_close = match.groups()
        lname = name.lower()
        if closing:
            if lname in _VOID_TAGS:
                continue
            if stack and stack[-1] == name:
                stack.pop()
            elif name in stack:
                while stack and stack.pop() != name:
                    pass
        elif not self_close and lname not in _VOID_TAGS:
            stack.append(name)

    for name in reversed(stack):
        text += f"</{name}>"
    if "</svg>" not in text.lower():
        text += "</svg>"
    return text


def _clean_attributes(element: ET.Element) -> None:
    for key in list(element.attrib):
        local = _local_name(key).lower()
        value = element.attrib.get(key, "")
        if local.startswith("on"):
            del element.attrib[key]
            continue
        if local in {"href", "xlink:href"}:
            if not value.strip().startswith("#"):
                del element.attrib[key]
            continue
        if local == "style" and _SAFE_STYLE_RE.search(value):
            del element.attrib[key]
            continue
        if local in {"src", "data"}:
            del element.attrib[key]


def _sanitize_tree(root: ET.Element) -> None:
    for parent in root.iter():
        for child in list(parent):
            if _local_name(child.tag) not in ALLOWED_TAGS:
                parent.remove(child)
            else:
                _clean_attributes(child)
    _clean_attributes(root)


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    match = re.match(r"\s*(-?\d+(?:\.\d+)?)\s*(?:px)?\s*$", str(value))
    if not match:
        return None
    return float(match.group(1))


def _ensure_dimensions(root: ET.Element, width: int | None, height: int | None) -> None:
    view_box = root.get("viewBox")
    vb_w: float | None = None
    vb_h: float | None = None
    if view_box:
        parts = re.split(r"[\s,]+", view_box.strip())
        if len(parts) == 4:
            try:
                vb_w = abs(float(parts[2]))
                vb_h = abs(float(parts[3]))
            except ValueError:
                vb_w = vb_h = None

    out_w = _parse_number(root.get("width")) or vb_w or (float(width) if width else None)
    out_h = _parse_number(root.get("height")) or vb_h or (float(height) if height else None)

    if not view_box and out_w and out_h:
        root.set("viewBox", f"0 0 {out_w:g} {out_h:g}")

    if width is not None:
        root.set("width", str(int(width)))
    elif out_w is not None:
        root.set("width", f"{out_w:g}")
    if height is not None:
        root.set("height", str(int(height)))
    elif out_h is not None:
        root.set("height", f"{out_h:g}")

    if _local_name(root.tag) == "svg" and "xmlns" not in root.attrib and not root.tag.startswith("{"):
        root.set("xmlns", SVG_NS)


def normalize_svg(
    raw: str,
    *,
    width: int | None = None,
    height: int | None = None,
) -> tuple[str, int]:
    """Extrai, repara, sanitiza e normaliza um SVG.

    Returns:
        ``(svg_text, path_count)`` — ``path_count`` conta paths e formas
        básicas (o métrico que o CLI mostra).

    Raises:
        SvgValidationError: XML inválido ou raiz diferente de ``<svg>``.
    """

    text = repair_truncated(extract_svg(raw))

    # Entidades desconhecidas (``&foo;``) não são XML válido. ``&amp;`` e as
    # entidades numéricas ficam intactas.
    def _keep_known(match: re.Match[str]) -> str:
        name = match.group(1).lower()
        if name in {"amp", "lt", "gt", "apos", "quot"}:
            return match.group(0)
        if name.startswith("#"):
            return match.group(0)
        return " "

    text = _ENTITY_RE.sub(_keep_known, text)

    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        snippet = text[:240].replace("\n", " ")
        raise SvgValidationError(f"XML inválido: {exc} ({snippet!r})") from exc

    if _local_name(root.tag) != "svg":
        raise SvgValidationError(f"raiz {_local_name(root.tag)!r} não é <svg>")

    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    _sanitize_tree(root)
    _ensure_dimensions(root, width, height)

    svg = ET.tostring(root, encoding="unicode")
    if not svg.lstrip().startswith("<svg"):
        raise SvgValidationError("a serialização não produziu um <svg>")

    path_count = sum(1 for el in root.iter() if _local_name(el.tag) in _SHAPE_TAGS)
    return svg, path_count


def count_svg_shapes(svg: str) -> int:
    """Conta paths/formas de um SVG já escrito (sem o reescrever)."""

    try:
        root = ET.fromstring(svg)
    except ET.ParseError:
        return 0
    return sum(1 for el in root.iter() if _local_name(el.tag) in _SHAPE_TAGS)


def count_svg_shapes_file(path: str | Path) -> int:
    try:
        return count_svg_shapes(Path(path).read_text(encoding="utf-8"))
    except OSError:
        return 0


def check_size(svg: str, *, max_kb: int, strict: bool) -> str | None:
    """Verifica o orçamento de bytes; devolve um warning ou levanta em strict."""

    size_kb = len(svg.encode("utf-8")) / 1024
    if size_kb <= max_kb:
        return None
    message = f"SVG com {size_kb:.0f} KB excede --svg-max-kb {max_kb} (paths a mais?)"
    if strict:
        raise SvgValidationError(message)
    return message
