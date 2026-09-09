"""Patch do slot ``occlusionTexture`` em GLBs (convenção ORM do pipeline).

O exporter glTF do Blender não emite ``occlusionTexture`` a partir do grafo de
nós que construimos, por isso o slot é escrito por patch JSON pós-export — uma
só vez, no ponto de origem (export do paint / rebake dos LODs). A partir daí,
re-imports/re-exports bpy e os passos gltf-transform do finish (dedup, prune,
uastc, meshopt) round-tripam o slot.

Convenção ORM (a mesma de ``Viber/scripts/enrich_materials.py``): a imagem
referenciada por ``metallicRoughnessTexture`` carrega **R=AO, G=roughness,
B=metallic** e ``occlusionTexture`` aponta para a mesma texture index.
"""

from __future__ import annotations

import json
import logging
import struct
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK_TYPE = 0x4E4F534A
_BIN_CHUNK_TYPE = 0x004E4942


def ensure_occlusion_texture(glb_path: str | Path, *, strength: float = 1.0, logger: Any = None) -> bool:
    """Aponta ``occlusionTexture`` para a textura ORM de cada material que falte.

    Heurística deliberadamente conservadora (só o nosso pipeline produz este
    padrão): material **com** ``metallicRoughnessTexture`` **e**
    ``normalTexture`` **e sem** ``occlusionTexture`` → occlusion aponta para a
    texture index do MR. Qualquer outra combinação fica intocada, e materiais
    que já têm ``occlusionTexture`` nunca são mexidos (idempotente).

    Args:
        glb_path: GLB a patchar in-place.
        strength: ``occlusionTexture.strength`` escrito no slot.
        logger: Logger opcional.

    Returns:
        ``True`` se algum material foi patched.
    """
    path = Path(glb_path)
    try:
        with open(path, "rb") as f:
            header = f.read(12)
            if len(header) < 12 or struct.unpack("<I", header[:4])[0] != _GLB_MAGIC:
                return False
            jlen, jtype = struct.unpack("<II", f.read(8))
            if jtype != _JSON_CHUNK_TYPE:
                return False
            js = json.loads(f.read(jlen))
            blen, btype = struct.unpack("<II", f.read(8))
            bin_data = f.read(blen) if btype == _BIN_CHUNK_TYPE else b""
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        if logger is not None:
            logger.warning("gltf_occlusion: leitura falhou (%s) — occlusion não patchada", exc)
        return False

    patched = 0
    for mat in js.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness") or {}
        mr = pbr.get("metallicRoughnessTexture")
        if not mr or "occlusionTexture" in mat or "normalTexture" not in mat:
            continue
        mat["occlusionTexture"] = {"index": mr["index"], "strength": float(strength)}
        patched += 1

    if not patched:
        return False

    raw = json.dumps(js, separators=(",", ":")).encode()
    raw += b" " * (-len(raw) % 4)
    out = (
        struct.pack("<III", _GLB_MAGIC, 2, 12 + 8 + len(raw) + 8 + len(bin_data))
        + struct.pack("<II", len(raw), _JSON_CHUNK_TYPE)
        + raw
        + struct.pack("<II", len(bin_data), _BIN_CHUNK_TYPE)
        + bin_data
    )
    try:
        path.write_bytes(out)
    except OSError as exc:
        if logger is not None:
            logger.warning("gltf_occlusion: escrita falhou (%s) — occlusion não patchada", exc)
        return False
    if logger is not None:
        logger.info("gltf_occlusion: occlusionTexture patchada em %d material(is) — %s", patched, path.name)
    return True
