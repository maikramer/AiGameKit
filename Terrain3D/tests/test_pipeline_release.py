"""Regressão: a pipeline tem de largar a VRAM no fim de cada geração.

``WorldPipeline.close()`` só fecha o tile store e o ficheiro temporário — nunca
toca nos modelos na GPU. Como o pipeline é reconstruído a cada
``generate_terrain``, os pesos do ciclo anterior ficavam presos: medido com
``ums calibrate``, o residente crescia ~651 MiB por geração (4660 → 5578 → 5720
ao fim de três) e o pico de admissão passava o total da placa.
"""

from __future__ import annotations

from terrain3d.generator import _release_pipeline


class FakePipeline:
    """Duplo com a superfície que o release toca."""

    def __init__(self, *, close_raises: bool = False, to_raises: bool = False) -> None:
        self.closed = False
        self.moved_to: str | None = None
        self.coarse_model = object()
        self.base_model = object()
        self.decoder_model = object()
        self.coarse = object()
        self.tile_store = object()
        self._close_raises = close_raises
        self._to_raises = to_raises

    def close(self) -> None:
        if self._close_raises:
            raise RuntimeError("tile store já fechado")
        self.closed = True

    def to(self, device: str) -> FakePipeline:
        if self._to_raises:
            raise RuntimeError("device inválido")
        self.moved_to = device
        return self


class TestReleasePipeline:
    def test_closes_the_pipeline(self):
        pipe = FakePipeline()
        _release_pipeline(pipe)
        assert pipe.closed is True

    def test_moves_weights_off_the_gpu(self):
        """Sem isto, o ``empty_cache`` não tem blocos para devolver."""
        pipe = FakePipeline()
        _release_pipeline(pipe)
        assert pipe.moved_to == "cpu"

    def test_drops_every_model_reference(self):
        pipe = FakePipeline()
        _release_pipeline(pipe)
        for attr in ("coarse_model", "base_model", "decoder_model", "coarse", "tile_store"):
            assert getattr(pipe, attr) is None, attr

    def test_none_pipeline_is_a_noop(self):
        _release_pipeline(None)  # não levanta

    def test_close_failure_still_releases_the_weights(self):
        """Um close partido não pode impedir a devolução da VRAM."""
        pipe = FakePipeline(close_raises=True)
        _release_pipeline(pipe)
        assert pipe.moved_to == "cpu"
        assert pipe.coarse_model is None

    def test_move_failure_still_drops_references(self):
        pipe = FakePipeline(to_raises=True)
        _release_pipeline(pipe)
        assert pipe.base_model is None

    def test_partially_built_pipeline_is_tolerated(self):
        """Falha a meio do load deixa o objeto sem alguns atributos."""

        class Partial:
            def close(self):
                pass

        _release_pipeline(Partial())  # não levanta
