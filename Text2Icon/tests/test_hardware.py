"""Testes da auto-detecção de hardware do Text2Icon (Sana Sprint 0.6B)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from click.testing import CliRunner

from text2icon.cli import cli
from text2icon.hardware import (
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    GIB,
    Text2IconHardwareProfile,
    detect_hardware_profile,
    hw_auto_enabled,
    profile_from_specs,
)


def _gib(n: float) -> int:
    return int(n * GIB)


def test_no_gpu_cpu_profile() -> None:
    p = profile_from_specs([])
    assert p.device == "cpu"
    assert p.cpu_offload is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_10gb_full_gpu_no_offload() -> None:
    """Clark Air Sana ~6 GB fp16 cabe folgado em 10 GiB."""
    p = profile_from_specs([(0, _gib(10))])
    assert p.device == "cuda"
    assert p.cpu_offload is False
    assert p.max_width is None
    assert p.max_height is None


def test_8gb_full_gpu_no_offload() -> None:
    """8 GiB ainda cabe o pipeline completo (~6 GB) sem offload."""
    p = profile_from_specs([(0, _gib(8))])
    assert p.device == "cuda"
    assert p.cpu_offload is False
    assert p.max_width is None
    assert p.max_height is None


def test_6gb_offload_keep_resolution() -> None:
    """6 GiB: transformer+Gemma cabem mas VAE precisa de espaço → offload."""
    p = profile_from_specs([(0, _gib(6))])
    assert p.device == "cuda"
    assert p.cpu_offload is True
    assert p.max_width is None
    assert p.max_height is None


def test_4gb_offload_clamp_512() -> None:
    """4 GiB: offload + clamp à resolução nativa 512x512."""
    p = profile_from_specs([(0, _gib(4))])
    assert p.device == "cuda"
    assert p.cpu_offload is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_2gb_offload_clamp_512() -> None:
    p = profile_from_specs([(0, _gib(2))])
    assert p.device == "cuda"
    assert p.cpu_offload is True
    assert p.max_width == 512
    assert p.max_height == 512


def test_dual_gpu_sets_gpu_ids() -> None:
    p = profile_from_specs([(0, _gib(8)), (1, _gib(8))])
    assert p.device == "cuda"
    assert p.cpu_offload is False
    assert p.gpu_ids == [0, 1]
    assert p.total_vram_gib == 16.0


def test_dual_small_gpu_clamp_and_ids() -> None:
    p = profile_from_specs([(0, _gib(2)), (1, _gib(2))])
    assert p.cpu_offload is True
    assert p.max_width == 512
    assert p.max_height == 512
    assert p.gpu_ids == [0, 1]


def test_detect_returns_profile() -> None:
    assert isinstance(detect_hardware_profile(), Text2IconHardwareProfile)


def test_env_kill_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TEXT2ICON_HW_AUTO", raising=False)
    assert hw_auto_enabled() is True
    monkeypatch.setenv("TEXT2ICON_HW_AUTO", "0")
    assert hw_auto_enabled() is False


def test_summary_contains_name() -> None:
    p = profile_from_specs([(0, _gib(8))])
    assert "cuda-1x8g" in p.summary()


@pytest.mark.parametrize("command", ["generate", "batch"])
def test_cli_exposes_hw_auto_flag(command: str) -> None:
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--hw-auto" in r.output


def test_hw_auto_clamps_higher_resolution() -> None:
    """hw-auto must clamp resolution down to max_width/max_height on small GPUs."""
    p = profile_from_specs([(0, _gib(2))])
    assert p.max_width is not None
    assert p.max_width <= DEFAULT_WIDTH
    assert p.max_height is not None
    assert p.max_height <= DEFAULT_HEIGHT


def test_hw_auto_does_not_clamp_explicit_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """When user explicitly sets -W, hw-auto must NOT clamp."""
    fake_profile = Text2IconHardwareProfile(
        name="cuda-1x2g",
        device="cuda",
        cpu_offload=True,
        max_width=512,
        max_height=512,
        gpu_ids=None,
        total_vram_gib=2.0,
        transformer_id="dummy/transformer",
        transformer_sdnq_preset="sdnq-int4",
    )
    monkeypatch.setattr("text2icon.hardware.detect_hardware_profile", lambda: fake_profile)
    monkeypatch.setattr("aigamekit_shared.gpu.warn_if_vram_occupied", lambda: None)
    monkeypatch.setattr("text2icon.cli.delegate_or_prepare", lambda *a, **k: False)
    monkeypatch.setattr("text2icon.cli.prepare_gpu_exclusive", lambda **k: None)

    mock_gen = MagicMock()
    mock_gen.generate.return_value = (MagicMock(), {"seed": 42, "prompt_final": "test"})
    monkeypatch.setattr("text2icon.cli.SanaIconGenerator", lambda **kw: mock_gen)
    monkeypatch.setattr("text2icon.image_processor.save_image", lambda *a, **kw: Path("/tmp/fake.png"))

    runner = CliRunner()
    r = runner.invoke(cli, ["generate", "test", "-W", "1024", "--hw-auto", "--no-vramd", "-o", "/tmp/out.png"])
    assert r.exit_code == 0, r.output
    _, kwargs = mock_gen.generate.call_args
    assert kwargs.get("width") == 1024


# ---------------------------------------------------------------------------
# Group offload + CUDA streams (default ON) — kill-switch, gate e alloc conf.
# ---------------------------------------------------------------------------


def test_group_offload_kill_switch_disables_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """TEXT2ICON_GROUP_OFFLOAD=0 desliga o GO em qualquer hardware."""
    from text2icon.hardware import group_offload_will_engage

    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.setenv("TEXT2ICON_GROUP_OFFLOAD", "0")
    assert group_offload_will_engage() is False


def test_group_offload_will_engage_pure_4gb(monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate puro com specs explícitos: GPU 4 GB (sana fp16 ~8.8 GiB pico) engaja GO.

    Usa a versão do Shared com ``gpu_specs`` — não depende do hardware do host.
    """
    from aigamekit_shared.group_offload import group_offload_will_engage as shared_will_engage
    from aigamekit_shared.lowvram import get_footprint
    from text2icon.generator import FULL_GPU_BUDGET_FRACTION, GROUP_OFFLOAD_ENV

    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.delenv("TEXT2ICON_GROUP_OFFLOAD", raising=False)
    assert (
        shared_will_engage(
            get_footprint("sana-sprint-600m"),
            full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
            gpu_specs=[(0, _gib(4))],
            tool_env_var=GROUP_OFFLOAD_ENV,
            allow_quant=("none",),
        )
        is True
    )
    # Kill-switch vence mesmo com specs que engajariam.
    monkeypatch.setenv("TEXT2ICON_GROUP_OFFLOAD", "0")
    assert (
        shared_will_engage(
            get_footprint("sana-sprint-600m"),
            full_gpu_budget_fraction=FULL_GPU_BUDGET_FRACTION,
            gpu_specs=[(0, _gib(4))],
            tool_env_var=GROUP_OFFLOAD_ENV,
            allow_quant=("none",),
        )
        is False
    )


