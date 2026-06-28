"""Testes do preflight de download (snapshot_download mockado, sem rede)."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from gamedev_shared import model_download


@pytest.fixture
def fake_hub(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Injeta um ``huggingface_hub`` falso com ``snapshot_download`` mockado."""
    mod = types.ModuleType("huggingface_hub")
    utils = types.ModuleType("huggingface_hub.utils")

    class LocalEntryNotFoundError(Exception):
        pass

    utils.LocalEntryNotFoundError = LocalEntryNotFoundError  # type: ignore[attr-defined]
    snap = MagicMock(return_value="/cache/models/fake")
    mod.snapshot_download = snap  # type: ignore[attr-defined]
    mod.utils = utils  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "huggingface_hub", mod)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", utils)
    return snap


class TestEnsureModel:
    def test_downloads_returns_path(self, fake_hub: MagicMock) -> None:
        path = model_download.ensure_model("org/model")
        assert path == Path("/cache/models/fake")
        # snapshot_download chamado para o download real (resume é automático no hub).
        assert fake_hub.called

    def test_status_callback_invoked(self, fake_hub: MagicMock) -> None:
        msgs: list[str] = []
        model_download.ensure_model("org/model", on_status=msgs.append)
        assert any("org/model" in m for m in msgs)

    def test_allow_patterns_forwarded(self, fake_hub: MagicMock) -> None:
        model_download.ensure_model("org/model", allow_patterns=["*.safetensors"])
        # call_args = a última chamada (o download), não a verificação de cache.
        _, kwargs = fake_hub.call_args
        assert kwargs["allow_patterns"] == ["*.safetensors"]


class TestIsModelCached:
    def test_true_when_local_snapshot_exists(self, fake_hub: MagicMock) -> None:
        fake_hub.return_value = "/cache/models/fake"
        assert model_download.is_model_cached("org/model") is True
        _, kwargs = fake_hub.call_args
        assert kwargs["local_files_only"] is True

    def test_false_when_not_cached(self, fake_hub: MagicMock) -> None:
        from huggingface_hub.utils import LocalEntryNotFoundError

        fake_hub.side_effect = LocalEntryNotFoundError("missing")
        assert model_download.is_model_cached("org/model") is False
