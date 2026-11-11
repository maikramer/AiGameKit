"""E2E: cliente AiGameKit → vramd remoto (TCP) → sessão/bundle → worker → ficheiro.

Usa um backend falso e uma env ``system`` para não construir torch; o resto do
caminho é real: TransferClient, hello/frames, blob-put, session-open, extracção
do bundle, spawn do worker com PYTHONPATH, generate, job-get e finalize.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
import threading
import time
from pathlib import Path

import pytest

from aigamekit_shared import vramd_bundle, vramd_client, vramd_remote

WORKER = r'''
import json, sys
for line in sys.stdin:
    try:
        req = json.loads(line)
    except Exception:
        continue
    cmd = req.get("cmd")
    if cmd == "load":
        print(json.dumps({"event": "ready", "vram_mib": 0}), flush=True)
    elif cmd == "generate":
        request = req.get("request") or {}
        out = request.get("output")
        if out:
            with open(out, "wb") as fh:
                fh.write(b"GLB-E2E")
        print(json.dumps({"event": "done", "result": {
            "status": "ok",
            "output": out,
            "tag": request.get("tag"),
        }}), flush=True)
    elif cmd == "unload":
        print(json.dumps({"event": "unloaded"}), flush=True)
    elif cmd == "ping":
        print(json.dumps({"event": "pong"}), flush=True)
    elif cmd == "shutdown":
        break
'''


def _bundle() -> vramd_bundle.Bundle:
    payload = WORKER.encode()
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tf:
        info = tarfile.TarInfo("src/mytool/__main__.py")
        info.size = len(payload)
        info.mode = 0o644
        info.mtime = 0
        tf.addfile(info, io.BytesIO(payload))
    data = buffer.getvalue()
    return vramd_bundle.Bundle(
        sha256=hashlib.sha256(data).hexdigest(),
        data=data,
        files=[
            {
                "path": "src/mytool/__main__.py",
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
        envs={"main": {"system": True}},
        backends=[
            {
                "name": "echo",
                "tool": "mytool",
                "env": "main",
                "code_roots": ["src"],
                "vram_mib": 512,
                "priority": 0,
                "load_keys": [],
                "shape_keys": [],
                "remote": {"inputs": [], "outputs": ["output"]},
                "models": [],
            }
        ],
    )


@pytest.fixture
def server(tmp_path: Path):
    from vramd.registry import Registry
    from vramd.server import VramdServer

    socket_path = tmp_path / "vramd.sock"
    srv = VramdServer(
        registry=Registry(descriptors={}),
        socket_path=socket_path,
        listen=("127.0.0.1", 0),
        allow_client_code=True,
        query_free_mib=lambda: 99999,
        clear_vram=lambda: None,
    )
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    deadline = time.monotonic() + 5.0
    while not socket_path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert socket_path.exists(), "vramd não arrancou"
    from vramd import transport

    info = transport.read_endpoint(socket_path)
    assert info is not None and info.port
    yield srv, info.host, info.port, info.token
    srv._running = False
    thread.join(timeout=5.0)


def test_e2e_client_session_worker(server, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _srv, host, port, token = server
    monkeypatch.setenv("VRAMD_REMOTE", f"{host}:{port}")
    monkeypatch.setenv("VRAMD_REMOTE_TOKEN", token or "")
    monkeypatch.setenv("VRAMD_AUTO_START", "0")
    monkeypatch.delenv("VRAMD_GPU_MODE", raising=False)
    vramd_bundle.reset()
    bundle = _bundle()
    monkeypatch.setattr(vramd_bundle, "build_bundle", lambda requested=None: bundle)
    monkeypatch.setattr(
        vramd_remote,
        "_SPECS",
        {"echo": vramd_remote.BackendRemoteSpec(inputs=(), outputs=("output",))},
    )

    out = tmp_path / "e2e.glb"
    response = vramd_client.delegate_to_vramd(
        "echo",
        {"output": str(out), "tag": "e2e"},
        remote=True,
        timeout_sec=120.0,
    )
    assert response is not None, "sem resposta do vramd"
    assert response.get("status") == "ok", response
    assert response.get("output") == str(out)
    assert response.get("tag") == "e2e"
    assert out.read_bytes() == b"GLB-E2E"
    # A sessão foi a mesma criada pelo bundle; o job levou session.
    session = vramd_bundle.session_id()
    assert session
    assert response.get("session") == session
    assert response.get("bundle_sha") == bundle.sha256
