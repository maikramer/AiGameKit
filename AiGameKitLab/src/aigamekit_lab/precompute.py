"""Pré-cálculo de colisores primitivos (cápsula/cilindro) a partir do GLB.

Parser header-only (sem bpy, sem trimesh) — alimenta o ``{id}_precompute.json``
que a engine VibeGame consome para montar colisores baratos e cortar o
navmesh sem esperar downloads de ``*_collision.glb``.

Decisão de forma por asset:

- ``--stump`` presente (árvores split) → cápsula do AABB do stump — o tronco
  exato, sem copa.
- categoria ``vegetation`` sem stump → fatia inferior do tronco: a largura do
  colisor vem só do tronco (não da copa), a altura é a total.
- categoria ``rock`` (ou ``terrain``, como as pedras do simple-rpg) → cilindro
  do AABB completo (topo plano, mais fiel a um penedo do que uma cápsula).
- resto → cápsula do AABB completo.

O raio dos ajustes por AABB é ``max(dx, dz)/2`` (círculo que contém o
footprint XZ — o envelope do trimesh antigo) **sem cap pela altura**: stumps
largos e baixos (alargamento de raízes) precisam do raio do alargamento.

Saída: ``{version, asset_id, category, aabb, collider, source, collectible_hint}``.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

from aigamekit_lab.glb_meta import _IDENTITY, _mesh_world_matrices, _transform_point

# ---------------------------------------------------------------------------
# Constantes do heurístico de tronco
# ---------------------------------------------------------------------------

# Fração inferior da altura considerada "tronco" quando não há stump (a copa
# das árvores sem split é larga; a largura do colisor deve vir só da base).
TRUNK_SLICE_FRACTION = 0.35
# Folga sobre o extents XZ da fatia (o tronco raramente é um círculo perfeito;
# a fatia pode cortar o alargamento da base).
TRUNK_RADIUS_FACTOR = 1.05

COLLIDER_CAPSULE = "capsule"
COLLIDER_CYLINDER = "cylinder"

# Hint de coletável por categoria — a decisão final é do jogo, isto é só metadata.
_COLLECTIBLE_KIND: dict[str, str] = {
    "vegetation": "wood",
    "rock": "stone",
    "terrain": "stone",  # pedras/penedos no simple-rpg usam category: terrain
}

# componentType → (formato, tamanho, divisor p/ normalized)
_COMPONENT_FORMAT: dict[int, tuple[str, int, float]] = {
    5120: ("b", 1, 127.0),  # int8
    5121: ("B", 1, 255.0),  # uint8
    5122: ("h", 2, 32767.0),  # int16
    5123: ("H", 2, 65535.0),  # uint16
    5126: ("f", 4, 1.0),  # float32
}


def _read_glb(path: Path) -> tuple[dict[str, Any], bytes] | None:
    """Lê o GLB e devolve ``(chunk JSON, bin chunk)``; None se inválido."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    if len(data) < 20 or data[:4] != b"glTF":
        return None
    json_len = struct.unpack_from("<I", data, 12)[0]
    if json_len <= 0 or 20 + json_len > len(data):
        return None
    try:
        chunk: dict[str, Any] = json.loads(data[20 : 20 + json_len])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    bin_data = b""
    offset = 20 + json_len
    if offset + 8 <= len(data):
        bin_len, bin_type = struct.unpack_from("<II", data, offset)
        if bin_type == 0x004E4942:  # b"BIN\0"
            start = offset + 8
            bin_data = data[start : start + bin_len]
    return chunk, bin_data


