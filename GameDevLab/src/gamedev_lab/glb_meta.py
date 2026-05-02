"""Extracção rápida de metadados do GLB via parsing binário do header glTF.

Sem bpy — apenas struct + json. Devolve atributos por primitive, extensions
usadas, mime types das imagens, e bounding box agregada via accessors.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any


def glb_extract_meta(path: str | Path) -> dict[str, Any]:
    """Extrai metadados de um GLB para validação.

    Returns:
        dict com:
        - ``attributes_present``: união de attributes em todos os primitives.
        - ``attributes_per_primitive``: lista de listas por primitive.
        - ``extensions_used``: lista de extensions declaradas como usadas.
        - ``extensions_required``: lista de extensions requeridas.
        - ``texture_mime_types``: mime type por imagem.
        - ``has_tangents``: True se algum primitive tem TANGENT.
        - ``primitive_count``: total de primitives.
        - ``v_per_tri``: vértices/triângulos agregado (None se sem indices).
        - ``world_bounds_y_min``: Y mínimo agregado a partir dos accessors POSITION.
    """
    p = Path(path).expanduser().resolve()
    with open(p, "rb") as f:
        data = f.read()

    if len(data) < 20 or data[:4] != b"glTF":
        return {"_error": "não é GLB"}

    json_len = struct.unpack_from("<I", data, 12)[0]
    chunk = json.loads(data[20 : 20 + json_len])

    accessors = chunk.get("accessors", []) or []
    images = chunk.get("images", []) or []

    attributes_per_primitive: list[list[str]] = []
    union_attrs: set[str] = set()
    total_v = 0
    total_i = 0
    y_min: float | None = None
    primitive_count = 0
    for m in chunk.get("meshes", []) or []:
        for p_ in m.get("primitives", []) or []:
            primitive_count += 1
            attrs_obj = p_.get("attributes", {}) or {}
            attrs = sorted(attrs_obj.keys())
            attributes_per_primitive.append(attrs)
            union_attrs.update(attrs)

            pos_idx = attrs_obj.get("POSITION")
            if pos_idx is not None and 0 <= pos_idx < len(accessors):
                acc = accessors[pos_idx]
                total_v += int(acc.get("count", 0))
                amin = acc.get("min")
                if isinstance(amin, list) and len(amin) >= 3:
                    yv = float(amin[1])
                    y_min = yv if y_min is None else min(y_min, yv)

            idx_idx = p_.get("indices")
            if idx_idx is not None and 0 <= idx_idx < len(accessors):
                total_i += int(accessors[idx_idx].get("count", 0))

    tris = total_i // 3 if total_i else 0
    v_per_tri: float | None = None
    if tris > 0:
        v_per_tri = round(total_v / tris, 4)

    texture_mime_types: list[str] = []
    for img in images:
        mt = img.get("mimeType") or ""
        ext = (img.get("extensions") or {})
        if "EXT_texture_webp" in ext or "image/webp" in mt:
            texture_mime_types.append("image/webp")
        elif "KHR_texture_basisu" in (chunk.get("extensionsUsed") or []) and not mt:
            texture_mime_types.append("image/ktx2")
        else:
            texture_mime_types.append(mt or "unknown")

    # KTX2 também aparece como ``image/ktx2`` em alguns exporters
    extensions_used = list(chunk.get("extensionsUsed") or [])
    extensions_required = list(chunk.get("extensionsRequired") or [])

    # Heurística: se há textures que apontam para KHR_texture_basisu, todas as
    # imagens fonte são KTX2 mesmo sem mimeType.
    if "KHR_texture_basisu" in extensions_used:
        texture_mime_types = [
            mt if mt and mt != "unknown" else "image/ktx2"
            for mt in texture_mime_types
        ]

    return {
        "attributes_present": sorted(union_attrs),
        "attributes_per_primitive": attributes_per_primitive,
        "extensions_used": extensions_used,
        "extensions_required": extensions_required,
        "texture_mime_types": texture_mime_types,
        "has_tangents": "TANGENT" in union_attrs,
        "primitive_count": primitive_count,
        "v_per_tri": v_per_tri,
        "world_bounds_y_min": y_min,
        "vertex_count_total": total_v,
        "triangle_count_total": tris,
    }