def test_group_offload_intent_flag_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``--no-group-offload`` (allow=False) e o env kill-switch cortam a intenção."""
    from text2icon.hardware import group_offload_intent

    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    monkeypatch.delenv("TEXT2ICON_GROUP_OFFLOAD", raising=False)
    assert group_offload_intent() is True
    assert group_offload_intent(allow=False) is False
    monkeypatch.setenv("TEXT2ICON_GROUP_OFFLOAD", "0")
    assert group_offload_intent() is False


def test_alloc_conf_by_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sem max_split_size_mb quando GO vai correr (fragmentação sob churn)."""
    from aigamekit_shared.group_offload import ALLOC_CONF_DEFAULT, ALLOC_CONF_GROUP_OFFLOAD
    from text2icon.hardware import cuda_alloc_conf_for

    monkeypatch.delenv("AIGAMEKIT_GROUP_OFFLOAD", raising=False)
    # GO desligado → conf clássico mesmo em GPU pequena (host-independent).
    monkeypatch.setenv("TEXT2ICON_GROUP_OFFLOAD", "0")
    assert cuda_alloc_conf_for(True) == ALLOC_CONF_DEFAULT
    assert "max_split_size_mb" in ALLOC_CONF_DEFAULT
    assert "max_split_size_mb" not in ALLOC_CONF_GROUP_OFFLOAD


