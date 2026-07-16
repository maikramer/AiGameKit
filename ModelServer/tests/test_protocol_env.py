"""Testes de overrides de env para constantes do protocolo UMS."""

from __future__ import annotations

import importlib

import pytest


class TestProtocolEnvOverrides:
    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GAMEDEV_UMS_MAX_AFFINITY_CUTS", raising=False)
        monkeypatch.delenv("GAMEDEV_UMS_MAX_QUEUE_DEPTH", raising=False)
        monkeypatch.delenv("GAMEDEV_UMS_MAX_INFLIGHT", raising=False)
        import modelserver.protocol as proto

        importlib.reload(proto)
        assert proto.MAX_AFFINITY_CUTS == 3
        assert proto.MAX_QUEUE_DEPTH == 32
        assert proto.MAX_INFLIGHT == 1

    def test_valid_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_MAX_AFFINITY_CUTS", "5")
        monkeypatch.setenv("GAMEDEV_UMS_MAX_QUEUE_DEPTH", "10")
        monkeypatch.setenv("GAMEDEV_UMS_MAX_INFLIGHT", "2")
        import modelserver.protocol as proto

        importlib.reload(proto)
        assert proto.MAX_AFFINITY_CUTS == 5
        assert proto.MAX_QUEUE_DEPTH == 10
        assert proto.MAX_INFLIGHT == 2

    def test_invalid_falls_back_to_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GAMEDEV_UMS_MAX_AFFINITY_CUTS", "abc")
        import modelserver.protocol as proto

        importlib.reload(proto)
        assert proto.MAX_AFFINITY_CUTS == 3

    def teardown_method(self) -> None:
        # Restaurar defaults para não contaminar outros testes no mesmo processo.
        import modelserver.protocol as proto

        importlib.reload(proto)
