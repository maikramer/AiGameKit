"""Cliente e staging de um vramd remoto (GPUs na rede).

O daemon remoto só executa jobs e guarda blobs num sandbox por job. Este
módulo é o lado que conhece o contrato dos backends: sobe os inputs, reescreve
os paths do request para o sandbox, descarrega os outputs (e sidecars) para os
paths locais e limpa o sandbox.

Env:
    VRAMD_REMOTE=host:port[,host:port]   endereço(s) do daemon remoto
    VRAMD_REMOTE_TOKEN=...               token do descritor do servidor
    VRAMD_REMOTE_STRICT=1                erro em vez de fallback local quando cai
    VRAMD_KEEP_SANDBOX=1                 não limpar o sandbox (debug/retry)
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import vramd_endpoint as endpoint

CHUNK_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
SPECS_PATH = Path(__file__).resolve().parent / "data" / "backends.yaml"


class RemoteDown(RuntimeError):
    """Nenhum endpoint remoto respondeu."""


class RemoteStagingError(RuntimeError):
    """O contrato de ficheiros do backend não permitiu preparar/recolher o job."""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def remote_enabled() -> bool:
    return bool(endpoint.remote_endpoints())


def strict() -> bool:
    return os.environ.get("VRAMD_REMOTE_STRICT", "1").strip().lower() not in ("0", "false", "no", "off")


def keep_sandbox() -> bool:
    return os.environ.get("VRAMD_KEEP_SANDBOX", "").strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Descritores `remote:` do backends.yaml
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BackendRemoteSpec:
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ("output",)
    sidecars: tuple[str, ...] = ()
    sidecar_globs: tuple[str, ...] = ()


_SPECS: dict[str, BackendRemoteSpec] | None = None


def load_specs(path: Path | None = None) -> dict[str, BackendRemoteSpec]:
    """Lê os blocos ``remote:`` do ``backends.yaml`` (uma vez por processo)."""
    global _SPECS
    if _SPECS is not None and path is None:
        return _SPECS
    specs: dict[str, BackendRemoteSpec] = {}
    try:
        data = yaml.safe_load((path or SPECS_PATH).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        data = {}
    for entry in data.get("backends") or []:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        block = entry.get("remote")
        if not name or not isinstance(block, dict):
            continue
        specs[name] = BackendRemoteSpec(
            inputs=tuple(str(x) for x in block.get("inputs") or ()),
            outputs=tuple(str(x) for x in block.get("outputs") or ("output",)),
            sidecars=tuple(str(x) for x in block.get("sidecars") or ()),
            sidecar_globs=tuple(str(x) for x in block.get("sidecar_globs") or ()),
        )
    if path is None:
        _SPECS = specs
    return specs


def spec_for(backend: str) -> BackendRemoteSpec:
    spec = load_specs().get(backend)
    if spec is None:
        raise RemoteStagingError(
            f"backend {backend!r} sem bloco `remote:` em {SPECS_PATH.name}; "
            "não é possível transferir os ficheiros"
        )
    return spec


# ---------------------------------------------------------------------------
# Cliente RPC
# ---------------------------------------------------------------------------


class RemoteClient:
    """RPC JSONL contra o vramd remoto; fixa o endpoint do job no ``stage``."""

    def __init__(self, endpoints: list[endpoint.EndpointInfo] | None = None) -> None:
        self.endpoints = endpoints if endpoints is not None else endpoint.remote_endpoints()
        self._pinned: endpoint.EndpointInfo | None = None

    def _rpc(self, request: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        candidates = ([self._pinned] if self._pinned else []) + [
            info for info in self.endpoints if info is not self._pinned
        ]
        last_error: Exception | None = None
        for info in candidates:
            try:
                sock, token = endpoint.connect_remote(info, timeout=timeout)
            except OSError as exc:
                last_error = exc
                continue
            data = b""
            try:
                sock.sendall((json.dumps(endpoint.with_auth(request, token)) + "\n").encode())
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    data += chunk
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise RemoteStagingError("resposta do vramd remoto excedeu o teto")
            except OSError as exc:
                last_error = exc
                continue
            finally:
                sock.close()
            lines = data.decode("utf-8", errors="replace").strip().split("\n")
            if not lines or not lines[-1]:
                last_error = RemoteDown("resposta vazia do vramd remoto")
                continue
            try:
                response = json.loads(lines[-1])
            except json.JSONDecodeError as exc:
                last_error = exc
                continue
            if not isinstance(response, dict):
                last_error = RemoteDown("resposta do vramd remoto não é um objecto JSON")
                continue
            self._pinned = info
            return response
        raise RemoteDown(f"vramd remoto inacessível ({self.endpoints}): {last_error}")

    def is_up(self, *, timeout: float = 3.0) -> bool:
        try:
            self.rpc({"cmd": "status"}, timeout=timeout)
            return True
        except (RemoteDown, RemoteStagingError, OSError):
            return False

    def rpc(self, request: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        """Envia um pedido ao daemon remoto (fixa o endpoint na primeira resposta)."""
        return self._rpc(request, timeout=timeout)

    def stage(self) -> tuple[str, str]:
        response = self._rpc({"cmd": "stage"}, timeout=30.0)
        if response.get("status") != "ok" or not response.get("job"):
            raise RemoteStagingError(f"stage falhou: {response.get('error') or response}")
        return str(response["job"]), str(response.get("root") or "")

    def put_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
        size = local.stat().st_size
        sha = _sha256(local)
        if size == 0:
            response = self._rpc(
                {"cmd": "put", "job": job, "path": rel, "offset": 0, "data": "", "eof": True, "sha256": sha},
                timeout=timeout,
            )
            if response.get("status") != "ok":
                raise RemoteStagingError(f"put {rel} falhou: {response.get('error') or response}")
            return 0
        offset = 0
        with local.open("rb") as handle:
            while offset < size:
                chunk = handle.read(min(CHUNK_BYTES, size - offset))
                last = offset + len(chunk) >= size
                response = self._rpc(
                    {
                        "cmd": "put",
                        "job": job,
                        "path": rel,
                        "offset": offset,
                        "data": base64.b64encode(chunk).decode("ascii"),
                        "eof": last,
                        "sha256": sha if last else None,
                    },
                    timeout=timeout,
                )
                if response.get("status") != "ok":
                    raise RemoteStagingError(f"put {rel} falhou: {response.get('error') or response}")
                offset += len(chunk)
        if offset != size:
            raise RemoteStagingError(f"upload de {local} incompleto ({offset}/{size} bytes)")
        return size

    def get_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
        local.parent.mkdir(parents=True, exist_ok=True)
        temporary = local.with_name(local.name + ".part")
        offset = 0
        size = 0
        try:
            with temporary.open("wb") as handle:
                while True:
                    response = self._rpc(
                        {
                            "cmd": "get",
                            "job": job,
                            "path": rel,
                            "offset": offset,
                            "length": CHUNK_BYTES,
                        },
                        timeout=timeout,
                    )
                    if response.get("status") != "ok":
                        raise RemoteStagingError(f"get {rel} falhou: {response.get('error') or response}")
                    payload = base64.b64decode(response.get("data") or "")
                    handle.write(payload)
                    offset += len(payload)
                    size = int(response.get("size") or 0)
                    if response.get("eof"):
                        break
            if offset != size:
                raise RemoteStagingError(f"download de {rel} incompleto ({offset}/{size} bytes)")
            os.replace(temporary, local)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return size

    def list(self, job: str, rel: str = "out") -> list[str]:
        response = self._rpc({"cmd": "list", "job": job, "path": rel}, timeout=30.0)
        if response.get("status") != "ok":
            raise RemoteStagingError(f"list {rel} falhou: {response.get('error') or response}")
        return [str(entry["name"]) for entry in response.get("entries") or [] if not entry.get("is_dir")]

    def clean(self, job: str) -> None:
        try:
            self._rpc({"cmd": "clean", "job": job}, timeout=30.0)
        except (RemoteDown, RemoteStagingError, OSError):
            pass


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


@dataclass
class StagedFile:
    field_name: str
    remote_rel: str
    remote_path: str
    local: Path


@dataclass
class StagedJob:
    client: RemoteClient
    job: str
    root: str
    request: dict[str, Any]
    outputs: list[StagedFile] = field(default_factory=list)
    sidecars: list[StagedFile] = field(default_factory=list)
    globs: list[tuple[str, Path]] = field(default_factory=list)
    keep: bool = False

    def discard(self) -> None:
        if not self.keep:
            self.client.clean(self.job)


def _resolve_local(value: str | os.PathLike[str]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (Path.cwd() / path)


def _sha256(path: Path, *, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            digest.update(block)
    return digest.hexdigest()


def _expand(template: str, primary: Path) -> str:
    return (
        template.replace("{output}", str(primary))
        .replace("{stem}", primary.stem)
        .replace("{suffix}", primary.suffix)
        .replace("{name}", primary.name)
    )


def stage_request(
    backend: str,
    request: dict[str, Any],
    *,
    client: RemoteClient | None = None,
) -> StagedJob:
    """Sobe os inputs, reescreve os paths e devolve o job preparado."""
    spec = spec_for(backend)
    client = client or RemoteClient()
    job, root = client.stage()
    root = root.rstrip("/")
    new_request = dict(request)
    uploaded: dict[Path, str] = {}
    counter = 0

    for field_name in spec.inputs:
        value = request.get(field_name)
        if value is None:
            continue
        is_list = isinstance(value, (list, tuple))
        values = list(value) if is_list else [value]
        mapped: list[Any] = []
        for item in values:
            if not isinstance(item, (str, os.PathLike)):
                mapped.append(item)  # não é um path — deixa como está
                continue
            local = _resolve_local(item)
            if not local.is_file():
                raise RemoteStagingError(f"{backend}.{field_name}: input não existe: {local}")
            rel = uploaded.get(local)
            if rel is None:
                counter += 1
                rel = f"in/{counter:02d}-{local.name}"
                client.put_file(job, rel, local)
                uploaded[local] = rel
            mapped.append(f"{root}/{rel}")
        new_request[field_name] = mapped if is_list else mapped[0]

    outputs: list[StagedFile] = []
    sidecars: list[StagedFile] = []
    globs: list[tuple[str, Path]] = []
    primary: Path | None = None
    for field_name in spec.outputs:
        value = request.get(field_name)
        if value is None:
            continue
        local = _resolve_local(value)
        rel = f"out/{local.name}"
        remote_path = f"{root}/{rel}"
        outputs.append(StagedFile(field_name, rel, remote_path, local))
        new_request[field_name] = remote_path
        if primary is None:
            primary = local
            for template in spec.sidecars:
                side_local = Path(_expand(template, local))
                sidecars.append(
                    StagedFile(field_name, f"out/{side_local.name}", f"{root}/out/{side_local.name}", side_local)
                )
            for template in spec.sidecar_globs:
                globs.append((_expand(template, local), local.parent))

    if primary is None or "output" not in new_request:
        raise RemoteStagingError(f"{backend}: request sem `output` para encaminhar")

    return StagedJob(
        client=client,
        job=job,
        root=root,
        request=new_request,
        outputs=outputs,
        sidecars=sidecars,
        globs=globs,
        keep=keep_sandbox(),
    )


def finalize(staged: StagedJob, response: Any) -> Any:
    """Descarrega outputs/sidecars para os paths locais e reescreve a resposta."""
    if not isinstance(response, dict):
        return response
    if response.get("status") != "ok":
        # Não limpar: um timeout do `wait` não garante que o job acabou — apagar
        # o sandbox podia remover ficheiros de um job ainda a correr. O TTL
        # (VRAMD_REMOTE_TTL_HOURS) recolhe-os mais tarde.
        return response

    for staged_file in [*staged.outputs, *staged.sidecars]:
        staged.client.get_file(staged.job, staged_file.remote_rel, staged_file.local)

    known = {item.local.name for item in [*staged.outputs, *staged.sidecars]}
    for pattern, local_dir in staged.globs:
        try:
            names = staged.client.list(staged.job, "out")
        except RemoteStagingError:
            names = []
        for name in names:
            if name in known or not fnmatch.fnmatch(name, pattern):
                continue
            staged.client.get_file(staged.job, f"out/{name}", local_dir / name)
            known.add(name)

    for staged_file in staged.outputs:
        response[staged_file.field_name] = str(staged_file.local)
    if "output" not in {item.field_name for item in staged.outputs} and staged.outputs:
        response["output"] = str(staged.outputs[0].local)
    staged.discard()
    return response


# ---------------------------------------------------------------------------
# submit/wait: o job_id é a chave entre os dois pedidos
# ---------------------------------------------------------------------------

_PENDING: dict[str, StagedJob] = {}
_PENDING_LOCK = threading.Lock()


def remember(job_id: str, staged: StagedJob) -> None:
    with _PENDING_LOCK:
        _PENDING[job_id] = staged


def take(job_id: str) -> StagedJob | None:
    with _PENDING_LOCK:
        return _PENDING.pop(job_id, None)


def peek(job_id: str) -> StagedJob | None:
    with _PENDING_LOCK:
        return _PENDING.get(job_id)