@pytest.mark.parametrize("command", ["generate", "batch"])
def test_cli_exposes_group_offload_flag(command: str) -> None:
    """--group-offload/--no-group-offload existe e é default ON."""
    runner = CliRunner()
    r = runner.invoke(cli, [command, "--help"])
    assert r.exit_code == 0
    assert "--group-offload" in r.output


def test_generator_go_class_attrs() -> None:
    """Kill-switch e fração de orçamento expostos no generator (contrato Shared)."""
    from text2icon.generator import SanaIconGenerator

    assert SanaIconGenerator.GROUP_OFFLOAD_ENV == "TEXT2ICON_GROUP_OFFLOAD"
    assert SanaIconGenerator.FULL_GPU_BUDGET_FRACTION == 0.70


class TestGroupOffloadAdapterAndPayload:
    """GO no adapter vramd e no builder de payload."""

    def test_adapter_maps_allow_group_offload_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """allow_group_offload False viaja para o ctor como group_offload False."""
        from text2icon.worker_serve_adapter import Adapter

        captured: dict[str, object] = {}

        class _FakeGen:
            def warmup(self) -> None:
                pass

        def _fake_ctor(**kwargs: object) -> _FakeGen:
            captured.update(kwargs)
            return _FakeGen()

        monkeypatch.setattr("text2icon.generator.SanaIconGenerator", _fake_ctor)
        monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
        Adapter().load(allow_group_offload=False)
        assert captured["group_offload"] is False
        assert "allow_group_offload" not in captured

    def test_adapter_group_offload_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sem allow_group_offload no request, o ctor fica GO default ON."""
        from text2icon.worker_serve_adapter import Adapter

        captured: dict[str, object] = {}

        class _FakeGen:
            def warmup(self) -> None:
                pass

        def _fake_ctor(**kwargs: object) -> _FakeGen:
            captured.update(kwargs)
            return _FakeGen()

        monkeypatch.setattr("text2icon.generator.SanaIconGenerator", _fake_ctor)
        monkeypatch.delenv("PYTORCH_CUDA_ALLOC_CONF", raising=False)
        Adapter().load()
        assert captured["group_offload"] is True

    def test_payload_allow_group_offload_travels(self) -> None:
        """allow_group_offload=True viaja no payload vramd (extra merge)."""
        from text2icon.vramd_payload import build_generate_request

        req = build_generate_request(prompt="icon", output="/tmp/i.png", allow_group_offload=True)
        assert req["allow_group_offload"] is True

    def test_payload_allow_group_offload_false(self) -> None:
        """allow_group_offload=False viaja como False."""
        from text2icon.vramd_payload import build_generate_request

        req = build_generate_request(prompt="icon", output="/tmp/i.png", allow_group_offload=False)
        assert req["allow_group_offload"] is False

    def test_payload_allow_group_offload_default_absent(self) -> None:
        """Sem allow_group_offload, o payload fica igual ao de antes (sem key)."""
        from text2icon.vramd_payload import build_generate_request

        req = build_generate_request(prompt="icon", output="/tmp/i.png")
        assert "allow_group_offload" not in req
