"""Bundle de código + locks para sessões vramd (monorepo real)."""

from __future__ import annotations

import os
import types

import pytest
import tomllib

from aigamekit_shared import vramd_bundle


class _OldClient:
    """Servidor 0.6: o handshake binário não existe."""

    def connect(self):
        import vramd.transfer as transfer

        raise transfer.TransferError("servidor vramd sem protocolo binário/blobs")

    def close(self) -> None:
        pass


def _fake_bundle() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        sha256="a" * 64,
        data=b"",
        manifest=lambda: {},
        backends=[],
    )


def test_ensure_session_old_server_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    import vramd.transfer as transfer

    vramd_bundle.reset()
    monkeypatch.setattr(vramd_bundle.vramd_remote, "remote_enabled", lambda: True)
    monkeypatch.setattr(vramd_bundle, "build_bundle", lambda requested=None: _fake_bundle())
    monkeypatch.setattr(transfer, "TransferClient", _OldClient)
    monkeypatch.delenv("VRAMD_SESSION", raising=False)
    assert vramd_bundle.ensure_session() is None


def test_ensure_session_on_requires_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    import vramd.transfer as transfer

    vramd_bundle.reset()
    monkeypatch.setattr(vramd_bundle.vramd_remote, "remote_enabled", lambda: True)
    monkeypatch.setattr(vramd_bundle, "build_bundle", lambda requested=None: _fake_bundle())
    monkeypatch.setattr(transfer, "TransferClient", _OldClient)
    monkeypatch.setenv("VRAMD_SESSION", "on")
    with pytest.raises(vramd_bundle.BundleError):
        vramd_bundle.ensure_session()


def test_ensure_session_without_vramd_client_explains(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem cliente vramd, o erro diz o que instalar (não 'backend desconhecido')."""
    import sys

    vramd_bundle.reset()
    monkeypatch.setattr(vramd_bundle.vramd_remote, "remote_enabled", lambda: True)
    monkeypatch.setitem(sys.modules, "vramd.transfer", None)
    monkeypatch.delenv("VRAMD_SESSION", raising=False)
    with pytest.raises(vramd_bundle.BundleError, match=r"vramd>=0\.7"):
        vramd_bundle.ensure_session()


def test_build_bundle_real_monorepo() -> None:
    root = vramd_bundle.try_find_monorepo_root()
    if root is None or not (root / "Text3D" / ".venv").is_dir():
        pytest.skip("monorepo/venvs das tools ausentes")

    bundle = vramd_bundle.build_bundle()
    # Determinístico: o mesmo código dá o mesmo hash (dedupe no CAS).
    assert vramd_bundle.build_bundle().sha256 == bundle.sha256
    assert bundle.sha256
    assert 0 < len(bundle.data) < 10 * 1024 * 1024
    paths = {item["path"] for item in bundle.files}
    assert "Shared/src/aigamekit_shared/vramd_remote.py" in paths
    assert "Text3D/src/text3d/cli.py" in paths
    # Pesos não entram no bundle (ficam no cache de modelos do servidor).
    assert not any(path.endswith((".pth", ".pt", ".safetensors", ".ckpt", ".bin", ".onnx")) for path in paths)
    assert bundle.envs
    for env in bundle.envs.values():
        assert env["lock"] in paths
        assert env["python"]
    names = {item["name"] for item in bundle.backends}
    assert {"text2d", "text3d", "paint3d", "text2sound", "rigging3d"} <= names
    # Hi3D fica local (cu124); não entra no bundle/sessão remota.
    assert "hi3d" not in names
    assert "Rigging3D/src/rigging3d/worker_serve_adapter.py" in paths
    for backend in bundle.backends:
        assert backend["code_roots"]
        assert backend["tool"]
        assert backend["env"] in bundle.envs


def test_build_bundle_rejects_oversized_code_root(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um code_root gigante (path mal configurado) falha antes de encher a RAM."""
    payload = tmp_path / "ToolX" / "src" / "a.py"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"x" * 128)
    entry = {
        "name": "fake",
        "session": {"env": "fake", "tool": "fake", "code_roots": ["ToolX/src"]},
    }
    monkeypatch.setattr(vramd_bundle, "try_find_monorepo_root", lambda: tmp_path)
    monkeypatch.setattr(vramd_bundle, "_backend_entries", lambda: {"fake": entry})
    monkeypatch.setattr(vramd_bundle, "_TOOL_DIRS", {"fake": "ToolX"})
    monkeypatch.setattr(vramd_bundle, "_freeze", lambda _tool, _root: ("3.13", "pkg==1\n"))
    monkeypatch.setattr(vramd_bundle, "_MAX_TOTAL_BYTES", 32)

    with pytest.raises(vramd_bundle.BundleError, match="excede"):
        vramd_bundle.build_bundle(["fake"])


