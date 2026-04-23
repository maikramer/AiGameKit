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


def test_query_gpu_free_mib_none_without_nvidia_smi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gamedev_shared.gpu.shutil.which", lambda _x: None)
    assert query_gpu_free_mib() is None


@pytest.mark.skipif(sys.platform == "win32", reason="flock lock só em Unix")
def test_batch_lock_skip_no_block(tmp_path: Path) -> None:
    from gameassets.batch_guard import batch_directory_lock

    manifest = tmp_path / "manifest.csv"
    manifest.write_text("id,idea\na,b\n", encoding="utf-8")
    with batch_directory_lock(manifest, skip=True), batch_directory_lock(manifest, skip=True):
        pass
