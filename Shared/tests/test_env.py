"""Testes para gamedev_shared.env."""

import os
import subprocess
from unittest.mock import MagicMock, patch

from gamedev_shared.env import (
    TOOL_BINS,
    detect_low_vram,
    ensure_pytorch_cuda_alloc_conf,
    get_tool_bin,
    subprocess_gpu_env,
)


class TestEnsurePytorchCudaAllocConf:
    def test_sets_if_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            ensure_pytorch_cuda_alloc_conf()
            assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "expandable_segments:True"

    def test_preserves_existing(self):
        with patch.dict(os.environ, {"PYTORCH_CUDA_ALLOC_CONF": "custom:True"}, clear=True):
            ensure_pytorch_cuda_alloc_conf()
            assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "custom:True"

    def test_custom_value(self):
        with patch.dict(os.environ, {}, clear=True):
            ensure_pytorch_cuda_alloc_conf("max_split_size_mb:128")
            assert os.environ["PYTORCH_CUDA_ALLOC_CONF"] == "max_split_size_mb:128"


class TestSubprocessGpuEnv:
    def test_includes_alloc_conf(self):
        with patch.dict(os.environ, {}, clear=True):
            env = subprocess_gpu_env()
            assert "PYTORCH_CUDA_ALLOC_CONF" in env

    def test_preserves_existing(self):
        with patch.dict(os.environ, {"PYTORCH_CUDA_ALLOC_CONF": "custom"}, clear=True):
            env = subprocess_gpu_env()
            assert env["PYTORCH_CUDA_ALLOC_CONF"] == "custom"

    def test_extra_env(self):
        with patch.dict(os.environ, {}, clear=True):
            env = subprocess_gpu_env({"MY_VAR": "123"})
            assert env["MY_VAR"] == "123"


class TestGetToolBin:
    def test_defined(self):
        with patch.dict(os.environ, {"TEXT2D_BIN": "/usr/bin/text2d"}):
            assert get_tool_bin("text2d") == "/usr/bin/text2d"

    def test_empty(self):
        with patch.dict(os.environ, {"TEXT2D_BIN": ""}):
            assert get_tool_bin("text2d") is None

    def test_not_defined(self):
        with patch.dict(os.environ, {}, clear=True):
            assert get_tool_bin("text2d") is None

    def test_unknown_tool(self):
        assert get_tool_bin("naoexiste") is None

    def test_skymap2d_rigging3d_gameassets_gamedevlab(self):
        with patch.dict(
            os.environ,
            {
                "SKYMAP2D_BIN": "/x/skymap2d",
                "RIGGING3D_BIN": "/x/rigging3d",
                "GAMEASSETS_BIN": "/x/gameassets",
                "GAMEDEVLAB_BIN": "/x/gamedev-lab",
            },
        ):
            assert get_tool_bin("skymap2d") == "/x/skymap2d"
            assert get_tool_bin("rigging3d") == "/x/rigging3d"
            assert get_tool_bin("gameassets") == "/x/gameassets"
            assert get_tool_bin("gamedevlab") == "/x/gamedev-lab"


class TestDetectLowVram:
    def test_returns_false_when_no_nvidia_smi(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(side_effect=FileNotFoundError("nvidia-smi not found")),
        )
        assert detect_low_vram() is False

    def test_returns_true_below_threshold(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=MagicMock(stdout="4096\n", stderr="")),
        )
        assert detect_low_vram(threshold_mb=8192) is True

    def test_returns_false_above_threshold(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=MagicMock(stdout="12288\n", stderr="")),
        )
        assert detect_low_vram(threshold_mb=8192) is False

    def test_returns_false_on_parse_error(self, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=MagicMock(stdout="not_a_number\n", stderr="")),
        )
        assert detect_low_vram() is False


class TestToolBins:
    def test_mapping(self):
        assert TOOL_BINS["text2d"] == "TEXT2D_BIN"
        assert TOOL_BINS["text3d"] == "TEXT3D_BIN"
        assert TOOL_BINS["text2sound"] == "TEXT2SOUND_BIN"
        assert TOOL_BINS["texture2d"] == "TEXTURE2D_BIN"
        assert TOOL_BINS["skymap2d"] == "SKYMAP2D_BIN"
        assert TOOL_BINS["rigging3d"] == "RIGGING3D_BIN"
        assert TOOL_BINS["gameassets"] == "GAMEASSETS_BIN"
        assert TOOL_BINS["gamedevlab"] == "GAMEDEVLAB_BIN"
        assert TOOL_BINS["materialize"] == "MATERIALIZE_BIN"
        assert TOOL_BINS["vibegame"] == "VIBEGAME_BIN"
