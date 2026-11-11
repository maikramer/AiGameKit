"""Endpoint/transporte do vramd no cliente do monorepo.

O supervisor é um processo à parte e as tools não dependem do pacote ``vramd``
(elas falam com ele por socket). Este módulo é o **espelho mínimo** de
``vramd.transport`` do lado cliente:

* POSIX — Unix domain socket, como sempre.
* Windows — o endpoint é um descritor JSON (``127.0.0.1:<porta>`` + token),
  porque ``AF_UNIX`` tem limite de 108 bytes e nem todas as versões o expõem.

A detecção é pelo conteúdo do path: descritor JSON ⇒ TCP; socket ⇒ Unix. Assim
as tools falam com qualquer supervisor (POSIX ou Windows, Unix ou TCP) sem
configuração.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IS_WINDOWS = os.name == "nt"
TRANSPORT_UNIX = "unix"
TRANSPORT_TCP = "tcp"
ENV_TRANSPORT = "VRAMD_TRANSPORT"
DEFAULT_TCP_HOST = "127.0.0.1"
DESCRIPTOR_VERSION = 1


# ---------------------------------------------------------------------------
# Paths (Windows usa %LOCALAPPDATA%; POSIX mantém XDG/~/.cache)
# ---------------------------------------------------------------------------


def _local_appdata() -> Path | None:
    base = os.environ.get("LOCALAPPDATA", "").strip()
    return Path(base) if base else None


def aigamekit_cache_dir() -> Path:
    """Cache/logs das tools (``~/.cache/aigamekit`` ou ``%LOCALAPPDATA%\\aigamekit``)."""
    env = os.environ.get("VRAMD_CACHE_DIR", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    if IS_WINDOWS:
        base = _local_appdata()
        if base is not None:
            return base / "aigamekit"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "aigamekit"
    return Path.home() / ".cache" / "aigamekit"


def vramd_cache_dir() -> Path:
    """Cache do supervisor (``~/.cache/vramd`` ou ``%LOCALAPPDATA%\\vramd``)."""
    env = os.environ.get("VRAMD_CACHE_DIR", "").strip()
    if env:
        return Path(os.path.expanduser(env))
    if IS_WINDOWS:
        base = _local_appdata()
        if base is not None:
            return base / "vramd"
    xdg = os.environ.get("XDG_CACHE_HOME", "").strip()
    if xdg:
        return Path(xdg) / "vramd"
    return Path.home() / ".cache" / "vramd"


def default_vramd_socket() -> Path:
    """Endpoint canónico do supervisor — tem de coincidir com o do vramd."""
    return vramd_cache_dir() / "vramd.sock"


def autostart_log_path() -> Path:
    """Log do auto-start do supervisor."""
    override = os.environ.get("VRAMD_AUTO_START_LOG", "").strip()
    return Path(os.path.expanduser(override)) if override else vramd_cache_dir() / "vramd-autostart.log"


# ---------------------------------------------------------------------------
# Processos
# ---------------------------------------------------------------------------


def pid_exists(pid: int) -> bool:
    """True se ``pid`` está vivo.

    No Windows **nunca** ``os.kill(pid, 0)``: a stdlib chama
    ``TerminateProcess`` e mataria o supervisor.
    """
    if pid <= 0:
        return False
    try:
        import psutil

        return bool(psutil.pid_exists(int(pid)))
    except Exception:
        if IS_WINDOWS:
            return False
        try:
            os.kill(int(pid), 0)
        except OSError:
            return False
        return True


def detached_popen_kwargs() -> dict[str, Any]:
    """Kwargs para destacar o supervisor do processo chamador."""
    import subprocess

    if IS_WINDOWS:
        flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        flags |= int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return {"creationflags": flags} if flags else {}
    return {"start_new_session": True}


# ---------------------------------------------------------------------------
# Descritor e ligação
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EndpointInfo:
    """Como chegar ao servidor (e com que credencial)."""

    transport: str
    host: str = ""
    port: int | None = None
    token: str | None = None
    version: int = DESCRIPTOR_VERSION


def has_unix_sockets() -> bool:
    return hasattr(socket, "AF_UNIX")


def read_endpoint(path: Path) -> EndpointInfo | None:
    """Lê um descritor TCP; ``None`` se o path não for um descritor válido."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or data.get("transport") != TRANSPORT_TCP:
        return None
    host = str(data.get("host") or DEFAULT_TCP_HOST).strip() or DEFAULT_TCP_HOST
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        return None
    if not (1 <= port <= 65535):
        return None
    token = data.get("token")
    if token is not None and not isinstance(token, str):
        return None
    return EndpointInfo(transport=TRANSPORT_TCP, host=host, port=port, token=token)


def connect(path: Path, *, timeout: float | None = None) -> tuple[socket.socket, str | None]:
    """Liga ao endpoint; devolve ``(socket, token)``.

    Descritor JSON ⇒ TCP (com token); caso contrário ⇒ Unix socket quando
    disponível. O fallback Unix cobre um supervisor antigo/servidor per-tool.
    """
    info = read_endpoint(path)
    if info is not None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if timeout is not None:
                sock.settimeout(timeout)
            sock.connect((info.host, int(info.port or 0)))
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            return sock, info.token
        except BaseException:
            sock.close()
            raise
    if has_unix_sockets():
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            if timeout is not None:
                sock.settimeout(timeout)
            sock.connect(str(path))
            return sock, None
        except BaseException:
            sock.close()
            raise
    raise FileNotFoundError(f"endpoint inválido: {path}")


