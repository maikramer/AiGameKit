"""Derivação PBR UV-aware (normal OpenGL + AO de fenda + calibração mate).

Resolve quatro causas raiz que faziam os GLBs parecerem planos ou com costuras:

1. **Resolução do binário Materialize**: prefere ``materialize-cli`` (e valida
   suporte a ``--only`` via ``--help``), evitando que um ``~/.local/bin/materialize``
   desactualizado silencie o enriquecimento com ``rc=2``.
2. **Sinal Y OpenGL verificado**: testa uma vez por binário numa cúpula sintética
   32x32 se ``--normal-format opengl`` devolve ``+Y`` para cima (topo ``G > 128``,
   contrato glTF 2.0 / MikkTSpace / Bevy). Corrige automaticamente se o binário
   inverter Y (como ``normal.wgsl`` antes de correcção upstream).
3. **Máscara de ilhas UV sem costuras**: preenche a gutter do albedo por Voronoi
   (nearest-texel) ANTES do Materialize — elimina saltos de borda/fundo preto —,
   combina o detalhe fino do Materialize com gradientes médios/macro da altura via
   RNM (sobrevive a mipmapping a 5-25 m), atenua suavemente normal→``(128,128,255)``
   e AO→``255`` nos ``fade_px`` junto às costuras UV, e dilata por Voronoi na gutter
   para filtragem trilinear limpa.
4. **Calibração de rugosidade saturada**: o branch ``mr`` do Hunyuan-Paint 2.1
   satura superfícies pintadas em ``roughness = 0.96-1.00`` (giz plano sem resposta
   especular nas arestas). Remapeia apenas a banda saturada ``> 0.88`` não-metálica
   para a faixa mate de guache/têmpera ``0.76-0.92`` modulada pela AO (fendas
   permanecem ultra-mate ``0.90-0.92``; arestas ganham leitura subtil sob sol rasante,
   sem brilho plástico).
"""

from __future__ import annotations

import functools
import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np

_TIMEOUT_S = 600

# Parâmetros por preset calibrados para atlases 1024-2048² de pintura mate.
_PRESET_TUNING: dict[str, dict[str, float]] = {
    "stone": {
        "normal_strength": 3.6,
        "macro_strength": 2.4,
        "ao_depth_scale": 14.0,
        "height_contrast": 2.0,
        "ao_cav": 2.3,
    },
    "wood": {
        "normal_strength": 3.4,
        "macro_strength": 2.2,
        "ao_depth_scale": 12.0,
        "height_contrast": 1.9,
        "ao_cav": 2.2,
    },
    "metal": {
        "normal_strength": 2.8,
        "macro_strength": 1.8,
        "ao_depth_scale": 10.0,
        "height_contrast": 1.7,
        "ao_cav": 1.8,
    },
    "fabric": {
        "normal_strength": 2.4,
        "macro_strength": 1.4,
        "ao_depth_scale": 9.0,
        "height_contrast": 1.6,
        "ao_cav": 1.7,
    },
    "leather": {
        "normal_strength": 2.4,
        "macro_strength": 1.4,
        "ao_depth_scale": 9.0,
        "height_contrast": 1.6,
        "ao_cav": 1.7,
    },
    "skin": {
        "normal_strength": 1.5,
        "macro_strength": 0.9,
        "ao_depth_scale": 6.0,
        "height_contrast": 1.3,
        "ao_cav": 1.1,
    },
    "default": {
        "normal_strength": 3.2,
        "macro_strength": 2.0,
        "ao_depth_scale": 12.0,
        "height_contrast": 1.8,
        "ao_cav": 2.0,
    },
}

_GLTF_CTYPE: dict[int, tuple[Any, float]] = {
    5120: (np.int8, 127.0),
    5121: (np.uint8, 255.0),
    5122: (np.int16, 32767.0),
    5123: (np.uint16, 65535.0),
    5125: (np.uint32, 1.0),
    5126: (np.float32, 1.0),
}
_GLTF_NCOMP: dict[str, int] = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}

