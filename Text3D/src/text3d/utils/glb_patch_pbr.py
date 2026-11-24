"""Patch PBR pós-LOD UV-aware: garante normal OpenGL + ORM (AO+R+M) em GLBs sem re-decimar.

O Hunyuan-Paint gera albedo+MR (com roughness frequentemente saturada em
``0.96-1.00``); o rebake de LOD pode deixar cair a occlusion ou resetar o canal
``R`` do ORM para branco ``255``. Este módulo é o passo idempotente pós-lod:

1. Extrai o albedo de cada material texturizado (KTX2→PNG via ``ktx extract`` ou PNG/JPEG).
2. Extrai os triângulos ``TEXCOORD_0`` reais do GLB (incluindo ``EXT_meshopt_compression``
   e ``KHR_mesh_quantization``) para mascarar as ilhas UV — **zero costuras nas bordas**.
3. Deriva normal OpenGL (``+Y`` verificado, multi-escala RNM) e AO de fenda via
   ``aigamekit_shared.pbr_uv`` + ``materialize-cli``.
4. Empacota **ORM** (``R=AO, G=roughness calibrada mate, B=metallic preservado``)
   e aponta ``metallicRoughnessTexture`` + ``occlusionTexture`` para o mesmo slot;
   substitui blobs in-place e reempacota o ``buffer 0`` sem bloat nem mexer na
   geometria (``EXT_meshopt_compression``, ``KHR_mesh_quantization``, ``TANGENT``,
   ``skins``, ``animations`` ficam 100% intactos).
5. Codifica os mapas novos em KTX2 UASTC+Zstd linear com DFD normalizado para RGB
   (``fix_ktx2_dfd`` — evita bug BC4/RRR do Bevy).
"""

from __future__ import annotations

import copy
import json
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import numpy as np

from aigamekit_shared.pbr_uv import (
    calibrate_matte_roughness,
    derive_uv_masked_pbr,
    extract_glb_uv_triangles,
    infer_pbr_preset,
    rasterize_uv_coverage,
    voronoi_fill,
)
from aigamekit_shared.pbr_uv import (
    resolve_materialize_bin as _shared_resolve_materialize_bin,
)

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
_TIMEOUT_S = 600


class PatchPbrResult:
    """Resultado de um patch: ``skipped`` = já completo (idempotência)."""

    def __init__(self, skipped: bool, reason: str, output: Path | None = None) -> None:
        self.skipped = skipped
        self.reason = reason
        self.output = output


def _load_glb(path: Path) -> tuple[dict[str, Any], bytearray]:
    data = path.read_bytes()
    assert data[:4] == b"glTF" and data[4:8] == struct.pack("<I", 2), "GLB inválido"
    off = 12
    chunks: dict[int, bytes] = {}
    while off < len(data):
        clen, ctype = struct.unpack("<II", data[off : off + 8])
        chunks[ctype] = data[off + 8 : off + 8 + clen]
        off += 8 + clen
    return json.loads(chunks[JSON_CHUNK].decode("utf-8")), bytearray(chunks.get(BIN_CHUNK, b""))


def _save_glb(path: Path, gltf: dict[str, Any], bin_data: bytearray) -> None:
    json_bytes = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_bytes += b" " * ((4 - len(json_bytes) % 4) % 4)
    bin_data += b"\x00" * ((4 - len(bin_data) % 4) % 4)
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    out = bytearray()
    out += struct.pack("<III", 0x46546C67, 2, total)
    out += struct.pack("<II", len(json_bytes), JSON_CHUNK)
    out += json_bytes
    out += struct.pack("<II", len(bin_data), BIN_CHUNK)
    out += bin_data
    path.write_bytes(bytes(out))


def material_slots(gltf: dict[str, Any], material_index: int = 0) -> dict[str, int | None]:
    """Índices de textura dos slots PBR do material (``None`` = ausente)."""
    mat = gltf.get("materials", [])[material_index]
    pbr = mat.get("pbrMetallicRoughness", {})
    return {
        "base": (pbr.get("baseColorTexture") or {}).get("index"),
        "mr": (pbr.get("metallicRoughnessTexture") or {}).get("index"),
        "normal": (mat.get("normalTexture") or {}).get("index"),
        "ao": (mat.get("occlusionTexture") or {}).get("index"),
    }


