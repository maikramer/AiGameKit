"""Sidecar de metadados do SVG (reprodutibilidade/QA)."""

from __future__ import annotations

import contextlib
import hashlib
import json
from pathlib import Path

from .options import SvgResult


def meta_path_for(svg_path: Path) -> Path:
    """``foo.svg`` → ``foo.svg.json`` (não colide com o ``foo.json`` do PNG)."""

    return svg_path.with_name(svg_path.name + ".json")


def write_svg_meta(svg_path: Path, result: SvgResult) -> Path:
    """Escreve os metadados ao lado do SVG, de forma atómica."""

    data = result.to_dict()
    with contextlib.suppress(OSError):
        data["svg_sha256"] = hashlib.sha256(Path(svg_path).read_bytes()).hexdigest()
    data["version"] = 1

    path = meta_path_for(Path(svg_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path
