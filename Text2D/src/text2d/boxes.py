"""Validação de layout por bounding boxes (layout control do FLUX.3 Image Klein).

Cada box orienta **colocação e escala** — não é máscara dura de pixels. Schema
(de acordo com a documentação diffusers do FLUX.3):

    {"id": "balloon_1", "desc": "a red hot-air balloon", "bbox": [y0, x0, y1, x1]}

- ``bbox``: ``[y_min, x_min, y_max, x_max]`` em 0-1 (origem top-left,
  independente do tamanho de saída). Obrigatório.
- ``id`` (str) e ``desc`` (str): obrigatórios — o caption cita ``<id>``.
- ``from`` (opcional): ``"target"`` (adicionar elemento novo; usa ``tgt_bbox``)
  ou ``"ref_image_N"`` (mover elemento de uma referência; usa ``src_bbox`` +
  ``tgt_bbox``; target null/remove). Valores permitidos validados contra o nº
  de referências quando disponível.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_REF_RE = re.compile(r"^ref_image_(\d+)$")


def _validate_bbox(bbox: Any, where: str) -> list[float]:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        raise ValueError(f"{where}.bbox deve ser [y_min, x_min, y_max, x_max], recebido {bbox!r}")
    try:
        vals = [float(v) for v in bbox]
    except (TypeError, ValueError) as e:
        raise ValueError(f"{where}.bbox tem valores não numéricos: {bbox!r}") from e
    if any(v < 0.0 or v > 1.0 for v in vals):
        raise ValueError(f"{where}.bbox fora do intervalo 0-1: {vals}")
    y0, x0, y1, x1 = vals
    if y1 <= y0 or x1 <= x0:
        raise ValueError(f"{where}.bbox vazio ou invertido (precisa y1>y0 e x1>x0): {vals}")
    return vals


def validate_box(box: Any, *, num_refs: int = 0) -> dict[str, Any]:
    """Valida um dict de box individual e devolve uma cópia normalizada.

    Raises:
        ValueError: schema inválido (falta campo, bbox fora de 0-1, ``from``
            desconhecido ou a apontar a referência inexistente).
    """
    if not isinstance(box, dict):
        raise ValueError(f"box deve ser um objeto, recebido {type(box).__name__}")
    for key in ("id", "desc", "bbox"):
        if key not in box:
            raise ValueError(f"box falta campo obrigatório '{key}': {box!r}")
    if not isinstance(box["id"], str) or not box["id"]:
        raise ValueError(f"box.id deve ser string não vazia: {box['id']!r}")
    if not isinstance(box["desc"], str) or not box["desc"]:
        raise ValueError(f"box.desc deve ser string não vazia: {box['desc']!r}")
    out: dict[str, Any] = {
        "id": box["id"],
        "desc": box["desc"],
        "bbox": _validate_bbox(box["bbox"], f"box '{box['id']}'"),
    }
    if "from" in box and box["from"] is not None:
        src = str(box["from"])
        match = _REF_RE.match(src)
        if src != "target" and match is None:
            raise ValueError(f"box '{box['id']}': from deve ser 'target' ou 'ref_image_N', recebido {src!r}")
        if match is not None and int(match.group(1)) >= num_refs:
            raise ValueError(f"box '{box['id']}': from aponta para {src} mas só há {num_refs} referência(s)")
        out["from"] = src
    for key in ("src_bbox", "tgt_bbox"):
        if box.get(key) is not None:
            out[key] = _validate_bbox(box[key], f"box '{box['id']}'.{key}")
    return out


def parse_boxes(value: str | Path | list[dict[str, Any]] | None, *, num_refs: int = 0) -> list[dict[str, Any]]:
    """Resolve ``--boxes`` (JSON inline, path para ficheiro JSON, ou lista pronta).

    Args:
        value: JSON inline, caminho de ficheiro, lista de dicts ou None.
        num_refs: nº de referências já aceites (valida ``from: ref_image_N``).

    Returns:
        Lista de boxes normalizados (vazia quando ``value`` é None/vazio).

    Raises:
        ValueError: JSON inválido, não é lista, ou box com schema inválido.
        FileNotFoundError: ``value`` parece um path mas o ficheiro não existe.
    """
    if value is None:
        return []
    if isinstance(value, list):
        return [validate_box(b, num_refs=num_refs) for b in value]
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("["):
        parsed = json.loads(raw)
    else:
        path = Path(raw)
        if not path.is_file():
            raise FileNotFoundError(f"--boxes: ficheiro não encontrado: {path}")
        parsed = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed, list):
        raise ValueError("--boxes deve ser uma lista JSON de boxes")
    return [validate_box(b, num_refs=num_refs) for b in parsed]