def glb_is_pbr_complete(gltf: dict[str, Any]) -> bool:
    mats = gltf.get("materials") or []
    if not mats:
        return False
    textured = [i for i in range(len(mats)) if material_slots(gltf, i)["base"] is not None]
    if not textured:
        return False
    return all(
        material_slots(gltf, i)["normal"] is not None and material_slots(gltf, i)["ao"] is not None for i in textured
    )


def _texture_image_index(gltf: dict[str, Any], tex_index: int) -> int | None:
    tex = gltf["textures"][tex_index]
    return tex.get("extensions", {}).get("KHR_texture_basisu", {}).get("source", tex.get("source"))


def _image_blob(gltf: dict[str, Any], bin_data: bytes, image_index: int) -> bytes:
    im = gltf["images"][image_index]
    ext = im.get("extensions", {})
    src = ext.get("KHR_texture_basisu", {}).get("source") if "KHR_texture_basisu" in ext else im.get("bufferView")
    bv = gltf["bufferViews"][src]
    return bin_data[bv.get("byteOffset", 0) : bv.get("byteOffset", 0) + bv["byteLength"]]


def _blob_to_image(blob: bytes, mime: str, stem: str, tmp: Path) -> Path:
    """KTX2→PNG via ``ktx extract``; PNG/JPEG saem direto (extensão pelo mime)."""
    if "ktx2" in mime or blob[:4] == b"\xabKTX":
        out = tmp / f"{stem}.png"
        src = tmp / f"{stem}.ktx2"
        src.write_bytes(blob)
        subprocess.run(
            ["ktx", "extract", str(src), str(out)],
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=True,
        )
        return out
    ext = "jpg" if "jpeg" in mime else "png"
    out = tmp / f"{stem}.{ext}"
    out.write_bytes(blob)
    return out


def _extract_texture_png(
    gltf: dict[str, Any],
    bin_data: bytes,
    tex_index: int,
    stem: str,
    tmp: Path,
) -> Path:
    img_idx = _texture_image_index(gltf, tex_index)
    if img_idx is None:
        raise RuntimeError(f"texture {tex_index} sem source")
    img = gltf["images"][img_idx]
    blob = _image_blob(gltf, bin_data, img_idx)
    return _blob_to_image(blob, img.get("mimeType", "image/png"), stem, tmp)


def extract_albedo_png(glb_path: Path, tmp: Path, *, material_index: int = 0) -> Path:
    """Albedo (``baseColor`` do material) como PNG/JPEG no dir temporário."""
    gltf, bin_data = _load_glb(glb_path)
    base = material_slots(gltf, material_index)["base"]
    if base is None:
        raise RuntimeError(f"{glb_path.name}: material {material_index} sem baseColorTexture")
    return _extract_texture_png(gltf, bytes(bin_data), base, f"albedo_{glb_path.stem}_m{material_index}", tmp)


def resolve_materialize_bin() -> str | None:
    """Resolve ``materialize-cli`` compatível (``--only``/``--no-seamless``)."""
    return _shared_resolve_materialize_bin()


