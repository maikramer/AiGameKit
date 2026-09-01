#!/usr/bin/env python3
"""Re-encode the shared-asset pool's KTX2 textures from ETC1S to UASTC+Zstd.

WHY: the pool's LOD GLBs carry KTX2 textures supercompressed with **BasisLZ**
(the output of ``gltf-transform etc1s``). Bevy 0.19's KTX2 reader implements
only ZLIB and Zstandard supercompression — its ``basis-universal`` feature
transcodes the *format* afterwards and is never reached — so every such GLB
fails to load in Viber with::

    Unsupported supercompression scheme: BasisLZ

``Text3D``'s ``gltf_transform_finish`` now emits ``uastc --zstd``, so newly
generated assets are fine. This script brings the existing pool forward.

WHAT IT DOES, per GLB: ``ktxdecompress`` (back to PNG) then
``gltf_transform_finish`` (UASTC+Zstd, and meshopt again for static meshes).
Skinned GLBs deliberately come out without meshopt — see the note in
``gltf_finish.py``: ``gltf-transform meshopt`` recenters the bbox and lifts a
character's feet off ``y = 0``.

SAFETY: every asset is verified before it replaces the original —
supercompression must read back as Zstandard, and a static input that had
meshopt must still have it. A file that fails verification is left untouched
and reported, so a failure mid-run cannot leave a half-converted pool.

    python3 Viber/scripts/reencode_ktx2_uastc.py --pool Viber/examples/shared-assets/public
    python3 Viber/scripts/reencode_ktx2_uastc.py --pool ... --limit 5 --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# KTX2 supercompression scheme ids (KTX v2 spec, header offset 12 + 32).
SCHEMES = {0: "None", 1: "BasisLZ", 2: "Zstandard", 3: "ZLIB"}
KTX2_IDENTIFIER = b"\xabKTX 20\xbb\r\n\x1a\n"
WANTED_SCHEME = "Zstandard"


@dataclass
class GlbInfo:
    """What the converter needs to know about a GLB before touching it."""

    extensions: list[str]
    skinned: bool
    schemes: list[str]

    @property
    def has_ktx2(self) -> bool:
        return bool(self.schemes)

    @property
    def has_meshopt(self) -> bool:
        return "EXT_meshopt_compression" in self.extensions


def read_glb(path: Path) -> GlbInfo | None:
    """Parses a GLB's JSON chunk and its KTX2 supercompression schemes."""
    try:
        raw = path.read_bytes()
        json_len = struct.unpack("<I", raw[12:16])[0]
        doc = json.loads(raw[20 : 20 + json_len])
    except Exception:
        return None

    offset = 20 + json_len
    binary = b""
    if len(raw) >= offset + 8:
        bin_len = struct.unpack("<I", raw[offset : offset + 4])[0]
        binary = raw[offset + 8 : offset + 8 + bin_len]

    schemes: list[str] = []
    views = doc.get("bufferViews", [])
    for image in doc.get("images", []):
        if image.get("mimeType") != "image/ktx2":
            continue
        view = views[image["bufferView"]]
        start = view.get("byteOffset", 0)
        data = binary[start : start + view["byteLength"]]
        if data[:12] != KTX2_IDENTIFIER:
            schemes.append("?")
            continue
        scheme = struct.unpack("<I", data[44:48])[0]
        schemes.append(SCHEMES.get(scheme, str(scheme)))

    return GlbInfo(
        extensions=list(doc.get("extensionsUsed", [])),
        skinned=bool(doc.get("skins")),
        schemes=schemes,
    )


