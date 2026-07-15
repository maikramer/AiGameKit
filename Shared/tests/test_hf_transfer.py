"""Testes do enable_hf_fast_download — backend Xet (hf_xet) para downloads HF."""

from __future__ import annotations

from unittest.mock import patch

from gamedev_shared.model_download import enable_hf_fast_download, enable_hf_transfer


class TestEnableHfFastDownload:
    """enable_hf_fast_download: True quando hf_xet está instalado."""

    def test_force_true_without_package(self) -> None:
        import builtins
        import sys

        real_import = builtins.__import__

        def _fake_import(name: str, *args, **kwargs):
            if name == "hf_xet":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_fake_import),
            patch.dict(sys.modules, {"hf_xet": None}),
        ):
            assert enable_hf_fast_download(force=True) is True
            assert enable_hf_fast_download() is False

    def test_returns_false_when_package_missing(self) -> None:
        import builtins
        import sys

        real_import = builtins.__import__

        def _fake_import(name: str, *args, **kwargs):
            if name == "hf_xet":
                raise ImportError("not installed")
            return real_import(name, *args, **kwargs)

        with (
            patch("builtins.__import__", side_effect=_fake_import),
            patch.dict(sys.modules, {"hf_xet": None}),
        ):
            assert enable_hf_fast_download() is False

    def test_returns_true_when_hf_xet_importable(self) -> None:
        import sys
        import types

        fake = types.ModuleType("hf_xet")
        with patch.dict(sys.modules, {"hf_xet": fake}):
            assert enable_hf_fast_download() is True

    def test_legacy_alias_enable_hf_transfer(self) -> None:
        assert enable_hf_transfer(force=True) is True

    def test_idempotent(self) -> None:
        enable_hf_fast_download(force=True)
        enable_hf_fast_download(force=True)
        assert enable_hf_fast_download(force=True) is True