def derive_maps(
    albedo_png: Path,
    tmp: Path,
    *,
    preset: str,
    logger: Any,
    want_normal: bool = True,
    want_ao: bool = True,
    coverage_u8: np.ndarray | None = None,
) -> tuple[Path | None, Path | None]:
    """Deriva normal OpenGL + AO UV-masked do albedo via ``aigamekit_shared.pbr_uv``."""
    from PIL import Image

    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        raise RuntimeError("materialize não encontrado (MATERIALIZE_BIN / Materialize/target/release / PATH)")
    if not want_normal and not want_ao:
        return None, None

    with Image.open(albedo_png) as im:
        albedo_rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)

    derived = derive_uv_masked_pbr(
        albedo_rgb,
        coverage_u8=coverage_u8,
        preset=preset,
        materialize_bin=materialize_bin,
        logger=logger,
    )
    if derived is None:
        raise RuntimeError("materialize falhou ao derivar normal/AO")
    normal_u8, ao_u8 = derived

    normal_p: Path | None = None
    ao_p: Path | None = None
    if want_normal:
        normal_p = tmp / f"{albedo_png.stem}_normal.png"
        Image.fromarray(normal_u8, mode="RGB").save(normal_p)
    if want_ao:
        ao_p = tmp / f"{albedo_png.stem}_ao.png"
        Image.fromarray(ao_u8, mode="RGB").save(ao_p)
    if logger is not None:
        only = ",".join(x for x, on in (("normal", want_normal), ("ao", want_ao)) if on)
        h, w = albedo_rgb.shape[:2]
        logger.info(
            "patch-pbr: %s derivados (%dx%d, preset=%s, uv_mask=%s)", only, w, h, preset, coverage_u8 is not None
        )
    return normal_p, ao_p


def _png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def _png_to_ktx2(png: Path, tmp: Path) -> Path:
    """PNG → KTX2 UASTC+Zstd via KTX-Software (linear, mapa de dados).

    Promove grayscale para RGB antes do encode **e** normaliza o DFD do
    resultado (``fix_ktx2_dfd`` — evita bug BC4/RRR do Bevy).
    """
    src = png
    try:
        from PIL import Image as _PILImage

        with _PILImage.open(png) as im:
            if im.mode not in ("RGB", "RGBA"):
                rgb = tmp / f"{png.stem}_rgb.png"
                im.convert("RGB").save(rgb)
                src = rgb
    except Exception:
        pass
    out = tmp / f"{png.stem}.ktx2"
    proc = subprocess.run(
        [
            "ktx",
            "create",
            "--format",
            "R8G8B8A8_UNORM",
            "--encode",
            "uastc",
            "--assign-tf",
            "linear",
            "--zstd",
            "18",
            str(src),
            str(out),
        ],
        capture_output=True,
        text=True,
        timeout=_TIMEOUT_S,
        check=False,
    )
    if proc.returncode != 0 or not out.is_file():
        tail = (proc.stderr.strip().splitlines() or [f"rc={proc.returncode}"])[-1]
        raise RuntimeError(f"ktx create falhou: {tail}")
    try:
        from aigamekit_shared.gltf_ktx2 import fix_ktx2_dfd

        fix_ktx2_dfd(out)
    except Exception:
        pass
    return out


def _image_ref_counts(gltf: dict[str, Any]) -> tuple[dict[int, int], dict[int, int]]:
    """Conta quantos slots de material referenciam cada texture e cada image."""
    tex_refs: dict[int, int] = {}
    for mat in gltf.get("materials") or []:
        pbr = mat.get("pbrMetallicRoughness") or {}
        for slot in (
            pbr.get("baseColorTexture"),
            pbr.get("metallicRoughnessTexture"),
            mat.get("normalTexture"),
            mat.get("occlusionTexture"),
            mat.get("emissiveTexture"),
        ):
            if isinstance(slot, dict) and "index" in slot:
                ti = int(slot["index"])
                tex_refs[ti] = tex_refs.get(ti, 0) + 1
    img_refs: dict[int, int] = {}
    for ti, cnt in tex_refs.items():
        if ti < len(gltf.get("textures") or []):
            ii = _texture_image_index(gltf, ti)
            if ii is not None:
                img_refs[ii] = img_refs.get(ii, 0) + cnt
    return tex_refs, img_refs


