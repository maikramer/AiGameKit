"""Testes do Adapter vramd (contrato JSONL) com modelo falso — sem GPU."""

from __future__ import annotations

import sys
import types

import pytest


@pytest.fixture()
def adapter():
    from intrinsic_worker.worker_serve_adapter import Adapter

    return Adapter()


class _FakeModels(dict):
    pass


class FakePipeline:
    @staticmethod
    def run_pipeline(models, img, device="cuda"):
        import numpy as np

        return {
            "hr_alb": np.zeros_like(img),
            "dif_shd": np.full_like(img, 2.0),
            "pos_res": np.zeros_like(img),
            "residual": np.zeros_like(img),
        }


@pytest.fixture()
def fake_intrinsic(monkeypatch):
    mod = types.ModuleType("intrinsic")
    mod.pipeline = FakePipeline
    monkeypatch.setitem(sys.modules, "intrinsic", mod)
    monkeypatch.setitem(sys.modules, "intrinsic.pipeline", FakePipeline)


class TestAdapterContract:
    def test_name_is_intrinsic(self, adapter):
        assert adapter.name == "intrinsic"

    def test_generate_requires_fields(self, adapter, fake_intrinsic, tmp_path):
        import numpy as np
        from PIL import Image

        Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(tmp_path / "a.png")
        model = {"models": {}}
        # Falta output_dir.
        resp = adapter.generate(model, {"image_path": str(tmp_path / "a.png")})
        assert resp["status"] == "error"
        assert "output_dir" in resp["error"]

    def test_generate_ok_shape(self, adapter, fake_intrinsic, tmp_path):
        import numpy as np
        from PIL import Image

        Image.fromarray(np.zeros((4, 4, 3), dtype=np.uint8)).save(tmp_path / "a.png")
        model = {"models": {}}
        resp = adapter.generate(
            model,
            {"image_path": str(tmp_path / "a.png"), "output_dir": str(tmp_path)},
        )
        assert resp["status"] == "ok"
        assert resp["output"].endswith("a_albedo.png")
        assert resp["output_shading"].endswith("a_shading.png")
        assert resp["output_specular"].endswith("a_specular.png")
        assert resp["seconds"] >= 0.0

    def test_unload_clears_dict(self, adapter):
        model = _FakeModels(models=1)
        adapter.unload(model)
        assert len(model) == 0

    def test_unload_tolerates_none(self, adapter):
        adapter.unload(None)

    def test_load_uses_release_kwarg(self, adapter, monkeypatch):
        captured = {}

        class FakeLoader:
            @staticmethod
            def load_models(release, device="cuda"):
                captured["release"] = release
                captured["device"] = device
                return {"net": object()}

        mod = types.ModuleType("intrinsic")
        mod.pipeline = FakeLoader
        monkeypatch.setitem(sys.modules, "intrinsic", mod)
        monkeypatch.setitem(sys.modules, "intrinsic.pipeline", FakeLoader)

        model = adapter.load(release="v2")
        assert captured == {"release": "v2", "device": "cuda"}
        assert "models" in model and model["release"] == "v2"


class TestWorkerLoopWiring:
    def test_serve_entrypoint_importable(self):
        from intrinsic_worker.cli import cli

        assert cli is not None

    def test_adapter_module_exposes_adapter_class(self):
        from intrinsic_worker.worker_serve_adapter import Adapter

        from aigamekit_shared.worker_serve_adapter_base import WorkerAdapter

        assert issubclass(Adapter, WorkerAdapter)
