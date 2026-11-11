"""Testes unitários do módulo generator (sem carregar modelo)."""

from __future__ import annotations

import pytest

from text2d.generator import default_model_id


def test_default_model_id_respects_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEXT2D_MODEL_ID", "org/custom-model")
    assert default_model_id() == "org/custom-model"


def test_default_model_id_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXT2D_MODEL_ID", raising=False)
    mid = default_model_id()
    assert "FLUX" in mid or "klein" in mid.lower() or "Disty0" in mid


def test_torch_dtype_cpu() -> None:
    from text2d.generator import _torch_dtype_for

    assert _torch_dtype_for("cpu") == __import__("torch").float32


class TestResolveRefImages:
    """Referências do workflow image_conditioned (sem modelo/GPU)."""

    @staticmethod
    def _gen() -> object:
        from text2d.generator import KleinFluxGenerator

        # O método não usa estado da instância; evita-se o __init__ pesado.
        return object.__new__(KleinFluxGenerator)

    def test_single_path_string_is_not_iterated(self, tmp_path) -> None:
        from PIL import Image

        path = tmp_path / "ref.png"
        Image.new("RGB", (4, 4), (10, 20, 30)).save(path)
        refs = self._gen()._resolve_ref_images(str(path))
        assert refs is not None and len(refs) == 1
        assert refs[0].getpixel((0, 0)) == (10, 20, 30)

    def test_limit_is_checked_before_opening(self, tmp_path) -> None:
        from text2d.generator import MAX_REFS

        missing = [tmp_path / f"ref_{i}.png" for i in range(MAX_REFS + 1)]
        with pytest.raises(ValueError, match="Máximo de"):
            self._gen()._resolve_ref_images(missing)

    def test_empty_refs_returns_none(self) -> None:
        assert self._gen()._resolve_ref_images([]) is None
        assert self._gen()._resolve_ref_images(None) is None

    def test_path_is_converted_and_fd_closed(self, tmp_path) -> None:
        import gc

        from PIL import Image

        path = tmp_path / "ref.png"
        Image.new("RGB", (4, 4), (1, 2, 3)).save(path)
        refs = self._gen()._resolve_ref_images([path])
        assert refs is not None and refs[0].mode == "RGB"
        gc.collect()
        # Se o handle tivesse ficado aberto, o unlink falhava em Windows.
        path.unlink()


class TestFlattenReference:
    def test_transparent_background_becomes_white(self) -> None:
        """``convert("RGB")`` num RGBA transparente guardava os RGB (0,0,0)."""
        from PIL import Image

        from text2d.generator import _flatten_reference

        ref = Image.new("RGBA", (4, 4), (0, 0, 0, 0))
        flat = _flatten_reference(ref)
        assert flat.mode == "RGB"
        assert flat.getpixel((0, 0)) == (255, 255, 255)

    def test_opaque_pixels_are_preserved(self) -> None:
        from PIL import Image

        from text2d.generator import _flatten_reference

        ref = Image.new("RGBA", (4, 4), (200, 30, 10, 255))
        assert _flatten_reference(ref).getpixel((0, 0)) == (200, 30, 10)


class TestRuntimeQuantize:
    """Falha de quantização tem de chegar ao planner (senão planeia a menos)."""

    @staticmethod
    def _gen() -> object:
        from text2d.generator import KleinFluxGenerator

        dummy = object.__new__(KleinFluxGenerator)
        dummy.device = "cpu"
        dummy.upsample = False
        dummy._log = lambda *_a, **_k: None
        dummy._clear_cache = lambda: None
        return dummy

    def test_all_components_quantized(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        import aigamekit_shared.sdnq as sdnq

        monkeypatch.setattr(sdnq, "quantize_model", lambda mod, *_a, **_k: "quant")
        pipe = SimpleNamespace(transformer=object(), text_encoder=object())
        assert self._gen()._runtime_quantize(pipe, "sdnq-int8") is True
        assert pipe.transformer == "quant" and pipe.text_encoder == "quant"

    def test_transformer_failure_reports_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from types import SimpleNamespace

        import aigamekit_shared.sdnq as sdnq

        def _quantize(mod, *_a, **_k):
            if mod == "transformer":
                raise RuntimeError("camada não suportada")
            return "quant"

        monkeypatch.setattr(sdnq, "quantize_model", _quantize)
        pipe = SimpleNamespace(transformer="transformer", text_encoder="te")
        assert self._gen()._runtime_quantize(pipe, "sdnq-int8") is False
        # O text_encoder segue quantizado; só o footprint do transformer importa.
        assert pipe.text_encoder == "quant"


class TestWorkerAdapterGenerate:
    """Contrato do adapter text2d do worker (sem modelo)."""

    @staticmethod
    def _model(images: list) -> object:
        class _Model:
            memory_efficient = False
            group_offload = False
            quant_preset = None
            model_id = "test/model"

            def generate(self, **_kwargs) -> tuple:
                return images[0], {"images": list(images)}

        return _Model()

    def test_webp_output_is_saved_as_webp(self, tmp_path) -> None:
        from PIL import Image

        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "icon.webp"
        res = Adapter().generate(
            self._model([Image.new("RGB", (4, 4), (1, 2, 3))]),
            {"prompt": "x", "output": str(out)},
        )
        assert res["status"] == "ok"
        assert res["output"] == str(out)
        assert Image.open(out).format == "WEBP"

    def test_num_images_reports_primary_path(self, tmp_path) -> None:
        from PIL import Image

        from text2d.worker_serve_adapter import Adapter

        out = tmp_path / "shot.png"
        images = [Image.new("RGB", (4, 4), (i, i, i)) for i in range(3)]
        res = Adapter().generate(self._model(images), {"prompt": "x", "output": str(out), "num_images": 3})
        assert res["status"] == "ok"
        # ``output`` tem de ser o path pedido, não a última parte ``shot_2.png``.
        assert res["output"] == str(out)
        assert out.is_file()
        assert (tmp_path / "shot_2.png").is_file()

    def test_transparent_rejects_jpeg(self, tmp_path) -> None:
        from PIL import Image

        from text2d.worker_serve_adapter import Adapter

        res = Adapter().generate(
            self._model([Image.new("RGB", (4, 4))]),
            {"prompt": "x", "output": str(tmp_path / "icon.jpg"), "transparent": True},
        )
        assert res["status"] == "error"
        assert "png" in res["error"]


class TestWorkerAdapterUnload:
    def test_cache_released_when_unload_raises(self, monkeypatch) -> None:
        import torch

        from text2d.worker_serve_adapter import Adapter

        calls: list[str] = []
        monkeypatch.setattr(torch.cuda, "empty_cache", lambda: calls.append("empty"))
        monkeypatch.setattr(torch.cuda, "ipc_collect", lambda: calls.append("ipc"))

        class _Model:
            def unload(self) -> None:
                raise RuntimeError("teardown falhou")

        Adapter().unload(_Model())
        assert calls == ["empty", "ipc"]
