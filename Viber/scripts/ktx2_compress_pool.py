#!/usr/bin/env python3
"""Compress a world's PNG/JPEG-textured GLBs to KTX2 (UASTC + Zstandard).

WHY: ``simple-rpg`` ships 169 GLBs whose textures are still plain PNG — 85 of
them 2048x2048. A PNG is compressed on disk and **uncompressed in VRAM**: the
GPU stores RGBA8, so those textures alone measured ~2.1 GiB of the engine's
2.8 GiB on a 6 GiB card. The same textures as KTX2/UASTC transcode to BC7 on
desktop — one byte per texel instead of four, ~530 MiB — with no change to
the meshes, the materials or the world XML.

The encode must be ``uastc`` and never ``etc1s``: Bevy 0.19 decompresses
only ZLIB and Zstandard supercompression, and ETC1S ships as BasisLZ, which
fails to load outright. Plain UASTC (``supercompressionScheme = 0``) is fine
— the ``basis-universal`` feature transcodes the *format* to BC7 afterwards,
which is where the 4x VRAM saving comes from; ``--zstd`` only shrinks the
file on disk. See ``reencode_ktx2_uastc.py`` for the sibling script that
drags an already-KTX2 pool off BasisLZ.

Meshes are deliberately left alone: ``gltf-transform meshopt`` recenters the
bbox and lifts a rigged character's feet off ``y = 0``.

SAFETY: each output is verified (every image is KTX2, supercompression reads
back as Zstandard) before it replaces the input, so a failure mid-run leaves
the pool consistent.

    python3 Viber/scripts/ktx2_compress_pool.py --assets Viber/examples/shared-assets/public/assets
    python3 Viber/scripts/ktx2_compress_pool.py --assets ... --dry-run

Assets partilhados vivem SÓ no pool (Viber/docs/ASSETS.md) — não correr isto
contra cópias por exemplo.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

#: KTX2 file identifier (spec §3.1).
KTX2_IDENTIFIER = b"\xabKTX 20\xbb\r\n\x1a\n"

#: Byte offsets of the KTX2 header fields this script reads (spec §3.1).
#: The header is a flat sequence of `uint32`s after the 12-byte identifier:
#: vkFormat, typeSize, pixelWidth, pixelHeight, pixelDepth, layerCount,
#: faceCount, levelCount, supercompressionScheme. Counting the wrong field
#: here is silent and dangerous — reading `layerCount` where the scheme
#: belongs reports "no supercompression" for *every* file, which would wave
#: a BasisLZ texture (the one thing Bevy cannot load) straight through.
KTX2_LEVEL_COUNT_OFFSET = 40
KTX2_SUPERCOMPRESSION_OFFSET = 44
#: ``supercompressionScheme`` values (spec §3.9). Bevy 0.19 reads NONE and
#: ZSTD (and ZLIB); BASIS_LZ is the one it cannot decompress, so it is the
#: only value this script treats as a failure.
SUPERCOMPRESSION_NONE = 0
SUPERCOMPRESSION_BASIS_LZ = 1
SUPERCOMPRESSION_ZSTD = 2
SUPERCOMPRESSION_READABLE = (SUPERCOMPRESSION_NONE, SUPERCOMPRESSION_ZSTD)


def glb_json(path: Path) -> dict | None:
    """Parse the JSON chunk of a binary glTF, or ``None`` if it is not one."""
    data = path.read_bytes()
    if data[:4] != b"glTF":
        return None
    length = struct.unpack_from("<I", data, 12)[0]
    return json.loads(data[20 : 20 + length])


def image_mime_types(doc: dict) -> list[str]:
    """MIME types of every embedded image, in document order."""
    return [image.get("mimeType", "") for image in doc.get("images", [])]


def is_pipeline_intermediate(path: Path) -> bool:
    """True for ``_intermediate/`` artifacts — pipeline inputs, not deliverables.

    Those GLBs are consumed by later stages (``rigging3d``, ``animator3d``,
    ``text3d lod``) through bpy, which cannot read KTX2 without a decode pass
    (``aigamekit_shared.gltf_decode.bpy_readable_glb``). Encoding them buys no
    VRAM — they never reach the GPU — and feeds a lossy UASTC round-trip into
    everything generated from them.
    """
    return "_intermediate" in path.parts


def needs_compression(path: Path) -> bool:
    """True when the GLB still carries at least one PNG/JPEG texture."""
    doc = glb_json(path)
    if doc is None:
        return False
    return any(mime in ("image/png", "image/jpeg") for mime in image_mime_types(doc))


def verify_ktx2_zstd(path: Path) -> tuple[bool, str]:
    """Check every image is KTX2 and Zstandard-supercompressed."""
    doc = glb_json(path)
    if doc is None:
        return False, "not a binary glTF"
    if "KHR_texture_basisu" not in doc.get("extensionsUsed", []):
        return False, "KHR_texture_basisu missing"
    mimes = image_mime_types(doc)
    if any(mime != "image/ktx2" for mime in mimes):
        return False, f"non-KTX2 image survived: {sorted(set(mimes))}"

    data = path.read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    body = 20 + length + 8
    views = doc.get("bufferViews", [])
    for image in doc.get("images", []):
        view = views[image["bufferView"]]
        start = body + view.get("byteOffset", 0)
        blob = data[start : start + view["byteLength"]]
        if blob[:12] != KTX2_IDENTIFIER:
            return False, "image is not a KTX2 stream"
        scheme = struct.unpack_from("<I", blob, KTX2_SUPERCOMPRESSION_OFFSET)[0]
        if scheme not in SUPERCOMPRESSION_READABLE:
            reason = "BasisLZ" if scheme == SUPERCOMPRESSION_BASIS_LZ else str(scheme)
            return False, f"supercompression {reason} — Bevy reads only none/Zstd"
        # A compressed texture with a single level gets no mipmaps at all:
        # `patch_image` in `src/textures.rs` only builds a mip chain for plain
        # RGBA8, so a 2048² BC7 without levels shimmers and thrashes the
        # texture cache. Anything above 1x1 must ship its chain.
        levels = struct.unpack_from("<I", blob, KTX2_LEVEL_COUNT_OFFSET)[0]
        width, height = struct.unpack_from("<II", blob, 12 + 2 * 4)
        if levels <= 1 and max(width, height) > 1:
            return False, f"{width}x{height} sem mipmaps (levelCount={levels})"
    return True, "ok"


def compress(path: Path, level: int, rdo_lambda: float, zstd: int) -> tuple[bool, str]:
    """Encode one GLB in place; the original survives any failure."""
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / path.name
        command = [
            "npx",
            "--no-install",
            "@gltf-transform/cli",
            "uastc",
            str(path),
            str(out),
            "--level",
            str(level),
            "--rdo",
            "--rdo-lambda",
            str(rdo_lambda),
            "--zstd",
            str(zstd),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            tail = (result.stderr or result.stdout).strip().splitlines()[-1:]
            return False, f"gltf-transform failed: {' '.join(tail)}"
        if not out.exists():
            return False, "gltf-transform wrote no output"
        ok, why = verify_ktx2_zstd(out)
        if not ok:
            return False, why
        shutil.move(str(out), str(path))
    return True, "ok"


# ------------------------------------------------------- loose textures

#: Standalone image files worth converting.
LOOSE_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")

#: Substrings that mark a file as **data**, not colour. Encoding a heightmap
#: with a lossy codec destroys the terrain; reference art under ``images/``
#: is never loaded by the engine at all.
LOOSE_SKIP = ("heightmap", "terrain.", "/images/", "_intermediate")

#: Maps whose values are numbers, not colour — they must stay linear or the
#: transfer function double-applies and lighting goes wrong.
LINEAR_HINTS = (
    "normal",
    "roughness",
    "smooth",  # smoothness = 1 - roughness, igualmente um número
    "metal",
    "_ao",
    "ao.",
    "occlusion",
    "height",
    "displace",
    "edge",  # máscara de aresta (curvature) — dados, não cor
    "mask",
    "specular",
)


def loose_targets(root: Path) -> list[Path]:
    """Standalone textures under `root` that have no `.ktx2` sibling yet."""
    out = []
    for path in sorted(root.rglob("*")):
        if path.suffix.lower() not in LOOSE_SUFFIXES:
            continue
        posix = path.as_posix().lower()
        if any(skip in posix for skip in LOOSE_SKIP):
            continue
        if path.with_suffix(".ktx2").exists():
            continue
        out.append(path)
    return out


def convert_loose(path: Path) -> tuple[bool, str]:
    """Encode one standalone texture to a sibling ``.ktx2`` (UASTC + mips).

    ``ktx create`` reads PNG and JPEG only, so WebP goes through Pillow first.
    The original is left in place: the world XML still points at it until the
    references are updated, and a half-converted world that renders is worth
    more than a tidy one that does not.
    """
    try:
        from PIL import Image
    except ImportError:
        return False, "Pillow não instalado (pip install pillow)"

    linear = any(hint in path.as_posix().lower() for hint in LINEAR_HINTS)
    out = path.with_suffix(".ktx2")
    with tempfile.TemporaryDirectory() as tmp:
        source = path
        if path.suffix.lower() == ".webp":
            source = Path(tmp) / "source.png"
            with Image.open(path) as image:
                image.convert("RGBA").save(source)
        command = [
            "ktx",
            "create",
            "--format",
            "R8G8B8A8_UNORM" if linear else "R8G8B8A8_SRGB",
            "--assign-tf",
            "linear" if linear else "srgb",
            "--encode",
            "uastc",
            "--generate-mipmap",
            str(source),
            str(out),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        out.unlink(missing_ok=True)
        tail = (result.stderr or result.stdout).strip().splitlines()[-1:]
        return False, f"ktx create failed: {' '.join(tail)}"
    if not out.exists():
        return False, "ktx create wrote no output"
    with out.open("rb") as handle:
        header = handle.read(64)
    if header[:12] != KTX2_IDENTIFIER:
        out.unlink(missing_ok=True)
        return False, "output is not a KTX2 stream"
    levels = struct.unpack_from("<I", header, KTX2_LEVEL_COUNT_OFFSET)[0]
    width, height = struct.unpack_from("<II", header, 12 + 2 * 4)
    if levels <= 1 and max(width, height) > 1:
        out.unlink(missing_ok=True)
        return False, f"{width}x{height} sem mipmaps (levelCount={levels})"
    return True, f"{'linear' if linear else 'srgb'}, {levels} mips"


def run_loose(root: Path, dry_run: bool) -> int:
    """Convert every standalone texture under `root`. Returns an exit code."""
    pending = loose_targets(root)
    print(f"\n{len(pending)} textura(s) solta(s) sem .ktx2 sob {root}")
    if dry_run:
        for path in pending:
            print(f"  would convert {path}")
        return 0
    failures: list[tuple[Path, str]] = []
    for index, path in enumerate(pending, start=1):
        ok, why = convert_loose(path)
        out = path.with_suffix(".ktx2")
        size = out.stat().st_size / 1048576 if ok else 0.0
        print(
            f"[{index}/{len(pending)}] {'ok ' if ok else 'FAIL'} {path.name} -> {out.name} ({size:.1f} MiB, {why})",
            flush=True,
        )
        if not ok:
            failures.append((path, why))
    if failures:
        print(f"\n{len(failures)} textura(s) solta(s) falharam:", file=sys.stderr)
        for path, why in failures:
            print(f"  {path}: {why}", file=sys.stderr)
        return 1
    if pending:
        print("\nATENÇÃO: os originais ficaram no sítio — actualize as")
        print("referências no world XML (`texture=`, `src=`) para os .ktx2.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--assets", required=True, type=Path, help="asset root to walk")
    parser.add_argument("--level", type=int, default=2, help="UASTC speed/quality (0-4)")
    parser.add_argument("--rdo-lambda", type=float, default=4.0, help="UASTC RDO lambda")
    parser.add_argument("--zstd", type=int, default=18, help="Zstandard level")
    parser.add_argument("--limit", type=int, default=0, help="stop after N files")
    parser.add_argument("--dry-run", action="store_true", help="list the work only")
    parser.add_argument(
        "--include-intermediate",
        action="store_true",
        help="also encode _intermediate/ artifacts (pipeline inputs — see the module docs)",
    )
    parser.add_argument(
        "--loose",
        action="store_true",
        help="also convert standalone PNG/JPEG/WebP textures to sibling .ktx2 files",
    )
    args = parser.parse_args()

    if not args.assets.is_dir():
        print(f"error: {args.assets} is not a directory", file=sys.stderr)
        return 2

    pending = sorted(
        p
        for p in args.assets.rglob("*.glb")
        if needs_compression(p) and (args.include_intermediate or not is_pipeline_intermediate(p))
    )
    if args.limit:
        pending = pending[: args.limit]
    print(f"{len(pending)} GLB(s) with uncompressed textures under {args.assets}")
    if args.dry_run:
        for path in pending:
            print(f"  would compress {path}")
        return run_loose(args.assets, True) if args.loose else 0
    if not pending and args.loose:
        return run_loose(args.assets, False)

    failures: list[tuple[Path, str]] = []
    for index, path in enumerate(pending, start=1):
        before = path.stat().st_size
        ok, why = compress(path, args.level, args.rdo_lambda, args.zstd)
        after = path.stat().st_size
        status = "ok " if ok else "FAIL"
        print(
            f"[{index}/{len(pending)}] {status} {path.name} "
            f"({before / 1048576:.1f} -> {after / 1048576:.1f} MiB on disk)" + ("" if ok else f" — {why}"),
            flush=True,
        )
        if not ok:
            failures.append((path, why))

    if failures:
        print(f"\n{len(failures)} file(s) left untouched:", file=sys.stderr)
        for path, why in failures:
            print(f"  {path}: {why}", file=sys.stderr)
        return 1
    print("\nall textures are KTX2/UASTC+Zstd — VRAM drops ~4x versus RGBA8")
    if args.loose:
        return run_loose(args.assets, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
