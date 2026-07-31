"""Decode GLB extensions that bpy's glTF importer cannot read.

Blender's importer rejects GLBs whose ``extensionsRequired`` contain
unsupported entries. Two cases matter for the pipeline:

* ``KHR_texture_basisu`` (KTX2/BasisU textures) — never supported by bpy;
  decoded to PNG via ``@gltf-transform/cli ktxdecompress``.
* ``EXT_meshopt_compression`` — native import only on bpy 5.2+; older bpy
  needs ``@gltf-transform/cli copy`` (read decodes meshopt, write strips it).

``KHR_mesh_quantization`` imports fine natively and is left untouched.
"""

from __future__ import annotations

import json
import logging
import shutil
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)

EXT_BASISU = "KHR_texture_basisu"
EXT_MESHOPT = "EXT_meshopt_compression"

_GLTF_TRANSFORM_TIMEOUT_S = 300


def glb_extensions(path: str | Path) -> tuple[list[str], list[str]]:
    """Return ``(extensionsUsed, extensionsRequired)`` from a GLB header.

    Lightweight binary parse of the first (JSON) chunk — no bpy required.
    Returns ``([], [])`` for non-GLB files (e.g. ``.gltf`` text) or on
    parse errors.
    """
    p = Path(path)
    try:
        with p.open("rb") as fh:
            header = fh.read(12)
            if len(header) < 12 or header[:4] != b"glTF":
                return [], []
            chunk_header = fh.read(8)
            if len(chunk_header) < 8:
                return [], []
            chunk_len, chunk_type = struct.unpack("<I4s", chunk_header)
            if chunk_type != b"JSON":
                return [], []
            doc = json.loads(fh.read(chunk_len).decode("utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return [], []
    used = [str(e) for e in doc.get("extensionsUsed", [])]
    required = [str(e) for e in doc.get("extensionsRequired", [])]
    return used, required


def run_gltf_transform(subcmd: str, src: Path, dst: Path) -> tuple[bool, str]:
    """Run ``npx @gltf-transform/cli <subcmd> src dst``.

    Returns:
        ``(success, error_message)`` — error message empty on success.
    """
    if shutil.which("npx") is None:
        return False, "npx not on PATH (Node.js required for gltf-transform decode)"
    try:
        r = subprocess.run(
            ["npx", "--yes", "@gltf-transform/cli", subcmd, str(src), str(dst)],
            capture_output=True,
            text=True,
            timeout=_GLTF_TRANSFORM_TIMEOUT_S,
            check=False,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return False, f"gltf-transform {subcmd} failed: {exc}"
    if r.returncode != 0:
        return False, f"gltf-transform {subcmd} rc={r.returncode}: {(r.stderr or '')[-300:]}"
    if not dst.is_file():
        return False, f"gltf-transform {subcmd} produced no output"
    return True, ""


def bpy_decode_subcommand(path: str | Path) -> str | None:
    """Pick the ``@gltf-transform/cli`` subcommand needed before bpy import.

    Returns:
        ``"ktxdecompress"`` when KTX2/BasisU textures are present (also
        strips meshopt as a side effect), ``"copy"`` when only meshopt
        needs decoding on an older bpy, or ``None`` when the file imports
        natively.
    """
    used, required = glb_extensions(path)
    exts = set(used) | set(required)
    if EXT_BASISU in exts:
        return "ktxdecompress"
    if EXT_MESHOPT in exts:
        try:
            from aigamekit_shared.bpy_mesh import gltf_import_supports_meshopt

            if gltf_import_supports_meshopt():
                return None
        except ImportError:
            pass
        return "copy"
    return None


@contextmanager
def bpy_readable_glb(path: str | Path) -> Iterator[Path]:
    """Yield a path bpy's glTF importer can read (KTX2- and meshopt-aware).

    Decodes to a temp GLB via ``@gltf-transform/cli`` only when needed.
    Falls back to the original path (with a warning) when npx or the
    decode step is unavailable, so the importer's own error surfaces.
    """
    src = Path(path).expanduser().resolve()
    subcmd = bpy_decode_subcommand(src)
    if subcmd is None:
        yield src
        return

    with tempfile.NamedTemporaryFile(suffix=".glb", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        ok, err = run_gltf_transform(subcmd, src, tmp_path)
        if ok:
            log.info("gltf_decode: %s via gltf-transform %s (%s)", src.name, subcmd, tmp_path.name)
            yield tmp_path
        else:
            log.warning("gltf_decode: %s — importing original %s", err, src.name)
            yield src
    finally:
        tmp_path.unlink(missing_ok=True)