def _decode_position_vertices(chunk: dict[str, Any], bin_data: bytes) -> list[tuple[float, float, float]] | None:
    """Decodifica POSITION (float32 ou KHR_mesh_quantization) em espaço local.

    Aplica a matriz world dos nós (``_mesh_world_matrices``) para devolver
    vértices em espaço do GLB — o mesmo contrato do ``glb_extract_meta``.
    """
    accessors = chunk.get("accessors", []) or []
    buffer_views = chunk.get("bufferViews", []) or []
    meshes_list = chunk.get("meshes", []) or []
    if not accessors or not bin_data:
        return None
    mesh_matrices = _mesh_world_matrices(chunk)

    verts: list[tuple[float, float, float]] = []
    for mesh_idx, m in enumerate(meshes_list):
        matrices = mesh_matrices.get(mesh_idx) or [_IDENTITY]
        for p_ in m.get("primitives", []) or []:
            pos_idx = (p_.get("attributes", {}) or {}).get("POSITION")
            if pos_idx is None or not 0 <= pos_idx < len(accessors):
                continue
            acc = accessors[pos_idx]
            comp = acc.get("componentType", 0)
            fmt, comp_size, divisor = _COMPONENT_FORMAT.get(comp, (None, 0, 1.0))
            if fmt is None:
                return None
            count = int(acc.get("count", 0))
            bv_idx = acc.get("bufferView")
            if bv_idx is None or not 0 <= bv_idx < len(buffer_views):
                return None
            bv = buffer_views[bv_idx]
            bv_start = int(bv.get("byteOffset", 0))
            stride = int(bv.get("byteStride", 0)) or (3 * comp_size)
            acc_offset = int(acc.get("byteOffset", 0))
            start = bv_start + acc_offset
            if start + (count - 1) * stride + 3 * comp_size > len(bin_data):
                return None
            normalized = bool(acc.get("normalized"))
            for i in range(count):
                base = start + i * stride
                raw = struct.unpack_from(f"<3{fmt}", bin_data, base)
                v = tuple(max(x / divisor, -1.0) for x in raw) if normalized else tuple(float(x) for x in raw)
                for world in matrices:
                    verts.append(_transform_point(world, v))
    return verts or None


def _aabb(
    verts: list[tuple[float, float, float]],
) -> tuple[list[float], list[float]]:
    vmin = [verts[0][0], verts[0][1], verts[0][2]]
    vmax = list(vmin)
    for x, y, z in verts:
        vmin[0] = min(vmin[0], x)
        vmin[1] = min(vmin[1], y)
        vmin[2] = min(vmin[2], z)
        vmax[0] = max(vmax[0], x)
        vmax[1] = max(vmax[1], y)
        vmax[2] = max(vmax[2], z)
    return vmin, vmax


def _capsule_from_aabb(vmin: list[float], vmax: list[float]) -> dict[str, float]:
    """Cápsula vertical que envolve o AABB: raio = min(dx, dz)/2 (cap dy/2)."""
    dx = vmax[0] - vmin[0]
    dy = vmax[1] - vmin[1]
    dz = vmax[2] - vmin[2]
    # max(dx, dz)/2 = círculo que contém o AABB XZ inteiro — o mesmo envelope
    # do trimesh antigo. SEM cap dy/2: stumps largos e baixos (alargamento de
    # raízes) ficavam com raio minúsculo e o jogador entrava no tronco.
    radius = max(dx, dz) / 2.0
    return {
        "shape": COLLIDER_CAPSULE,
        "radius": round(max(radius, 0.01), 4),
        "height": round(max(dy, 0.01), 4),
        "base_y": round(vmin[1], 4),
    }


def _cylinder_from_aabb(vmin: list[float], vmax: list[float]) -> dict[str, float]:
    """Cilindro vertical do AABB completo (pedras): topo plano."""
    dx = vmax[0] - vmin[0]
    dy = vmax[1] - vmin[1]
    dz = vmax[2] - vmin[2]
    radius = max(dx, dz) / 2.0
    return {
        "shape": COLLIDER_CYLINDER,
        "radius": round(max(radius, 0.01), 4),
        "height": round(max(dy, 0.01), 4),
        "base_y": round(vmin[1], 4),
    }


