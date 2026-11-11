"""Bundle de código + locks para sessões vramd (monorepo real)."""

from __future__ import annotations

import types

import pytest

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
    assert not any(
        path.endswith((".pth", ".pt", ".safetensors", ".ckpt", ".bin", ".onnx"))
        for path in paths
    )
    assert bundle.envs
    for env in bundle.envs.values():
        assert env["lock"] in paths
        assert env["python"]
    names = {item["name"] for item in bundle.backends}
    assert {"text2d", "text3d", "paint3d", "text2sound"} <= names
    # Hi3D fica local (cu124); não entra no bundle/sessão remota.
    assert "hi3d" not in names
    for backend in bundle.backends:
        assert backend["code_roots"]
        assert backend["tool"]
        assert backend["env"] in bundle.envs
