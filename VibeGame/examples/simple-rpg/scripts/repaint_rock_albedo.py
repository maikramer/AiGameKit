#!/usr/bin/env python3
"""Repinta o albedo dos GLBs de rocha do pool shared-assets para pedra neutra.

Porquê: o paint (Hunyuan3D-Paint) devolveu `form_stack_6`, `form_stack_11` e
`stone_cairn` com albedo lavanda/azul/laranja — no mapa liam-se como gelatinas
pastel, não como cairns. Os meshes estão certos; só a textura é que não.

Os GLBs do pool NÃO são versionados (`shared-assets/.gitignore` ignora
`public/assets/meshes/`), por isso este passo tem de ser re-executável depois
de cada `npm run fetch-assets`. Um sidecar `<glb>.repainted.json` marca o que
já foi tratado — correr duas vezes não volta a escurecer.

Pipeline por ficheiro: KTX2 do GLB → `ktx extract` → rampa de luminância →
`ktx create --encode basis-lz` → repack do GLB (bufferViews + offsets do
EXT_meshopt_compression recalculados).

Requisitos: `ktx` (KTX-Software) no PATH — ver docs/GLB_FINISH_COMPRESSION.md.

Uso:
    python3 scripts/repaint_rock_albedo.py [--meshes <dir>] [--force]
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

DEFAULT_MESHES = Path(__file__).resolve().parents[2] / "shared-assets" / "public" / "assets" / "meshes" / "terrain"

TARGETS = [f"{name}_lod{lod}.glb" for name in ("form_stack_6", "form_stack_11", "stone_cairn") for lod in (0, 1, 2)]

# Rampa de pedra: sombra neutra fria → realce quente claro.
RAMP: list[tuple[float, tuple[int, int, int]]] = [
    (0.00, (34, 32, 30)),
    (0.30, (72, 68, 62)),
    (0.60, (108, 102, 93)),
    (0.85, (146, 139, 128)),
    (1.00, (178, 170, 158)),
]
# Fração da cor original que volta ao resultado: mantém variação sem o pastel.
ORIGINAL_MIX = 0.12


def _lut() -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for i in range(256):
        t = i / 255.0
        for k in range(len(RAMP) - 1):
            t0, c0 = RAMP[k]
            t1, c1 = RAMP[k + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                out.append(tuple(round(c0[j] + (c1[j] - c0[j]) * f) for j in range(3)))  # type: ignore[arg-type]
                break
        else:
            out.append(RAMP[-1][1])
    return out


LUT = _lut()


def regrade(src: Path, dst: Path) -> None:
    im = Image.open(src).convert("RGB")
    px = im.load()
    w, h = im.size
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            sr, sg, sb = LUT[int(0.299 * r + 0.587 * g + 0.114 * b)]
            px[x, y] = (
                round(sr * (1 - ORIGINAL_MIX) + r * ORIGINAL_MIX),
                round(sg * (1 - ORIGINAL_MIX) + g * ORIGINAL_MIX),
                round(sb * (1 - ORIGINAL_MIX) + b * ORIGINAL_MIX),
            )
    im.save(dst)


def glb_read(path: Path) -> tuple[dict, bytes]:
    data = path.read_bytes()
    if data[:4] != b"glTF":
        raise ValueError(f"{path.name}: não é um GLB")
    off, js, binc = 12, None, b""
    while off < len(data):
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8 : off + 8 + clen]
        if ctype == 0x4E4F534A:
            js = json.loads(chunk)
        elif ctype == 0x004E4942:
            binc = chunk
        off += 8 + clen
    if js is None:
        raise ValueError(f"{path.name}: sem chunk JSON")
    return js, binc


def glb_write(path: Path, js: dict, binc: bytes) -> None:
    jb = json.dumps(js, separators=(",", ":")).encode()
    jb += b" " * ((4 - len(jb) % 4) % 4)
    bb = binc + b"\0" * ((4 - len(binc) % 4) % 4)
    total = 12 + 8 + len(jb) + 8 + len(bb)
    out = bytearray(b"glTF" + struct.pack("<II", 2, total))
    out += struct.pack("<II", len(jb), 0x4E4F534A) + jb
    out += struct.pack("<II", len(bb), 0x004E4942) + bb
    path.write_bytes(bytes(out))


def _segments(js: dict) -> list[tuple[tuple[str, int], int, int]]:
    """Tudo o que aponta para o buffer 0 (view normal ou payload meshopt)."""
    segs: list[tuple[tuple[str, int], int, int]] = []
    for i, bv in enumerate(js["bufferViews"]):
        ext = bv.get("extensions", {}).get("EXT_meshopt_compression")
        if ext is not None and ext.get("buffer") == 0:
            segs.append((("ext", i), ext["byteOffset"], ext["byteLength"]))
        elif ext is None and bv.get("buffer") == 0:
            segs.append((("bv", i), bv.get("byteOffset", 0), bv["byteLength"]))
    segs.sort(key=lambda s: s[1])
    return segs


def repack(path: Path, js: dict, binc: bytes, image_bv: int, new_image: bytes) -> None:
    out = bytearray()
    for (kind, idx), off, ln in _segments(js):
        payload = new_image if (kind == "bv" and idx == image_bv) else binc[off : off + ln]
        while len(out) % 4:
            out.append(0)
        new_off = len(out)
        out += payload
        if kind == "bv":
            js["bufferViews"][idx]["byteOffset"] = new_off
            js["bufferViews"][idx]["byteLength"] = len(payload)
        else:
            e = js["bufferViews"][idx]["extensions"]["EXT_meshopt_compression"]
            e["byteOffset"] = new_off
            e["byteLength"] = len(payload)
    js["buffers"][0]["byteLength"] = len(out)
    glb_write(path, js, bytes(out))


def repaint(glb: Path, work: Path, force: bool) -> str:
    marker = glb.with_suffix(".repainted.json")
    if marker.exists() and not force:
        return "já repintado"
    js, binc = glb_read(glb)
    images = js.get("images", [])
    if not images or images[0].get("mimeType") != "image/ktx2":
        return "sem albedo KTX2 — ignorado"
    bv_idx = images[0]["bufferView"]
    bv = js["bufferViews"][bv_idx]
    off = bv.get("byteOffset", 0)
    stem = glb.stem
    k_in, p_in = work / f"{stem}.in.ktx2", work / f"{stem}.in.png"
    p_out, k_out = work / f"{stem}.out.png", work / f"{stem}.out.ktx2"
    k_in.write_bytes(binc[off : off + bv["byteLength"]])
    subprocess.run(["ktx", "extract", "--level", "0", str(k_in), str(p_in)], check=True)
    regrade(p_in, p_out)
    subprocess.run(
        [
            "ktx",
            "create",
            "--format",
            "R8G8B8A8_SRGB",
            "--encode",
            "basis-lz",
            "--generate-mipmap",
            "--assign-tf",
            "srgb",
            str(p_out),
            str(k_out),
        ],
        check=True,
        capture_output=True,
    )
    repack(glb, js, binc, bv_idx, k_out.read_bytes())
    marker.write_text(json.dumps({"ramp": RAMP, "original_mix": ORIGINAL_MIX}, indent=2))
    return f"repintado ({k_out.stat().st_size} B)"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--meshes", type=Path, default=DEFAULT_MESHES)
    ap.add_argument("--force", action="store_true", help="repinta mesmo com sidecar")
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix="repaint-rock-") as tmp:
        work = Path(tmp)
        for name in TARGETS:
            glb = args.meshes / name
            if not glb.exists():
                print(f"  {name}: em falta — corre `npm run fetch-assets` primeiro")
                continue
            print(f"  {name}: {repaint(glb, work, args.force)}")


if __name__ == "__main__":
    main()