# Script Node mínimo: descodifica APENAS os bufferViews de TEXCOORD_0 + indices
# de GLBs com EXT_meshopt_compression (~100 ms vs ~5 s do npx gltf-transform).
_NODE_MESHOPT_UV_SCRIPT = r"""
const fs = require('fs');
const mod = require(process.argv[2]);
const MeshoptDecoder = mod.MeshoptDecoder || mod;
(async () => {
  await MeshoptDecoder.ready;
  const buf = fs.readFileSync(process.argv[3]);
  const jlen = buf.readUInt32LE(12);
  const gltf = JSON.parse(buf.slice(20, 20 + jlen).toString('utf8'));
  const binOff = 28 + jlen;
  const wantViews = new Set();
  const prims = [];
  for (const m of (gltf.meshes || [])) {
    for (const pr of (m.primitives || [])) {
      const uvAcc = pr.attributes && pr.attributes.TEXCOORD_0;
      if (uvAcc === undefined) continue;
      wantViews.add(gltf.accessors[uvAcc].bufferView);
      if (pr.indices !== undefined) wantViews.add(gltf.accessors[pr.indices].bufferView);
      prims.push({ uvAcc, idxAcc: pr.indices, mat: pr.material ?? 0 });
    }
  }
  const decoded = {};
  for (const bvIdx of wantViews) {
    const bv = gltf.bufferViews[bvIdx];
    const ext = bv.extensions && bv.extensions.EXT_meshopt_compression;
    if (!ext) {
      const off = binOff + (bv.byteOffset || 0);
      decoded[bvIdx] = buf.slice(off, off + bv.byteLength);
    } else {
      const off = binOff + (ext.byteOffset || 0);
      const src = new Uint8Array(buf.buffer, buf.byteOffset + off, ext.byteLength);
      const dst = new Uint8Array(ext.count * ext.byteStride);
      MeshoptDecoder.decodeGltfBuffer(dst, ext.count, ext.byteStride, src, ext.mode, ext.filter || 'NONE');
      decoded[bvIdx] = Buffer.from(dst.buffer, dst.byteOffset, dst.byteLength);
    }
  }
  const meta = { prims, accessors: gltf.accessors, bufferViews: gltf.bufferViews, views: {} };
  const chunks = [];
  let cur = 0;
  for (const [k, v] of Object.entries(decoded)) {
    meta.views[k] = { offset: cur, length: v.length };
    chunks.push(v);
    cur += v.length;
  }
  const hb = Buffer.from(JSON.stringify(meta));
  const lenBuf = Buffer.alloc(4);
  lenBuf.writeUInt32LE(hb.length, 0);
  fs.writeFileSync(process.argv[4], Buffer.concat([lenBuf, hb, ...chunks]));
})();
"""


