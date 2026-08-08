"""Regressão: limpeza de VRAM com device indexado e áudio fora da GPU.

Defeitos medidos com ``ums calibrate`` (residente a crescer entre corridas):

1. o ``empty_cache`` corria com o tensor do job ainda vivo, portanto não tinha
   blocos para devolver;
2. o guard usava igualdade exata com ``"cuda"`` — com ``device="cuda:0"`` a
   limpeza ficava **silenciosamente desligada**, ao contrário do resto do
   ficheiro, que já usava ``startswith``;
3. o VAE do ``stable-audio-tools`` usa ``weight_norm``: cada decode reescreve
   ``module.weight`` e o allocator acumula ~34 MiB/run — ``_fuse_weight_norm``
   no ``load`` congela os pesos.
"""

from __future__ import annotations

import inspect

import torch
from torch import nn
from torch.nn.utils import weight_norm

from text2sound.generator import AudioGenerator


class TestDeviceGuards:
    def test_indexed_device_still_clears_vram(self):
        """``cuda:0`` é tão CUDA como ``cuda`` — o guard não pode discriminar."""
        gen = AudioGenerator.__new__(AudioGenerator)
        gen._device = "cuda:0"
        gen._auto_clear = True
        cleared: list[bool] = []
        gen._clear_cuda = lambda: cleared.append(True)  # type: ignore[method-assign]

        with gen._generation_context():
            pass
        assert cleared == [True]

    def test_plain_cuda_device_still_clears(self):
        gen = AudioGenerator.__new__(AudioGenerator)
        gen._device = "cuda"
        gen._auto_clear = True
        cleared: list[bool] = []
        gen._clear_cuda = lambda: cleared.append(True)  # type: ignore[method-assign]

        with gen._generation_context():
            pass
        assert cleared == [True]

    def test_cpu_device_does_not_clear(self):
        gen = AudioGenerator.__new__(AudioGenerator)
        gen._device = "cpu"
        gen._auto_clear = True
        cleared: list[bool] = []
        gen._clear_cuda = lambda: cleared.append(True)  # type: ignore[method-assign]

        with gen._generation_context():
            pass
        assert cleared == []

    def test_auto_clear_off_disables_the_clear(self):
        gen = AudioGenerator.__new__(AudioGenerator)
        gen._device = "cuda:1"
        gen._auto_clear = False
        cleared: list[bool] = []
        gen._clear_cuda = lambda: cleared.append(True)  # type: ignore[method-assign]

        with gen._generation_context():
            pass
        assert cleared == []

    def test_no_exact_cuda_comparisons_remain(self):
        """Qualquer `== "cuda"` novo reintroduz o bug para devices indexados."""
        source = inspect.getsource(AudioGenerator)
        assert '_device == "cuda"' not in source


class TestAudioLeavesTheGpu:
    def test_generate_moves_audio_to_cpu_before_clearing(self):
        """O tensor do job tem de morrer *dentro* do contexto que limpa a cache."""
        source = inspect.getsource(AudioGenerator.generate)
        move_idx = source.index('.to("cpu")')
        del_idx = source.index("del output")
        assert move_idx < del_idx, "o áudio deve ir para CPU antes de largar o tensor CUDA"
        # Ambos dentro do ``with self._generation_context():``.
        ctx_idx = source.index("with self._generation_context():")
        assert ctx_idx < move_idx


class TestFuseWeightNorm:
    def test_fuse_removes_hooks_and_keeps_forward(self):
        """Hook WeightNorm some; Conv continua a produzir o mesmo shape."""
        conv = weight_norm(nn.Conv1d(4, 8, kernel_size=3, padding=1))
        assert any(type(h).__name__ == "WeightNorm" for h in conv._forward_pre_hooks.values())
        x = torch.zeros(1, 4, 16)
        before = conv(x).detach().clone()

        fused = AudioGenerator._fuse_weight_norm(conv)
        assert fused == 1
        assert not any(type(h).__name__ == "WeightNorm" for h in conv._forward_pre_hooks.values())
        assert "weight" in conv._parameters
        after = conv(x)
        assert after.shape == before.shape
        assert torch.allclose(before, after)

    def test_fuse_is_idempotent(self):
        conv = weight_norm(nn.Conv1d(2, 2, kernel_size=1))
        assert AudioGenerator._fuse_weight_norm(conv) == 1
        assert AudioGenerator._fuse_weight_norm(conv) == 0

    def test_load_calls_fuse_weight_norm(self):
        """O caminho de load tem de fundir — senão a fuga de 34 MiB/run volta."""
        source = inspect.getsource(AudioGenerator.load)
        assert "_fuse_weight_norm" in source
