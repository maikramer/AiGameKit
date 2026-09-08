"""Testes do resolver de pesos (compat v2.1) — torch mockado, sem GPU."""

from __future__ import annotations

import sys
import types


def _fake_torch(monkeypatch, tmp_path):
    """Monta get_dir/load_state_dict_from_url no módulo torch.hub real —
    os `from torch.hub import ...` dentro do weights.py continuam válidos."""
    import torch

    monkeypatch.setattr(torch.hub, "get_dir", lambda: str(tmp_path))
    return torch.hub


def _fake_download(hub, tmp_path, write_files: bool = True):
    def fake_download(url, map_location, progress):
        # torch.hub cacheia em <get_dir()>/checkpoints/<fname>.
        name = url.rsplit("/", 1)[-1]
        if write_files:
            ckpt = tmp_path / "checkpoints"
            ckpt.mkdir(parents=True, exist_ok=True)
            (ckpt / name).write_bytes(b"torch-state")
        return {"fake": "state"}

    hub.load_state_dict_from_url = fake_download


class TestTorchHubDir:
    def test_default_uses_torch_hub_get_dir(self, monkeypatch, tmp_path):
        _fake_torch(monkeypatch, tmp_path)
        from intrinsic_worker.weights import torch_hub_dir

        assert torch_hub_dir() == tmp_path / "checkpoints"

    def test_honours_torch_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TORCH_HOME", str(tmp_path / "custom"))
        _fake_torch(monkeypatch, tmp_path)
        from intrinsic_worker.weights import torch_hub_dir

        assert torch_hub_dir() == tmp_path / "custom" / "hub" / "checkpoints"


class TestDownloadV21:
    def test_downloads_all_five_with_v21_names(self, monkeypatch, tmp_path):
        hub = _fake_torch(monkeypatch, tmp_path)
        _fake_download(hub, tmp_path)
        from intrinsic_worker.weights import download_v21_weights

        paths = download_v21_weights()
        names = [p.name for p in paths]
        assert names == [f"stage_{n}_v21.pt" for n in range(5)]

    def test_raises_when_cache_missing(self, monkeypatch, tmp_path):
        hub = _fake_torch(monkeypatch, tmp_path)
        _fake_download(hub, tmp_path, write_files=False)
        from intrinsic_worker.weights import download_v21_weights

        try:
            download_v21_weights()
            raise AssertionError("esperava RuntimeError")
        except RuntimeError as e:
            assert "não apareceu na cache" in str(e)


class TestLoadModelsCompat:
    def _fake_intrinsic(self, monkeypatch, loader):
        mod = types.ModuleType("intrinsic")
        pipeline = types.SimpleNamespace(load_models=loader)
        monkeypatch.setitem(sys.modules, "intrinsic", mod)
        monkeypatch.setitem(sys.modules, "intrinsic.pipeline", pipeline)

    def test_non_v21_goes_canonical(self, monkeypatch):
        calls: list = []

        def loader(*a, **kw):
            calls.append(("canonical", a, kw))
            return {"models": "ok"}

        self._fake_intrinsic(monkeypatch, loader)
        from intrinsic_worker.weights import load_models_compat

        out = load_models_compat("v2", device="cpu")
        assert out == {"models": "ok"}
        assert calls[0] == ("canonical", ("v2",), {"device": "cpu"})

    def test_v21_canonical_success_no_fallback(self, monkeypatch):
        calls: list = []

        def loader(*a, **kw):
            calls.append(a)
            return {"models": "ok"}

        self._fake_intrinsic(monkeypatch, loader)
        from intrinsic_worker.weights import load_models_compat

        out = load_models_compat("v2.1", device="cuda")
        assert out == {"models": "ok"}
        assert len(calls) == 1

    def test_v21_falls_back_to_file_list_with_alb_residual(self, monkeypatch, tmp_path):
        calls: list = []

        def loader(*a, **kw):
            if isinstance(a[0], str):
                calls.append(("canonical", a, kw))
                raise RuntimeError("HTTP 404")
            calls.append(("list", a, kw))
            return {"models": "fallback"}

        self._fake_intrinsic(monkeypatch, loader)
        hub = _fake_torch(monkeypatch, tmp_path)
        _fake_download(hub, tmp_path)

        from intrinsic_worker.weights import load_models_compat

        out = load_models_compat("v2.1", device="cuda")
        assert out == {"models": "fallback"}
        assert calls[0][0] == "canonical"
        assert calls[1][0] == "list"
        assert len(calls[1][1][0]) == 5
        # alb_residual=True replicado do ramo v2.1 interno.
        assert calls[1][2]["alb_residual"] is True

    def test_v21_double_failure_wraps_message(self, monkeypatch, tmp_path):
        def loader(*a, **kw):
            raise RuntimeError("HTTP 404")

        self._fake_intrinsic(monkeypatch, loader)
        hub = _fake_torch(monkeypatch, tmp_path)

        def also_failing(url, map_location, progress):
            raise OSError("network down")

        monkeypatch.setattr(hub, "load_state_dict_from_url", also_failing)

        from intrinsic_worker.weights import load_models_compat

        try:
            load_models_compat("v2.1", device="cuda")
            raise AssertionError("esperava RuntimeError")
        except RuntimeError as e:
            assert "release='v2'" in str(e)
