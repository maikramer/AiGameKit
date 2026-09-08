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

from .gltf_finish import _run_gltf_transform

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


def _blob_to_png(blob: bytes, mime: str, out: Path) -> Path:
    """KTX2→PNG via ``ktx extract``; PNG sai direto."""
    if "ktx2" in mime:
        src = out.with_suffix(".ktx2")
        src.write_bytes(blob)
        subprocess.run(
            ["ktx", "extract", str(src), str(out)],
            capture_output=True,
            timeout=_TIMEOUT_S,
            check=True,
        )
        return out
    out.write_bytes(blob)
    return out


def extract_albedo_png(glb_path: Path, tmp: Path) -> Path:
    """Albedo (baseColor do material 0) como PNG no dir temporário."""
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
    out = tmp / f"albedo_{glb_path.stem}.png"
    return _blob_to_png(blob, img.get("mimeType", "image/png"), out)


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


def derive_maps(albedo_png: Path, tmp: Path, *, preset: str, logger: Any) -> tuple[Path, Path]:
    """Normal+AO do albedo via Materialize (convenção do pbr_enrich do paint3d)."""
    materialize_bin = resolve_materialize_bin()
    if materialize_bin is None:
        raise RuntimeError("materialize não encontrado (MATERIALIZE_BIN / Materialize/target/release / PATH)")
    cmd = [
        materialize_bin,
        str(albedo_png),
        "-o",
        str(tmp),
        "-p",
        preset,
        "--only",
        "normal,ao",
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
    if not normal_p.is_file() or not ao_p.is_file():
        raise RuntimeError("mapas esperados ausentes após materialize")
    logger.info("patch-pbr: normal+ao derivados do albedo (%dx%d)", *_png_size(normal_p))
    return normal_p, ao_p


def _png_size(path: Path) -> tuple[int, int]:
    from PIL import Image

    with Image.open(path) as im:
        return im.size


def _inject_maps(gltf: dict[str, Any], bin_data: bytearray, normal_png: Path, ao_png: Path) -> None:
    mat = gltf["materials"][0]
    images = gltf.setdefault("images", [])
    textures = gltf.setdefault("textures", [])
    buffer_views = gltf.setdefault("bufferViews", [])
    for slot, png in (("normalTexture", normal_png), ("occlusionTexture", ao_png)):
        blob = png.read_bytes()
        blob += b"\x00" * ((4 - len(blob) % 4) % 4)
        buffer_views.append({"buffer": 0, "byteOffset": len(bin_data), "byteLength": len(blob)})
        images.append({"bufferView": len(buffer_views) - 1, "mimeType": "image/png", "name": png.stem.split("_")[-1]})
        textures.append({"source": len(images) - 1, "sampler": 0})
        mat[slot] = {"index": len(textures) - 1, **({"scale": 1.0} if slot == "normalTexture" else {"strength": 1.0})}
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
    """Idempotente: injeta normal+AO se faltarem; geometria intacta.

    Skip quando o material já tem os dois slots (``force`` refaz mesmo assim).
    """
    gltf, _bin = _load_glb(glb_path)
    if glb_is_pbr_complete(gltf) and not force:
        return PatchPbrResult(True, "já tem normalTexture + occlusionTexture")
    if gltf.get("materials", [{}])[0].get("pbrMetallicRoughness", {}).get("baseColorTexture") is None:
        return PatchPbrResult(True, "sem baseColorTexture — GLB sem PBR para enriquecer")

    dst = output_path or glb_path
    with tempfile.TemporaryDirectory(prefix="text3d_patch_pbr_") as td_raw:
        tmp = Path(td_raw)
        albedo_png = extract_albedo_png(glb_path, tmp)
        normal_png, ao_png = derive_maps(albedo_png, tmp, preset=preset, logger=logger)

        gltf, bin_data = _load_glb(glb_path)
        _inject_maps(gltf, bin_data, normal_png, ao_png)
        patched = tmp / "patched.glb"
        _save_glb(patched, gltf, bin_data)

        # KTX2 UASTC (flags do gltf_finish) + meshopt re-aplicado (o uastc
        # decodifica o meshopt existente).
        uastc = tmp / "uastc.glb"
        ok, err = _run_gltf_transform(
            "uastc", patched, uastc, ["--level", "2", "--rdo", "1.0", "--zstd", "18", "--slots", "*"]
        )
        if not ok:
            raise RuntimeError(f"gltf-transform uastc falhou: {err}")
        final = tmp / "final.glb"
        ok, err = _run_gltf_transform("meshopt", uastc, final, ["--level", "high"])
        if not ok:
            raise RuntimeError(f"gltf-transform meshopt falhou: {err}")

        # Escrita atómica (crash entre passes não deixa GLB intermédio).
        os.replace(final, dst)

    return PatchPbrResult(False, "normal+AO injetados", output=dst)