def _repack_buffer0(gltf: dict[str, Any], bin_data: bytes, bv_overrides: dict[int, bytes]) -> bytearray:
    """Reempacota o ``buffer 0`` com alinhamento a 4 bytes, preservando meshopt intacto.

    BufferViews com ``EXT_meshopt_compression`` guardam os bytes comprimidos em
    ``ext.byteOffset`` (no ``buffer 0``) e um offset virtual em ``bv.byteOffset``
    (no ``buffer 1`` fallback): só ``ext.byteOffset`` é actualizado.
    BufferViews órfãos de imagens antigas não referenciadas ficam com 0 bytes no
    ``buffer 0`` (evita bloat ao re-patchar com ``--force``).
    """
    live_bvs: set[int] = set()
    for acc in gltf.get("accessors") or []:
        if "bufferView" in acc:
            live_bvs.add(int(acc["bufferView"]))
        sparse = acc.get("sparse") or {}
        for sub in ("indices", "values"):
            if sub in sparse and "bufferView" in sparse[sub]:
                live_bvs.add(int(sparse[sub]["bufferView"]))

    # Imagens referenciadas por texturas usadas em algum material.
    _, img_refs = _image_ref_counts(gltf)
    for img_idx, cnt in img_refs.items():
        if cnt <= 0 or img_idx >= len(gltf.get("images") or []):
            continue
        im = gltf["images"][img_idx]
        if "bufferView" in im:
            live_bvs.add(int(im["bufferView"]))

    # Qualquer bufferView com EXT_meshopt_compression ou target de geometria fica vivo.
    bvs = gltf.get("bufferViews") or []
    for i, bv in enumerate(bvs):
        if (
            ("extensions" in bv and "EXT_meshopt_compression" in bv["extensions"])
            or "target" in bv
            or i in bv_overrides
        ):
            live_bvs.add(i)

    new_bin = bytearray()
    for i, bv in enumerate(bvs):
        ext_mo = (bv.get("extensions") or {}).get("EXT_meshopt_compression")
        if ext_mo is not None:
            if ext_mo.get("buffer", 0) != 0:
                continue
            src_off = int(ext_mo.get("byteOffset", 0))
            src_len = int(ext_mo["byteLength"])
            blob = bv_overrides.get(i, bin_data[src_off : src_off + src_len])
            pad = (4 - len(new_bin) % 4) % 4
            if pad:
                new_bin += b"\x00" * pad
            ext_mo["byteOffset"] = len(new_bin)
            ext_mo["byteLength"] = len(blob)
            new_bin += blob
            continue

        if bv.get("buffer", 0) != 0:
            continue
        if i not in live_bvs:
            bv["byteOffset"] = 0
            bv["byteLength"] = 0
            continue
        src_off = int(bv.get("byteOffset", 0))
        src_len = int(bv["byteLength"])
        blob = bv_overrides.get(i, bin_data[src_off : src_off + src_len])
        pad = (4 - len(new_bin) % 4) % 4
        if pad:
            new_bin += b"\x00" * pad
        bv["byteOffset"] = len(new_bin)
        bv["byteLength"] = len(blob)
        new_bin += blob

    pad = (4 - len(new_bin) % 4) % 4
    if pad:
        new_bin += b"\x00" * pad
    if gltf.get("buffers"):
        gltf["buffers"][0]["byteLength"] = len(new_bin)
    return new_bin


