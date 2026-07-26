"""GLBs sintéticos para testes de resume/classificação.

``_glb_has_geometry`` (``gameassets.paths``) parseia o JSON chunk via
``gamedev_shared.glb_verify.extract_glb_meta``, logo ficheiros dummy (``b"x"``)
contam como colapsados. Estes helpers escrevem GLBs com JSON chunk válido —
sem BIN chunk, porque só os metadados são lidos.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

_ATTR_DEFAULT = ("POSITION", "NORMAL", "TEXCOORD_0")


def write_min_glb(
    path: Path,
    *,
    tris: int = 128,
    verts: int | None = None,
    attrs: tuple[str, ...] = _ATTR_DEFAULT,
    y_min: float = 0.0,
    with_paint: bool = True,
) -> Path:
    """Escreve um GLB cujo JSON chunk declara ``tris`` triângulos."""
    vcount = tris if verts is None else verts
    accessors = [
        {
            "componentType": 5126,
            "count": vcount,
            "type": "VEC3",
            "min": [-1.0, y_min, -1.0],
            "max": [1.0, y_min + 2.0, 1.0],
        },
        {"componentType": 5123, "count": tris * 3, "type": "SCALAR"},
    ]
    chunk = {
        "asset": {"version": "2.0", "generator": "gameassets tests"},
        "accessors": accessors,
        "meshes": [{"primitives": [{"attributes": dict.fromkeys(attrs, 0), "indices": 1}]}],
        # Mantém o ficheiro acima do teto de 200 B usado como guarda anti-stub.
        "extras": {"pad": "x" * 160},
    }
    if with_paint:
        chunk["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        chunk["textures"] = [{"source": 0}]
        chunk["images"] = [{"uri": "data:image/png;base64,x"}]
    raw = json.dumps(chunk, separators=(",", ":")).encode("utf-8")
    raw += b" " * (-len(raw) % 4)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(raw)) + struct.pack("<I4s", len(raw), b"JSON") + raw)
    return path


def write_collapsed_glb(path: Path) -> Path:
    """GLB parseável mas colapsado (poucas faces) — deve ser rejeitado."""
    return write_min_glb(path, tris=13)
