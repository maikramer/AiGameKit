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
import binascii
import contextlib
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

# Cliente do protocolo binário/blobs (vramd >= 0.7). É uma dependência leve,
# mas mantemos o import opcional: um venv antigo sem `vramd` continua a usar
# o caminho JSON/base64 em vez de rebentar no import.
try:  # pragma: no cover - depende do venv onde a tool corre
    from vramd.transfer import TransferClient, TransferError
except ImportError:  # pragma: no cover
    TransferClient = None

    class TransferError(RuntimeError):  # type: ignore[no-redef]
        """Placeholder quando o pacote vramd não está instalado."""


CHUNK_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
# Jobs submetidos à espera de ``wait``. Um batch que submete e nunca espera
# (crash do caller) não pode deixar ligações/sandboxes referenciados para
# sempre; os mais antigos são fechados (o sandbox fica para o TTL do servidor).
PENDING_MAX = 256
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


def binary_enabled() -> bool:
    """``VRAMD_REMOTE_PROTOCOL``: auto (default), binary ou json.

    ``auto`` usa frames/blobs quando o pacote ``vramd`` está instalado; um
    servidor antigo faz o ``stage_request`` cair para o caminho JSON/base64.
    ``json`` força o caminho antigo (debug/rollback).
    """
    mode = os.environ.get("VRAMD_REMOTE_PROTOCOL", "auto").strip().lower()
    if mode in ("json", "legacy", "0", "off", "false", "no"):
        return False
    if TransferClient is None:
        if mode in ("binary", "frames", "1", "on", "true", "yes"):
            raise RemoteDown("VRAMD_REMOTE_PROTOCOL=binary mas o pacote vramd>=0.7 não está instalado no venv da tool")
        return False
    return True


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
            f"backend {backend!r} sem bloco `remote:` em {SPECS_PATH.name}; não é possível transferir os ficheiros"
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
            response = self.rpc({"cmd": "status"}, timeout=timeout)
        except (RemoteDown, RemoteStagingError, OSError):
            return False
        # O daemon devolve JSON de erro com 200 de transporte quando o token
        # está errado ou o comando é inválido. Considerar isso "up" fazia o
        # ensure_vramd_running dizer que estava pronto e todos os jobs falharem
        # com UNAUTHORIZED.
        return response.get("status") in ("ok", "status")

    def rpc(self, request: dict[str, Any], *, timeout: float = 60.0) -> dict[str, Any]:
        """Envia um pedido ao daemon remoto (fixa o endpoint na primeira resposta)."""
        return self._rpc(request, timeout=timeout)

    def stage(self) -> tuple[str, str]:
        response = self._rpc({"cmd": "stage"}, timeout=30.0)
        if response.get("status") != "ok" or not response.get("job"):
            raise RemoteStagingError(f"stage falhou: {response.get('error') or response}")
        root = str(response.get("root") or "").strip()
        if not root:
            # Sem root os outputs virariam ``/out/x`` (fora do sandbox); o
            # staging tem de falhar alto em vez de escrever no servidor.
            raise RemoteStagingError(f"stage sem root do sandbox: {response}")
        return str(response["job"]), root

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
                    raw = response.get("data") or ""
                    if not isinstance(raw, str):
                        raise RemoteStagingError(f"get {rel}: campo data não é base64 textual")
                    try:
                        payload = base64.b64decode(raw, validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise RemoteStagingError(f"get {rel}: base64 inválido ({exc})") from exc
                    if not payload and not response.get("eof"):
                        # Sem bytes e sem EOF o offset não avança: o loop
                        # anterior ficava a martelar o servidor para sempre.
                        raise RemoteStagingError(f"get {rel}: chunk vazio antes do EOF")
                    handle.write(payload)
                    offset += len(payload)
                    try:
                        size = int(response.get("size") or 0)
                    except (TypeError, ValueError) as exc:
                        raise RemoteStagingError(f"get {rel}: size inválido") from exc
                    if size < 0:
                        raise RemoteStagingError(f"get {rel}: size negativo ({size})")
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
        names: list[str] = []
        for entry in response.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("is_dir"):
                continue
            name = entry.get("name")
            if isinstance(name, str) and name:
                names.append(name)
        return names

    def clean(self, job: str) -> None:
        with contextlib.suppress(RemoteDown, RemoteStagingError, OSError):
            self._rpc({"cmd": "clean", "job": job}, timeout=30.0)


# ---------------------------------------------------------------------------
# Staging
# ---------------------------------------------------------------------------


@dataclass
class StagedFile:
    field_name: str
    remote_rel: str
    remote_path: str
    local: Path


@dataclass(frozen=True)
class _Rel:
    """Path relativo ao sandbox, resolvido para ``<root>/<rel>`` após o stage."""

    rel: str


@dataclass
class StagedJob:
    # RemoteClient (JSON/base64) ou TransferClient (frames/blobs).
    client: Any
    job: str
    root: str
    request: dict[str, Any]
    outputs: list[StagedFile] = field(default_factory=list)
    sidecars: list[StagedFile] = field(default_factory=list)
    globs: list[tuple[str, Path]] = field(default_factory=list)
    keep: bool = False

    def close(self) -> None:
        """Fecha a ligação sem tocar no sandbox (o TTL do servidor recolhe-o)."""
        close = getattr(self.client, "close", None)
        if not callable(close):
            return
        with contextlib.suppress(Exception):
            close()

    def discard(self) -> None:
        try:
            if not self.keep:
                with contextlib.suppress(Exception):
                    self.client.clean(self.job)
        finally:
            # O close tem de acontecer mesmo que o clean rebente; senão um
            # job descartado deixa o socket persistente do TransferClient.
            self.close()


def reconnect(staged: StagedJob) -> bool:
    """Reabre a ligação do staging no mesmo servidor.

    O servidor fecha ligações persistentes ao fim de ``VRAMD_CLIENT_IDLE_TIMEOUT_SEC``
    (300 s por omissão). Um batch pode submeter uma wave e só chamar ``wait``
    muito depois; sem reconexão o job (que está vivo) era reportado como
    ``VRAMD_REMOTE_DOWN``. Só o cliente binário sabe o endpoint e o handshake;
    o caminho legacy fica como está.
    """
    if TransferClient is None or not isinstance(staged.client, TransferClient):
        return False
    try:
        staged.client.reconnect()
    except (TransferError, OSError):
        return False
    return True


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


def _prepare_outputs(
    backend: str,
    spec: BackendRemoteSpec,
    request: dict[str, Any],
    root: str,
) -> tuple[dict[str, Any], list[StagedFile], list[StagedFile], list[tuple[str, Path]]]:
    """Reescreve os outputs/sidecars para o sandbox remoto."""
    new_request = dict(request)
    outputs: list[StagedFile] = []
    sidecars: list[StagedFile] = []
    globs: list[tuple[str, Path]] = []
    primary: Path | None = None
    used_rels: set[str] = set()
    for field_name in spec.outputs:
        value = request.get(field_name)
        if value is None:
            continue
        local = _resolve_local(value)
        rel = f"out/{local.name}"
        # Dois outputs com o mesmo basename (``a/x.glb`` e ``b/x.glb``) iam
        # para o mesmo path do sandbox: o segundo apagava o primeiro e ambos
        # os downloads recebiam o mesmo ficheiro.
        suffix = 1
        while rel in used_rels:
            suffix += 1
            rel = f"out/{suffix}-{local.name}"
        used_rels.add(rel)
        remote_path = f"{root}/{rel}"
        outputs.append(StagedFile(field_name, rel, remote_path, local))
        new_request[field_name] = remote_path
        if primary is None:
            primary = local
            for template in spec.sidecars:
                side_local = Path(_expand(template, local))
                sidecars.append(
                    StagedFile(
                        field_name,
                        f"out/{side_local.name}",
                        f"{root}/out/{side_local.name}",
                        side_local,
                    )
                )
            for template in spec.sidecar_globs:
                globs.append((_expand(template, local), local.parent))

    if primary is None or "output" not in new_request:
        raise RemoteStagingError(f"{backend}: request sem `output` para encaminhar")
    return new_request, outputs, sidecars, globs


def _stage_legacy(
    backend: str,
    request: dict[str, Any],
    *,
    client: RemoteClient | None = None,
) -> StagedJob:
    """Caminho antigo: stage → put_file base64 → paths do sandbox."""
    spec = spec_for(backend)
    client = client or RemoteClient()
    job, root = client.stage()
    root = root.rstrip("/")
    new_request = dict(request)
    uploaded: dict[Path, str] = {}
    counter = 0

    try:
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
                # ``refs/a.png`` e ``refs/../refs/a.png`` são o mesmo ficheiro:
                # deduplicar pelo path resolvido evita dois uploads.
                key = local.resolve()
                rel = uploaded.get(key)
                if rel is None:
                    counter += 1
                    rel = f"in/{counter:02d}-{local.name}"
                    client.put_file(job, rel, local)
                    uploaded[key] = rel
                mapped.append(f"{root}/{rel}")
            new_request[field_name] = mapped if is_list else mapped[0]

        new_request, outputs, sidecars, globs = _prepare_outputs(backend, spec, new_request, root)
    except BaseException:
        # O sandbox foi criado por ``stage`` mas nunca chegou a ser devolvido:
        # sem este clean ficava órfão até ao TTL (e, num batch, acumulava).
        if not keep_sandbox():
            with contextlib.suppress(Exception):
                client.clean(job)
        raise
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


def _stage_blobs(
    backend: str,
    request: dict[str, Any],
    client: TransferClient,
) -> StagedJob:
    """Caminho v2: sobe cada input uma vez como blob e materializa por hardlink."""
    spec = spec_for(backend)
    new_request = dict(request)
    uploaded: dict[Path, tuple[str, str]] = {}
    blob_map: dict[str, str] = {}
    pending: dict[str, tuple[bool, list[Any]]] = {}
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
                mapped.append(item)
                continue
            local = _resolve_local(item)
            if not local.is_file():
                raise RemoteStagingError(f"{backend}.{field_name}: input não existe: {local}")
            key = local.resolve()
            entry = uploaded.get(key)
            if entry is None:
                counter += 1
                rel = f"in/{counter:02d}-{local.name}"
                sha = _sha256(local)
                client.put_blob(sha, local)
                entry = (rel, sha)
                uploaded[key] = entry
                blob_map[rel] = sha
            mapped.append(_Rel(entry[0]))
        pending[field_name] = (is_list, mapped)

    job, root = client.stage(blob_map)
    root = root.rstrip("/")
    try:
        for field_name, (is_list, mapped) in pending.items():
            rewritten = [f"{root}/{item.rel}" if isinstance(item, _Rel) else item for item in mapped]
            new_request[field_name] = rewritten if is_list else rewritten[0]

        new_request, outputs, sidecars, globs = _prepare_outputs(backend, spec, new_request, root)
    except BaseException:
        if not keep_sandbox():
            with contextlib.suppress(Exception):
                client.clean(job)
        raise
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


def stage_request(
    backend: str,
    request: dict[str, Any],
    *,
    client: RemoteClient | None = None,
) -> StagedJob:
    """Sobe os inputs, reescreve os paths e devolve o job preparado.

    Usa frames/blobs quando o servidor os anuncia; se o servidor for antigo
    (sem ``binary``/``blobs``), cai para o caminho JSON/base64. Um ``client``
    explícito força o caminho legacy (usado pelos testes e por rollback).
    """
    # Sessão de código do cliente: o bundle/env é aberto no primeiro job de
    # cada backend (wave) e reutilizado nos jobs seguintes. Num servidor sem
    # sessões (ou com `VRAMD_SESSION=off`) isto é `None` e o job usa os
    # backends fixos.
    # Antes do staging: uma falha de bundle não deixa um sandbox órfão.
    from . import vramd_bundle

    try:
        session = vramd_bundle.ensure_session([backend])
    except vramd_bundle.BundleError as exc:
        raise RemoteStagingError(str(exc)) from exc

    if client is not None:
        staged = _stage_legacy(backend, request, client=client)
    elif binary_enabled():
        transfer: TransferClient | None = None
        success = False
        try:
            transfer = TransferClient()
            transfer.connect()
            staged = _stage_blobs(backend, request, transfer)
            success = True
        except TransferError as exc:
            if "sem protocolo binário" not in str(exc):
                raise RemoteDown(str(exc)) from exc
            # O handshake binário falhou nesta ligação persistente; não a
            # reutilizar no caminho legacy (ficaria um socket aberto por job
            # até ao GC).
            if transfer is not None:
                transfer.close()
            # Servidor 0.6/0.7 sem features: segue pelo caminho antigo.
            staged = _stage_legacy(backend, request)
            success = True
        except OSError as exc:
            raise RemoteDown(str(exc)) from exc
        finally:
            # Qualquer falha (missing input, staging recusado, OSError) tem de
            # fechar a ligação persistente; antes só o TransferError o fazia e
            # um batch com inputs em falta vazava um socket por job.
            if transfer is not None and not success:
                transfer.close()
    else:
        staged = _stage_legacy(backend, request)

    if session:
        staged.request["session"] = session
    return staged


def _is_missing_remote_file(exc: Exception) -> bool:
    """True se o erro do servidor é "esse ficheiro não existe no sandbox"."""
    return "não existe" in str(exc).lower() or "nao existe" in str(exc).lower()


def finalize(staged: StagedJob, response: Any) -> Any:
    """Descarrega outputs/sidecars para os paths locais e reescreve a resposta."""
    if not isinstance(response, dict):
        return response
    if response.get("status") != "ok":
        # Não limpar: um timeout do `wait` não garante que o job acabou — apagar
        # o sandbox podia remover ficheiros de um job ainda a correr. O TTL
        # (VRAMD_REMOTE_TTL_HOURS) recolhe-os mais tarde. Fechar a ligação é
        # seguro e evita um socket persistente por job falhado.
        staged.close()
        return response

    try:
        for staged_file in staged.outputs:
            staged.client.get_file(staged.job, staged_file.remote_rel, staged_file.local)
        for staged_file in staged.sidecars:
            try:
                staged.client.get_file(staged.job, staged_file.remote_rel, staged_file.local)
            except (RemoteStagingError, TransferError) as exc:
                # Um sidecar declarado pode não ser produzido por todos os modos
                # (ex.: metadados opcionais). Não perder o output principal por
                # causa disso; um erro de rede continua a propagar.
                if _is_missing_remote_file(exc):
                    continue
                raise

        known = {item.local.name for item in [*staged.outputs, *staged.sidecars]}
        for pattern, local_dir in staged.globs:
            try:
                names = staged.client.list(staged.job, "out")
            except (RemoteStagingError, TransferError):
                names = []
            # `list` devolve nomes soltos de `out/`; um template expandido com
            # `{output}` traria o path absoluto e `fnmatch` nunca casaria.
            basename_pattern = Path(pattern).name
            for name in names:
                # Defesa em profundidade: o nome vem do servidor e vai ser usado
                # para escrever localmente. Só componentes simples são aceites.
                if not name or name in (".", "..") or Path(name).name != name:
                    continue
                if name in known or not fnmatch.fnmatch(name, basename_pattern):
                    continue
                staged.client.get_file(staged.job, f"out/{name}", local_dir / name)
                known.add(name)
    except BaseException:
        # O download falhou: não deixar o socket persistente aberto. O sandbox
        # fica para o TTL porque o job pode ter produzido o resto.
        staged.close()
        raise

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
        previous = _PENDING.pop(job_id, None)
        if previous is not None:
            # Dois submits para o mesmo job_id não podem deixar o primeiro
            # staged (socket + sandbox) sem dono.
            previous.close()
        while len(_PENDING) >= PENDING_MAX:
            # ``dict`` preserva a ordem de inserção desde o 3.7: a primeira
            # chave é o job mais antigo.
            _oldest = next(iter(_PENDING))
            _PENDING.pop(_oldest).close()
        _PENDING[job_id] = staged


def take(job_id: str) -> StagedJob | None:
    with _PENDING_LOCK:
        return _PENDING.pop(job_id, None)


def peek(job_id: str) -> StagedJob | None:
    with _PENDING_LOCK:
        return _PENDING.get(job_id)