def _drop_orphan_images(gltf: dict[str, Any]) -> int:
    """Remove as imagens que o ``_repack_buffer0`` esvaziou, com as texturas e views delas.

    O Bevy descodifica **todas** as imagens do GLB, usadas ou não: uma órfã com
    ``byteLength`` 0 derruba o asset inteiro (``Failed to parse ktx2 file:
    UnexpectedEnd``). Só saem imagens vazias que nenhum slot ``*Texture`` de
    material referencia; ``textures``, ``images`` e ``bufferViews`` são
    renumerados. Se alguma verificação falhar, o ``gltf`` fica intacto.
    Devolve quantas imagens saíram.
    """
    images = gltf.get("images") or []
    textures = gltf.get("textures") or []
    views = gltf.get("bufferViews") or []
    empty = {
        i
        for i, im in enumerate(images)
        if "bufferView" in im and int(views[int(im["bufferView"])].get("byteLength", 0)) == 0
    }
    if not empty:
        return 0

    def image_index(tex_index: int) -> int | None:
        if not 0 <= tex_index < len(textures):
            return None
        return _texture_image_index(gltf, tex_index)

    used_tex: set[int] = set()
    referenced: set[int] = set()

    def visit(node: Any, key: str, *, views_only: bool = False) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if views_only and k == "bufferView" and isinstance(v, int):
                    referenced.add(v)
                elif not views_only and k == "index" and isinstance(v, int) and key.endswith("Texture"):
                    used_tex.add(v)
                else:
                    visit(v, k, views_only=views_only)
        elif isinstance(node, list):
            for v in node:
                visit(v, key, views_only=views_only)

    for mat in gltf.get("materials") or []:
        visit(mat, "")
    orphan_tex = {t for t in range(len(textures)) if image_index(t) in empty}
    if used_tex & orphan_tex:
        return 0  # imagem vazia ainda em uso: não é órfã

    new = copy.deepcopy(gltf)
    for i, im in enumerate(images):
        if i not in empty and "bufferView" in im:
            referenced.add(int(im["bufferView"]))
    for key, value in new.items():
        if key != "images":
            visit(value, "", views_only=True)
    drop_view = {int(images[i]["bufferView"]) for i in empty} - referenced

    img_map = {old: i for i, old in enumerate(x for x in range(len(images)) if x not in empty)}
    tex_map = {old: i for i, old in enumerate(x for x in range(len(textures)) if x not in orphan_tex)}
    view_map = {old: i for i, old in enumerate(x for x in range(len(views)) if x not in drop_view)}

    def remap(node: Any, key: str, *, in_texture: bool = False) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                if k == "bufferView" and isinstance(v, int):
                    node[k] = view_map[v]
                elif in_texture and k == "source" and isinstance(v, int):
                    node[k] = img_map[v]
                elif k == "index" and isinstance(v, int) and key.endswith("Texture"):
                    node[k] = tex_map[v]
                else:
                    remap(v, k, in_texture=in_texture)
        elif isinstance(node, list):
            for v in node:
                remap(v, key, in_texture=in_texture)

    try:
        # Textura que sobrevive com um `source` a apontar para a imagem vazia.
        for tex in new.get("textures") or []:
            for holder in [tex, *(tex.get("extensions") or {}).values()]:
                if isinstance(holder, dict) and holder.get("source") in empty:
                    holder.pop("source")
        for key, value in new.items():
            if key not in ("images", "textures"):
                remap(value, "")
        for tex in new.get("textures") or []:
            remap(tex, "", in_texture=True)
        new["images"] = [im for i, im in enumerate(new.get("images") or []) if i not in empty]
        new["textures"] = [tex for i, tex in enumerate(new.get("textures") or []) if i not in orphan_tex]
        new["bufferViews"] = [view for i, view in enumerate(new.get("bufferViews") or []) if i not in drop_view]
        for im in new["images"]:
            if "bufferView" in im:
                im["bufferView"] = view_map[int(im["bufferView"])]
    except (KeyError, IndexError, TypeError, ValueError):
        return 0

    new_views = new["bufferViews"]
    if any(
        "bufferView" in im and int(new_views[int(im["bufferView"])].get("byteLength", 0)) == 0 for im in new["images"]
    ):
        return 0
    slots_ok = True
    for mat in new.get("materials") or []:
        for holder in (mat.get("pbrMetallicRoughness") or {}, mat):
            for k, v in holder.items():
                if not k.endswith("Texture") or not isinstance(v, dict) or "index" not in v:
                    continue
                ti = int(v["index"])
                ii = _texture_image_index(new, ti) if 0 <= ti < len(new["textures"]) else None
                if ii is None or ii >= len(new["images"]):
                    slots_ok = False
    if not slots_ok:
        return 0
    gltf.clear()
    gltf.update(new)
    return len(empty)


