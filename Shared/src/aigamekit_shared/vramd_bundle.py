"""Bundle de código + locks para sessões vramd.

O servidor não tem o AiGameKit instalado. No início de um batch, este módulo
junta ``Shared/src`` e ``<Tool>/src`` (código puro, sem venvs), gera um lock
exacto por tool a partir do venv local e envia tudo como um blob
content-addressed. O ``session-open`` devolve um id; os jobs seguintes levam
``session`` e o servidor arranca o worker com ``PYTHONPATH`` para o bundle.

O bundle é montado no primeiro job de cada backend (wave) e reutilizado nos
jobs seguintes desse backend. Se o
servidor reiniciar (perde as sessões em memória), ``reset()`` força uma nova
abertura no próximo job.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import os
import re
import shutil
import subprocess
import tarfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import vramd_remote
from .monorepo import try_find_monorepo_root

SPECS_PATH = Path(__file__).resolve().parent / "data" / "backends.yaml"
_MAX_FILE_BYTES = 64 * 1024 * 1024
# O bundle viaja inteiro em memória (BytesIO) e é enviado como um blob. Um
# code_root mal configurado (ex.: a apontar para ~/Downloads) não pode
# materializar gigabytes no cliente antes de o servidor o recusar.
_MAX_TOTAL_BYTES = 256 * 1024 * 1024
_SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"}
# Pesos não são código: ficam no cache de modelos do servidor (HF/ensure_*).
_SKIP_SUFFIXES = {
    ".pyc",
    ".pyo",
    ".pth",
    ".pt",
    ".safetensors",
    ".ckpt",
    ".bin",
    ".onnx",
    ".engine",
    ".h5",
    ".pkl",
}

# Fallback quando o backends.yaml não declara `session:`. Mantém o bundle
# pequeno: só as tools que já correm no servidor.
_DEFAULT_SESSIONS: dict[str, dict[str, Any]] = {
    "text2d": {"env": "text2d", "tool": "text2d", "code_roots": ["Shared/src", "Text2D/src"]},
    "text3d": {
        "env": "text3d",
        "tool": "text3d",
        "code_roots": ["Shared/src", "Text2D/src", "Text3D/src"],
    },
    "paint3d": {"env": "paint3d", "tool": "paint3d", "code_roots": ["Shared/src", "Paint3D/src"]},
    "text2sound": {
        "env": "text2sound",
        "tool": "text2sound",
        "code_roots": ["Shared/src", "Text2Sound/src"],
    },
    "rigging3d": {
        "env": "rigging3d",
        "tool": "rigging3d",
        "code_roots": ["Shared/src", "Rigging3D/src"],
    },
}

# Pacotes sem uso no código remoto: spconv/cumm são wheels CUDA 12.1 (sem
# equivalente cu13) e restos do UniRig no venv local; pyrender/pyopengl são só
# visualização e o par do venv (0.1.45/3.1.10) é insatisfazível para o uv
# (pyrender exige pyopengl==3.1.0). Ficam fora do lock remoto.
_REMOTE_UNUSED_PACKAGES = {
    "spconv",
    "cumm",
    "pyrender",
    "pyopengl",
    # Wheels `+pt211` construídos localmente (sem build no índice do torch).
    "torch-cluster",
    "torch-scatter",
}

# CUDA 13 em todo o pipeline remoto. O Hi3D (refine) fica **local**: o seu
# stack é Python 3.10 + torch 2.5.0+cu124 (spconv-cu120/flash-attn), sem build
# cu13; não entra em bundles/sessões nem no servidor.
_CUDA12_MARKERS = ("-cu12", "+cu12")

_TOOL_DIRS = {
    "text2d": "Text2D",
    "text3d": "Text3D",
    "paint3d": "Paint3D",
    "text2sound": "Text2Sound",
    "rigging3d": "Rigging3D",
    "hi3d": "Hi3D",
    "texture2d": "Texture2D",
    "skymap2d": "Skymap2D",
    "part3d": "Part3D",
    "terrain3d": "Terrain3D",
}

_LOCK = threading.RLock()
_SESSION: dict[str, Any] | None = None


class BundleError(RuntimeError):
    """Falha ao montar/enviar o bundle de código."""


@dataclass(frozen=True)
class Bundle:
    sha256: str
    data: bytes
    files: list[dict[str, Any]]
    envs: dict[str, dict[str, Any]]
    backends: list[dict[str, Any]]

    def manifest(self) -> dict[str, Any]:
        return {
            "version": 1,
            "bundle": {"sha256": self.sha256, "size": len(self.data), "format": "tar.gz"},
            "files": self.files,
            "envs": self.envs,
            "backends": self.backends,
        }


def _session_block(entry: dict[str, Any], name: str) -> dict[str, Any] | None:
    raw = entry.get("session")
    if isinstance(raw, dict):
        roots = raw.get("code_roots") or ()
        return {
            "env": str(raw.get("env") or name),
            "tool": str(raw.get("tool") or name),
            "code_roots": [str(item) for item in roots],
        }
    return _DEFAULT_SESSIONS.get(name)


_BACKEND_ENTRIES_CACHE: tuple[str, int, int, dict[str, dict[str, Any]]] | None = None
_BACKEND_ENTRIES_LOCK = threading.Lock()


def _backend_entries() -> dict[str, dict[str, Any]]:
    global _BACKEND_ENTRIES_CACHE
    try:
        st = SPECS_PATH.stat()
    except OSError as exc:
        raise BundleError(f"não foi possível ler {SPECS_PATH.name}: {exc}") from exc
    key = (str(SPECS_PATH), st.st_mtime_ns, st.st_size)
    with _BACKEND_ENTRIES_LOCK:
        cached = _BACKEND_ENTRIES_CACHE
        if cached is not None and cached[:3] == key:
            return cached[3]
    try:
        data = yaml.safe_load(SPECS_PATH.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise BundleError(f"não foi possível ler {SPECS_PATH.name}: {exc}") from exc
    out: dict[str, dict[str, Any]] = {}
    for entry in data.get("backends") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        out[str(entry["name"])] = entry
    with _BACKEND_ENTRIES_LOCK:
        _BACKEND_ENTRIES_CACHE = (*key, out)
    return out


_FREEZE_CACHE: dict[tuple[str, str], tuple[tuple[int, int], tuple[str, str]]] = {}
_FREEZE_CACHE_LOCK = threading.Lock()


def _venv_signature(venv: Path) -> tuple[int, int] | None:
    """Assinatura ``(pyvenv_cfg_mtime_ns, site_packages_mtime_ns)`` do venv."""
    try:
        cfg = venv / "pyvenv.cfg"
        cfg_m = cfg.stat().st_mtime_ns if cfg.is_file() else 0
        sp_m = 0
        for candidate in (venv / "Lib" / "site-packages", *venv.glob("lib/python*/site-packages")):
            if candidate.is_dir():
                sp_m = max(sp_m, candidate.stat().st_mtime_ns)
        return (cfg_m, sp_m)
    except OSError:
        return None


def _freeze(tool_dir: str, root: Path) -> tuple[str, str]:
    """Devolve ``(python_version, lock_text)`` a partir do venv da tool."""
    venv = root / tool_dir / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise BundleError(f"venv da tool não encontrado: {venv} — instala {tool_dir} antes de usar GPU remota")
    cache_key = (tool_dir, str(python))
    sig = _venv_signature(venv)
    if sig is not None and sig != (0, 0):
        with _FREEZE_CACHE_LOCK:
            cached = _FREEZE_CACHE.get(cache_key)
            if cached is not None and cached[0] == sig:
                return cached[1]
    try:
        version = subprocess.run(
            [str(python), "-c", "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if version.returncode != 0:
            raise BundleError(f"não foi possível ler a versão de {python}: {version.stderr[-300:]}")
        python_version = version.stdout.strip()
        # Os venvs criados pelo `uv` podem não ter pip; `uv pip freeze` lê o
        # site-packages do interpretador indicado sem instalar nada.
        uv = shutil.which("uv")
        if uv:
            freeze_cmd = [uv, "pip", "freeze", "--python", str(python), "--exclude-editable"]
        else:
            freeze_cmd = [str(python), "-m", "pip", "freeze", "--exclude-editable"]
        frozen = subprocess.run(
            freeze_cmd,
            capture_output=True,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BundleError(f"falha ao gerar o freeze de {tool_dir}: {exc}") from exc
    if frozen.returncode != 0:
        raise BundleError(f"`freeze` falhou em {tool_dir}: {frozen.stderr[-500:]}")
    lines: list[str] = []
    cuda_tag: str | None = None
    dropped_cu12_torch = False
    root_text = str(root)
    local_names = {
        "aigamekit-shared",
        "aigamekit_shared",
        "aigamekit-lab",
        "shared",
        "gameassets",
        "animator3d",
        "motion3d",
        "rocks3d",
        *(_name.lower().replace("_", "-") for _name in _TOOL_DIRS),
    }
    for line in frozen.stdout.splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith("-e "):
            continue
        if " @ file://" in text or root_text in text:
            continue
        package = text.split("==", 1)[0].split("@", 1)[0].strip().lower().replace("_", "-")
        if package in local_names:
            # O código local vem do bundle via PYTHONPATH, não do PyPI.
            continue
        if package in _REMOTE_UNUSED_PACKAGES or package.startswith(("spconv-cu", "cumm-cu")):
            # Ver _REMOTE_UNUSED_PACKAGES (pesos/código não usam nenhum).
            continue
        if any(marker in text for marker in _CUDA12_MARKERS):
            # Só cu13 no pipeline remoto; ver o comentário em _DEFAULT_SESSIONS.
            if package == "torch" or package.startswith("torch-"):
                dropped_cu12_torch = True
            continue
        match = re.search(r"\+cu(\d{2,3})", text)
        if match and cuda_tag is None:
            cuda_tag = f"cu{match.group(1)}"
        lines.append(text)
    if dropped_cu12_torch and not any(line.split("==", 1)[0].strip().lower().startswith("torch") for line in lines):
        # Filtrar o torch cu12 sem alternativa deixava um lock sem torch: a
        # env remota era construída e o worker morria no import. Falhar aqui
        # diz o que fazer, em vez de rebentar 20 min depois no servidor.
        raise BundleError(
            f"o venv de {tool_dir} tem torch CUDA 12; o pipeline remoto só suporta "
            "CUDA 13 — reinstala a tool com torch cu13 ou marca os assets `gpu: local`"
        )
    if not lines:
        raise BundleError(f"freeze de {tool_dir} ficou vazio")
    header = ["--index-url https://pypi.org/simple"]
    if cuda_tag:
        header.append(f"--extra-index-url https://download.pytorch.org/whl/{cuda_tag}")
    result = (python_version, "\n".join([*header, *lines]) + "\n")
    if sig is not None and sig != (0, 0):
        with _FREEZE_CACHE_LOCK:
            _FREEZE_CACHE[cache_key] = (sig, result)
    return result


def _scan_dir(base: Path) -> Iterable[tuple[Path, int]]:
    """Percorre ``base`` em ordem determinística usando ``os.scandir``.

    Usa ``d_type`` do ``DirEntry`` para ignorar symlinks e diretórios saltados
    sem ``stat`` extra, fazendo um único ``lstat`` nos ficheiros elegíveis.
    """
    try:
        entries = sorted(os.scandir(base), key=lambda e: e.name)
    except OSError:
        return
    dirs: list[Path] = []
    for entry in entries:
        name = entry.name
        try:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if name not in _SKIP_DIRS:
                    dirs.append(Path(entry.path))
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            p = Path(entry.path)
            if p.suffix in _SKIP_SUFFIXES:
                continue
            size = entry.stat(follow_symlinks=False).st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES:
            continue
        yield p, size
    for sub in dirs:
        yield from _scan_dir(sub)


def _iter_files(root: Path, rel_roots: Iterable[str]) -> Iterable[tuple[str, Path, int]]:
    seen: set[str] = set()
    for rel_root in rel_roots:
        base = root / rel_root
        if not base.is_dir():
            raise BundleError(f"code_root não existe: {rel_root}")
        for path, size in _scan_dir(base):
            rel = path.relative_to(root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            yield rel, path, size


def build_bundle(backends: Iterable[str] | None = None) -> Bundle:
    """Monta o bundle (código + locks) para os backends pedidos."""
    root = try_find_monorepo_root()
    if root is None:
        raise BundleError("raiz do monorepo AiGameKit não encontrada")
    entries = _backend_entries()
    selected: list[str] = []
    for name in backends if backends is not None else entries:
        entry = entries.get(str(name))
        if entry is None:
            continue
        block = _session_block(entry, str(name))
        if block is None:
            continue
        tool_dir = _TOOL_DIRS.get(str(name))
        if tool_dir is None or not (root / tool_dir / "src").is_dir():
            continue
        selected.append(str(name))
    if not selected:
        raise BundleError("nenhum backend com sessão disponível no monorepo")

    envs: dict[str, dict[str, Any]] = {}
    extras: dict[str, bytes] = {}
    code_roots: list[str] = []
    backend_specs: list[dict[str, Any]] = []
    for name in sorted(set(selected)):
        entry = entries[name]
        block = _session_block(entry, name)
        assert block is not None
        env_name = str(block["env"])
        tool_dir = _TOOL_DIRS[name]
        if env_name not in envs:
            python_version, lock_text = _freeze(tool_dir, root)
            rel = f"session/envs/{env_name}.lock"
            extras[rel] = lock_text.encode()
            envs[env_name] = {"python": python_version, "lock": rel}
        code_roots.extend(block["code_roots"])
        remote_raw = entry.get("remote")
        remote: dict[str, Any] = remote_raw if isinstance(remote_raw, dict) else {}
        backend_specs.append(
            {
                "name": name,
                "tool": block["tool"],
                "env": env_name,
                "code_roots": list(block["code_roots"]),
                "vram_mib": int(entry.get("vram_mib") or 4096),
                "priority": int(entry.get("priority") or 0),
                "load_keys": list(entry.get("load_keys") or []),
                "shape_keys": list(entry.get("shape_keys") or []),
                "remote": dict(remote),
                "models": list(entry.get("models") or []),
            }
        )

    files: list[dict[str, Any]] = []
    payloads: dict[str, bytes] = {}
    total_bytes = 0
    for rel, path, size in _iter_files(root, sorted(set(code_roots))):
        total_bytes += size
        if total_bytes > _MAX_TOTAL_BYTES:
            # ``build_bundle`` materializa tudo em memória antes do blob; um
            # code_root errado não pode esgotar a RAM do cliente.
            raise BundleError(
                f"bundle excede {_MAX_TOTAL_BYTES // (1024 * 1024)} MiB de código "
                f"(chegou a {total_bytes // (1024 * 1024)} MiB em {rel})"
            )
        try:
            payloads[rel] = path.read_bytes()
        except OSError:
            total_bytes -= size
            continue
    payloads.update(extras)
    if sum(len(data) for data in payloads.values()) > _MAX_TOTAL_BYTES:
        raise BundleError(f"bundle excede {_MAX_TOTAL_BYTES // (1024 * 1024)} MiB (código + locks)")
    for rel in sorted(payloads):
        data = payloads[rel]
        files.append({"path": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})

    buffer = io.BytesIO()
    # gzip mtime=0 + metadados fixos no tar → o mesmo código dá o mesmo hash
    # (dedupe do bundle no CAS entre processos/batches).
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=1, mtime=0) as gz,
        tarfile.open(fileobj=gz, mode="w") as tf,
    ):
        for rel in sorted(payloads):
            data = payloads[rel]
            info = tarfile.TarInfo(rel)
            info.size = len(data)
            info.mode = 0o755 if rel.endswith(".py") and "/scripts/" in rel else 0o644
            info.mtime = 0
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            tf.addfile(info, io.BytesIO(data))
    data = buffer.getvalue()
    return Bundle(
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        files=files,
        envs=envs,
        backends=backend_specs,
    )


def ensure_session(backends: Iterable[str] | None = None) -> str | None:
    """Abre/reutiliza a sessão no servidor; ``None`` se sessões não se aplicam."""
    global _SESSION
    mode = os.environ.get("VRAMD_SESSION", "auto").strip().lower()
    if mode in ("off", "0", "false", "no"):
        return None
    requested = set(backends) if backends is not None else None
    with _LOCK:
        if _SESSION is not None:
            covered = set(_SESSION["backends"])
            if requested is None or requested <= covered:
                return str(_SESSION["session"])
        if not vramd_remote.remote_enabled():
            return None
        try:
            from vramd.transfer import TransferClient, TransferError
        except ImportError:
            # Sem o cliente vramd>=0.7 o staging remoto não abre sessão e o
            # servidor responde "backend desconhecido: <tool>" — um erro que
            # não aponta para a causa. Falha alto e diz o que instalar.
            raise BundleError(
                "cliente `vramd` não instalado neste venv (precisa vramd>=0.7): "
                "sem ele o staging remoto não abre sessão e o servidor responde "
                "'backend desconhecido'. Instala: pip install 'vramd>=0.7'."
            ) from None
        bundle = build_bundle(requested)
        client = TransferClient()
        try:
            try:
                hello = client.connect()
            except TransferError as exc:
                # Servidor antigo (0.6/0.7 sem frames/blobs): não é uma sessão
                # falhada — o staging legacy continua a funcionar.
                if "sem protocolo binário" in str(exc) and mode not in (
                    "on",
                    "1",
                    "true",
                    "yes",
                ):
                    return None
                raise
            if "sessions" not in set(hello.get("features") or ()):
                if mode in ("on", "1", "true", "yes"):
                    raise BundleError("o servidor vramd não anuncia suporte a sessões")
                return None
            client.put_blob_bytes(bundle.sha256, bundle.data)
            response = client.request(
                {"cmd": "session-open", "manifest": bundle.manifest()},
                timeout=float(os.environ.get("VRAMD_SESSION_OPEN_TIMEOUT_SEC", 86400)),
            )
        except TransferError as exc:
            raise BundleError(f"session-open falhou: {exc}") from exc
        finally:
            client.close()
        if response.get("status") != "ok":
            code = response.get("error_code")
            if code == "SESSION_DISABLED" and mode not in ("on", "1", "true", "yes"):
                return None
            raise BundleError(f"session-open recusado: {response.get('error') or response}")
        if not response.get("session"):
            raise BundleError(f"session-open sem id de sessão: {response}")
        previous = _SESSION
        new_session = str(response["session"])
        _SESSION = {
            "session": new_session,
            "bundle_sha": bundle.sha256,
            "backends": {item["name"] for item in bundle.backends},
        }
    # Fechar a sessão antiga FORA de ``_LOCK``: o RPC de rede não deve
    # bloquear outras threads a ler ``session_id()`` / ``ensure_session()``.
    if previous is not None and previous["session"] != new_session:
        old_client = TransferClient()
        try:
            old_client.connect()
            old_client.request({"cmd": "session-close", "session": previous["session"]})
        except Exception:
            pass
        finally:
            old_client.close()
    return new_session


def session_id() -> str | None:
    with _LOCK:
        return str(_SESSION["session"]) if _SESSION is not None else None


def reset() -> None:
    """Esquece a sessão local (o servidor reiniciou ou fechou-a)."""
    global _SESSION
    with _LOCK:
        _SESSION = None