@functools.lru_cache(maxsize=16)
def _supports_modern_flags(bin_path: str) -> bool:
    """Confirma que o binário suporta ``--only`` e ``--no-seamless``."""
    try:
        proc = subprocess.run([bin_path, "--help"], capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    help_text = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0 and "--only" in help_text and "--no-seamless" in help_text


def resolve_materialize_bin() -> str | None:
    """Resolve um binário ``materialize-cli`` compatível (com suporte a ``--only``).

    Ordem: ``MATERIALIZE_BIN`` → checkout release do monorepo → ``PATH``
    (``materialize-cli`` antes de ``materialize``). Um binário legado que não
    aceite ``--only`` é ignorado automaticamente.
    """
    candidates: list[str] = []
    env_bin = os.environ.get("MATERIALIZE_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        candidates.append(env_bin)

    # Shared/src/aigamekit_shared/pbr_uv.py → raiz do monorepo AiGameKit
    repo_root = Path(__file__).resolve().parents[3]
    for name in ("materialize-cli", "materialize"):
        cand = repo_root / "Materialize" / "target" / "release" / name
        if cand.is_file() and os.access(cand, os.X_OK):
            candidates.append(str(cand))

    for name in ("materialize-cli", "materialize"):
        found = shutil.which(name)
        if found and found not in candidates:
            candidates.append(found)

    for cand in candidates:
        if _supports_modern_flags(cand):
            return cand
    return None


@functools.lru_cache(maxsize=8)
def materialize_needs_flip_y(materialize_bin: str) -> bool:
    """Detecta se ``--normal-format opengl`` devolve Y invertido (DirectX).

    Numa cúpula branca sobre fundo escuro, a borda superior (``y < centro``)
    tem normal a apontar para cima em OpenGL (``+Y`` → ``G > 128``). Se o
    binário produzir ``G < 128`` no topo, devolve ``True`` para inverter Y.
    """
    from PIL import Image

    if not Path(materialize_bin).is_file():
        return False

    yy, xx = np.mgrid[0:64, 0:64]
    r = np.sqrt((xx - 32.0) ** 2 + (yy - 32.0) ** 2)
    h = np.clip((18.0 - r) / 5.0, 0.0, 1.0)
    gray = (h * 220.0 + 20.0).astype(np.uint8)
    rgb = np.stack([gray, gray, gray], axis=-1)

    with tempfile.TemporaryDirectory(prefix="pbr_uv_probe_") as td_raw:
        td = Path(td_raw)
        src = td / "bump.png"
        Image.fromarray(rgb, mode="RGB").save(src)
        proc = subprocess.run(
            [
                materialize_bin,
                str(src),
                "-o",
                str(td),
                "-p",
                "default",
                "--only",
                "normal",
                "--no-seamless",
                "--normal-format",
                "opengl",
                "-f",
                "png",
            ],
            capture_output=True,
            timeout=60,
            check=False,
        )
        n_path = td / "bump_normal.png"
        if proc.returncode != 0 or not n_path.is_file():
            return False
        arr = np.asarray(Image.open(n_path).convert("RGB"), dtype=np.float32)
        if arr.shape[0] < 64 or arr.shape[1] < 64:
            return False
        # Borda superior da cúpula (r ≈ 15..18 em x=32 → y ≈ 14..17)
        top_g = float(arr[14:18, 30:34, 1].mean())
        bot_g = float(arr[46:50, 30:34, 1].mean())
        return top_g < bot_g


def infer_pbr_preset(hint: str | Path) -> str:
    """Escolhe preset do Materialize coerente com a categoria/nome do asset."""
    s = str(hint).lower().replace("\\", "/")
    if any(k in s for k in ("/characters/", "npc_", "hero_", "player_", "cartografo", "ferreiro", "aldeao")):
        return "fabric"
    if any(k in s for k in ("/fauna/", "wolf", "boar", "deer", "bear", "crow", "owl", "rat", "spider", "worm")):
        return "leather"
    if any(
        k in s
        for k in (
            "anvil",
            "brazier",
            "iron",
            "sword",
            "axe",
            "pickaxe",
            "hammer",
            "shield",
            "cauldron",
            "kettle",
            "lantern",
            "lamp",
            "bell",
            "sino",
            "carrilhao",
            "chime",
            "metal",
            "brass",
            "bronze",
            "steel",
        )
    ):
        return "metal"
    if any(
        k in s
        for k in (
            "/terrain/",
            "/desert/",
            "/swamp/",
            "rock",
            "stone",
            "cliff",
            "ruin",
            "arch",
            "pillar",
            "statue",
            "monolith",
            "well",
            "bridge",
            "forge",
            "wall",
            "cobble",
            "boulder",
            "pedra",
            "muro",
        )
    ):
        return "stone"
    if any(k in s for k in ("hay", "straw", "sack", "tent", "banner", "flag", "bedroll", "rug", "cloth", "curtain")):
        return "fabric"
    if any(
        k in s
        for k in (
            "/village/",
            "/farm/",
            "/forest/",
            "/interiors/",
            "house",
            "barn",
            "longhouse",
            "cottage",
            "chapel",
            "tower",
            "salao",
            "wood",
            "log",
            "stump",
            "barrel",
            "crate",
            "bench",
            "table",
            "chair",
            "bed",
            "shelf",
            "chest",
            "cart",
            "boat",
            "fence",
            "gate",
            "stall",
            "sign",
            "torch",
            "door",
            "bridge",
            "ponte",
        )
    ):
        return "wood"
    return "default"


def rasterize_uv_coverage(
    uv_tris: np.ndarray,
    height: int,
    width: int,
    *,
    flip_v: bool = False,
) -> np.ndarray:
    """Rasteriza triângulos UV ``(N, 3, 2)`` numa máscara binária ``(H, W)`` uint8."""
    cov = np.zeros((height, width), dtype=np.uint8)
    if uv_tris is None or len(uv_tris) == 0:
        return cov
    pts = np.asarray(uv_tris, dtype=np.float32).copy()
    if flip_v:
        pts[..., 1] = 1.0 - pts[..., 1]
    pts[..., 0] = np.clip(pts[..., 0] * width, 0.0, width - 1.0)
    pts[..., 1] = np.clip(pts[..., 1] * height, 0.0, height - 1.0)
    # Desenhar por lotes reduz overhead do fillPoly em malhas grandes.
    polys = [t for t in pts.astype(np.int32)]
    cv2.fillPoly(cov, polys, 255)
    return cov


def voronoi_fill(image: np.ndarray, coverage_u8: np.ndarray) -> np.ndarray:
    """Preenche todos os pixels fora de ``coverage_u8`` com o texel coberto mais próximo."""
    if coverage_u8 is None or (coverage_u8 > 0).all() or not (coverage_u8 > 0).any():
        return image.copy()
    try:
        from scipy.ndimage import distance_transform_edt

        idxs = distance_transform_edt(coverage_u8 == 0, return_distances=False, return_indices=True)
        return image[idxs[0], idxs[1]].copy()
    except ImportError:
        # Fallback pure-OpenCV com dilatação iterativa se scipy não estiver disponível.
        out = image.copy()
        mask = (coverage_u8 > 0).astype(np.uint8)
        kernel = np.ones((5, 5), np.uint8)
        for _ in range(32):
            if mask.all():
                break
            grown = cv2.dilate(mask, kernel, iterations=1)
            ring = (grown > 0) & (mask == 0)
            if not ring.any():
                break
            if out.ndim == 3:
                for c in range(out.shape[2]):
                    ch = cv2.dilate(out[:, :, c], kernel, iterations=1)
                    out[:, :, c][ring] = ch[ring]
            else:
                ch = cv2.dilate(out, kernel, iterations=1)
                out[ring] = ch[ring]
            mask = grown
        return out


@functools.lru_cache(maxsize=1)
def _find_meshopt_decoder_cjs() -> Path | None:
    """Encontra ``meshopt_decoder.cjs`` no cache npx ou node_modules locais."""
    search_roots = [
        Path.home() / ".npm" / "_npx",
        Path.home() / ".local" / "lib" / "node_modules",
        Path("/usr/local/lib/node_modules"),
        Path("/usr/lib/node_modules"),
    ]
    for root in search_roots:
        if not root.is_dir():
            continue
        for hit in root.glob("**/meshoptimizer/meshopt_decoder.cjs"):
            if hit.is_file():
                return hit
    return None


def _read_accessor(
    accessors: list[dict[str, Any]],
    buffer_views: list[dict[str, Any]],
    view_getter: Any,
    acc_index: int,
) -> np.ndarray:
    acc = accessors[acc_index]
    bv_idx = acc["bufferView"]
    bv = buffer_views[bv_idx]
    view_bytes = view_getter(bv_idx)
    dt, norm_div = _GLTF_CTYPE[acc["componentType"]]
    ncomp = _GLTF_NCOMP[acc["type"]]
    itemsize = np.dtype(dt).itemsize * ncomp
    stride = bv.get("byteStride", itemsize)
    off = acc.get("byteOffset", 0)
    cnt = acc["count"]
    if stride == itemsize:
        arr = np.frombuffer(view_bytes, dtype=dt, count=cnt * ncomp, offset=off).reshape(cnt, ncomp)
    else:
        buf_u8 = np.frombuffer(view_bytes, dtype=np.uint8)
        arr = (
            np.lib.stride_tricks.as_strided(buf_u8[off:], shape=(cnt, itemsize), strides=(stride, 1))
            .copy()
            .view(dt)
            .reshape(cnt, ncomp)
        )
    if acc.get("normalized"):
        arr = np.clip(arr.astype(np.float32) / norm_div, -1.0, 1.0)
    return arr


def _apply_texture_transform(tris: np.ndarray, gltf: dict[str, Any], material_index: int) -> np.ndarray:
    mats = gltf.get("materials") or []
    if material_index >= len(mats):
        return tris
    bct = (mats[material_index].get("pbrMetallicRoughness") or {}).get("baseColorTexture") or {}
    tt = (bct.get("extensions") or {}).get("KHR_texture_transform")
    if not tt:
        return tris
    out = tris.copy()
    rot = float(tt.get("rotation", 0.0))
    sc = tt.get("scale", [1.0, 1.0])
    of = tt.get("offset", [0.0, 0.0])
    out[..., 0] *= float(sc[0])
    out[..., 1] *= float(sc[1])
    if abs(rot) > 1e-6:
        c, s = float(np.cos(rot)), float(np.sin(rot))
        u, v = out[..., 0].copy(), out[..., 1].copy()
        out[..., 0] = c * u + s * v
        out[..., 1] = -s * u + c * v
    out[..., 0] += float(of[0])
    out[..., 1] += float(of[1])
    return out


def extract_glb_uv_triangles(glb_path: Path, material_index: int = 0) -> np.ndarray | None:
    """Extrai triângulos ``TEXCOORD_0`` ``(N, 3, 2)`` do GLB (suporta meshopt + quantization).

    Não modifica o GLB nem toca em KTX2. Devolve ``None`` se não houver UVs
    para ``material_index``.
    """
    data = Path(glb_path).read_bytes()
    if len(data) < 28 or data[:4] != b"glTF":
        return None
    jlen, _jtype = struct.unpack("<II", data[12:20])
    gltf = json.loads(data[20 : 20 + jlen].decode("utf-8"))
    blen, _btype = struct.unpack("<II", data[20 + jlen : 28 + jlen])
    bin_bytes = data[28 + jlen : 28 + jlen + blen]

    prims: list[dict[str, Any]] = []
    for m in gltf.get("meshes") or []:
        for pr in m.get("primitives") or []:
            attrs = pr.get("attributes") or {}
            if "TEXCOORD_0" not in attrs:
                continue
            pr_mat = pr.get("material", 0)
            if pr_mat == material_index:
                prims.append(pr)
    if not prims:
        return None

    bvs = gltf.get("bufferViews") or []
    accs = gltf.get("accessors") or []
    need_meshopt = False
    for pr in prims:
        for ai in (pr["attributes"]["TEXCOORD_0"], pr.get("indices")):
            if ai is None:
                continue
            bv = bvs[accs[ai]["bufferView"]]
            if "EXT_meshopt_compression" in (bv.get("extensions") or {}):
                need_meshopt = True

    if not need_meshopt:

        def _direct_view(bv_idx: int) -> bytes:
            bv = bvs[bv_idx]
            off = bv.get("byteOffset", 0)
            return bin_bytes[off : off + bv["byteLength"]]

        chunks: list[np.ndarray] = []
        for pr in prims:
            uv = _read_accessor(accs, bvs, _direct_view, pr["attributes"]["TEXCOORD_0"]).astype(np.float32)
            idx_ai = pr.get("indices")
            if idx_ai is not None:
                idx = _read_accessor(accs, bvs, _direct_view, idx_ai).reshape(-1)
                n_tris = len(idx) // 3
                if n_tris > 0:
                    chunks.append(uv[idx[: n_tris * 3]].reshape(n_tris, 3, 2))
            else:
                n_tris = len(uv) // 3
                if n_tris > 0:
                    chunks.append(uv[: n_tris * 3].reshape(n_tris, 3, 2))
        if not chunks:
            return None
        return _apply_texture_transform(np.concatenate(chunks, axis=0), gltf, material_index)

    # Caminho rápido meshopt (~100 ms): Node + meshopt_decoder.cjs
    mo = _find_meshopt_decoder_cjs()
    node_bin = shutil.which("node")
    if mo is not None and node_bin is not None:
        with tempfile.TemporaryDirectory(prefix="pbr_uv_meshopt_") as td_raw:
            td = Path(td_raw)
            js_p = td / "dec.js"
            out_p = td / "uvs.bin"
            js_p.write_text(_NODE_MESHOPT_UV_SCRIPT, encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(js_p), str(mo), str(glb_path), str(out_p)],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if proc.returncode == 0 and out_p.is_file():
                raw = out_p.read_bytes()
                hlen = struct.unpack("<I", raw[:4])[0]
                meta = json.loads(raw[4 : 4 + hlen].decode("utf-8"))
                payload = raw[4 + hlen :]

                def _node_view(bv_idx: int) -> bytes:
                    vi = meta["views"][str(bv_idx)]
                    return payload[vi["offset"] : vi["offset"] + vi["length"]]

                chunks = []
                for pr in meta["prims"]:
                    if pr["mat"] != material_index:
                        continue
                    uv = _read_accessor(meta["accessors"], meta["bufferViews"], _node_view, pr["uvAcc"]).astype(
                        np.float32
                    )
                    if pr["idxAcc"] is not None:
                        idx = _read_accessor(meta["accessors"], meta["bufferViews"], _node_view, pr["idxAcc"]).reshape(
                            -1
                        )
                        n_tris = len(idx) // 3
                        if n_tris > 0:
                            chunks.append(uv[idx[: n_tris * 3]].reshape(n_tris, 3, 2))
                    else:
                        n_tris = len(uv) // 3
                        if n_tris > 0:
                            chunks.append(uv[: n_tris * 3].reshape(n_tris, 3, 2))
                if chunks:
                    return _apply_texture_transform(np.concatenate(chunks, axis=0), gltf, material_index)

    # Fallback: descompacta só geometria via gltf-transform copy (sem KTX2)
    gt_bin = shutil.which("gltf-transform")
    npx_bin = shutil.which("npx")
    if gt_bin is None and npx_bin is None:
        return None
    with tempfile.TemporaryDirectory(prefix="pbr_uv_gt_") as td_raw:
        td = Path(td_raw)
        geom_gltf = dict(gltf)
        for k in ("images", "textures", "samplers", "materials"):
            geom_gltf.pop(k, None)
        if "meshes" in geom_gltf:
            geom_gltf["meshes"] = [
                {
                    **m,
                    "primitives": [{k: v for k, v in pr.items() if k != "material"} for pr in m.get("primitives", [])],
                }
                for m in geom_gltf["meshes"]
            ]
        for k in ("extensionsUsed", "extensionsRequired"):
            if k in geom_gltf:
                kept = [e for e in geom_gltf[k] if e != "KHR_texture_basisu"]
                if kept:
                    geom_gltf[k] = kept
                else:
                    geom_gltf.pop(k)
        js_b = json.dumps(geom_gltf, separators=(",", ":")).encode("utf-8")
        js_b += b" " * ((4 - len(js_b) % 4) % 4)
        bin_pad = bin_bytes + b"\x00" * ((4 - len(bin_bytes) % 4) % 4)
        src_geom = td / "geom.glb"
        dst_geom = td / "unpacked.glb"
        src_geom.write_bytes(
            struct.pack("<III", 0x46546C67, 2, 12 + 8 + len(js_b) + 8 + len(bin_pad))
            + struct.pack("<II", len(js_b), 0x4E4F534A)
            + js_b
            + struct.pack("<II", len(bin_pad), 0x004E4942)
            + bin_pad
        )
        cmd = (
            [gt_bin, "copy", str(src_geom), str(dst_geom)]
            if gt_bin
            else [npx_bin, "--yes", "@gltf-transform/cli", "copy", str(src_geom), str(dst_geom)]
        )
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120, check=False)
        if proc.returncode != 0 or not dst_geom.is_file():
            return None
        tris = extract_glb_uv_triangles(dst_geom, material_index=0)
        return _apply_texture_transform(tris, gltf, material_index) if tris is not None else None


def blend_rnm(base_xyz: np.ndarray, detail_xyz: np.ndarray) -> np.ndarray:
    """Reoriented Normal Mapping (Barré-Brisebois & Hill 2012) em espaço tangente."""
    tx, ty, tz = base_xyz[..., 0], base_xyz[..., 1], base_xyz[..., 2] + 1.0
    ux, uy, uz = -detail_xyz[..., 0], -detail_xyz[..., 1], detail_xyz[..., 2]
    dot_tu = tx * ux + ty * uy + tz * uz
    inv_tz = 1.0 / np.maximum(tz, 1e-4)
    rx = tx * dot_tu * inv_tz - ux
    ry = ty * dot_tu * inv_tz - uy
    rz = tz * dot_tu * inv_tz - uz
    inv_len = 1.0 / np.sqrt(rx * rx + ry * ry + rz * rz + 1e-8)
    out = np.stack([rx * inv_len, ry * inv_len, np.clip(rz * inv_len, 0.0, 1.0)], axis=-1)
    return out


def calibrate_matte_roughness(
    roughness_u8: np.ndarray,
    metallic_u8: np.ndarray,
    ao_u8: np.ndarray,
) -> np.ndarray:
    """Alivia a saturação de giz (``0.95-1.00``) mantendo acabamento mate (``0.76-0.92``).

    Pixels com ``roughness <= 0.88`` ou metálicos (``metallic >= 0.20``) ficam
    intocados. Acima de ``0.88`` em superfícies não-metálicas, remapeia
    suavemente para ``[0.76, 0.92]`` guiado pela AO (fendas ficam ``~0.91``
    ultra-mate; arestas expostas descem a ``~0.76-0.82`` para apanhar o sol
    rasante sem parecer plástico).
    """
    r = roughness_u8.astype(np.float32) / 255.0
    m = metallic_u8.astype(np.float32) / 255.0
    ao = ao_u8.astype(np.float32) / 255.0

    # Peso suave: 0 em r <= 0.86, 1 em r >= 0.94; anulado em metal (m >= 0.20)
    t_r = np.clip((r - 0.86) / 0.08, 0.0, 1.0)
    t_r = t_r * t_r * (3.0 - 2.0 * t_r)
    t_m = 1.0 - np.clip((m - 0.08) / 0.14, 0.0, 1.0)
    blend = t_r * t_m

    # Alvo mate guache/têmpera (docs/ART.md: 0.78-0.95): fendas (ao=0.5) → 0.92,
    # planos/arestas expostas (ao=1.0) → 0.78, com micro-variação da original.
    ao_norm = np.clip((ao - 0.45) / 0.55, 0.0, 1.0)
    target = 0.92 - 0.14 * ao_norm + (r - 0.96) * 0.20
    target = np.clip(target, 0.74, 0.93)

    out = r * (1.0 - blend) + target * blend
    return np.clip(out * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)


def mask_and_dilate_pbr_maps(
    normal_xyz: np.ndarray,
    ao_01: np.ndarray,
    coverage_u8: np.ndarray | None,
    *,
    fade_px: float | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Atenua normal→``(0,0,1)`` e AO→``1`` junto às costuras UV e dilata por Voronoi."""
    _h, w = normal_xyz.shape[:2]
    nx = normal_xyz[..., 0].copy()
    ny = normal_xyz[..., 1].copy()
    ao = ao_01.copy()

    has_gutter = coverage_u8 is not None and (coverage_u8 > 0).any() and (coverage_u8 == 0).any()
    if has_gutter:
        dist_in = cv2.distanceTransform(coverage_u8, cv2.DIST_L2, 5)
        fn = float(fade_px) if fade_px is not None else max(4.0, w / 340.0)
        t_n = np.clip(dist_in / fn, 0.0, 1.0)
        w_n = t_n * t_n * (3.0 - 2.0 * t_n)
        nx *= w_n
        ny *= w_n

        fa = fn * 1.5
        t_ao = np.clip(dist_in / fa, 0.0, 1.0)
        w_ao = t_ao * t_ao * (3.0 - 2.0 * t_ao)
        ao = 1.0 - w_ao * (1.0 - ao)

    nz = np.sqrt(np.clip(1.0 - nx * nx - ny * ny, 0.0, 1.0))
    n_u8 = np.stack([(nx * 0.5 + 0.5) * 255.0, (ny * 0.5 + 0.5) * 255.0, (nz * 0.5 + 0.5) * 255.0], axis=-1)
    n_u8 = np.clip(n_u8 + 0.5, 0.0, 255.0).astype(np.uint8)

    ao_u8_1 = np.clip(ao * 255.0 + 0.5, 0.0, 255.0).astype(np.uint8)
    ao_u8 = np.stack([ao_u8_1, ao_u8_1, ao_u8_1], axis=-1)

    if has_gutter:
        n_u8 = voronoi_fill(n_u8, coverage_u8)
        ao_u8 = voronoi_fill(ao_u8, coverage_u8)
    return n_u8, ao_u8


def derive_uv_masked_pbr(
    albedo_rgb: np.ndarray,
    *,
    coverage_u8: np.ndarray | None = None,
    preset: str = "default",
    materialize_bin: str | None = None,
    normal_strength: float | None = None,
    macro_strength: float | None = None,
    ao_depth_scale: float | None = None,
    height_contrast: float | None = None,
    logger: Any = None,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Deriva ``(normal_rgb_u8, ao_rgb_u8)`` OpenGL Y-up livres de costuras UV.

    Args:
        albedo_rgb: ``(H, W, 3)`` uint8 RGB.
        coverage_u8: Máscara binária ``(H, W)`` uint8 das ilhas UV (``255`` dentro,
            ``0`` na gutter). Quando fornecida, evita arestas falsas nas costuras.
        preset: Preset do Materialize (``default``, ``stone``, ``wood``, ``metal``,
            ``fabric``, ``leather``, ``skin``, …).
        materialize_bin: Caminho opcional do binário (default: :func:`resolve_materialize_bin`).

    Returns:
        ``(normal_u8, ao_u8)`` ``(H, W, 3)`` uint8 RGB ou ``None`` se o binário
        estiver ausente ou falhar.
    """
    from PIL import Image

    mbin = materialize_bin or resolve_materialize_bin()
    if mbin is None:
        if logger is not None:
            logger.warning("pbr_uv: materialize-cli não encontrado — asset sai sem normal/AO")
        return None

    h, w = albedo_rgb.shape[:2]
    tune = _PRESET_TUNING.get(preset, _PRESET_TUNING["default"])
    ns = tune["normal_strength"] if normal_strength is None else float(normal_strength)
    ms = tune["macro_strength"] if macro_strength is None else float(macro_strength)
    ao_ds = tune["ao_depth_scale"] if ao_depth_scale is None else float(ao_depth_scale)
    hc = tune["height_contrast"] if height_contrast is None else float(height_contrast)
    ao_cav_gain = tune["ao_cav"]

    # 1. Preenche a gutter por Voronoi antes de entregar ao Materialize.
    work_albedo = (
        voronoi_fill(np.ascontiguousarray(albedo_rgb[..., :3]), coverage_u8)
        if coverage_u8 is not None
        else np.ascontiguousarray(albedo_rgb[..., :3])
    )

    with tempfile.TemporaryDirectory(prefix="pbr_uv_mat_") as td_raw:
        tdir = Path(td_raw)
        src = tdir / "albedo.png"
        Image.fromarray(work_albedo, mode="RGB").save(src)
        cmd = [
            mbin,
            str(src),
            "-o",
            str(tdir),
            "-p",
            preset,
            "--only",
            "height,normal,ao",
            "--no-seamless",
            "--normal-format",
            "opengl",
            "--height-contrast",
            f"{hc:.2f}",
            "--normal-strength",
            f"{ns:.2f}",
            "--ao-depth-scale",
            f"{ao_ds:.2f}",
            "-f",
            "png",
        ]
        try:
            proc = subprocess.run(cmd, check=False, capture_output=True, timeout=_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired) as exc:
            if logger is not None:
                logger.warning("pbr_uv: materialize falhou (%s) — asset sai sem normal/AO", exc)
            return None
        if proc.returncode != 0:
            stderr = proc.stderr.decode(errors="replace").strip().splitlines()
            tail = stderr[-1] if stderr else f"rc={proc.returncode}"
            if logger is not None:
                logger.warning("pbr_uv: materialize rc=%d (%s) — asset sai sem normal/AO", proc.returncode, tail)
            return None

        n_path = tdir / "albedo_normal.png"
        ao_path = tdir / "albedo_ao.png"
        h_path = tdir / "albedo_height.png"
        if not n_path.is_file() or not ao_path.is_file():
            if logger is not None:
                logger.warning("pbr_uv: mapas esperados ausentes após materialize")
            return None

        def _load(path: Path, mode: str) -> np.ndarray:
            with Image.open(path) as im:
                arr = np.asarray(im.convert(mode), dtype=np.float32)
            if arr.shape[:2] != (h, w):
                arr = np.asarray(
                    Image.fromarray(arr.astype(np.uint8)).resize((w, h), Image.Resampling.LANCZOS),
                    dtype=np.float32,
                )
            return arr

        n_raw = _load(n_path, "RGB")
        ao_raw = _load(ao_path, "L") / 255.0
        hm = (_load(h_path, "L") / 255.0) if h_path.is_file() else None

    # 2. Garante convenção OpenGL (+Y para cima), compensando bug de sinal do binário.
    nx_f = (n_raw[..., 0] / 255.0) * 2.0 - 1.0
    ny_f = (n_raw[..., 1] / 255.0) * 2.0 - 1.0
    if materialize_needs_flip_y(mbin):
        ny_f = -ny_f
    nz_f = np.clip((n_raw[..., 2] / 255.0) * 2.0 - 1.0, 1e-4, 1.0)
    fine_xyz = np.stack([nx_f, ny_f, nz_f], axis=-1)

    # 3. Gradientes médios/macro da altura (RNM) — sobrevivem ao mipmapping a 5-25 m.
    if hm is not None and ms > 0.0 and min(h, w) >= 32:
        scale_fac = w / 2048.0
        h_med = cv2.GaussianBlur(hm, (0, 0), max(0.8, 2.5 * scale_fac))
        h_mac = cv2.GaussianBlur(hm, (0, 0), max(1.6, 6.0 * scale_fac))
        gx_m = cv2.Sobel(h_med, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy_m = cv2.Sobel(h_med, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        gx_l = cv2.Sobel(h_mac, cv2.CV_32F, 1, 0, ksize=3) / 8.0
        gy_l = cv2.Sobel(h_mac, cv2.CV_32F, 0, 1, ksize=3) / 8.0
        # OpenGL (+X direita, +Y cima = -y_img): normal = (-dh/dx, +dh/dy_img, 1)
        sx = -ms * (gx_m * 1.4 + gx_l * 2.4)
        sy = +ms * (gy_m * 1.4 + gy_l * 2.4)
        inv_l = 1.0 / np.sqrt(sx * sx + sy * sy + 1.0)
        macro_xyz = np.stack([sx * inv_l, sy * inv_l, inv_l], axis=-1)
        comb_xyz = blend_rnm(macro_xyz, fine_xyz)
    else:
        comb_xyz = fine_xyz

    # 4. AO de fenda multi-escala (HBAO + concavidade local da altura).
    if hm is not None and min(h, w) >= 32:
        scale_fac = w / 2048.0
        local_mean = cv2.GaussianBlur(hm, (0, 0), max(2.0, 10.0 * scale_fac))
        concavity = np.clip(local_mean - hm, 0.0, 1.0)
        ao_cav = np.clip(1.0 - concavity * ao_cav_gain, 0.45, 1.0)
        ao_01 = np.clip(ao_raw * ao_cav, 0.35, 1.0)
    else:
        ao_01 = np.clip(ao_raw, 0.0, 1.0)

    # 5. Atenua nas costuras UV e dilata para a gutter.
    return mask_and_dilate_pbr_maps(comb_xyz, ao_01, coverage_u8)