def _inject_maps(
    gltf: dict[str, Any],
    bin_data: bytearray,
    normal_png: Path | None,
    ao_png: Path | None,
    tmp: Path,
    *,
    inject_normal: bool = True,
    inject_ao: bool = True,
    material_index: int = 0,
) -> None:
    """Injeta normal+AO como KTX2 (compatível com testes unitários existentes)."""
    mat = gltf["materials"][material_index]
    images = gltf.setdefault("images", [])
    textures = gltf.setdefault("textures", [])
    buffer_views = gltf.setdefault("bufferViews", [])
    samplers = gltf.setdefault("samplers", [])
    if not samplers:
        samplers.append({})
    used = gltf.setdefault("extensionsUsed", [])
    if "KHR_texture_basisu" not in used:
        used.append("KHR_texture_basisu")
    slots: list[tuple[str, Path]] = []
    if inject_normal and normal_png is not None:
        slots.append(("normalTexture", normal_png))
    if inject_ao and ao_png is not None:
        slots.append(("occlusionTexture", ao_png))
    for slot, png in slots:
        ktx2 = _png_to_ktx2(png, tmp)
        blob = ktx2.read_bytes()
        blob += b"\x00" * ((4 - len(blob) % 4) % 4)
        buffer_views.append({"buffer": 0, "byteOffset": len(bin_data), "byteLength": len(blob)})
        img_idx = len(images)
        images.append({"bufferView": len(buffer_views) - 1, "mimeType": "image/ktx2", "name": png.stem.split("_")[-1]})
        textures.append(
            {
                "sampler": 0,
                "extensions": {"KHR_texture_basisu": {"source": img_idx}},
            }
        )
        mat[slot] = {
            "index": len(textures) - 1,
            **({"scale": 1.0} if slot == "normalTexture" else {"strength": 1.0}),
        }
        bin_data += blob
    gltf["buffers"][0]["byteLength"] = len(bin_data)


def _upsert_ktx2_texture(
    gltf: dict[str, Any],
    bv_overrides: dict[int, bytes],
    ktx2_blob: bytes,
    name: str,
    existing_tex_idx: int | None,
    protected_images: set[int],
) -> int:
    """Substitui KTX2 in-place quando a imagem é exclusiva, ou cria nova entrada."""
    images = gltf.setdefault("images", [])
    textures = gltf.setdefault("textures", [])
    buffer_views = gltf.setdefault("bufferViews", [])
    samplers = gltf.setdefault("samplers", [])
    if not samplers:
        samplers.append({})
    used = gltf.setdefault("extensionsUsed", [])
    if "KHR_texture_basisu" not in used:
        used.append("KHR_texture_basisu")

    if existing_tex_idx is not None and existing_tex_idx < len(textures):
        img_idx = _texture_image_index(gltf, existing_tex_idx)
        if img_idx is not None and img_idx not in protected_images:
            img = images[img_idx]
            bv_idx = img.get("bufferView")
            if bv_idx is not None:
                bv_overrides[int(bv_idx)] = ktx2_blob
                img["mimeType"] = "image/ktx2"
                img["name"] = name
                tex = textures[existing_tex_idx]
                tex.pop("source", None)
                tex.setdefault("sampler", 0)
                tex.setdefault("extensions", {})["KHR_texture_basisu"] = {"source": img_idx}
                return existing_tex_idx

    bv_idx = len(buffer_views)
    buffer_views.append({"buffer": 0, "byteOffset": 0, "byteLength": len(ktx2_blob)})
    bv_overrides[bv_idx] = ktx2_blob
    img_idx = len(images)
    images.append({"bufferView": bv_idx, "mimeType": "image/ktx2", "name": name})
    tex_idx = len(textures)
    textures.append({"sampler": 0, "extensions": {"KHR_texture_basisu": {"source": img_idx}}})
    return tex_idx