def _trunk_capsule(
    verts: list[tuple[float, float, float]],
) -> dict[str, float] | None:
    """Cápsula cuja largura vem só do tronco (fatia inferior do mesh).

    Os vértices abaixo de ``y_min + TRUNK_SLICE_FRACTION * altura`` definem o
    raio; a altura do colisor é a total do asset. Devolve None se a fatia for
    degenerada (menos de 3 vértices ou extents nulos) — o chamador faz
    fallback para o AABB completo.
    """
    vmin, vmax = _aabb(verts)
    dy = vmax[1] - vmin[1]
    if dy <= 0:
        return None
    slice_y = vmin[1] + TRUNK_SLICE_FRACTION * dy
    slice_x_min = slice_z_min = float("inf")
    slice_x_max = slice_z_max = float("-inf")
    count = 0
    for x, y, z in verts:
        if y > slice_y:
            continue
        count += 1
        slice_x_min = min(slice_x_min, x)
        slice_x_max = max(slice_x_max, x)
        slice_z_min = min(slice_z_min, z)
        slice_z_max = max(slice_z_max, z)
    if count < 3 or slice_x_max <= slice_x_min or slice_z_max <= slice_z_min:
        return None
    radius = max(slice_x_max - slice_x_min, slice_z_max - slice_z_min) / 2.0
    radius *= TRUNK_RADIUS_FACTOR
    return {
        "shape": COLLIDER_CAPSULE,
        "radius": round(max(radius, 0.01), 4),
        "height": round(max(dy, 0.01), 4),
        "base_y": round(vmin[1], 4),
    }


def _round_aabb(vmin: list[float], vmax: list[float]) -> dict[str, list[float]]:
    return {
        "min": [round(v, 4) for v in vmin],
        "max": [round(v, 4) for v in vmax],
    }


def precompute_asset(
    main_glb: str | Path,
    stump_glb: str | Path | None = None,
    category: str | None = None,
    asset_id: str | None = None,
) -> dict[str, Any]:
    """Calcula o bloco de pré-cálculo para um asset.

    Args:
        main_glb: GLB de referência — de preferência o ``*_collision.glb``
            (uncompressed); o parser também lê ``*_lod0.glb``.
        stump_glb: ``*_stump_collision.glb`` de árvores split — o tronco exato.
        category: categoria do manifest (``vegetation``, ``rock``, ...).
        asset_id: id do asset para o JSON.

    Returns:
        dict com o schema de precompute; ``{"error": ...}`` em falha soft.
    """
    main_path = Path(main_glb).expanduser().resolve()
    result: dict[str, Any] = {"version": 1}
    if asset_id:
        result["asset_id"] = asset_id
    if category:
        result["category"] = category

    if stump_glb is not None:
        stump_path = Path(stump_glb).expanduser().resolve()
        parsed = _read_glb(stump_path) if stump_path.is_file() else None
        verts = _decode_position_vertices(*parsed) if parsed is not None else None
        if not verts:
            result["error"] = f"stump sem POSITION decodificável: {stump_path}"
            return result
        vmin, vmax = _aabb(verts)
        collider = _capsule_from_aabb(vmin, vmax)
        collider["shape"] = COLLIDER_CAPSULE
        result["source"] = "stump"
    else:
        parsed = _read_glb(main_path)
        verts = _decode_position_vertices(*parsed) if parsed is not None else None
        if not verts:
            result["error"] = f"GLB sem POSITION decodificável: {main_path}"
            return result
        vmin, vmax = _aabb(verts)
        if category == "vegetation":
            trunk = _trunk_capsule(verts)
            if trunk is not None:
                collider = trunk
                result["source"] = "trunk-slice"
            else:
                collider = _capsule_from_aabb(vmin, vmax)
                result["source"] = "aabb"
        elif category in ("rock", "terrain"):
            collider = _cylinder_from_aabb(vmin, vmax)
            result["source"] = "aabb"
        else:
            collider = _capsule_from_aabb(vmin, vmax)
            result["source"] = "aabb"

    result["aabb"] = _round_aabb(vmin, vmax)
    result["collider"] = collider
    result["collectible_hint"] = {"kind": _COLLECTIBLE_KIND.get(category or "")}
    return result
