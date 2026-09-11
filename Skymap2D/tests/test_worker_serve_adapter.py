"""Testes do adapter worker_serve (mapeamento vramd load kwargs → ctor)."""

from __future__ import annotations

from typing import Any

import pytest

from skymap2d.worker_serve_adapter import Adapter


class _FakeGen:
    """Captura os kwargs do ctor em vez de carregar o pipeline."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs

    def warmup(self) -> None:
        return None


@pytest.fixture()
def fake_generator(monkeypatch: pytest.MonkeyPatch) -> type[_FakeGen]:
    monkeypatch.setattr("skymap2d.generator.SkymapGenerator", _FakeGen)
    # O bloco de alloc conf por-request pode escrever PYTORCH_CUDA_ALLOC_CONF
    # diretamente — registar a chave no monkeypatch garante restore no teardown.
    monkeypatch.setenv("PYTORCH_CUDA_ALLOC_CONF", "test")
    return _FakeGen


class TestAdapterLoadGroupOffload:
    def test_allow_group_offload_false_maps_to_ctor_flag(self, fake_generator: type[_FakeGen]) -> None:
        gen = Adapter().load(model_id="some/lora", allow_group_offload=False)
        assert isinstance(gen, _FakeGen)
        assert gen.kwargs["group_offload"] is False
        # A key vramd não vaza para o ctor.
        assert "allow_group_offload" not in gen.kwargs

    def test_allow_group_offload_true_maps_to_ctor_flag(self, fake_generator: type[_FakeGen]) -> None:
        gen = Adapter().load(model_id="some/lora", allow_group_offload=True)
        assert isinstance(gen, _FakeGen)
        assert gen.kwargs["group_offload"] is True

    def test_allow_group_offload_defaults_to_true(self, fake_generator: type[_FakeGen]) -> None:
        gen = Adapter().load(model_id="some/lora")
        assert isinstance(gen, _FakeGen)
        assert gen.kwargs["group_offload"] is True