def ktx_decompress(src: Path, dst: Path) -> tuple[bool, str]:
    """Decodes KTX2 textures back to PNG so they can be re-encoded."""
    result = subprocess.run(
        ["npx", "--yes", "@gltf-transform/cli", "ktxdecompress", str(src), str(dst)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        return False, detail[-1] if detail else f"exit {result.returncode}"
    return True, ""


def verify(path: Path, before: GlbInfo) -> str | None:
    """Returns a reason string when the converted GLB is not acceptable."""
    after = read_glb(path)
    if after is None:
        return "output is not a readable GLB"
    if not after.has_ktx2:
        return "output lost its KTX2 textures"
    bad = [s for s in after.schemes if s != WANTED_SCHEME]
    if bad:
        return f"supercompression is {sorted(set(bad))}, expected {WANTED_SCHEME}"
    # Static meshes must keep their geometry compression; skinned ones are
    # expected to lose it (see the module docstring).
    if before.has_meshopt and not after.has_meshopt and not before.skinned:
        return "static mesh lost EXT_meshopt_compression"
    return None


def convert(path: Path, finish, dry_run: bool) -> tuple[str, str]:
    """Converts one GLB in place. Returns ``(status, detail)``."""
    before = read_glb(path)
    if before is None:
        return "skip", "not a readable GLB"
    if not before.has_ktx2:
        return "skip", "no KTX2 textures"
    if all(s == WANTED_SCHEME for s in before.schemes):
        return "skip", "already Zstandard"
    if dry_run:
        kind = "skinned" if before.skinned else "static"
        return "would-convert", f"{kind}, {sorted(set(before.schemes))}"

    with tempfile.TemporaryDirectory(prefix="ktx2_uastc_") as tmpdir:
        tmp = Path(tmpdir)
        decoded = tmp / "decoded.glb"
        ok, err = ktx_decompress(path, decoded)
        if not ok:
            return "fail", f"ktxdecompress: {err}"
        out = tmp / "out.glb"
        try:
            # Tangents are already baked in the pool; re-running MikkTSpace
            # here would need bpy and change nothing.
            finish(decoded, out, apply_tangents=False)
        except Exception as exc:
            return "fail", f"finish: {exc}"
        if not out.is_file():
            return "fail", "finish produced no file"
        reason = verify(out, before)
        if reason:
            return "fail", reason
        # Atomic replace, and deliberately NOT a write-through of the existing
        # inode: the example trees hardlink these files, so writing in place
        # would mutate assets under a running game. `os.replace` swaps the
        # directory entry instead, leaving any existing hardlink on the old
        # content until its holder re-syncs.
        staged = path.with_name(path.name + ".new")
        shutil.copy2(out, staged)
        os.replace(staged, path)
        return "ok", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", type=Path, required=True, help="shared-assets `public` dir")
    parser.add_argument("--limit", type=int, default=0, help="convert at most N assets")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args()

    meshes = args.pool / "assets" / "meshes"
    if not meshes.is_dir():
        print(f"no meshes under {meshes}", file=sys.stderr)
        return 2

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "Text3D" / "src"))
    from text3d.utils.gltf_finish import gltf_transform_finish

    targets = sorted(meshes.rglob("*.glb"))
    counts: dict[str, int] = {}
    failures: list[tuple[Path, str]] = []
    converted = 0
    before_bytes = after_bytes = 0

    for path in targets:
        if args.limit and converted >= args.limit:
            break
        size_before = path.stat().st_size
        status, detail = convert(path, gltf_transform_finish, args.dry_run)
        counts[status] = counts.get(status, 0) + 1
        if status in {"ok", "would-convert"}:
            converted += 1
            before_bytes += size_before
            after_bytes += path.stat().st_size
            print(f"[{converted:4d}] {status:13s} {path.name}  {detail}", flush=True)
        elif status == "fail":
            failures.append((path, detail))
            print(f"       FAIL          {path.name}  {detail}", file=sys.stderr, flush=True)

    print("\n--- resumo ---")
    for status, count in sorted(counts.items()):
        print(f"  {status:13s} {count}")
    if before_bytes:
        delta = 100 * (after_bytes - before_bytes) / before_bytes
        print(f"  tamanho       {before_bytes / 1048576:.0f} -> {after_bytes / 1048576:.0f} MB ({delta:+.0f}%)")
    if failures:
        print(f"\n{len(failures)} ficheiro(s) mantidos intactos por falharem a verificação:")
        for path, reason in failures:
            print(f"  {path.name}: {reason}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
