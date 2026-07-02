"""Testes dos tiers hw-auto (puros — sem GPU)."""

from __future__ import annotations

import pytest

pytest.importorskip("gamedev_shared.hardware")

from gamedev_shared.hardware import GIB
from text2sound.hardware import HW_AUTO_ENV, HardwareProfile, hw_auto_enabled, profile_from_specs


class TestProfileFromSpecs:
    def test_cpu_when_no_gpus(self) -> None:
        p = profile_from_specs([])
        assert p.device == "cpu"
        assert p.half is False
        assert p.chunked_vae is True

    def test_6gb_class_uses_half_and_chunked_vae(self) -> None:
        # RTX 4050 laptop: diffusion fits in fp16; full-latent VAE decode OOMs.
        p = profile_from_specs([(0, int(6 * GIB))])
        assert p.device == "cuda"
        assert p.half is True
        assert p.chunked_vae is True
        assert p.gpu_ids is None

    def test_10gb_class_half_without_chunking(self) -> None:
        p = profile_from_specs([(0, int(10 * GIB))])
        assert p.half is True
        assert p.chunked_vae is False

    def test_16gb_class_full_precision(self) -> None:
        p = profile_from_specs([(0, int(16 * GIB))])
        assert p.half is False
        assert p.chunked_vae is False

    def test_multi_gpu_sums_capacity_and_sets_ids(self) -> None:
        p = profile_from_specs([(0, int(6 * GIB)), (1, int(6 * GIB))])
        assert p.gpu_ids == [0, 1]
        # 12 GiB combined → full precision, no chunking
        assert p.half is False
        assert p.chunked_vae is False

    def test_summary_mentions_strategy(self) -> None:
        p = profile_from_specs([(0, int(6 * GIB))])
        s = p.summary()
        assert "half=on" in s
        assert "vae=chunked" in s


class TestKillSwitch:
    def test_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(HW_AUTO_ENV, "0")
        assert hw_auto_enabled() is False

    def test_default_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(HW_AUTO_ENV, raising=False)
        assert hw_auto_enabled() is True


def test_profile_is_frozen() -> None:
    p = HardwareProfile(
        name="x",
        device="cuda",
        gpu_ids=None,
        half=True,
        chunked_vae=True,
        total_vram_gib=6.0,
    )
    with pytest.raises(AttributeError):
        p.half = False  # type: ignore[misc]