def test_freeze_rejects_torch_cu12_without_replacement(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Um lock só com torch cu12 não pode seguir para o servidor (CUDA 13)."""
    python = tmp_path / "Tool" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    results = iter(
        [
            Result("3.13\n"),
            Result("torch==2.5.0+cu124\nnumpy==2.1.0\n"),
        ]
    )
    monkeypatch.setattr(vramd_bundle.shutil, "which", lambda _name: None)
    monkeypatch.setattr(vramd_bundle.subprocess, "run", lambda *_a, **_k: next(results))

    with pytest.raises(vramd_bundle.BundleError, match="CUDA 12"):
        vramd_bundle._freeze("Tool", tmp_path)


def test_freeze_drops_cuda12_only_packages(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """spconv/cumm cu121 não usados pelo código ficam fora do lock remoto."""
    python = tmp_path / "Tool" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")

    class Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str) -> None:
            self.stdout = stdout

    results = iter(
        [
            Result("3.13\n"),
            Result(
                "torch==2.13.0+cu130\nspconv-cu121==2.3.8\ncumm-cu121==0.8.2\n"
                "pyrender==0.1.45\npyopengl==3.1.10\n"
                "torch-cluster==1.6.3+pt211cu130\ntorch-scatter==2.1.2+pt211cu130\n"
                "numpy==2.1.0\n"
            ),
        ]
    )
    monkeypatch.setattr(vramd_bundle.shutil, "which", lambda _name: None)
    monkeypatch.setattr(vramd_bundle.subprocess, "run", lambda *_a, **_k: next(results))

    _version, lock = vramd_bundle._freeze("Tool", tmp_path)
    for unused in ("spconv", "cumm", "pyrender", "pyopengl", "torch-cluster", "torch-scatter"):
        assert unused not in lock
    assert "torch==2.13.0+cu130" in lock
    assert "download.pytorch.org/whl/cu130" in lock


def test_vramd_pin_covers_shared_requirement() -> None:
    """O pin do supervisor não pode ser mais antigo que o cliente exige.

    O ``Shared/pyproject.toml`` declara ``vramd>=0.7.0``; um ``Vramd/.venv``
    instalado a partir do pin antigo (0.6) fazia o staging remoto cair no
    caminho JSON/base64 e não tinha ``topology``.
    """
    root = vramd_bundle.try_find_monorepo_root()
    if root is None or not (root / "Vramd" / "pyproject.toml").is_file():
        pytest.skip("checkout do monorepo incompleto")

    def _floor(path) -> tuple[int, ...]:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        for dep in data.get("project", {}).get("dependencies", []):
            name, _, spec = dep.partition(">=")
            if name.strip().replace("_", "-") == "vramd" and spec:
                return tuple(int(part) for part in spec.strip().split("."))
        raise AssertionError(f"{path} não declara vramd>=…")

    shared = _floor(root / "Shared" / "pyproject.toml")
    pinned = _floor(root / "Vramd" / "pyproject.toml")
    assert pinned >= shared, f"Vramd pin {pinned} < Shared requirement {shared}"
