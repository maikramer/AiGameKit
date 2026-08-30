"""Download e cache de packs de animação do itch.io (fluxo "name-your-own-price"
com mínimo USD 0 — **sem login**).

Packs registados (:data:`_PACK_LOCKFILES` — as chaves são a API pública):

- ``quaternius``  — UAL1 [Standard] do Quaternius: 43 animações (locomoção,
  jump, combate, pistola, feitiço, hit/death, natação, social...). Rig UE5
  mannequin. Servido como GLB único com todas as actions.
- ``quaternius2`` — UAL2 [Standard] do Quaternius: 43 animações complementares
  (farming, chopping, combos de espada, shield, zombie, climb, slide, ninja...).
  Rig idêntico ao da UAL1. GLB único.
- ``villager``    — Human Villager Animations FREE do Kevin Iglesias (kevdev):
  animações de trabalho (farming, pesca, gathering, hammering, mining) em FBX
  individuais (versões Male/Female; o retarget por-ficheiro vive no Animator3D,
  perfis ``villager``/``villager-f``). Licença EULA do autor (não é CC0).

O itch.io não expõe um hotlink estável: o URL real é um *signed* Cloudflare R2
que expira em 60s. Este módulo reproduz o fluxo em dois passos:

1. ``POST <itch_base>/<slug>/file/<upload_id>`` → JSON com ``{url: <signed R2 url>}``
2. ``GET <signed_url>`` → ``.zip``

Os IDs estáveis (``game_id``, ``upload_id``, ``itch_base``) e o ``sha256``
esperado vivem em :data/quaternius.lock.json, :data/quaternius2.lock.json e
:data/villager.lock.json. Isto permite re-fetch determinístico e verificação de
integridade, sem prompts interativos. Tiers pagos (Pro/Source, Asset Store)
ficam fora deste fluxo anónimo.

O cache fica **fora do git**, em ``$XDG_CACHE_HOME/aigamekit/`` (fallback
``~/.cache/aigamekit``), subdir por pack (``cache_dir`` no lockfile; packs
Quaternius mantêm ``quaternius/`` por compat), e é idempotente: se o zip já
estiver presente e o ``sha256`` bater, nenhuma transferência é feita.

Env vars:
    QUATERNIUS_PACK_URL / VILLAGER_PACK_URL: override do slug itch (raro).
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
from functools import cache
from pathlib import Path
from typing import Any, cast

_DATA_DIR = Path(__file__).resolve().parent / "data"

# Pack key -> lockfile. As chaves são a API pública (``fetch_itch_pack(pack=...)``).
_PACK_LOCKFILES: dict[str, str] = {
    "quaternius": "quaternius.lock.json",
    "quaternius2": "quaternius2.lock.json",
    "villager": "villager.lock.json",
}
DEFAULT_PACK = "quaternius"
# Packs Quaternius (GLB único com todas as actions; API ``fetch_quaternius_pack``).
_QUATERNIUS_PACKS = frozenset({"quaternius", "quaternius2"})

_ITCH_BASE = "https://quaternius.itch.io"  # default; lockfiles trazem ``itch_base``
# itch.io exige headers de browser para não devolver 403/empty no endpoint de file.
_DEFAULT_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36"


@dataclass(frozen=True)
class QuaterniusPack:
    """Referência resolvida para o pack Quaternius já em cache (paths prontos)."""

    root: Path
    glb: Path
    fbx: Path
    glb_root_motion: Path
    version: str
    pack: str = DEFAULT_PACK


@dataclass(frozen=True)
class ItchPack:
    """Pack itch.io genérico em cache: diretório extraído + metadados.

    ``root`` é a pasta do conteúdo (para packs Quaternius, o dir interno com o
    GLB/FBX; para packs por-ficheiro como ``villager``, a raiz do zip extraído).
    """

    root: Path
    version: str
    pack: str


def pack_names() -> tuple[str, ...]:
    """Chaves de pack suportadas (ex.: para validação de opções CLI)."""
    return tuple(sorted(_PACK_LOCKFILES))


def quaternius_pack_names() -> tuple[str, ...]:
    """Chaves dos packs Quaternius (GLB único; ``fetch_quaternius_pack``)."""
    return tuple(p for p in pack_names() if p in _QUATERNIUS_PACKS)


@cache
def _lock_data(pack: str = DEFAULT_PACK) -> dict[str, Any]:
    """Lê e cacheia o lockfile JSON do pack pedido.

    Raises:
        ValueError: chave de pack desconhecida.
    """
    fname = _PACK_LOCKFILES.get(pack)
    if fname is None:
        raise ValueError(f"Pack itch desconhecido: {pack!r} (disponíveis: {', '.join(pack_names())})")
    with (_DATA_DIR / fname).open("r", encoding="utf-8") as f:
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
    """Diretório raiz do cache do Quaternius (``<cache>/quaternius``) — compat."""
    return cache_root("quaternius")


def cache_root(pack: str = DEFAULT_PACK) -> Path:
    """Diretório de cache do pack (``<cache>/<cache_dir do lockfile>``).

    Packs Quaternius mantêm ``<cache>/quaternius`` (compat com caches antigos);
    novos lockfiles trazem o seu ``cache_dir`` (ex.: ``itch/villager``).
    """
    subdir = str(_lock_data(pack).get("cache_dir") or "quaternius")
    return cache_dir() / subdir


def is_pack_cached(*, verify: bool = True, pack: str = DEFAULT_PACK) -> bool:
    """True se o zip do pack já está presente (e, se ``verify``, com ``sha256`` válido)."""
    lock = _lock_data(pack)
    zip_path = cache_root(pack) / lock["upload_filename"]
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


def _resolve_signed_download_url(lock: dict, pack: str = DEFAULT_PACK) -> str:
    """POST ao itch.io para obter o URL R2 assinado (válido ~60s).

    Raises:
        RuntimeError: o itch.io não devolveu ``url`` (upload offline / bloqueado).
    """
    base = str(lock.get("itch_base") or _ITCH_BASE)
    slug_env = os.environ.get(f"{pack.upper()}_PACK_URL")
    slug = slug_env or f"{base}/{lock['game_slug']}"
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
            f"(resposta: {str(payload)[:200]}). Descarrega manualmente em {base}/{lock['game_slug']}."
        )
    return str(signed)


def _download(url: str, dest: Path, *, referer_base: str = _ITCH_BASE, expected_sha256: str | None = None) -> None:
    """Descarrega ``url`` para ``dest`` com verificação opcional de integridade."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(
        url,
        headers={"User-Agent": _DEFAULT_UA, "Referer": f"{referer_base}/"},
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
            raise RuntimeError(f"sha256 mismatch no pack: esperado {expected_sha256}, obtido {actual}.")
    tmp.replace(dest)