def _build_orm_png(
    gltf: dict[str, Any],
    bin_bytes: bytes,
    mat_idx: int,
    ao_u8: np.ndarray,
    coverage_u8: np.ndarray | None,
    tmp: Path,
    *,
    calibrate_roughness: bool = True,
) -> Path:
    """Constrói ORM (``R=AO, G=roughness, B=metallic``) preservando o MR existente."""
    from PIL import Image

    h, w = ao_u8.shape[:2]
    mat = gltf["materials"][mat_idx]
    pbr = mat.setdefault("pbrMetallicRoughness", {})
    mr_slot = pbr.get("metallicRoughnessTexture")

    if mr_slot is not None:
        mr_png = _extract_texture_png(gltf, bin_bytes, int(mr_slot["index"]), f"mr_m{mat_idx}", tmp)
        with Image.open(mr_png) as im:
            if im.size != (w, h):
                im = im.resize((w, h), Image.Resampling.LANCZOS)
            mr_rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
        rgh_u8 = mr_rgb[..., 1]
        met_u8 = mr_rgb[..., 2]
    else:
        r_fac = float(pbr.get("roughnessFactor", 1.0))
        m_fac = float(pbr.get("metallicFactor", 0.0))
        rgh_u8 = np.full((h, w), round(np.clip(r_fac, 0.0, 1.0) * 255.0), dtype=np.uint8)
        met_u8 = np.full((h, w), round(np.clip(m_fac, 0.0, 1.0) * 255.0), dtype=np.uint8)
        pbr["roughnessFactor"] = 1.0
        pbr["metallicFactor"] = 1.0 if m_fac > 0.0 else 0.0

    ao_ch = ao_u8[..., 0]
    if calibrate_roughness:
        rgh_u8 = calibrate_matte_roughness(rgh_u8, met_u8, ao_ch)

    orm = np.stack([ao_ch, rgh_u8, met_u8], axis=-1)
    if coverage_u8 is not None:
        orm = voronoi_fill(orm, coverage_u8)

    out = tmp / f"orm_m{mat_idx}.png"
    Image.fromarray(orm, mode="RGB").save(out)
    return out


