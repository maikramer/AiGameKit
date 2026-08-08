"""Download e cache do pack CC0 *Universal Animation Library* do Quaternius.

O pack (120+ animações humanoides, licença CC0) é servido pelo itch.io sob um
fluxo "name-your-own-price" com mínimo USD 0 — **sem login**. O itch.io não expõe
um hotlink estável: o URL real é um *signed* Cloudflare R2 que expira em 60s.
Este módulo reproduz o fluxo em dois passos:

1. ``POST /file/<upload_id>`` → JSON com ``{url: <signed R2 url>}``
2. ``GET <signed_url>`` → ``.zip``

Os IDs estáveis (``game_id``, ``upload_id``) e o ``sha256`` esperado vivem em
:data/quaternius.lock.json. Isto permite re-fetch determinístico e verificação de
integridade, sem prompts interativos.

O cache fica **fora do git**, em ``$XDG_CACHE_HOME/aigamekit/quaternius/`` (fallback
``~/.cache/...``), e é idempotente: se o zip já estiver presente e o ``sha256``
bater, nenhuma transferência é feita.

Env vars:
    QUATERNIUS_PACK_URL: override do slug itch (raramente necessário).
    VRAMD_CACHE_DIR: diretório base de cache (default: XDG/``~/.cache/aigamekit``).
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, cast

_DATA_DIR = Path(__file__).resolve().parent / "data"
_LOCKFILE = _DATA_DIR / "quaternius.lock.json"

_ITCH_BASE = "https://quaternius.itch.io"
# itch.io exige headers de browser para não devolver 403/empty no endpoint de file.
_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


@dataclass(frozen=True)
class QuaterniusPack:
    """Referência resolvida para o pack já em cache (paths prontos a usar)."""

    root: Path
    glb: Path
    fbx: Path
    glb_root_motion: Path
    version: str


@lru_cache(maxsize=1)
def _lock_data() -> dict[str, Any]:
    """Lê e cacheia o lockfile JSON do pack."""
    with _LOCKFILE.open("r", encoding="utf-8") as f:
        return cast(dict[str, Any], json.load(f))


def cache_dir() -> Path:
    """Diretório base de cache do AiGameKit (fora do git).

    Ordem de precedência: ``VRAMD_CACHE_DIR`` → ``XDG_CACHE_HOME/aigamekit`` →
    ``~/.cache/aigamekit``.
    """
    env = os.environ.get("VRAMD_CACHE_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "aigamekit"
    return Path.home() / ".cache" / "aigamekit"


def quaternius_cache_root() -> Path:
    """Diretório raiz do cache do Quaternius (``<cache>/quaternius``)."""
    return cache_dir() / "quaternius"


def is_pack_cached(*, verify: bool = True) -> bool:
    """True se o zip do pack já está presente (e, se ``verify``, com ``sha256`` válido)."""
    lock = _lock_data()
    zip_path = quaternius_cache_root() / lock["upload_filename"]
    if not zip_path.is_file():
        return False
    if not verify:
        return True
    return _sha256(zip_path) == str(lock["expected_sha256"])


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_signed_download_url(lock: dict) -> str:
    """POST ao itch.io para obter o URL R2 assinado (válido ~60s).

    Raises:
        RuntimeError: o itch.io não devolveu ``url`` (upload offline / bloqueado).
    """
    slug = os.environ.get("QUATERNIUS_PACK_URL") or f"{_ITCH_BASE}/{lock['game_slug']}"
    post_url = f"{slug}/file/{lock['upload_id']}?source=game_download&as_props=1"
    req = urllib.request.Request(
        post_url,
        method="POST",
        data=b"",
        headers={
            "User-Agent": _DEFAULT_UA,
            "Referer": f"{slug}/download",
            "X-Requested-With": "XMLHttpRequest",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    signed = payload.get("url")
    if not signed:
        raise RuntimeError(
            f"itch.io não devolveu 'url' assinado para o upload {lock['upload_id']} "
            f"(resposta: {str(payload)[:200]}). Descarrega manualmente em {slug}."
        )
    return str(signed)


def _download(url: str, dest: Path, *, expected_sha256: str | None = None) -> None:
    """Descarrega ``url`` para ``dest`` com verificação opcional de integridade."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _DEFAULT_UA, "Referer": f"{_ITCH_BASE}/"},
    )
    with urllib.request.urlopen(req, timeout=180) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
    if expected_sha256:
        actual = _sha256(tmp)
        if actual != expected_sha256:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"sha256 mismatch no pack Quaternius: esperado {expected_sha256}, obtido {actual}.")
    tmp.replace(dest)


def _extract(zip_path: Path, extract_root: Path) -> Path:
    """Extrai o zip (idempotente) e devolve o caminho do diretório interno do pack."""
    extract_root.mkdir(parents=True, exist_ok=True)
    lock = _lock_data()
    inner: Path = extract_root / str(lock["inner_dir"])
    # Idempotente: só extrai se o GLB esperado não existir.
    if not (inner / lock["files"]["glb"]).is_file():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
    return inner


def fetch_quaternius_pack(
    *,
    force: bool = False,
    on_status: Callable[[str], None] | None = None,
) -> QuaterniusPack:
    """Garante que o pack Quaternius está em cache; descarrega+extrai se faltar.

    Não-interativo: nunca pede input. Idempotente quando ``force=False`` e o cache
    já tem o zip com ``sha256`` válido.

    Args:
        force: re-descarrega mesmo que o cache pareça válido.
        on_status: callback opcional ``(msg: str) -> None`` (ex.: Logger.info).

    Returns:
        :class:`QuaterniusPack` com os caminhos absolutos já resolvidos.

    Raises:
        RuntimeError: falha de rede, sha256 inválido, ou itch.io indisponível.
    """
    lock = _lock_data()
    root = quaternius_cache_root()
    zip_path = root / lock["upload_filename"]

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if force or not is_pack_cached():
        _status(f"a descarregar {lock['name']} ({lock['expected_size'] // (1 << 20)} MB)...")
        signed = _resolve_signed_download_url(lock)
        _download(signed, zip_path, expected_sha256=lock["expected_sha256"])
        _status("download completo.")
    else:
        _status("pack Quaternius já em cache.")

    inner = _extract(zip_path, root / "extracted")
    _status(f"pack pronto: {inner}")

    files = lock["files"]
    return QuaterniusPack(
        root=inner,
        glb=inner / files["glb"],
        fbx=inner / files["fbx"],
        glb_root_motion=inner / files["glb_root_motion"],
        version=lock["version"],
    )


def get_glb_path() -> Path:
    """Conveniência: devolve o path do GLB (sem root-motion), garantindo o cache."""
    return fetch_quaternius_pack().glb
