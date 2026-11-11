"""Staging de ficheiros do vramd remoto (sem rede: cliente falso)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aigamekit_shared import vramd_remote
from aigamekit_shared.vramd_remote import (
    BackendRemoteSpec,
    RemoteClient,
    RemoteStagingError,
    finalize,
    load_specs,
    stage_request,
)


class FakeClient:
    """Sandbox remoto em memória, com o contrato do RemoteClient."""

    def __init__(self, job: str = "a" * 32, root: str = "/srv/remote/a") -> None:
        self.job = job
        self.root = root
        self.files: dict[str, bytes] = {}
        self.puts: list[str] = []
        self.gets: list[str] = []
        self.cleaned = False

    def stage(self) -> tuple[str, str]:
        return self.job, self.root

    def put_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
        assert job == self.job
        self.files[rel] = local.read_bytes()
        self.puts.append(rel)
        return len(self.files[rel])

    def get_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
        assert rel in self.files, f"get de {rel} sem ficheiro remoto"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[rel])
        self.gets.append(rel)
        return len(self.files[rel])

    def list(self, job: str, rel: str = "out") -> list[str]:
        prefix = rel.rstrip("/") + "/"
        return sorted(
            name[len(prefix) :] for name in self.files if name.startswith(prefix) and "/" not in name[len(prefix) :]
        )

    def clean(self, job: str) -> None:
        self.cleaned = True


@pytest.fixture
def specs(monkeypatch):
    monkeypatch.setattr(
        vramd_remote,
        "_SPECS",
        {
            "text3d": BackendRemoteSpec(
                inputs=("from_image", "pose_file"),
                outputs=("output",),
                sidecars=("{output}.omni.json",),
            ),
            "text2d": BackendRemoteSpec(
                inputs=("refs",),
                outputs=("output",),
                sidecar_globs=("{stem}_*{suffix}",),
            ),
            "part3d": BackendRemoteSpec(
                inputs=("mesh_path", "segmentation_proxy"),
                outputs=("output", "output_segmented"),
            ),
        },
    )


def test_specs_cover_the_registry_backends() -> None:
    """Todo o backend GPU tem bloco `remote:` (o intrinsic fica de fora)."""
    specs = load_specs()
    for name in (
        "text2d",
        "text3d",
        "paint3d",
        "part3d",
        "hi3d",
        "text2sound",
        "terrain3d",
        "texture2d",
        "skymap2d",
    ):
        assert name in specs, f"{name} sem `remote:` em backends.yaml"
    assert "intrinsic" not in specs  # output_dir (diretório) não é suportado


def test_stage_uploads_and_rewrites(tmp_path: Path, monkeypatch, specs) -> None:
    monkeypatch.chdir(tmp_path)
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    out = tmp_path / "out" / "hero.glb"

    client = FakeClient()
    staged = stage_request(
        "text3d",
        {"prompt": "hero", "from_image": str(image), "pose_file": None, "output": str(out)},
        client=client,
    )

    assert client.puts == ["in/01-hero.png"]
    assert staged.request["from_image"] == f"{client.root}/in/01-hero.png"
    assert staged.request["output"] == f"{client.root}/out/hero.glb"
    assert staged.request["prompt"] == "hero"
    assert [s.remote_rel for s in staged.outputs] == ["out/hero.glb"]
    assert [s.remote_rel for s in staged.sidecars] == ["out/hero.glb.omni.json"]
    assert staged.sidecars[0].local == Path(str(out) + ".omni.json")


def test_stage_resolves_relative_paths_and_dedupes(tmp_path: Path, monkeypatch, specs) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "refs").mkdir()
    ref = tmp_path / "refs" / "a.png"
    ref.write_bytes(b"A")
    out = tmp_path / "x.png"

    client = FakeClient()
    # refs e image_path apontam para o mesmo ficheiro → um só upload
    monkeypatch.setattr(
        vramd_remote,
        "_SPECS",
        {"text2d": BackendRemoteSpec(inputs=("refs",), outputs=("output",), sidecar_globs=("{stem}_*{suffix}",))},
    )
    staged = stage_request(
        "text2d",
        {"refs": ["refs/a.png", "refs/a.png"], "output": str(out)},
        client=client,
    )
    assert client.puts == ["in/01-a.png"]
    assert staged.request["refs"] == [f"{client.root}/in/01-a.png"] * 2


def test_stage_multiple_outputs(tmp_path: Path, monkeypatch, specs) -> None:
    mesh = tmp_path / "m.glb"
    mesh.write_bytes(b"GLB")
    client = FakeClient()
    staged = stage_request(
        "part3d",
        {
            "mesh_path": str(mesh),
            "output": str(tmp_path / "m_parts.glb"),
            "output_segmented": str(tmp_path / "m_seg.glb"),
        },
        client=client,
    )
    assert [s.remote_rel for s in staged.outputs] == ["out/m_parts.glb", "out/m_seg.glb"]
    assert staged.request["output_segmented"] == f"{client.root}/out/m_seg.glb"


def test_missing_input_is_an_error(tmp_path: Path, monkeypatch, specs) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RemoteStagingError, match="não existe"):
        stage_request(
            "text3d",
            {"from_image": "nope.png", "output": str(tmp_path / "x.glb")},
            client=FakeClient(),
        )


def test_finalize_downloads_outputs_and_sidecars(tmp_path: Path, monkeypatch, specs) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    out = tmp_path / "out" / "hero.glb"
    client = FakeClient()
    client.files["out/hero.glb"] = b"GLB"
    client.files["out/hero.glb.omni.json"] = b"{}"

    staged = stage_request("text3d", {"from_image": str(image), "output": str(out)}, client=client)
    response = {
        "status": "ok",
        "output": f"{client.root}/out/hero.glb",
        "seed": 1,
    }
    result = finalize(staged, response)

    assert result["output"] == str(out)
    assert result["seed"] == 1
    assert out.read_bytes() == b"GLB"
    assert Path(str(out) + ".omni.json").read_bytes() == b"{}"
    assert client.cleaned is True


def test_finalize_downloads_globbed_extras(tmp_path: Path, monkeypatch, specs) -> None:
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"R")
    out = tmp_path / "hero.png"
    client = FakeClient()
    client.files["out/hero.png"] = b"0"
    client.files["out/hero_1.png"] = b"1"
    client.files["out/hero_2.png"] = b"2"
    client.files["out/hero_notes.txt"] = b"n"

    staged = stage_request("text2d", {"prompt": "hero", "refs": [str(ref)], "output": str(out)}, client=client)
    finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.png"})

    assert out.read_bytes() == b"0"
    assert (tmp_path / "hero_1.png").read_bytes() == b"1"
    assert (tmp_path / "hero_2.png").read_bytes() == b"2"
    assert not (tmp_path / "hero_notes.txt").exists()


def test_finalize_glob_template_with_output_path(tmp_path: Path, monkeypatch, specs) -> None:
    """Um glob expandido com `{output}` é absoluto; `list` devolve nomes soltos."""
    spec = BackendRemoteSpec(inputs=(), outputs=("output",), sidecar_globs=("{output}_*.png",))
    monkeypatch.setattr(vramd_remote, "spec_for", lambda _backend: spec)
    client = FakeClient()
    out = tmp_path / "hero.png"
    client.files["out/hero.png"] = b"0"
    client.files["out/hero.png_1.png"] = b"1"

    staged = vramd_remote.stage_request("text2d", {"output": str(out)}, client=client)
    finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.png"})

    assert out.read_bytes() == b"0"
    assert (tmp_path / "hero.png_1.png").read_bytes() == b"1"


def test_finalize_error_keeps_sandbox(tmp_path: Path, specs) -> None:
    image = tmp_path / "x.png"
    image.write_bytes(b"X")
    client = FakeClient()
    staged = stage_request("text3d", {"from_image": str(image), "output": str(tmp_path / "x.glb")}, client=client)
    result = finalize(staged, {"status": "error", "error": "boom"})
    assert result["status"] == "error"
    assert client.gets == []
    # O job pode ainda estar a correr: o sandbox fica para o TTL.
    assert client.cleaned is False


def test_keep_sandbox_env(monkeypatch, specs) -> None:
    monkeypatch.setenv("VRAMD_KEEP_SANDBOX", "1")
    client = FakeClient()
    staged = stage_request(
        "text3d",
        {"from_image": str(Path(__file__)), "output": "/tmp/x.glb"},
        client=client,
    )
    staged.discard()
    assert client.cleaned is False


class FakeTransfer:
    """Contrato mínimo do ``vramd.transfer.TransferClient`` para os testes."""

    def __init__(self, job: str = "b" * 32, root: str = "/srv/remote/b") -> None:
        self.job = job
        self.root = root
        self.puts: list[tuple[str, str]] = []
        self.staged_blobs: dict[str, str] = {}
        self.files: dict[str, bytes] = {}
        self.gets: list[str] = []
        self.cleaned = False
        self.closed = False

    def connect(self) -> dict:
        return {"status": "ok", "features": ["binary", "blobs", "sessions"]}

    def stage(self, blobs: dict[str, str] | None = None) -> tuple[str, str]:
        self.staged_blobs = dict(blobs or {})
        return self.job, self.root

    def put_blob(self, sha256: str, path: Path, *, chunk_size: int = 0) -> dict:
        self.puts.append((sha256, str(path)))
        return {"status": "ok", "sha256": sha256, "size": path.stat().st_size}

    def get_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
        assert rel in self.files, f"get de {rel} sem ficheiro remoto"
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(self.files[rel])
        self.gets.append(rel)
        return len(self.files[rel])

    def list(self, job: str, rel: str = "out") -> list[str]:
        prefix = rel.rstrip("/") + "/"
        return sorted(
            name[len(prefix) :] for name in self.files if name.startswith(prefix) and "/" not in name[len(prefix) :]
        )

    def clean(self, job: str) -> None:
        self.cleaned = True

    def close(self) -> None:
        self.closed = True


def test_stage_blobs_uploads_once_and_rewrites(tmp_path: Path, monkeypatch, specs) -> None:
    monkeypatch.chdir(tmp_path)
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    out = tmp_path / "out" / "hero.glb"
    client = FakeTransfer()

    staged = vramd_remote._stage_blobs(
        "text3d",
        {
            "prompt": "hero",
            "from_image": ["hero.png", "hero.png"],
            "output": str(out),
        },
        client,
    )

    sha = hashlib.sha256(b"PNG").hexdigest()
    assert client.puts == [(sha, str(image))]
    assert client.staged_blobs == {"in/01-hero.png": sha}
    assert staged.request["from_image"] == [f"{client.root}/in/01-hero.png"] * 2
    assert staged.request["output"] == f"{client.root}/out/hero.glb"
    assert [s.remote_rel for s in staged.outputs] == ["out/hero.glb"]
    assert [s.remote_rel for s in staged.sidecars] == ["out/hero.glb.omni.json"]


def test_stage_blobs_finalize_and_close(tmp_path: Path, monkeypatch, specs) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    out = tmp_path / "out" / "hero.glb"
    client = FakeTransfer()
    client.files["out/hero.glb"] = b"GLB"
    client.files["out/hero.glb.omni.json"] = b"{}"

    staged = vramd_remote._stage_blobs("text3d", {"from_image": str(image), "output": str(out)}, client)
    result = finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.glb"})

    assert result["output"] == str(out)
    assert out.read_bytes() == b"GLB"
    assert Path(str(out) + ".omni.json").read_bytes() == b"{}"
    assert client.cleaned is True
    assert client.closed is True


def test_stage_request_falls_back_to_legacy_on_old_server(tmp_path: Path, monkeypatch, specs) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    legacy = FakeClient()

    class OldTransfer(FakeTransfer):
        def connect(self) -> dict:
            raise vramd_remote.TransferError("servidor vramd sem protocolo binário/blobs")

    monkeypatch.setattr(vramd_remote, "TransferClient", OldTransfer)
    monkeypatch.setattr(vramd_remote, "RemoteClient", lambda: legacy)

    staged = stage_request(
        "text3d",
        {"from_image": str(image), "output": str(tmp_path / "hero.glb")},
    )
    assert staged.client is legacy
    assert legacy.puts == ["in/01-hero.png"]


def test_stage_request_honours_json_protocol(tmp_path: Path, monkeypatch, specs) -> None:
    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    legacy = FakeClient()
    monkeypatch.setenv("VRAMD_REMOTE_PROTOCOL", "json")
    monkeypatch.setattr(vramd_remote, "TransferClient", FakeTransfer)
    monkeypatch.setattr(vramd_remote, "RemoteClient", lambda: legacy)

    staged = stage_request(
        "text3d",
        {"from_image": str(image), "output": str(tmp_path / "hero.glb")},
    )
    assert staged.client is legacy
    assert legacy.puts == ["in/01-hero.png"]


def test_stage_request_adds_session(tmp_path: Path, monkeypatch, specs) -> None:
    from aigamekit_shared import vramd_bundle

    image = tmp_path / "hero.png"
    image.write_bytes(b"PNG")
    monkeypatch.setattr(vramd_bundle, "ensure_session", lambda backends=None: "sess-123")
    staged = stage_request(
        "text3d",
        {"from_image": str(image), "output": str(tmp_path / "hero.glb")},
        client=FakeClient(),
    )
    assert staged.request["session"] == "sess-123"


# ---------------------------------------------------------------------------
# Robustez do cliente/transferências (sem rede)
# ---------------------------------------------------------------------------


class TestRemoteUp:
    def test_auth_error_is_not_up(self) -> None:
        client = RemoteClient(endpoints=[])
        client.rpc = lambda *_a, **_k: {"status": "error", "error_code": "UNAUTHORIZED"}  # type: ignore[method-assign]
        assert client.is_up() is False

    def test_status_response_is_up(self) -> None:
        client = RemoteClient(endpoints=[])
        client.rpc = lambda *_a, **_k: {"status": "status", "pid": 1}  # type: ignore[method-assign]
        assert client.is_up() is True


class TestGetFileGuards:
    @staticmethod
    def _client() -> RemoteClient:
        return RemoteClient(endpoints=[])

    def test_empty_chunk_before_eof_is_an_error(self, tmp_path: Path) -> None:
        client = self._client()
        calls = {"n": 0}

        def _rpc(_request, *, timeout=60.0):
            calls["n"] += 1
            return {"status": "ok", "data": "", "size": 10, "eof": False}

        client._rpc = _rpc  # type: ignore[method-assign]
        target = tmp_path / "x.bin"
        with pytest.raises(RemoteStagingError, match="chunk vazio"):
            client.get_file("a" * 32, "out/x.bin", target)
        assert calls["n"] == 1
        assert not (tmp_path / "x.bin.part").exists()

    def test_invalid_base64_is_an_error(self, tmp_path: Path) -> None:
        client = self._client()
        client._rpc = lambda *_a, **_k: {  # type: ignore[method-assign]
            "status": "ok",
            "data": "!!!não-base64!!!",
            "size": 5,
            "eof": True,
        }
        with pytest.raises(RemoteStagingError, match="base64"):
            client.get_file("a" * 32, "out/x.bin", tmp_path / "x.bin")

    def test_stage_without_root_is_an_error(self) -> None:
        client = self._client()
        client._rpc = lambda *_a, **_k: {"status": "ok", "job": "a" * 32}  # type: ignore[method-assign]
        with pytest.raises(RemoteStagingError, match="root"):
            client.stage()


class TestStagingCleanup:
    def test_legacy_cleans_sandbox_when_upload_fails(self, tmp_path: Path, monkeypatch, specs) -> None:
        first = tmp_path / "a.png"
        second = tmp_path / "b.png"
        first.write_bytes(b"A")
        second.write_bytes(b"B")

        class FailingSecond(FakeClient):
            def put_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
                if rel.endswith("b.png"):
                    raise OSError("ligação caiu a meio do upload")
                return super().put_file(job, rel, local, timeout=timeout)

        client = FailingSecond()
        with pytest.raises(OSError):
            stage_request(
                "text3d",
                {"from_image": str(first), "pose_file": str(second), "output": str(tmp_path / "x.glb")},
                client=client,
            )
        assert client.cleaned is True

    def test_blobs_clean_sandbox_when_prepare_fails(self, tmp_path: Path, monkeypatch, specs) -> None:
        image = tmp_path / "hero.png"
        image.write_bytes(b"PNG")
        client = FakeTransfer()

        def _boom(*_a, **_k):
            raise RemoteStagingError("output inválido")

        monkeypatch.setattr(vramd_remote, "_prepare_outputs", _boom)
        with pytest.raises(RemoteStagingError):
            vramd_remote._stage_blobs("text3d", {"from_image": str(image), "output": str(tmp_path / "x.glb")}, client)
        assert client.cleaned is True

    def test_stage_request_closes_transfer_on_missing_input(self, tmp_path: Path, monkeypatch, specs) -> None:
        client = FakeTransfer()
        monkeypatch.setattr(vramd_remote, "TransferClient", lambda: client)
        with pytest.raises(RemoteStagingError, match="não existe"):
            stage_request(
                "text3d",
                {"from_image": str(tmp_path / "missing.png"), "output": str(tmp_path / "x.glb")},
            )
        assert client.closed is True

    def test_stage_request_closes_transfer_on_legacy_fallback(self, tmp_path: Path, monkeypatch, specs) -> None:
        """Handshake binário recusado: fechar a ligação antes de ir pelo legacy."""
        image = tmp_path / "hero.png"
        image.write_bytes(b"PNG")
        closed: list[bool] = []

        class OldTransfer(FakeTransfer):
            def connect(self) -> dict:
                raise vramd_remote.TransferError("servidor vramd sem protocolo binário/blobs")

            def close(self) -> None:
                closed.append(True)

        monkeypatch.setattr(vramd_remote, "TransferClient", OldTransfer)
        monkeypatch.setattr(vramd_remote, "RemoteClient", lambda: FakeClient())

        stage_request(
            "text3d",
            {"from_image": str(image), "output": str(tmp_path / "hero.glb")},
        )

        assert closed == [True]


class TestFinalizeRobustness:
    def test_missing_optional_sidecar_does_not_lose_output(self, tmp_path: Path, monkeypatch, specs) -> None:
        image = tmp_path / "hero.png"
        image.write_bytes(b"PNG")
        out = tmp_path / "hero.glb"

        class NoSidecar(FakeClient):
            def get_file(self, job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
                if rel.endswith(".omni.json"):
                    raise RemoteStagingError(f"ficheiro não existe no sandbox: {rel}")
                return super().get_file(job, rel, local, timeout=timeout)

        client = NoSidecar()
        client.files["out/hero.glb"] = b"GLB"
        staged = stage_request("text3d", {"from_image": str(image), "output": str(out)}, client=client)

        result = finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.glb"})

        assert result["status"] == "ok"
        assert out.read_bytes() == b"GLB"
        assert not Path(str(out) + ".omni.json").exists()

    def test_error_response_closes_without_cleaning(self, tmp_path: Path, specs) -> None:
        image = tmp_path / "x.png"
        image.write_bytes(b"X")

        class Closable(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.closed = False

            def close(self) -> None:
                self.closed = True

        client = Closable()
        staged = stage_request("text3d", {"from_image": str(image), "output": str(tmp_path / "x.glb")}, client=client)

        finalize(staged, {"status": "error", "error": "oom"})

        assert client.closed is True
        assert client.cleaned is False  # sandbox fica para o TTL

    def test_failed_download_closes_without_cleaning(self, tmp_path: Path, specs) -> None:
        """Falha a meio do download: fechar o socket, mas manter o sandbox."""
        image = tmp_path / "x.png"
        image.write_bytes(b"X")

        class FailingGet(FakeClient):
            def __init__(self) -> None:
                super().__init__()
                self.closed = False

            def get_file(self, _job: str, _rel: str, _local: Path, *, timeout: float = 120.0) -> int:
                raise RemoteStagingError("ligação caiu a meio do get")

            def close(self) -> None:
                self.closed = True

        client = FailingGet()
        staged = stage_request("text3d", {"from_image": str(image), "output": str(tmp_path / "x.glb")}, client=client)

        with pytest.raises(RemoteStagingError, match="caiu"):
            finalize(staged, {"status": "ok", "output": f"{client.root}/out/x.glb"})

        assert client.closed is True
        assert client.cleaned is False  # o job pode ter produzido o resto

    def test_glob_ignores_path_traversal_names(self, tmp_path: Path, monkeypatch) -> None:
        spec = BackendRemoteSpec(inputs=(), outputs=("output",), sidecar_globs=("hero_*",))
        monkeypatch.setattr(vramd_remote, "spec_for", lambda _backend: spec)
        client = FakeClient()
        client.files["out/hero.png"] = b"0"
        client.files["out/hero_1.png"] = b"1"

        def _list(_job: str, _rel: str = "out") -> list[str]:
            return ["../evil", "/etc/passwd", "hero_1.png"]

        fetched: list[str] = []
        original = client.get_file

        def _get(job: str, rel: str, local: Path, *, timeout: float = 120.0) -> int:
            fetched.append(rel)
            return original(job, rel, local, timeout=timeout)

        client.list = _list  # type: ignore[method-assign]
        client.get_file = _get  # type: ignore[method-assign]
        staged = stage_request("text2d", {"output": str(tmp_path / "hero.png")}, client=client)

        finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.png"})

        assert (tmp_path / "hero_1.png").read_bytes() == b"1"
        assert fetched == ["out/hero.png", "out/hero_1.png"]
        assert not (tmp_path.parent / "evil").exists()


class TestPendingRegistry:
    def _dummy(self) -> object:
        class Dummy:
            def __init__(self) -> None:
                self.closed = False
                self.cleaned = False

            def close(self) -> None:
                self.closed = True

            def clean(self, _job: str) -> None:
                self.cleaned = True

        return Dummy()

    def test_same_job_replaces_and_closes_previous(self) -> None:
        first, second = self._dummy(), self._dummy()
        try:
            vramd_remote.remember("job", first)  # type: ignore[arg-type]
            vramd_remote.remember("job", second)  # type: ignore[arg-type]
            assert vramd_remote.peek("job") is second
            assert first.closed is True
            assert first.cleaned is False  # o sandbox do job continua válido
        finally:
            vramd_remote.take("job")

    def test_oldest_is_evicted_at_capacity(self, monkeypatch) -> None:
        monkeypatch.setattr(vramd_remote, "PENDING_MAX", 1)
        first, second = self._dummy(), self._dummy()
        try:
            vramd_remote.remember("job-1", first)  # type: ignore[arg-type]
            vramd_remote.remember("job-2", second)  # type: ignore[arg-type]
            assert vramd_remote.peek("job-1") is None
            assert vramd_remote.peek("job-2") is second
            assert first.closed is True
            assert first.cleaned is False
        finally:
            vramd_remote.take("job-1")
            vramd_remote.take("job-2")


def test_output_basename_collision_gets_unique_remote_paths(tmp_path: Path, monkeypatch, specs) -> None:
    client = FakeClient()
    staged = stage_request(
        "part3d",
        {
            "mesh_path": None,
            "output": str(tmp_path / "a" / "hero.glb"),
            "output_segmented": str(tmp_path / "b" / "hero.glb"),
        },
        client=client,
    )
    rels = [item.remote_rel for item in staged.outputs]
    assert rels == ["out/hero.glb", "out/2-hero.glb"]


def test_stage_dedupes_paths_that_resolve_to_the_same_file(tmp_path: Path, monkeypatch, specs) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "refs").mkdir()
    (tmp_path / "refs" / "a.png").write_bytes(b"A")
    client = FakeClient()
    staged = stage_request(
        "text2d",
        {
            "refs": ["refs/a.png", "refs/../refs/a.png"],
            "output": str(tmp_path / "x.png"),
        },
        client=client,
    )
    assert client.puts == ["in/01-a.png"]
    assert staged.request["refs"] == [f"{client.root}/in/01-a.png"] * 2
