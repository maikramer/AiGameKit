"""Build/locate the vendored SprongMotion C runtime.

The runtime lives in ``src/motion3d/vendor/sprongmotion/`` (upstream v1.1.0,
Apache-2.0). It is compiled **out of tree** into
``~/.cache/aigamekit/sprongmotion/<version>-<source-hash>/`` so the checkout
stays clean and parallel checkouts never race on object files.

Only a C11 compiler is needed (``cc``); there is no Python/GPU dependency.

Examples:
    >>> path = ensure_runtime()          # doctest: +SKIP
    >>> path.name
    'libsprongmotion.so'
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor" / "sprongmotion"
_CACHE_BASE = Path(os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")) / "aigamekit" / "sprongmotion"

# Same flags as upstream ``make lib`` minus ``-Werror`` (a newer compiler must
# not break an install). ``-ffp-contract=off`` keeps results bit-identical to
# every other exact build — never add ``-ffp-contract=fast``.
_BASE_CFLAGS = (
    "-std=c11",
    "-O2",
    "-ffp-contract=off",
    "-fvisibility=hidden",
    "-DSM_SHARED",
    "-DSM_BUILD",
)
_WARNING_FLAGS = ("-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wshadow")


class RuntimeBuildError(RuntimeError):
    """The C runtime could not be built; the message says why and how to fix."""


def library_suffix() -> str:
    """Shared-library suffix for the current platform."""
    if sys.platform == "darwin":
        return ".dylib"
    if sys.platform.startswith("win"):
        return ".dll"
    return ".so"


def vendored_version() -> str:
    """``SM_VERSION`` from the vendored header (e.g. ``"1.1.0"``).

    Raises:
        RuntimeBuildError: header missing or malformed (broken checkout).
    """
    header = VENDOR_DIR / "include" / "sprongmotion.h"
    try:
        text = header.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeBuildError(
            f"SprongMotion runtime em falta: {header} ({exc}). "
            "O diretório vendor/sprongmotion/ deve estar no pacote (reinstala com ./install.sh motion3d)."
        ) from exc
    match = re.search(r'^#define SM_VERSION "([^"]+)"', text, flags=re.MULTILINE)
    if match is None:
        raise RuntimeBuildError(f"SM_VERSION não encontrado em {header}")
    return match.group(1)


def source_digest() -> str:
    """SHA-256 of the vendored sources (path + bytes), used as a build cache key."""
    paths = sorted(p for p in VENDOR_DIR.rglob("*") if p.suffix in {".c", ".h"})
    if not paths:
        raise RuntimeBuildError(f"sem fontes C em {VENDOR_DIR}")
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.relative_to(VENDOR_DIR).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_dir() -> Path:
    """Cache directory holding the library built from *this* source tree."""
    tag = f"{vendored_version()}-{source_digest()[:12]}-{sys.platform}"
    return _CACHE_BASE / tag


def library_path() -> Path:
    """Expected library path for the vendored sources (may not exist yet)."""
    return build_dir() / f"libsprongmotion{library_suffix()}"


def _compiler() -> str:
    return os.environ.get("SPRONG_CC", "").strip() or os.environ.get("CC", "").strip() or "cc"


def _sources() -> list[Path]:
    return sorted((VENDOR_DIR / "src").glob("*.c"))


def build_command(cc: str | None = None) -> list[str]:
    """Compile command (exposed for ``doctor`` and tests)."""
    compiler = cc or _compiler()
    if sys.platform == "darwin":
        link = ["-dynamiclib", "-install_name", "@rpath/libsprongmotion.dylib"]
    else:
        link = ["-shared"]
    extra = tuple(flag for flag in os.environ.get("SPRONG_CFLAGS", "").split() if flag)
    return [
        compiler,
        *_WARNING_FLAGS,
        *_BASE_CFLAGS,
        *extra,
        "-fPIC",
        "-I",
        str(VENDOR_DIR / "include"),
        "-pthread",
        *link,
        "-pthread",
        *(str(path) for path in _sources()),
        "-lm",
        "-o",  # replaced with the temp output below
    ]


def build_runtime(
    *,
    cc: str | None = None,
    rebuild: bool = False,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Compile the vendored runtime into the cache and return the library path.

    Args:
        cc: Compiler override (default: ``SPRONG_CC`` → ``CC`` → ``cc``).
        rebuild: Rebuild even when the cached library already exists.
        on_status: Called with a human-readable progress line.

    Returns:
        Path to ``libsprongmotion.{so,dylib}``.

    Raises:
        RuntimeBuildError: No C compiler, or the compile failed (stderr included).
    """
    target = library_path()
    if target.is_file() and not rebuild:
        return target

    compiler = cc or _compiler()
    executable = shutil.which(compiler)
    if executable is None:
        raise RuntimeBuildError(
            f"compilador C não encontrado: {compiler!r}. "
            "Instala um toolchain C11 (ex.: `apt install build-essential` ou `xcode-select --install`) "
            "ou aponta SPRONG_CC para o compilador."
        )
    if not _sources():
        raise RuntimeBuildError(f"sem fontes C em {VENDOR_DIR / 'src'}")

    target.parent.mkdir(parents=True, exist_ok=True)
    command = build_command(executable)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix="build-") as tmp:
        tmp_lib = Path(tmp) / target.name
        full = [*command, str(tmp_lib)]
        if on_status is not None:
            on_status(f"A compilar SprongMotion runtime ({len(_sources())} ficheiros) com {executable}")
        proc = subprocess.run(full, capture_output=True, text=True, check=False)
        if proc.returncode != 0 or not tmp_lib.is_file():
            detail = (proc.stderr or proc.stdout or "").strip()
            raise RuntimeBuildError(
                "falha a compilar o runtime SprongMotion. stderr:\n" + (detail[-2000:] or "(vazio)")
            )
        os.replace(tmp_lib, target)
    if on_status is not None:
        on_status(f"Runtime pronto: {target}")
    return target


def ensure_runtime(
    *,
    rebuild: bool = False,
    on_status: Callable[[str], None] | None = None,
) -> Path:
    """Return the shared library, building it on first use.

    ``SPRONG_LIB`` short-circuits everything (distro package / custom build).
    """
    override = os.environ.get("SPRONG_LIB", "").strip()
    if override:
        path = Path(override).expanduser()
        if not path.is_file():
            raise RuntimeBuildError(f"SPRONG_LIB aponta para um ficheiro inexistente: {path}")
        return path
    return build_runtime(rebuild=rebuild, on_status=on_status)


def runtime_info() -> dict[str, str]:
    """Diagnostics for ``motion3d doctor`` (never raises on a broken checkout)."""
    info = {
        "vendor_dir": str(VENDOR_DIR),
        "compiler": _compiler(),
        "platform": f"{platform.system().lower()}/{platform.machine()}",
    }
    try:
        info["version"] = vendored_version()
        info["sources"] = str(len(_sources()))
        info["digest"] = source_digest()[:12]
        info["build_dir"] = str(build_dir())
    except RuntimeBuildError as exc:
        info["error"] = str(exc)
    override = os.environ.get("SPRONG_LIB", "").strip()
    if override:
        info["lib"] = override
        info["lib_state"] = "ok" if Path(override).expanduser().is_file() else "missing"
    else:
        try:
            lib = library_path()
            info["lib"] = str(lib)
            info["lib_state"] = "ok" if lib.is_file() else "not built"
        except RuntimeBuildError:
            info["lib_state"] = "missing"
    return info


if __name__ == "__main__":  # pragma: no cover - CLI helper for installers
    print(ensure_runtime(on_status=print))
