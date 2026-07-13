"""Testes do enable_hf_transfer — ativação do backend Rust para downloads HF."""

from __future__ import annotations

import os
from unittest.mock import patch

from gamedev_shared.model_download import enable_hf_transfer


class TestEnableHfTransfer:
    """enable_hf_transfer: ativa HF_HUB_ENABLE_HF_TRANSFER=1 quando hf_transfer está instalado."""

    def test_already_active_returns_true(self) -> None:
        with patch.dict("os.environ", {"HF_HUB_ENABLE_HF_TRANSFER": "1"}):
            assert enable_hf_transfer() is True

    def test_activates_when_package_present(self) -> None:
        """hf_transfer está instalado no venv de teste → ativa."""
        with patch.dict("os.environ", {}, clear=True):
            result = enable_hf_transfer()
            # hf_transfer 0.1.9 está instalado neste venv.
            assert result is True
            assert os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1"

    def test_force_activates_even_without_package(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            result = enable_hf_transfer(force=True)
            assert result is True
            assert os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1"

    def test_returns_false_when_package_missing(self) -> None:
        """Sem hf_transfer instalado e sem force → não ativa (evita erro no download)."""
        import builtins
        import sys

        real_import = builtins.__import__

        def _fake_import(name: str, *args, **kwargs):
            if name == "hf_transfer":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with (
            patch.dict("os.environ", {}, clear=True),
            patch("builtins.__import__", side_effect=_fake_import),
            patch.dict(sys.modules, {"hf_transfer": None}),
        ):
            result = enable_hf_transfer()
            assert result is False
            assert os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") != "1"

    def test_idempotent(self) -> None:
        """Chamar múltiplas vezes não causa problemas."""
        with patch.dict("os.environ", {}, clear=True):
            enable_hf_transfer()
            enable_hf_transfer()
            enable_hf_transfer()
            assert os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1"
