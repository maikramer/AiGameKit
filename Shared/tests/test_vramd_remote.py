"""Staging de ficheiros do vramd remoto (sem rede: cliente falso)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from aigamekit_shared import vramd_remote
from aigamekit_shared.vramd_remote import (
    BackendRemoteSpec,
    RemoteStagingError,
    finalize,
    load_specs,
    spec_for,
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
            name[len(prefix) :]
            for name in self.files
            if name.startswith(prefix) and "/" not in name[len(prefix) :]
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

    staged = stage_request(
        "text3d", {"from_image": str(image), "output": str(out)}, client=client
    )
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

    staged = stage_request(
        "text2d", {"prompt": "hero", "refs": [str(ref)], "output": str(out)}, client=client
    )
    finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.png"})

    assert out.read_bytes() == b"0"
    assert (tmp_path / "hero_1.png").read_bytes() == b"1"
    assert (tmp_path / "hero_2.png").read_bytes() == b"2"
    assert not (tmp_path / "hero_notes.txt").exists()


def test_finalize_error_keeps_sandbox(tmp_path: Path, specs) -> None:
    image = tmp_path / "x.png"
    image.write_bytes(b"X")
    client = FakeClient()
    staged = stage_request(
        "text3d", {"from_image": str(image), "output": str(tmp_path / "x.glb")}, client=client
    )
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
            name[len(prefix) :]
            for name in self.files
            if name.startswith(prefix) and "/" not in name[len(prefix) :]
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

    staged = vramd_remote._stage_blobs(
        "text3d", {"from_image": str(image), "output": str(out)}, client
    )
    result = finalize(staged, {"status": "ok", "output": f"{client.root}/out/hero.glb"})

    assert result["output"] == str(out)
    assert out.read_bytes() == b"GLB"
    assert Path(str(out) + ".omni.json").read_bytes() == b"{}"
    assert client.cleaned is True
    assert client.closed is True


def test_stage_request_falls_back_to_legacy_on_old_server(
    tmp_path: Path, monkeypatch, specs
) -> None:
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
