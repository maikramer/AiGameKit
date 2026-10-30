"""SprongMotion-14M weights: HuggingFace download, cache and SHA-256 check.

Model: ``SegerEnd/SprongMotion-14M`` — one 33 MB GGUF file (Q8_0 generator,
F32 text encoder/decoder). Apache-2.0.

Cache: standard HuggingFace hub cache (same helper as Text3D:
:func:`aigamekit_shared.model_download.ensure_model`). Override the whole path
with ``SPRONG_MODEL`` (offline installs, CI fixtures).
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from pathlib import Path

HF_REPO = "SegerEnd/SprongMotion-14M"
MODEL_FILE = "SprongMotion-14M-v1.0-Q8_0.gguf"
MODEL_SHA256 = "68fbd311a0d7d75f60508b35254171583c85880e7e124140457b57ac84c5de9d"
MODEL_BYTES = 33_337_824
LICENSE = "Apache-2.0"
CACHE_HINT = "~/.cache/huggingface/hub/models--SegerEnd--SprongMotion-14M/"

StatusCallback = Callable[[str], None]


class ModelIntegrityError(RuntimeError):
    """The GGUF on disk is missing, truncated or does not match ``MODEL_SHA256``."""


def sha256_file(path: str | Path, *, chunk: int = 1 << 20) -> str:
    """Streaming SHA-256 of ``path`` (never loads the file into memory)."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


def _override() -> Path | None:
    raw = os.environ.get("SPRONG_MODEL", "").strip()
    return Path(raw).expanduser() if raw else None


def cached_model_path() -> Path | None:
    """Model path when already on disk (never touches the network).

    Uses ``hf_hub_download(local_files_only=True)``: the repo has a single file,
    so the snapshot-level check in ``is_model_cached`` would also demand every
    other file in the repo and report a complete cache as missing.
    """
    override = _override()
    if override is not None:
        return override if override.is_file() else None
    try:
        from huggingface_hub import hf_hub_download

        candidate = Path(hf_hub_download(HF_REPO, MODEL_FILE, local_files_only=True))
        return candidate if candidate.is_file() else None
    except Exception:
        return None


def verify_model(path: str | Path, *, expected_sha256: str = MODEL_SHA256) -> tuple[bool, str]:
    """Check size then SHA-256.

    Returns:
        ``(ok, message)`` — message names the problem when ``ok`` is false.
    """
    candidate = Path(path).expanduser()
    if not candidate.is_file():
        return False, f"modelo em falta: {candidate}"
    size = candidate.stat().st_size
    if size != MODEL_BYTES:
        return False, f"tamanho {size} != {MODEL_BYTES} bytes (download truncado?)"
    digest = sha256_file(candidate)
    if digest != expected_sha256:
        return False, f"sha256 {digest[:12]}… != {expected_sha256[:12]}… (ficheiro corrompido)"
    return True, "ok"


def ensure_model_path(
    *,
    force_download: bool = False,
    verify: bool = True,
    on_status: StatusCallback | None = None,
) -> Path:
    """Return a local GGUF path, downloading it from HuggingFace when missing.

    Args:
        force_download: Re-fetch even when a cached copy exists.
        verify: Check SHA-256 (default on; ~0.1 s for 33 MB).
        on_status: Progress callback (Rich console / logger).

    Raises:
        ModelIntegrityError: Download failed, truncated or hash mismatch.
    """
    from huggingface_hub import hf_hub_download

    override = _override()
    if override is not None and not force_download:
        if not override.is_file():
            raise ModelIntegrityError(f"SPRONG_MODEL aponta para um ficheiro inexistente: {override}")
        path = override
    else:
        if on_status is not None:
            on_status(f"{'A descarregar' if force_download else 'A obter'} {HF_REPO}/{MODEL_FILE}")
        try:
            raw = hf_hub_download(HF_REPO, MODEL_FILE, force_download=force_download)
        except Exception as exc:  # network/offline/auth — never leave a bare traceback
            raise ModelIntegrityError(
                f"não foi possível obter {HF_REPO}/{MODEL_FILE}: {exc}. "
                "Com rede, corre `motion3d download-model`; offline, aponta SPRONG_MODEL para um .gguf local."
            ) from exc
        path = Path(raw)
    if verify:
        ok, message = verify_model(path)
        if not ok:
            raise ModelIntegrityError(
                f"{message}. Apaga o ficheiro em cache ({path}) e corre `motion3d download-model`."
            )
    if on_status is not None:
        on_status(f"Modelo pronto: {path}")
    return path


def model_info() -> dict[str, str]:
    """Diagnostics for ``motion3d doctor`` (never raises)."""
    info = {"repo": HF_REPO, "file": MODEL_FILE, "license": LICENSE, "expected_sha256": MODEL_SHA256}
    path = cached_model_path()
    if path is None:
        info["path"] = CACHE_HINT
        info["state"] = "missing"
        return info
    info["path"] = str(path)
    ok, message = verify_model(path, expected_sha256=MODEL_SHA256)
    info["state"] = "ok" if ok else "corrupt"
    info["detail"] = message
    return info
