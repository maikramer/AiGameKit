"""Teste end-to-end: UMS em venv A faz pedido para backend da tool B via subprocesso.

Cenário real (objectivo da Fase 3+4):
- UMS arranca num venv (aqui: o Python actual do teste) que **não tem** o
  package da tool B (simulado com backend 'mock_tool' sem módulo).
- Backend mock_tool tem ``tool: mock_tool`` no registry → BackendManager
  despacha para SubprocessWorkerPool.
- SubprocessWorkerPool spawna ``<venv>/python -m mock_tool.serve --ums-worker``
  via spawn_fn injectada (aqui: um subprocesso Python que usa gamedev_shared
  directamente — sem necessidade do package mock_tool real).
- O ciclo completo (load → generate → done) acontece sem o UMS precisar de
  importar a tool.

Isto prova que ``text3d generate`` (UMS em ModelServer/.venv) pode servir
``paint3d`` (em Paint3D/.venv) sem ImportError — desde que exista o adapter
em Paint3D/src/paint3d/worker_serve_adapter.py + serve no CLI (Fase 2/4).
"""

from __future__ import annotations

import os
import sys
import textwrap
from pathlib import Path

import pytest
from modelserver.backend_manager import BackendManager
from modelserver.registry import BackendDescriptor, Registry
from modelserver.subprocess_pool import SubprocessWorkerPool

# Script worker: simula o `serve --ums-worker` de uma tool real. Não precisa
# de package instalado — corre gamedev_shared.worker_serve.run_worker_loop.
WORKER_SCRIPT = textwrap.dedent("""
    import sys
    from gamedev_shared.worker_serve import run_worker_loop

    class Adapter:
        name = "mock_tool"

        def load(self, **kwargs):
            # Simula um generator com warmup.
            return {"loaded": True, **kwargs}

        def generate(self, model, request):
            progress = request.get("_progress")
            if callable(progress):
                progress(0.5, "gerando")
            return {
                "status": "ok",
                "output": request.get("output", "/tmp/subprocess_out.glb"),
                "via": "subprocesso",
            }

        def unload(self, model):
            pass

    run_worker_loop(Adapter, backend_name="mock_tool")
""")


@pytest.fixture
def worker_script_path(tmp_path: Path) -> Path:
    p = tmp_path / "mock_tool_worker.py"
    p.write_text(WORKER_SCRIPT)
    return p


def _make_pool(worker_script: Path, tmp_path: Path) -> SubprocessWorkerPool:
    """Cria um pool que spawna o script worker real."""
    log_path = tmp_path / "ums-worker-mock_tool.log"

    def spawn_fn(cmd, stdin, stdout, stderr):
        import subprocess

        return subprocess.Popen(
            [sys.executable, "-u", str(worker_script)],
            stdin=stdin,
            stdout=stdout,
            stderr=stderr,
            text=True,
            bufsize=1,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

    return SubprocessWorkerPool(
        spawn_fn=spawn_fn,
        log_path_fn=lambda _backend: log_path,
        load_timeout_sec=15.0,
        event_timeout_sec=15.0,
        abort_timeout_sec=5.0,
        ping_timeout_sec=5.0,
        python_override={"mock_tool": "/nonexistent"},  # spawn_fn custom
    )


def _make_registry() -> Registry:
    """Registry com backend 'mock_tool' em modo subprocesso."""
    descriptors = {
        "mock_tool": BackendDescriptor(
            name="mock_tool",
            adapter="_mock_missing",  # não existe — propositadamente
            vram_mib=1500,
            priority=30,
            tool="mock_tool",
        ),
    }
    return Registry(descriptors=descriptors)


class TestSubprocessEndToEnd:
    """UMS serve um backend sem o ter instalado — via subprocesso."""

    def test_generate_via_subprocess_without_adapter_import(
        self,
        worker_script_path: Path,
        tmp_path: Path,
    ) -> None:
        """O adapter '_mock_missing' NÃO é importável. Mesmo assim, o generate
        sucede porque o BackendManager despacha para o SubprocessWorkerPool
        (que spawnou o worker real noutro processo).
        """
        pool = _make_pool(worker_script_path, tmp_path)
        mgr = BackendManager(
            _make_registry(),
            query_free_mib=lambda: 99999,
            clear_vram=lambda: None,
            subprocess_pool=pool,
        )
        try:
            # Provar que o adapter NÃO é importável (modo in-process falharia).
            with pytest.raises(ImportError):
                mgr._registry.adapter("mock_tool")

            # Mas o generate via subprocesso funciona sem tocar no adapter.
            progresses: list[tuple[float | None, str | None]] = []
            result = mgr.generate(
                "mock_tool",
                {
                    "prompt": "x",
                    "output": "/tmp/x.glb",
                    "_progress": lambda pct, msg: progresses.append((pct, msg)),
                },
            )
            assert result["status"] == "ok"
            assert result["output"] == "/tmp/x.glb"
            assert result["via"] == "subprocesso"
            # Progress veio do worker via JSONL.
            assert (0.5, "gerando") in progresses
        finally:
            pool.shutdown_all()

    def test_evict_unloads_subprocess_without_adapter_import(
        self,
        worker_script_path: Path,
        tmp_path: Path,
    ) -> None:
        pool = _make_pool(worker_script_path, tmp_path)
        mgr = BackendManager(
            _make_registry(),
            query_free_mib=lambda: 99999,
            clear_vram=lambda: None,
            subprocess_pool=pool,
        )
        try:
            mgr.generate("mock_tool", {"prompt": "x", "output": "/tmp/x.glb"})
            assert mgr.is_loaded("mock_tool")
            # Evict sem invocar o adapter in-process.
            assert mgr.evict("mock_tool") is True
            assert not mgr.is_loaded("mock_tool")
        finally:
            pool.shutdown_all()

    def test_status_reports_loaded_via_subprocess(
        self,
        worker_script_path: Path,
        tmp_path: Path,
    ) -> None:
        pool = _make_pool(worker_script_path, tmp_path)
        mgr = BackendManager(
            _make_registry(),
            query_free_mib=lambda: 99999,
            clear_vram=lambda: None,
            subprocess_pool=pool,
        )
        try:
            mgr.generate("mock_tool", {"prompt": "x", "output": "/tmp/x.glb"})
            snap = mgr.status()
            loaded = [b for b in snap["backends"] if b["loaded"]]
            assert len(loaded) == 1
            assert loaded[0]["name"] == "mock_tool"
        finally:
            pool.shutdown_all()

    def test_disable_subprocess_via_env_falls_back_to_inprocess(
        self,
        worker_script_path: Path,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Com GAMEDEV_UMS_SUBPROCESS=0, o manager tenta o adapter in-process
        (que aqui falha com ImportError — provando que sem subprocesso o
        backend está inacessível)."""
        monkeypatch.setenv("GAMEDEV_UMS_SUBPROCESS", "0")
        pool = _make_pool(worker_script_path, tmp_path)
        mgr = BackendManager(
            _make_registry(),
            query_free_mib=lambda: 99999,
            clear_vram=lambda: None,
            subprocess_pool=pool,
        )
        try:
            with pytest.raises(ImportError):
                # Sem subprocesso, o manager tenta importar o adapter (missing).
                mgr.generate("mock_tool", {"prompt": "x", "output": "/tmp/x.glb"})
        finally:
            pool.shutdown_all()
