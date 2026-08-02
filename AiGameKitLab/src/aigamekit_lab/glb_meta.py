"""Extracção rápida de metadados do GLB via parsing binário do header glTF.

Sem bpy — apenas struct + json. Devolve atributos por primitive, extensions
usadas, mime types das imagens, e bounding box agregada via accessors.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

# KHR_mesh_quantization: divisor por componentType para accessors ``normalized``.
_NORMALIZE_DIVISOR: dict[int, float] = {5120: 127.0, 5121: 255.0, 5122: 32767.0, 5123: 65535.0}


def _accessor_pos_bounds(acc: dict[str, Any]) -> tuple[list[float], list[float]] | None:
    """Devolve ``(min, max)`` do accessor POSITION em espaço local (dequantizado)."""
    amin, amax = acc.get("min"), acc.get("max")
    if not (isinstance(amin, list) and len(amin) >= 3):
        return None
    if not (isinstance(amax, list) and len(amax) >= 3):
        amax = amin
    vmin = [float(x) for x in amin[:3]]
    vmax = [float(x) for x in amax[:3]]
    if acc.get("normalized"):
        div = _NORMALIZE_DIVISOR.get(int(acc.get("componentType", 0)))
        if div:
            vmin = [max(v / div, -1.0) for v in vmin]
            vmax = [max(v / div, -1.0) for v in vmax]
    return vmin, vmax


def _mat_mul(a: list[float], b: list[float]) -> list[float]:
    """Multiplica matrizes 4x4 column-major (convenção glTF)."""
    out = [0.0] * 16
    for col in range(4):
        for row in range(4):
            out[col * 4 + row] = sum(a[k * 4 + row] * b[col * 4 + k] for k in range(4))
    return out


def _node_local_matrix(node: dict[str, Any]) -> list[float]:
    """Matriz local do nó (column-major): ``matrix`` explícita ou T·R·S."""
    if isinstance(node.get("matrix"), list) and len(node["matrix"]) == 16:
        return [float(x) for x in node["matrix"]]
    tx, ty, tz = (node.get("translation") or [0.0, 0.0, 0.0])[:3]
    qx, qy, qz, qw = (node.get("rotation") or [0.0, 0.0, 0.0, 1.0])[:4]
    sx, sy, sz = (node.get("scale") or [1.0, 1.0, 1.0])[:3]
    # Matriz de rotação a partir do quaternion (linhas), depois column-major T·R·S.
    r = [
        [1 - 2 * (qy * qy + qz * qz), 2 * (qx * qy - qz * qw), 2 * (qx * qz + qy * qw)],
        [2 * (qx * qy + qz * qw), 1 - 2 * (qx * qx + qz * qz), 2 * (qy * qz - qx * qw)],
        [2 * (qx * qz - qy * qw), 2 * (qy * qz + qx * qw), 1 - 2 * (qx * qx + qy * qy)],
    ]
    scale = (sx, sy, sz)
    m = [0.0] * 16
    for col in range(3):
        for row in range(3):
            m[col * 4 + row] = r[row][col] * scale[col]
    m[12], m[13], m[14], m[15] = float(tx), float(ty), float(tz), 1.0
    return m


def _transform_point(m: list[float], p: tuple[float, float, float]) -> tuple[float, float, float]:
    x, y, z = p
    return (
        m[0] * x + m[4] * y + m[8] * z + m[12],
        m[1] * x + m[5] * y + m[9] * z + m[13],
        m[2] * x + m[6] * y + m[10] * z + m[14],
    )


_IDENTITY = [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]


def _mesh_world_matrices(chunk: dict[str, Any]) -> dict[int, list[list[float]]]:
    """Mapa ``mesh index -> matrizes world`` percorrendo a hierarquia de nós."""
    nodes = chunk.get("nodes", []) or []
    children_set: set[int] = set()
    for n in nodes:
        children_set.update(n.get("children", []) or [])
    roots = [i for i in range(len(nodes)) if i not in children_set]

    result: dict[int, list[list[float]]] = {}

    def walk(idx: int, parent: list[float]) -> None:
        node = nodes[idx]
        world = _mat_mul(parent, _node_local_matrix(node))
        mesh_idx = node.get("mesh")
        if isinstance(mesh_idx, int):
            result.setdefault(mesh_idx, []).append(world)
        for child in node.get("children", []) or []:
            if 0 <= child < len(nodes):
                walk(child, world)

    for root in roots:
        walk(root, _IDENTITY)
    return result


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
        - ``mesh_count``: número de entries em ``meshes``.
        - ``vertex_count_total`` / ``triangle_count_total`` / ``face_count_total``.
        - ``v_per_tri``: vértices/triângulos agregado (None se sem indices).
        - ``world_bounds_y_min``: Y mínimo agregado a partir dos accessors POSITION.
        - ``world_bounds_min`` / ``world_bounds_max``: AABB completo (3 eixos)
          em espaço mundo (None se sem POSITION).
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
    world_min: list[float] | None = None
    world_max: list[float] | None = None
    primitive_count = 0
    meshes_list = chunk.get("meshes", []) or []
    mesh_count = len(meshes_list)
    mesh_matrices = _mesh_world_matrices(chunk)
    for mesh_idx, m in enumerate(meshes_list):
        matrices = mesh_matrices.get(mesh_idx) or [_IDENTITY]
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
                bounds = _accessor_pos_bounds(acc)
                if bounds is not None:
                    # AABB local (dequantizado) → mundo: transformar os 8 cantos
                    # cobre rotação/escala de nó (KHR_mesh_quantization usa a
                    # escala do nó para dequantizar posições).
                    vmin, vmax = bounds
                    for world in matrices:
                        for cx in (vmin[0], vmax[0]):
                            for cy in (vmin[1], vmax[1]):
                                for cz in (vmin[2], vmax[2]):
                                    wx, wy, wz = _transform_point(world, (cx, cy, cz))
                                    y_min = wy if y_min is None else min(y_min, wy)
                                    if world_min is None:
                                        world_min = [wx, wy, wz]
                                        world_max = [wx, wy, wz]
                                    else:
                                        for ax, val in enumerate((wx, wy, wz)):
                                            world_min[ax] = min(world_min[ax], val)
                                            world_max[ax] = max(world_max[ax], val)

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
        ext = img.get("extensions") or {}
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
        texture_mime_types = [mt if mt and mt != "unknown" else "image/ktx2" for mt in texture_mime_types]

    return {
        "attributes_present": sorted(union_attrs),
        "attributes_per_primitive": attributes_per_primitive,
        "extensions_used": extensions_used,
        "extensions_required": extensions_required,
        "texture_mime_types": texture_mime_types,
        "has_tangents": "TANGENT" in union_attrs,
        "primitive_count": primitive_count,
        "mesh_count": mesh_count,
        "v_per_tri": v_per_tri,
        "world_bounds_y_min": y_min,
        "world_bounds_min": world_min,
        "world_bounds_max": world_max,
        "vertex_count_total": total_v,
        "triangle_count_total": tris,
        "face_count_total": tris,  # alias: tris ≈ faces após export GLTF triangulado
    }
