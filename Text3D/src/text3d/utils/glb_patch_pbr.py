"""Patch PBR pós-LOD: garante normal + occlusion em GLBs (sem re-decimar).

O Hunyuan-Paint gera albedo+MR; o pbr_enrich do paint3d deriva normal/AO do
albedo (via Materialize) e embute no painted — mas o LOD rebake pode deixar
cair a occlusion (o remesh preserva base/mr/normal, não trata ao). Este módulo
é o passo idempotente pós-lod: se o material já tem ``normalTexture`` E
``occlusionTexture`` → skip; senão deriva os mapas do albedo do próprio GLB
(mesmo UV atlas, resolução do nível) e injeta-os via cirurgia nos chunks
JSON/BIN. A geometria não é tocada — os bufferViews existentes e as extensões
(meshopt/quantization/texture_basisu) ficam intactos.

Pipeline por ficheiro:
  extrai albedo (KTX2→PNG via ``ktx extract`` ou PNG direto)
  → materialize --only normal,ao (mesma convenção do pbr_enrich do paint3d)
  → injeta PNGs + liga os slots do material
  → ``gltf-transform uastc`` (flags do gltf_finish) → ``meshopt --level high``
  → escrita atómica (tmp + os.replace)
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

JSON_CHUNK = 0x4E4F534A
BIN_CHUNK = 0x004E4942
_TIMEOUT_S = 600


class PatchPbrResult:
    """Resultado de um patch: `skipped` = já completo (idempotência)."""

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
    return json.loads(chunks[JSON_CHUNK].decode("utf-8")), bytearray(chunks[BIN_CHUNK])


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
    """Índices de textura dos slots PBR do material (None = ausente)."""
    mat = gltf.get("materials", [])[material_index]
    pbr = mat.get("pbrMetallicRoughness", {})
    return {
        "base": (pbr.get("baseColorTexture") or {}).get("index"),
        "mr": (pbr.get("metallicRoughnessTexture") or {}).get("index"),
        "normal": (mat.get("normalTexture") or {}).get("index"),
        "ao": (mat.get("occlusionTexture") or {}).get("index"),
    }


def glb_is_pbr_complete(gltf: dict[str, Any]) -> bool:
    slots = material_slots(gltf)
    return slots["base"] is not None and slots["normal"] is not None and slots["ao"] is not None


def _image_blob(gltf: dict[str, Any], bin_data: bytes, image_index: int) -> bytes:
    im = gltf["images"][image_index]
    ext = im.get("extensions", {})
    src = ext.get("KHR_texture_basisu", {}).get("source") if "KHR_texture_basisu" in ext else im.get("bufferView")
    bv = gltf["bufferViews"][src]
    return bin_data[bv.get("byteOffset", 0) : bv.get("byteOffset", 0) + bv["byteLength"]]


def _blob_to_image(blob: bytes, mime: str, stem: str, tmp: Path) -> Path:
    """KTX2→PNG via ``ktx extract``; PNG/JPEG saem direto (extensão pelo mime).

    O albedo do paint export pode ser JPEG (AUTO no exporter) — materialize
    lê jpg, mas tem de estar com a extensão certa.
    """
    if "ktx2" in mime:
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


def extract_albedo_png(glb_path: Path, tmp: Path) -> Path:
    """Albedo (baseColor do material 0) como PNG/JPEG no dir temporário."""
    gltf, bin_data = _load_glb(glb_path)
    base = material_slots(gltf)["base"]
    if base is None:
        raise RuntimeError(f"{glb_path.name}: material sem baseColorTexture")
    tex = gltf["textures"][base]
    # O source pode viver no texture (KHR_texture_basisu) em GLBs gltf-transform.
    src = tex.get("extensions", {}).get("KHR_texture_basisu", {}).get("source", tex.get("source"))
    if src is None:
        raise RuntimeError(f"{glb_path.name}: texture sem source")
    img = gltf["images"][src]
    blob = _image_blob(gltf, bytes(bin_data), src)
    return _blob_to_image(blob, img.get("mimeType", "image/png"), f"albedo_{glb_path.stem}", tmp)


def resolve_materialize_bin() -> str | None:
    """MATERIALIZE_BIN → checkout release → PATH (convenção kind=rust)."""
    env_bin = os.environ.get("MATERIALIZE_BIN", "").strip()
    if env_bin and Path(env_bin).is_file():
        return env_bin
    # utils → text3d → src → Text3D → raiz do monorepo.
    repo_root = Path(__file__).resolve().parents[4]
    for name in ("materialize", "materialize-cli"):
        cand = repo_root / "Materialize" / "target" / "release" / name
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return shutil.which("materialize") or shutil.which("materialize-cli")


def derive_maps(
    albedo_png: Path,
    tmp: Path,
    *,
    preset: str,
    logger: Any,
    want_normal: bool = True,
    want_ao: bool = True,
) -> tuple[Path | None, Path | None]:
    """Normal+AO do albedo via Materialize (convenção do pbr_enrich do paint3d).

    ``want_normal``/``want_ao`` limitam o custo: um GLB que só precisa de AO
    não paga a derivação da normal (que é a que cria arestas falsas no atlas).
    """
    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        raise RuntimeError("materialize não encontrado (MATERIALIZE_BIN / Materialize/target/release / PATH)")
    only = ",".join(x for x, on in (("normal", want_normal), ("ao", want_ao)) if on)
    if not only:
        return None, None
    cmd = [
        materialize_bin,
        str(albedo_png),
        "-o",
        str(tmp),
        "-p",
        preset,
        "--only",
        only,
        "--no-seamless",
        "--normal-format",
        "opengl",
        "-f",
        "png",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT_S, check=False)
    if proc.returncode != 0:
        tail = (proc.stderr.strip().splitlines() or [f"rc={proc.returncode}"])[-1]
        raise RuntimeError(f"materialize falhou: {tail}")
    normal_p = tmp / f"{albedo_png.stem}_normal.png"
    ao_p = tmp / f"{albedo_png.stem}_ao.png"
    if want_normal and not normal_p.is_file():
        raise RuntimeError("normal esperada ausente após materialize")
    if want_ao and not ao_p.is_file():
        raise RuntimeError("ao esperado ausente após materialize")
    logger.info("patch-pbr: %s derivados do albedo (%dx%d)", only, *_png_size(normal_p if want_normal else ao_p))
    return (normal_p if want_normal else None), (ao_p if want_ao else None)


def _png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def _png_to_ktx2(png: Path, tmp: Path) -> Path:
    """PNG → KTX2 UASTC+Zstd via KTX-Software (linear, mapa de dados).

    PNGs grayscale (ex.: AO do Materialize) saem com DFD ``UASTC_RRRR`` — o
    Bevy 0.19 fatia o payload pelo block size do BC4 e o transcode falha.
    Promover para RGB antes do encode **e** normalizar o DFD do resultado
    (belt-and-suspenders; ver ``aigamekit_shared.gltf_ktx2``).
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
        pass  # sem PIL: o normalizador de DFD a jusante ainda apanha
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


