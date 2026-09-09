#!/usr/bin/env python3
"""Enrich the mesh pool's GLB materials with synthesized PBR maps.

WHY: every GLB in the pool ships albedo-only — no normal maps, no roughness,
no occlusion, no emissive masks. Close-up, props read as painted plastic:
flat walls, windows that stay dark at night, opaque crystals. This pipeline
DERIVES the missing maps from the albedo itself (the photographic/AI albedos
carry shading in their luminance) and writes enriched COPIES to a parallel
tree, leaving the canonical pool untouched for the rest of the monorepo.

    meshes/house_lod0.glb  ->  meshes_enriched/house_lod0.glb
      + normalTexture        Sobel of blurred luminance (relief from shading)
      + metallicRoughnessTex G = roughness from local detail variance, B = 0
      + occlusionTexture     R = AO from luminance vs. large blur (same ORM
                              image as metallicRoughness — the glTF idiom)
      + emissiveTexture      hot/saturated albedo pixels, NAME-gated
                              (window/lamp/lantern/glow/flame/crystal/...)

Formats follow the pool contract: KTX2 UASTC + Zstandard, never ETC1S;
normal/ORM are LINEAR (`--assign-tf linear`), emissive is sRGB. Mipmaps are
generated (`--generate-mipmap`) — `patch_image` only builds chains for plain
RGBA8, so a chainless BC7 shimmers.

Textured materials only: flat-color GLBs (half the pool) have nothing to
derive from and are copied verbatim. Idempotent: a material that already has
a normalTexture is skipped, so re-runs converge.

    python3 Viber/scripts/enrich_materials.py \
        --src  Viber/examples/simple-rpg/assets/meshes \
        --dst  Viber/examples/simple-rpg/assets/meshes_enriched
    ... --dry-run     list what would happen, write nothing
    ... --only house  restrict to paths containing the substring
    ... --force       re-enrich even if the destination looks current
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

#: Name fragments that mark a material as light-emitting (windows, lamps,
#: crystals...). The emissive MASK still gates by pixel color, so a "window"
#: material with grey frames only glows in the glass panes.
EMISSIVE_NAME_HINTS = (
    "window",
    "lamp",
    "lantern",
    "glow",
    "flame",
    "crystal",
    "torch",
    "ember",
    "rune",
    "fire",
    "candle",
    "magic",
)

#: GLB container magic + chunk types.
GLB_MAGIC = 0x46546C67
CHUNK_JSON = 0x4E4F534A
CHUNK_BIN = 0x004E4942

#: KTX2 identifier (for encode verification).
KTX2_IDENTIFIER = b"\xabKTX 20\xbb\r\n\x1a\n"
KTX2_LEVEL_COUNT_OFFSET = 14


# --------------------------------------------------------------- GLB I/O


def parse_glb(blob: bytes) -> tuple[dict, bytes]:
    """Split a GLB into its JSON manifest and binary chunk."""
    # Header: magic(4) + version(4) + length(4) — o length está NO OFFSET 8.
    if len(blob) < 12 or struct.unpack_from("<I", blob, 0)[0] != GLB_MAGIC:
        raise ValueError("not a GLB stream")
    length = struct.unpack_from("<I", blob, 8)[0]
    if length > len(blob):
        raise ValueError("truncated GLB")
    pos, doc, binary = 12, None, b""
    while pos < length:
        chunk_len, chunk_type = struct.unpack_from("<II", blob, pos)
        body = blob[pos + 8 : pos + 8 + chunk_len]
        if chunk_type == CHUNK_JSON:
            doc = json.loads(body.decode("utf-8"))
        elif chunk_type == CHUNK_BIN:
            binary = body
        pos += 8 + chunk_len + (-chunk_len) % 4
    if doc is None:
        raise ValueError("GLB without JSON chunk")
    return doc, binary


def build_glb(doc: dict, binary: bytes) -> bytes:
    """Repack a manifest + binary chunk into a GLB (4-byte padded)."""
    json_blob = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    json_blob += b" " * (-len(json_blob) % 4)
    padded_bin = binary + b"\x00" * (-len(binary) % 4)
    total = 12 + 8 + len(json_blob) + (8 + len(padded_bin) if padded_bin else 0)
    out = bytearray()
    out += struct.pack("<III", GLB_MAGIC, 2, total)
    out += struct.pack("<II", len(json_blob), CHUNK_JSON) + json_blob
    if padded_bin:
        out += struct.pack("<II", len(padded_bin), CHUNK_BIN) + padded_bin
    return bytes(out)


def image_bytes(doc: dict, binary: bytes, image: dict) -> bytes:
    """The embedded bytes of one glTF image (bufferView slice)."""
    view = doc["bufferViews"][image["bufferView"]]
    start = view.get("byteOffset", 0)
    return binary[start : start + view["byteLength"]]


# ------------------------------------------------------- map synthesis
# Técnicas ao estilo MATERIALIZE (Bounding Box Software — a ferramenta com
# que o pool foi gerado, ver SOURCES.md): separação de frequências para a
# altura, blur progressivo (cadeia de box ≈ gaussiana) antes do gradiente, e
# AO por amostragem de horizonte na altura (o rácio luma/blur confunde
# material escuro com cavidade).


def to_linear_rgb(arr: np.ndarray) -> np.ndarray:
    """sRGB bytes → linear floats (the shader contract)."""
    return (arr / 255.0) ** 2.2


def blur(arr: np.ndarray, radius: int) -> np.ndarray:
    """Separable box blur, float, edge-clamped."""
    out = arr.astype(np.float32)
    size = 2 * radius + 1
    for axis in (0, 1):
        padded = np.pad(out, [(radius, radius) if i == axis else (0, 0) for i in range(2)], mode="edge")
        out = (
            np.stack([np.take(padded, np.arange(arr.shape[axis]) + i, axis=axis) for i in range(size)]).sum(axis=0)
            / size
        )
    return out


def progressive_blur(arr: np.ndarray, passes: int, radius: int) -> np.ndarray:
    """Cadeia de box blurs ≈ gaussian (Materialize blurs in stages)."""
    out = arr.astype(np.float32)
    for _ in range(passes):
        out = blur(out, radius)
    return out


def normalize(arr: np.ndarray, constant: float = 0.5) -> np.ndarray:
    """Stretch to 0..1; input CONSTANTE mapeia para `constant` — a altura
    plana fica a 0.5 (neutra), o DETALHE nulo fica a 0.0 (rough máximo),
    que é o que cada chamador quer."""
    lo, hi = float(arr.min()), float(arr.max())
    if hi - lo < 1e-6:
        return np.full_like(arr, constant, dtype=np.float32)
    return ((arr - lo) / (hi - lo)).astype(np.float32)


def luminance01(albedo: np.ndarray) -> np.ndarray:
    return (0.299 * albedo[..., 0] + 0.587 * albedo[..., 1] + 0.114 * albedo[..., 2]).astype(np.float32) / 255.0


def synthesize_height(albedo: np.ndarray) -> np.ndarray:
    """Altura 0..1 em DUAS frequências (Materialize "base + detail"):

    base  = blur profundo da luminância (a forma grande: tábuas, rebordos)
    detail= resíduo de blur fino (o grão do material)
    Uma altura só-da-luminância confunde "escuro" com "fundo"; a separação
    mantém o relevo fino mesmo em materiais escuros.
    """
    luma = luminance01(albedo)
    base = normalize(progressive_blur(luma, passes=4, radius=6))
    detail = luma - progressive_blur(luma, passes=2, radius=1)
    detail = normalize(detail, constant=0.5)
    return normalize(0.6 * base + 0.4 * detail)


def normal_from_height(height: np.ndarray, strength: float = 2.4) -> np.ndarray:
    """Sobel sobre a altura SUAVIZADA (progressive blur mata o ruído de
    texel antes do gradiente — gradiente em ruído = normal map a tremelicar).
    """
    smooth = progressive_blur(height, passes=2, radius=1)
    gx = np.zeros_like(smooth)
    gy = np.zeros_like(smooth)
    gx[:, 1:-1] = (smooth[:, 2:] - smooth[:, :-2]) * 0.5
    gy[1:-1, :] = (smooth[2:, :] - smooth[:-2, :]) * 0.5
    gx *= strength
    gy *= strength
    length = np.sqrt(gx * gx + gy * gy + 1.0)
    normal = np.stack(
        (-gx / length * 0.5 + 0.5, -gy / length * 0.5 + 0.5, 1.0 / length),
        axis=-1,
    )
    return np.clip(normal * 255.0 + 0.5, 0, 255).astype(np.uint8)


def synthesize_normal(albedo: np.ndarray, strength: float = 2.4) -> np.ndarray:
    return normal_from_height(synthesize_height(albedo), strength)


def ao_from_height(height: np.ndarray, radius: int = 6, samples: int = 12) -> np.ndarray:
    """AO por AMOSTRAGEM DE HORIZONTE (Materialize): para cada texel, 12
    direções; vizinhos ACIMA da altura tapam o céu. Oclusão real de
    cavidades — um rácio luma/blur escurece painéis escuros por engano.
    """
    occ = np.zeros_like(height)
    for step in range(samples):
        angle = 2.0 * np.pi * step / samples
        dx, dy = int(np.round(np.cos(angle) * radius)), int(np.round(np.sin(angle) * radius))
        if dx == 0 and dy == 0:
            continue
        shifted = np.roll(height, (-dy, -dx), axis=(0, 1))
        # roll faz WRAP — para texturas tiled é o comportamento certo (a
        # textura repete); nos UVs não-tiling o erro fica nos 6 texels da
        # borda, invisível sob o repeat do sampler de mundo.
        occ += np.maximum(0.0, shifted - height)
    ao = 1.0 - np.clip(occ / samples * 3.0, 0.0, 1.0)
    return np.clip(ao * 0.4 + 0.6, 0.0, 1.0)  # banda 0.6..1.0 (nunca breu)


def synthesize_orm(albedo: np.ndarray) -> np.ndarray:
    """ORM bytes: R = ambient occlusion, G = roughness, B = metallic(0).

    Roughness from local detail: busy texels are rough, flat washes are
    smooth (banda 0.45..1.0 — extremos de gloss leriam a plástico molhado).
    """
    height = synthesize_height(albedo)
    luma = luminance01(albedo)
    detail = np.abs(luma - progressive_blur(luma, passes=2, radius=2))
    detail = progressive_blur(detail, passes=1, radius=3)
    # Detalhe NULO (textura lisa) → roughness máxima; o default 0.5 do
    # normalize fabricaria detalhe médio numa parede chapada.
    detail = normalize(detail, constant=0.0)
    rough = 1.0 - np.clip(detail * 1.8, 0.0, 0.55)
    ao = ao_from_height(height)
    return np.clip(np.stack([ao, rough, np.zeros_like(rough)], axis=-1) * 255.0 + 0.5, 0, 255).astype(np.uint8)


def emissive_mask(albedo: np.ndarray) -> np.ndarray:
    """Hot/saturated albedo pixels as an emissive mask (linear, 0..1).

    Windows/lamps in this pool bake their glow INTO the albedo — bright,
    saturated, usually warm. O limiar é o ponto MÉDIO entre o corpo da
    textura (q90) e o máximo (q99.9): separa bimodais (glow vs parede) sem
    um quantile fixo que morre quando o brilho ocupa mais que 1% dos texels.
    """
    linear = to_linear_rgb(albedo)
    value = linear.max(axis=-1)
    saturation = (linear.max(axis=-1) - linear.min(axis=-1)) / np.maximum(linear.max(axis=-1), 1e-4)
    hot = value * (0.35 + 0.65 * saturation)
    low, high = np.quantile(hot, 0.90), np.quantile(hot, 0.999)
    threshold = max((low + high) * 0.5, 0.10)
    span = max(high - threshold, 1e-3)
    return np.clip((hot - threshold) / span, 0, 1)


def wants_emissive(material: dict) -> bool:
    name = (material.get("name") or "").lower()
    return any(hint in name for hint in EMISSIVE_NAME_HINTS)


# ----------------------------------------------------- ktx2 encode/decode


def ktx_decode(ktx2: bytes, tmp: Path, tag: str) -> np.ndarray:
    """Embedded KTX2 → RGBA uint8 array via `ktx extract`."""
    src = tmp / f"{tag}.ktx2"
    dst = tmp / f"{tag}.png"
    src.write_bytes(ktx2)
    result = subprocess.run(["ktx", "extract", str(src), str(dst)], capture_output=True, text=True)
    if result.returncode != 0 or not dst.exists():
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        raise RuntimeError(f"ktx extract failed: {' '.join(tail)}")
    with Image.open(dst) as image:
        return np.asarray(image.convert("RGBA"), dtype=np.uint8)


def ktx_encode(arr: np.ndarray, tmp: Path, tag: str, linear: bool) -> bytes:
    """RGBA uint8 array → KTX2 UASTC+Zstd bytes with a mip chain."""
    src = tmp / f"{tag}_in.png"
    dst = tmp / f"{tag}_out.ktx2"
    # O formato é R8G8B8A8: sintetizadores de 3 canais (normal) ganham o
    # alpha a 255 aqui.
    if arr.shape[-1] == 3:
        arr = np.dstack([arr, np.full(arr.shape[:2], 255, dtype=np.uint8)])
    arr = np.ascontiguousarray(arr)
    Image.fromarray(arr, mode="RGBA").save(src)
    result = subprocess.run(
        [
            "ktx",
            "create",
            "--format",
            "R8G8B8A8_UNORM" if linear else "R8G8B8A8_SRGB",
            "--assign-tf",
            "linear" if linear else "srgb",
            "--encode",
            "uastc",
            "--generate-mipmap",
            str(src),
            str(dst),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not dst.exists():
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        raise RuntimeError(f"ktx create failed: {' '.join(tail)}")
    blob = dst.read_bytes()
    if blob[:12] != KTX2_IDENTIFIER:
        raise RuntimeError("encode wrote a non-KTX2 stream")
    levels = struct.unpack_from("<I", blob, KTX2_LEVEL_COUNT_OFFSET)[0]
    h, w = arr.shape[:2]
    if levels <= 1 and max(w, h) > 1:
        raise RuntimeError(f"{w}x{h} sem mipmaps (levelCount={levels})")
    return blob


# ------------------------------------------------------------ enrichment


def append_image(doc: dict, binary: bytearray, blob: bytes) -> int:
    """Append one embedded image to the GLB, returning its index."""
    offset = len(binary)
    binary.extend(blob)
    binary.extend(b"\x00" * (-len(blob) % 4))
    doc["bufferViews"].append(
        {
            "buffer": 0,
            "byteOffset": offset,
            "byteLength": len(blob),
        }
    )
    doc["images"].append({"mimeType": "image/ktx2", "bufferView": len(doc["bufferViews"]) - 1})
    return len(doc["images"]) - 1


def enrich_glb(blob: bytes, force: bool) -> tuple[bytes, int]:
    """One GLB in, (enriched GLB, nº of materials enriched) out."""
    doc, binary = parse_glb(blob)
    if "images" not in doc:
        doc["images"] = []
    if "bufferViews" not in doc:
        doc["bufferViews"] = []
    if "materials" not in doc:
        return blob, 0
    binary = bytearray(binary)
    enriched = 0
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp = Path(tmp_dir)
        for material in doc["materials"]:
            base_tex = (material.get("pbrMetallicRoughness") or {}).get("baseColorTexture")
            if base_tex is None:
                continue
            if material.get("normalTexture") and not force:
                continue  # já enriquecido (idempotência)
            base_image = doc["images"][base_tex["index"]]
            if "bufferView" not in base_image:
                continue  # imagem externa — fora do contrato do pool
            try:
                albedo = ktx_decode(image_bytes(doc, bytes(binary), base_image), tmp, "base")
            except RuntimeError as error:
                print(f"    ! albedo indecodável: {error}", file=sys.stderr)
                continue
            try:
                normal_idx = append_image(doc, binary, ktx_encode(synthesize_normal(albedo), tmp, "nrm", linear=True))
                orm_idx = append_image(doc, binary, ktx_encode(synthesize_orm(albedo), tmp, "orm", linear=True))
                material["normalTexture"] = {"index": normal_idx}
                material["occlusionTexture"] = {"index": orm_idx, "strength": 0.6}
                material.setdefault("pbrMetallicRoughness", {})["metallicRoughnessTexture"] = {"index": orm_idx}
                if wants_emissive(material):
                    linear = to_linear_rgb(albedo)
                    mask = emissive_mask(albedo)
                    emissive = np.clip(linear * mask[..., None] * 2.2 * 255.0 + 0.5, 0, 255).astype(np.uint8)
                    emissive[..., 3] = 255
                    emissive_idx = append_image(doc, binary, ktx_encode(emissive, tmp, "emi", linear=False))
                    material["emissiveTexture"] = {"index": emissive_idx}
                    material["emissiveFactor"] = [1.0, 1.0, 1.0]
                enriched += 1
            except RuntimeError as error:
                print(f"    ! enrich falhou: {error}", file=sys.stderr)
                continue
    doc.setdefault("buffers", [{"byteLength": 0}])[0]["byteLength"] = len(binary)
    return build_glb(doc, bytes(binary)), enriched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, required=True, help="pool canónico (meshes/)")
    parser.add_argument("--dst", type=Path, required=True, help="árvore enriquecida (meshes_enriched/)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default="", help="substring de filtro de caminho")
    parser.add_argument("--force", action="store_true", help="re-enriquece materiais já com normalTexture")
    args = parser.parse_args(argv)

    if not args.src.is_dir():
        print(f"error: {args.src} não é uma pasta", file=sys.stderr)
        return 2

    glbs = sorted(p for p in args.src.rglob("*.glb") if args.only in p.as_posix())
    print(f"enrich: {len(glbs)} GLB(s) de {args.src} → {args.dst}")
    total = done = skipped = failed = 0
    for path in glbs:
        rel = path.relative_to(args.src)
        target = args.dst / rel
        blob = path.read_bytes()
        try:
            doc, _ = parse_glb(blob)
            textured = any(
                (m.get("pbrMetallicRoughness") or {}).get("baseColorTexture") for m in doc.get("materials", [])
            )
        except (ValueError, json.JSONDecodeError) as error:
            print(f"  ✗ {rel}: {error}", file=sys.stderr)
            failed += 1
            continue
        if not textured:
            # Flat-color: cópia verbatim (o XML aponta meshes_enriched/).
            if not args.dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(blob)
            skipped += 1
            continue
        if args.dry_run:
            print(f"  ~ {rel}")
            continue
        try:
            out, count = enrich_glb(blob, args.force)
        except (ValueError, RuntimeError) as error:
            print(f"  ✗ {rel}: {error}", file=sys.stderr)
            failed += 1
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(out)
        total += count
        done += 1
        print(f"  ✓ {rel} (+{count} material(is))")
    print(f"enrich: {done} GLB(s) enriquecidos ({total} materiais), {skipped} flat copiados, {failed} falhas")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