def patch_glb_pbr(
    glb_path: Path,
    *,
    output_path: Path | None = None,
    preset: str = "auto",
    force: bool = False,
    calibrate_roughness: bool = True,
    logger: Any = None,
) -> PatchPbrResult:
    """Injeta normal OpenGL UV-masked + ORM (R=AO, G=roughness, B=metallic) sem mexer na geometria.

    Idempotente por defeito (``force=False``): salta GLBs em que todos os
    materiais texturizados já têm ``normalTexture`` + ``occlusionTexture``.
    Com ``force=True``, regenera normal + ORM (in-place, sem bloat) para todos
    os materiais texturizados.
    """
    from PIL import Image

    gltf, bin_data = _load_glb(glb_path)
    if glb_is_pbr_complete(gltf) and not force:
        return PatchPbrResult(True, "já tem normalTexture + occlusionTexture")

    mats = gltf.get("materials") or []
    textured_indices = [i for i in range(len(mats)) if material_slots(gltf, i)["base"] is not None]
    if not textured_indices:
        return PatchPbrResult(True, "sem baseColorTexture — GLB sem PBR para enriquecer")

    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        raise RuntimeError("materialize não encontrado (MATERIALIZE_BIN / Materialize/target/release / PATH)")

    resolved_preset = infer_pbr_preset(glb_path) if preset == "auto" else preset
    dst = output_path or glb_path
    dst.parent.mkdir(parents=True, exist_ok=True)
    orig_bin_bytes = bytes(bin_data)

    # Imagens de baseColor e emissive nunca são sobrescritas in-place.
    protected_images: set[int] = set()
    for m in mats:
        pbr = m.get("pbrMetallicRoughness") or {}
        for slot in (pbr.get("baseColorTexture"), m.get("emissiveTexture")):
            if isinstance(slot, dict) and "index" in slot:
                ii = _texture_image_index(gltf, int(slot["index"]))
                if ii is not None:
                    protected_images.add(ii)

    bv_overrides: dict[int, bytes] = {}
    patched_mats = 0
    added_normal = False
    added_ao = False

    with tempfile.TemporaryDirectory(prefix="text3d_patch_pbr_", dir=dst.parent) as td_raw:
        tmp = Path(td_raw)
        for mat_idx in textured_indices:
            mat = mats[mat_idx]
            slots = material_slots(gltf, mat_idx)
            want_normal = force or slots["normal"] is None
            want_ao = force or slots["ao"] is None
            if not want_normal and not want_ao:
                continue

            albedo_png = _extract_texture_png(gltf, orig_bin_bytes, int(slots["base"]), f"alb_m{mat_idx}", tmp)
            with Image.open(albedo_png) as im:
                albedo_rgb = np.asarray(im.convert("RGB"), dtype=np.uint8)
            h, w = albedo_rgb.shape[:2]

            uv_tris = extract_glb_uv_triangles(glb_path, material_index=mat_idx)
            coverage_u8 = rasterize_uv_coverage(uv_tris, h, w, flip_v=False) if uv_tris is not None else None

            derived = derive_uv_masked_pbr(
                albedo_rgb,
                coverage_u8=coverage_u8,
                preset=resolved_preset,
                materialize_bin=materialize_bin,
                logger=logger,
            )
            if derived is None:
                raise RuntimeError(f"materialize falhou para {glb_path.name} (material {mat_idx})")
            normal_u8, ao_u8 = derived

            if want_normal:
                n_png = tmp / f"normal_m{mat_idx}.png"
                Image.fromarray(normal_u8, mode="RGB").save(n_png)
                n_ktx2 = _png_to_ktx2(n_png, tmp).read_bytes()
                n_tex_idx = _upsert_ktx2_texture(
                    gltf,
                    bv_overrides,
                    n_ktx2,
                    f"normal_m{mat_idx}",
                    slots["normal"],
                    protected_images,
                )
                mat["normalTexture"] = {"index": n_tex_idx, "scale": 1.0}
                # Protege a imagem recém-escrita caso outro material aponte para o mesmo índice.
                ni = _texture_image_index(gltf, n_tex_idx)
                if ni is not None:
                    protected_images.add(ni)
                added_normal = True

            if want_ao:
                orm_png = _build_orm_png(
                    gltf,
                    orig_bin_bytes,
                    mat_idx,
                    ao_u8,
                    coverage_u8,
                    tmp,
                    calibrate_roughness=calibrate_roughness,
                )
                orm_ktx2 = _png_to_ktx2(orm_png, tmp).read_bytes()
                orm_tex_idx = _upsert_ktx2_texture(
                    gltf,
                    bv_overrides,
                    orm_ktx2,
                    f"orm_m{mat_idx}",
                    slots["mr"],
                    protected_images,
                )
                pbr = mat.setdefault("pbrMetallicRoughness", {})
                pbr["metallicRoughnessTexture"] = {"index": orm_tex_idx}
                mat["occlusionTexture"] = {"index": orm_tex_idx, "strength": 1.0}
                oi = _texture_image_index(gltf, orm_tex_idx)
                if oi is not None:
                    protected_images.add(oi)
                added_ao = True

            patched_mats += 1
            if logger is not None:
                logger.info(
                    "patch-pbr: %s mat[%d] (%dx%d, preset=%s, uv_mask=%s)",
                    glb_path.name,
                    mat_idx,
                    w,
                    h,
                    resolved_preset,
                    coverage_u8 is not None,
                )

        if patched_mats == 0:
            return PatchPbrResult(True, "nada em falta")

        repacked_bin = _repack_buffer0(gltf, orig_bin_bytes, bv_overrides)
        dropped = _drop_orphan_images(gltf)
        if dropped and logger is not None:
            logger.info("patch-pbr: %s -%d imagem(ns) órfã(s)", glb_path.name, dropped)
        final = tmp / "final.glb"
        _save_glb(final, gltf, repacked_bin)
        os.replace(final, dst)

    added = "+".join(x for x, on in (("normal", added_normal), ("ORM/AO", added_ao)) if on)
    return PatchPbrResult(False, f"{added} ({patched_mats} mat, preset={resolved_preset})", output=dst)