def parse_host_port(text: str) -> tuple[str, int]:
    """``HOST:PORT`` → ``(host, port)``; aceita ``[::1]:8790``.

    Raises:
        ValueError: formato inválido ou porta fora de 1..65535.
    """
    spec = text.strip()
    if not spec:
        raise ValueError("endpoint vazio")
    if spec.startswith("["):
        host_text, _, rest = spec[1:].partition("]")
        port_text = rest.lstrip(":")
    elif spec.count(":") == 1:
        host_text, _, port_text = spec.partition(":")
    else:
        raise ValueError(f"esperado HOST:PORT, recebi {text!r}")
    if not host_text:
        raise ValueError(f"endpoint sem host: {text!r}")
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"porta inválida em {text!r}") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"porta fora de 1..65535 em {text!r}")
    return host_text, port


def remote_endpoints() -> list[EndpointInfo]:
    """Endpoints remotos de ``VRAMD_REMOTE`` (CSV) e token de ``VRAMD_REMOTE_TOKEN``.

    Vazio ⇒ modo local (descritor/socket). Um endpoint inválido é ignorado com
    aviso, para um typo na lista não impedir os restantes.
    """
    raw = os.environ.get("VRAMD_REMOTE", "").strip()
    if not raw:
        return []
    token = os.environ.get("VRAMD_REMOTE_TOKEN", "").strip() or None
    endpoints: list[EndpointInfo] = []
    for item in raw.split(","):
        if not item.strip():
            continue
        try:
            host_text, port = parse_host_port(item)
        except ValueError:
            continue
        endpoints.append(EndpointInfo(transport=TRANSPORT_TCP, host=host_text, port=port, token=token))
    return endpoints


def connect_remote(info: EndpointInfo, *, timeout: float | None = None) -> tuple[socket.socket, str | None]:
    """Liga a um endpoint remoto explícito (``VRAMD_REMOTE``)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if timeout is not None:
            sock.settimeout(timeout)
        sock.connect((info.host, int(info.port or 0)))
        with contextlib.suppress(OSError):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        return sock, info.token
    except BaseException:
        sock.close()
        raise


def _write_descriptor(path: Path, *, port: int, token: str) -> None:
    payload = {
        "version": DESCRIPTOR_VERSION,
        "transport": TRANSPORT_TCP,
        "host": DEFAULT_TCP_HOST,
        "port": int(port),
        "token": token,
        "pid": os.getpid(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    text = json.dumps(payload, separators=(",", ":")) + "\n"
    tmp.write_text(text, encoding="utf-8")
    # No Windows um cliente a ler o descritor (ou o AV) pode impedir o replace
    # durante alguns ms. Tentar; em último recurso escrever no destino.
    for attempt in range(8):
        try:
            os.replace(tmp, path)
            break
        except OSError:
            if attempt == 7:
                path.write_text(text, encoding="utf-8")
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
                break
            time.sleep(0.02)
    if not IS_WINDOWS:
        with contextlib.suppress(OSError):
            os.chmod(path, 0o600)


def listen(path: Path) -> tuple[socket.socket, EndpointInfo]:
    """Abre o endpoint de um servidor per-tool (legacy).

    POSIX: Unix socket (ou TCP se ``VRAMD_TRANSPORT=tcp``). Windows: TCP+token.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = os.environ.get(ENV_TRANSPORT, "").strip().lower()
    use_unix = (mode != TRANSPORT_TCP) and (not IS_WINDOWS) and has_unix_sockets()
    if use_unix:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            sock.settimeout(1.0)
            sock.bind(str(path))
            sock.listen(8)
        except OSError:
            sock.close()
            raise
        return sock, EndpointInfo(transport=TRANSPORT_UNIX)

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            with contextlib.suppress(OSError):
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        sock.bind((DEFAULT_TCP_HOST, 0))
        sock.listen(8)
        sock.settimeout(1.0)
        port = int(sock.getsockname()[1])
        token = secrets.token_urlsafe(32)
        _write_descriptor(path, port=port, token=token)
    except OSError:
        sock.close()
        raise
    return sock, EndpointInfo(transport=TRANSPORT_TCP, host=DEFAULT_TCP_HOST, port=port, token=token)


def remove_endpoint(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def with_auth(request: dict[str, Any], token: str | None) -> dict[str, Any]:
    """Cópia do request com o token do endpoint (modo TCP)."""
    if not token:
        return request
    return {**request, "auth": token}


def is_authorized(request: dict[str, Any], token: str | None) -> bool:
    """True se o request traz o token correcto (ou não há token)."""
    if not token:
        return True
    supplied = request.get("auth")
    if not isinstance(supplied, str):
        return False
    return secrets.compare_digest(supplied, token)


def endpoint_label(path: Path) -> str:
    info = read_endpoint(path)
    if info is not None:
        return f"tcp://{info.host}:{info.port}"
    return str(path)
