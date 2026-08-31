#!/usr/bin/env python3
"""Mirror assets referenced by migrated Viber worlds from the shared-assets pool.

Scans every ``*.xml`` under ``--world`` (the migrated tree, e.g.
``Viber/examples/simple-rpg/``) for ``url="..."`` attributes (also
``model-url``, ``texture-url``, ...), and mirrors each asset from the
VibeGame shared-assets pool into the world's asset root:

    <path>  (e.g. ``/assets/meshes/hero.glb``)
      fonte:   ``<pool>/<path sem / inicial>``
      destino: ``<world>/<path>``  (``/assets/x`` -> ``<world>/assets/x``)

Viber resolves ``/``-paths against the asset root — the folder that CONTAINS
``assets/`` (the world dir itself when it has one, like the pool layout), so
the mirrored tree matches what ``GltfScene url`` expects.

Handling per extension:
* ``.glb``  — if the GLB JSON chunk lists ``EXT_meshopt_compression`` in
  ``extensionsUsed``, decompress with ``npx @gltf-transform/cli copy`` (the
  CLI decodes meshopt while reading); otherwise hardlink (fallback: copy).
* ``.png`` / ``.jpg`` / ``.jpeg`` / ``.webp`` — hardlink (fallback: copy).
* other extensions (audio, heightmaps, ...) — ignored for now.

Idempotent: a destination is skipped when it already exists with the same
size as the source (hardlinks) or simply exists (decompressed GLBs have no
size relation to their source, so existence is the marker).

    python3 Viber/scripts/sync_assets.py \
        --world Viber/examples/simple-rpg \
        --pool VibeGame/examples/shared-assets/public [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
LINK_EXTS = IMAGE_EXTS | {".ogg"}  # áudio: hardlink (fallback copy)
GLB_MAGIC = b"glTF"
GLB_CHUNK_JSON = 0x4E4F534A  # "JSON"

# Any attribute whose name ends in ``url`` (url, model-url, texture-url, ...).
_URL_RE = re.compile(r"[\w-]*url\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
# <Vegetation meshes="url1 url2 …"> — space-separated multi-url attribute
_MESHES_RE = re.compile(r"\bmeshes\s*=\s*\"([^\"]+)\"", re.IGNORECASE)
# <MusicLayer sound="bgm-explore"> → /assets/audio/bgm/explore.ogg (convenção)
_SOUND_RE = re.compile(r"\bsound\s*=\s*\"bgm-([a-z0-9-]+)\"", re.IGNORECASE)
# <Terrain heightmap="/assets/terrain/terrain.ahgt"> e afins
_HEIGHTMAP_RE = re.compile(r"\bheightmap\s*=\s*\"([^\"]+)\"", re.IGNORECASE)


# ---------------------------------------------------------------------------
# GLB inspection (stdlib: struct + json on the JSON chunk)
# ---------------------------------------------------------------------------
def glb_json_chunk(data: bytes) -> dict | None:
    """Parse the JSON chunk of a GLB container; None when not a valid GLB."""
    if len(data) < 20 or data[:4] != GLB_MAGIC:
        return None
    chunk_len = struct.unpack_from("<I", data, 12)[0]
    chunk_type = struct.unpack_from("<I", data, 16)[0]
    if chunk_type != GLB_CHUNK_JSON or chunk_len > len(data) - 20:
        return None
    try:
        doc = json.loads(data[20 : 20 + chunk_len].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def glb_has_meshopt(data: bytes) -> bool:
    """True when the GLB declares EXT_meshopt_compression in extensionsUsed."""
    doc = glb_json_chunk(data)
    if doc is None:
        return False
    return "EXT_meshopt_compression" in (doc.get("extensionsUsed") or [])


def glb_extensions_used(data: bytes) -> set[str]:
    """``extensionsUsed`` set from the GLB JSON chunk (empty on parse errors)."""
    doc = glb_json_chunk(data)
    if doc is None:
        return set()
    return set(doc.get("extensionsUsed") or [])


# ---------------------------------------------------------------------------
# url extraction
# ---------------------------------------------------------------------------
def extract_urls(text: str) -> list[str]:
    """All ``...url="..."`` values plus space-separated ``meshes="…"`` lists."""
    urls = _URL_RE.findall(text)
    for listing in _MESHES_RE.findall(text):
        urls.extend(listing.split())
    for layer in _SOUND_RE.findall(text):
        urls.append(f"/assets/audio/bgm/{layer}.ogg")
    for hm in _HEIGHTMAP_RE.findall(text):
        urls.append(hm)
    return urls


def collect_urls(world_dir: Path) -> list[str]:
    """Deduplicated urls across every migrated XML, deterministic order."""
    urls: list[str] = []
    seen: set[str] = set()
    for path in sorted(world_dir.rglob("*.xml")):
        for url in extract_urls(path.read_text(encoding="utf-8")):
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# copy strategies
# ---------------------------------------------------------------------------
def hardlink_or_copy(src: Path, dst: Path) -> None:
    """Hardlink when the filesystem allows it, otherwise copy."""
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def decompress_with_npx(src: Path, dst: Path) -> None:
    """Decode a GLB bevy can't read natively via @gltf-transform/cli.

    Chained steps (bevy 0.19 rejects all three extension syntaxes):
    ``ktxdecompress`` for KTX2/BasisU textures (also strips meshopt),
    ``copy`` when only meshopt needs decoding, ``dequantize`` when
    ``KHR_mesh_quantization`` is required.
    """
    used = glb_extensions_used(src.read_bytes())
    steps: list[str] = []
    if "KHR_texture_basisu" in used:
        steps.append("ktxdecompress")
    elif "EXT_meshopt_compression" in used:
        steps.append("copy")
    if "KHR_mesh_quantization" in used:
        steps.append("dequantize")
    if not steps:
        raise RuntimeError("decompress called but GLB needs no decode steps")
    current = src
    for index, subcmd in enumerate(steps):
        out = dst if index == len(steps) - 1 else dst.with_name(dst.name + ".step")
        result = subprocess.run(
            ["npx", "--yes", "@gltf-transform/cli", subcmd, str(current), str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError(
                f"gltf-transform {subcmd} falhou: {detail[-1] if detail else result.returncode}"
            )
        current = out
    for leftover in dst.parent.glob(dst.name + ".step"):
        leftover.unlink(missing_ok=True)


def plan_for(src: Path) -> str | None:
    """``decompress`` | ``link`` | None (ignored extension), from the source file."""
    ext = src.suffix.lower()
    if ext == ".ahgt":
        return "link"
    if ext == ".glb":
        try:
            used = glb_extensions_used(src.read_bytes())
        except OSError:
            return None
        needs_decode = bool(
            used & {"EXT_meshopt_compression", "KHR_texture_basisu", "KHR_mesh_quantization"}
        )
        return "decompress" if needs_decode else "link"
    if ext in LINK_EXTS:
        return "link"
    return None


# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
@dataclass
class SyncStats:
    """Counters for one sync run (or dry-run)."""

    decompressed: int = 0
    linked: int = 0
    skipped: int = 0
    ignored: int = 0
    missing: int = 0
    errors: int = 0
    errors_detail: list[str] = field(default_factory=list)


def mirror_total_bytes(world_dir: Path) -> int:
    """Total size of the mirrored asset tree (0 when absent)."""
    if not world_dir.is_dir():
        return 0
    return sum(path.stat().st_size for path in world_dir.rglob("*") if path.is_file())


def sync_assets(
    world_dir: Path,
    pool: Path,
    dry_run: bool = False,
    decompress: Callable[[Path, Path], None] | None = None,
) -> SyncStats:
    """Mirror every referenced asset; deterministic order, idempotent."""
    decompress = decompress or decompress_with_npx
    stats = SyncStats()

    for url in collect_urls(world_dir):
        if not url.startswith("/"):
            stats.ignored += 1  # only asset-root absolute paths are mirrored
            continue
        rel = url.lstrip("/")
        src = pool / rel
        dst = world_dir / rel
        if not src.is_file():
            stats.missing += 1
            continue
        mode = plan_for(src)
        if mode is None:
            stats.ignored += 1
            continue
        # idempotency: equal-size destination (links) or existing destination
        # (decompressed GLBs have no size relation to the source) -> skip
        if dst.is_file() and (mode == "decompress" or dst.stat().st_size == src.stat().st_size):
            stats.skipped += 1
            continue
        if dry_run:
            if mode == "decompress":
                stats.decompressed += 1
            else:
                stats.linked += 1
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            if mode == "decompress":
                decompress(src, dst)
                stats.decompressed += 1
            else:
                hardlink_or_copy(src, dst)
                stats.linked += 1
        except Exception as exc:  # keep going; report at the end
            stats.errors += 1
            stats.errors_detail.append(f"{url}: {exc}")

    return stats


def print_summary(stats: SyncStats, world_dir: Path, dry_run: bool) -> None:
    total = stats.decompressed + stats.linked
    label = "sincronizados (dry-run)" if dry_run else "sincronizados"
    print(
        f"{total} ficheiros {label} "
        f"({stats.decompressed} decomprimidos, {stats.linked} ligados, "
        f"{stats.skipped} já presentes, {stats.ignored} ignorados)."
    )
    if stats.missing:
        print(f"Aviso: {stats.missing} url(s) sem fonte no pool.")
    for detail in stats.errors_detail:
        print(f"Erro: {detail}", file=sys.stderr)
    mb = mirror_total_bytes(world_dir) / (1024 * 1024)
    print(f"Espelho {world_dir / 'assets'}: {mb:.1f} MB")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Espelha assets referenciados pelos mundos Viber migrados a partir do pool partilhado.",
    )
    parser.add_argument("--world", default="Viber/examples/simple-rpg", help="diretório do mundo migrado")
    parser.add_argument("--pool", default="VibeGame/examples/shared-assets/public", help="raiz do pool de assets")
    parser.add_argument("--dry-run", action="store_true", help="não escreve nada; só reporta")
    args = parser.parse_args(argv)

    world_dir = Path(args.world)
    if not world_dir.is_dir():
        parser.error(f"--world não é um diretório: {args.world}")
    pool = Path(args.pool)
    if not pool.is_dir():
        parser.error(f"--pool não é um diretório: {args.pool}")

    stats = sync_assets(world_dir, pool, dry_run=args.dry_run)
    print_summary(stats, world_dir, args.dry_run)
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
