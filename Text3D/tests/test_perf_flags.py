"""Testes das flags de aceleração de inferência (volume decoder, mc_algo, compile, sage-attn)."""

from __future__ import annotations

from typing import Any, ClassVar

import pytest
import trimesh
from click.testing import CliRunner
from PIL import Image

from text3d.cli import cli
from text3d.generator import HunyuanTextTo3DGenerator

# ---------------------------------------------------------------------------
# Validação do construtor
# ---------------------------------------------------------------------------


def test_invalid_volume_decoder_raises() -> None:
    with pytest.raises(ValueError, match="volume_decoder"):
        HunyuanTextTo3DGenerator(device="cpu", volume_decoder="turbo")


def test_invalid_mc_algo_raises() -> None:
    with pytest.raises(ValueError, match="mc_algo"):
        HunyuanTextTo3DGenerator(device="cpu", mc_algo="marching")


def test_defaults_preserve_original_behavior() -> None:
    gen = HunyuanTextTo3DGenerator(device="cpu")
    assert gen.volume_decoder == "vanilla"
    assert gen.mc_algo is None
    assert gen.compile_models is False
    assert gen.sage_attention is False
    assert gen.sdnq_quantized_matmul is False


def test_sage_attention_falls_back_without_package(monkeypatch) -> None:
    """Sem o pacote sageattention instalado, a flag desactiva-se com aviso."""
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "sageattention":
            raise ImportError("not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.delenv("CA_USE_SAGEATTN", raising=False)
    gen = HunyuanTextTo3DGenerator(device="cpu", sage_attention=True)
    assert gen.sage_attention is False


# ---------------------------------------------------------------------------
# _configure_acceleration
# ---------------------------------------------------------------------------


class _FakeVAE:
    def __init__(self) -> None:
        self.flashvdm_kwargs: dict[str, Any] | None = None
        self.surface_extractor: Any = None

    def enable_flashvdm_decoder(self, **kwargs: Any) -> None:
        self.flashvdm_kwargs = kwargs


class _FakePipe:
    def __init__(self) -> None:
        self.vae = _FakeVAE()
        self.compiled = False
        self.device = "cpu"

    def compile(self) -> None:
        self.compiled = True

    def __call__(self, **kwargs: Any) -> trimesh.Trimesh:
        return trimesh.creation.box()


def test_vanilla_decoder_touches_nothing() -> None:
    gen = HunyuanTextTo3DGenerator(device="cpu")
    pipe = _FakePipe()
    gen._configure_acceleration(pipe)
    assert pipe.vae.flashvdm_kwargs is None
    assert pipe.vae.surface_extractor is None
    assert pipe.compiled is False


def test_hierarchical_decoder_disables_adaptive_kv() -> None:
    gen = HunyuanTextTo3DGenerator(device="cpu", volume_decoder="hierarchical")
    pipe = _FakePipe()
    gen._configure_acceleration(pipe)
    assert pipe.vae.flashvdm_kwargs is not None
    assert pipe.vae.flashvdm_kwargs["adaptive_kv_selection"] is False
    assert pipe.vae.flashvdm_kwargs["mc_algo"] == "mc"


def test_flashvdm_decoder_enables_adaptive_kv() -> None:
    gen = HunyuanTextTo3DGenerator(device="cpu", volume_decoder="flashvdm")
    pipe = _FakePipe()
    gen._configure_acceleration(pipe)
    assert pipe.vae.flashvdm_kwargs is not None
    assert pipe.vae.flashvdm_kwargs["adaptive_kv_selection"] is True


def test_dmc_on_cpu_falls_back_to_mc() -> None:
    pytest.importorskip("skimage")
    from text3d.hy3dshape.models.autoencoders.surface_extractors import MCSurfaceExtractor

    gen = HunyuanTextTo3DGenerator(device="cpu", mc_algo="dmc")
    pipe = _FakePipe()
    gen._configure_acceleration(pipe)
    assert isinstance(pipe.vae.surface_extractor, MCSurfaceExtractor)


def test_compile_flag_calls_pipe_compile() -> None:
    gen = HunyuanTextTo3DGenerator(device="cpu", compile_models=True)
    pipe = _FakePipe()
    gen._configure_acceleration(pipe)
    assert pipe.compiled is True


# ---------------------------------------------------------------------------
# Cache do BiRefNet em modo batch (keep_loaded)
# ---------------------------------------------------------------------------


class _FakeBGRemover:
    instances: ClassVar[list[_FakeBGRemover]] = []

    def __init__(self, device: str | None = None) -> None:
        self.unloaded = False
        _FakeBGRemover.instances.append(self)

    def remove_background(self, image: Image.Image) -> Image.Image:
        return image.convert("RGBA")

    def unload(self) -> None:
        self.unloaded = True


@pytest.fixture()
def _bg_generator(monkeypatch) -> HunyuanTextTo3DGenerator:
    _FakeBGRemover.instances = []
    monkeypatch.setattr("text3d.generator.BiRefNetBGRemover", _FakeBGRemover)
    gen = HunyuanTextTo3DGenerator(device="cpu")
    monkeypatch.setattr(gen, "_load_hunyuan", lambda: _FakePipe())
    return gen


def _white_image() -> Image.Image:
    return Image.new("RGB", (16, 16), color=(255, 255, 255))


def test_bg_remover_reused_with_keep_loaded(_bg_generator) -> None:
    gen = _bg_generator
    gen.generate_from_image(_white_image(), keep_loaded=True)
    gen.generate_from_image(_white_image(), keep_loaded=True)
    assert len(_FakeBGRemover.instances) == 1
    assert _FakeBGRemover.instances[0].unloaded is False

    gen.unload_hunyuan()
    assert _FakeBGRemover.instances[0].unloaded is True
    assert gen._bg_remover is None


def test_bg_remover_unloaded_without_keep_loaded(_bg_generator) -> None:
    gen = _bg_generator
    gen.generate_from_image(_white_image(), keep_loaded=False)
    assert len(_FakeBGRemover.instances) == 1
    assert _FakeBGRemover.instances[0].unloaded is True
    assert gen._bg_remover is None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_generate_help_lists_perf_flags() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "--help"])
    assert r.exit_code == 0
    for flag in ("--volume-decoder", "--mc-algo", "--compile", "--sage-attn", "--sdnq-matmul"):
        assert flag in r.output


def test_generate_batch_help_lists_perf_flags() -> None:
    runner = CliRunner()
    r = runner.invoke(cli, ["generate-batch", "--help"])
    assert r.exit_code == 0
    for flag in ("--volume-decoder", "--mc-algo", "--compile", "--sage-attn", "--sdnq-matmul"):
        assert flag in r.output
