"""Testes do batch_guard (VRAM / lock — lock só em Unix)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from gameassets.batch_guard import query_gpu_free_mib, subprocess_gpu_env


def test_subprocess_gpu_env_respects_existing_pytorch_conf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "max_split_size_mb:512")
    env = subprocess_gpu_env()
    assert env["PYTORCH_CUDA_ALLOC_CONF"] == "max_split_size_mb:512"


def test_subprocess_gpu_env_sets_expandable_when_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
    d = subprocess_gpu_env()
    assert "expandable_segments" in d.get("PYTORCH_CUDA_ALLOC_CONF", "")


def test_query_gpu_free_mib_none_without_nvml_or_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("aigamekit_shared.gpu._nvml_init", lambda: False)
    monkeypatch.setattr("aigamekit_shared.gpu.shutil.which", lambda _x: None)
    assert query_gpu_free_mib() is None


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_batch_lock_skip_no_block(tmp_path: Path) -> None:
    from gameassets.batch_guard import batch_directory_lock

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: []\n", encoding="utf-8")
    with batch_directory_lock(manifest, skip=True), batch_directory_lock(manifest, skip=True):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_batch_lock_blocks_second_run_and_records_owner(tmp_path: Path) -> None:
    """O mesmo manifesto (local+remoto, batch+resume) não corre duas vezes."""
    import json

    import click

    from gameassets.batch_guard import batch_directory_lock

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: []\n", encoding="utf-8")
    with batch_directory_lock(manifest, command="resume", mode="remote"):
        meta = json.loads((tmp_path / ".gameassets_batch.lock").read_text(encoding="utf-8"))
        assert meta["command"] == "resume"
        assert meta["mode"] == "remote"
        assert meta["manifest"] == str(manifest.resolve())
        assert meta["host"]
        with (
            pytest.raises(click.ClickException, match="Já existe um batch/resume"),
            batch_directory_lock(manifest, command="batch", mode="local"),
        ):
            pass


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_batch_lock_recovers_dead_pid_on_same_host(tmp_path: Path) -> None:
    import json
    import socket

    from gameassets.batch_guard import batch_directory_lock

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: []\n", encoding="utf-8")
    (tmp_path / ".gameassets_batch.lock").write_text(
        json.dumps({"pid": 99_999_999, "host": socket.gethostname()}), encoding="utf-8"
    )
    with batch_directory_lock(manifest, command="resume"):
        pass


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_batch_lock_overwrites_stale_file_from_other_host(tmp_path: Path) -> None:
    """Ficheiro sem flock vivo (crash) não bloqueia; o metadata é reescrito."""
    import json

    from gameassets.batch_guard import batch_directory_lock

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: []\n", encoding="utf-8")
    (tmp_path / ".gameassets_batch.lock").write_text(
        json.dumps({"pid": 99_999_999, "host": "outra-maquina"}), encoding="utf-8"
    )
    with batch_directory_lock(manifest, command="resume"):
        meta = json.loads((tmp_path / ".gameassets_batch.lock").read_text(encoding="utf-8"))
        assert meta["command"] == "resume"
        assert meta["pid"] == __import__("os").getpid()
    assert not (tmp_path / ".gameassets_batch.lock").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_resume_lock_guard_blocks_and_respects_skip(tmp_path: Path) -> None:
    import click

    from gameassets.batch_guard import batch_directory_lock
    from gameassets.resume_cmd import _manifest_lock_guard

    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("assets:\n  - id: a\n    idea: b\n    pipeline: []\n", encoding="utf-8")

    @_manifest_lock_guard
    def _run(**kwargs: object) -> str:
        return "ok"

    with batch_directory_lock(manifest, command="batch"):
        with pytest.raises(click.ClickException, match="Já existe um batch/resume"):
            _run(manifest_path=manifest, dry_run=False, skip_batch_lock=False)
        assert _run(manifest_path=manifest, dry_run=False, skip_batch_lock=True) == "ok"
        assert _run(manifest_path=manifest, dry_run=True, skip_batch_lock=False) == "ok"
