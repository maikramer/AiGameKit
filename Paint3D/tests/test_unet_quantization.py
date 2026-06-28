"""Testes dos helpers de quantização do UNet (paths/env) — sem inferência."""

from __future__ import annotations

from pathlib import Path

import pytest

from paint3d.utils.unet_quantization import (
    quantized_unet_artifacts_exist,
    unet_quantized_paths,
    want_quantized_unet,
)


def _make_artifacts(model_dir: Path, st_bytes: int = 1024, map_bytes: int = 256) -> None:
    """Create non-empty quantized-UNet artifacts under ``model_dir/unet/``."""
    st, jm = unet_quantized_paths(model_dir)
    st.parent.mkdir(parents=True, exist_ok=True)
    st.write_bytes(b"\x00" * st_bytes)
    jm.write_bytes(b"\x00" * map_bytes)


class TestUnetQuantizedPaths:
    def test_paths_layout(self, tmp_path: Path) -> None:
        st, jm = unet_quantized_paths(tmp_path)
        assert st == tmp_path / "unet" / "unet-qint8.safetensors"
        assert jm == tmp_path / "unet" / "unet-qint8-quantization_map.json"


class TestArtifactsExist:
    def test_false_when_missing(self, tmp_path: Path) -> None:
        assert quantized_unet_artifacts_exist(tmp_path) is False

    def test_false_when_zero_size(self, tmp_path: Path) -> None:
        _make_artifacts(tmp_path, st_bytes=0, map_bytes=0)
        assert quantized_unet_artifacts_exist(tmp_path) is False

    def test_true_when_both_present_nonempty(self, tmp_path: Path) -> None:
        _make_artifacts(tmp_path)
        assert quantized_unet_artifacts_exist(tmp_path) is True


class TestWantQuantizedUnet:
    @pytest.mark.parametrize("value", ["0", "false", "no", "off"])
    def test_env_disabled_returns_false(self, tmp_path: Path, monkeypatch, value: str) -> None:
        _make_artifacts(tmp_path)
        monkeypatch.setenv("PAINT3D_USE_QUANTIZED_UNET", value)
        assert want_quantized_unet("cuda", tmp_path) is False

    def test_missing_artifacts_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setenv("PAINT3D_USE_QUANTIZED_UNET", "1")
        assert want_quantized_unet("cuda", tmp_path) is False

    def test_env_enabled_with_artifacts_returns_true(self, tmp_path: Path, monkeypatch) -> None:
        _make_artifacts(tmp_path)
        monkeypatch.setenv("PAINT3D_USE_QUANTIZED_UNET", "1")
        assert want_quantized_unet("cuda", tmp_path) is True

    def test_non_cuda_device_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        _make_artifacts(tmp_path)
        monkeypatch.delenv("PAINT3D_USE_QUANTIZED_UNET", raising=False)
        assert want_quantized_unet("cpu", tmp_path) is False

    def test_cuda_unavailable_returns_false(self, tmp_path: Path, monkeypatch) -> None:
        import torch

        _make_artifacts(tmp_path)
        monkeypatch.delenv("PAINT3D_USE_QUANTIZED_UNET", raising=False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
        assert want_quantized_unet("cuda", tmp_path) is False

    def test_auto_enabled_on_low_vram(self, tmp_path: Path, monkeypatch) -> None:
        import torch

        _make_artifacts(tmp_path)
        monkeypatch.delenv("PAINT3D_USE_QUANTIZED_UNET", raising=False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        class _Props:
            total_memory = 6 * (1024**3)  # 6 GiB → below the 10 GiB threshold

        monkeypatch.setattr(torch.cuda, "get_device_properties", lambda _idx: _Props())
        assert want_quantized_unet("cuda", tmp_path) is True

    def test_auto_disabled_on_high_vram(self, tmp_path: Path, monkeypatch) -> None:
        import torch

        _make_artifacts(tmp_path)
        monkeypatch.delenv("PAINT3D_USE_QUANTIZED_UNET", raising=False)
        monkeypatch.setattr(torch.cuda, "is_available", lambda: True)

        class _Props:
            total_memory = 12 * (1024**3)  # 12 GiB → at/above the threshold

        monkeypatch.setattr(torch.cuda, "get_device_properties", lambda _idx: _Props())
        assert want_quantized_unet("cuda", tmp_path) is False