def _extract(zip_path: Path, extract_root: Path, inner_dir: str, marker: str) -> Path:
    """Extrai o zip (idempotente) e devolve o caminho do diretório interno do pack.

    ``inner_dir`` vazio (zip sem pasta-raiz, ex. villager) extrai directamente em
    ``extract_root``. ``marker`` é o ficheiro que prova que a extracção ocorreu.
    """
    extract_root.mkdir(parents=True, exist_ok=True)
    inner: Path = extract_root / inner_dir if inner_dir else extract_root
    # Idempotente: só extrai se o ficheiro marcador ainda não existir.
    if not (inner / marker).is_file():
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extract_root)
    return inner


def fetch_itch_pack(
    *,
    force: bool = False,
    on_status: Callable[[str], None] | None = None,
    pack: str = DEFAULT_PACK,
) -> ItchPack:
    """Garante que um pack itch.io está em cache; descarrega+extrai se faltar.

    Não-interativo: nunca pede input. Idempotente quando ``force=False`` e o cache
    já tem o zip com ``sha256`` válido.

    Args:
        force: re-descarrega mesmo que o cache pareça válido.
        on_status: callback opcional ``(msg: str) -> None`` (ex.: Logger.info).
        pack: chave em :data:`_PACK_LOCKFILES` (``quaternius``, ``quaternius2``,
            ``villager``).

    Returns:
        :class:`ItchPack` com o diretório extraído.

    Raises:
        ValueError: chave de pack desconhecida.
        RuntimeError: falha de rede, sha256 inválido, ou itch.io indisponível.
    """
    lock = _lock_data(pack)
    base = str(lock.get("itch_base") or _ITCH_BASE)
    root = cache_root(pack)
    zip_path = root / lock["upload_filename"]

    def _status(msg: str) -> None:
        if on_status:
            on_status(msg)

    if force or not is_pack_cached(pack=pack):
        _status(f"a descarregar {lock['name']} ({lock['expected_size'] // (1 << 20)} MB)...")
        signed = _resolve_signed_download_url(lock, pack)
        _download(signed, zip_path, referer_base=base, expected_sha256=lock["expected_sha256"])
        _status("download completo.")
    else:
        _status(f"pack {pack} já em cache.")

    files = dict(lock.get("files") or {})
    marker = str(files.get("glb") or lock.get("marker") or "")
    if not marker:
        raise ValueError(f"Lockfile do pack {pack!r} sem 'files.glb' nem 'marker' (idempotência da extracção).")
    inner = _extract(zip_path, root / "extracted", str(lock.get("inner_dir") or ""), marker)
    _status(f"pack pronto: {inner}")

    return ItchPack(root=inner, version=str(lock["version"]), pack=pack)


def fetch_quaternius_pack(
    *,
    force: bool = False,
    on_status: Callable[[str], None] | None = None,
    pack: str = DEFAULT_PACK,
) -> QuaterniusPack:
    """Garante que um pack Quaternius está em cache (GLB único com actions).

    Wrapper de :func:`fetch_itch_pack` para os packs ``quaternius``/``quaternius2``,
    mantendo a API antiga com os paths ``glb``/``fbx``/``glb_root_motion``.
    """
    if pack not in _QUATERNIUS_PACKS:
        raise ValueError(
            f"fetch_quaternius_pack é só para packs Quaternius {sorted(_QUATERNIUS_PACKS)}; "
            f"usa fetch_itch_pack(pack={pack!r})."
        )
    itch = fetch_itch_pack(force=force, on_status=on_status, pack=pack)
    files = _lock_data(pack)["files"]
    return QuaterniusPack(
        root=itch.root,
        glb=itch.root / files["glb"],
        fbx=itch.root / files["fbx"],
        glb_root_motion=itch.root / files["glb_root_motion"],
        version=itch.version,
        pack=pack,
    )


def get_glb_path(pack: str = DEFAULT_PACK) -> Path:
    """Conveniência: devolve o path do GLB (sem root-motion), garantindo o cache."""
    return fetch_quaternius_pack(pack=pack).glb