def _inject_maps(
    gltf: dict[str, Any],
    bin_data: bytearray,
    normal_png: Path | None,
    ao_png: Path | None,
    tmp: Path,
    *,
    inject_normal: bool = True,
    inject_ao: bool = True,
) -> None:
    """Injeta normal+AO como KTX2 (convenção gltf-transform: image/ktx2 +
    ``extensions.KHR_texture_basisu.source`` no texture). Geometria e meshopt
    existentes ficam intactos — nenhum re-encode.

    Só injeta os slots pedidos: um normal map existente nunca é substituído
    por um derivado do albedo (o derivado tem arestas falsas em cada ilha UV).
    """
    mat = gltf["materials"][0]
    images = gltf.setdefault("images", [])
    textures = gltf.setdefault("textures", [])
    buffer_views = gltf.setdefault("bufferViews", [])
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


def patch_glb_pbr(
    glb_path: Path,
    *,
    output_path: Path | None = None,
    preset: str = "default",
    force: bool = False,
    logger: Any = None,
) -> PatchPbrResult:
    """Idempotente: injeta só os slots em falta; geometria intacta.

    Um normal map existente **nunca** é substituído por um derivado do albedo
    (o derivado tem arestas falsas em cada ilha UV). ``force`` refaz ambos.
    """
    gltf, _bin = _load_glb(glb_path)
    if glb_is_pbr_complete(gltf) and not force:
        return PatchPbrResult(True, "já tem normalTexture + occlusionTexture")
    mats = gltf.get("materials") or []
    if not mats or mats[0].get("pbrMetallicRoughness", {}).get("baseColorTexture") is None:
        return PatchPbrResult(True, "sem baseColorTexture — GLB sem PBR para enriquecer")
    if len(mats) > 1 and logger is not None:
        logger.warning(
            "patch-pbr: %d materiais — só o material 0 é enriquecido (mapas derivados do albedo dele)",
            len(mats),
        )
    has_normal = "normalTexture" in mats[0]
    has_ao = "occlusionTexture" in mats[0]
    inject_normal = force or not has_normal
    inject_ao = force or not has_ao
    if not inject_normal and not inject_ao:
        return PatchPbrResult(True, "nada em falta")

    dst = output_path or glb_path
    # Tempdir no filesystem do DESTINO: os.replace é atómico no mesmo device
    # (EXDEV se o /tmp for outro filesystem, como no pool /media).
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="text3d_patch_pbr_", dir=dst.parent) as td_raw:
        tmp = Path(td_raw)
        albedo_png = extract_albedo_png(glb_path, tmp)
        normal_png, ao_png = derive_maps(
            albedo_png,
            tmp,
            preset=preset,
            logger=logger,
            want_normal=inject_normal,
            want_ao=inject_ao,
        )

        gltf, bin_data = _load_glb(glb_path)
        _inject_maps(
            gltf,
            bin_data,
            normal_png,
            ao_png,
            tmp,
            inject_normal=inject_normal,
            inject_ao=inject_ao,
        )
        final = tmp / "final.glb"
        _save_glb(final, gltf, bin_data)

        # Escrita atómica (crash entre passes não deixa GLB intermédio).
        os.replace(final, dst)

    added = "+".join(x for x, on in (("normal", inject_normal), ("AO", inject_ao)) if on)
    return PatchPbrResult(False, f"{added} injetados", output=dst)
