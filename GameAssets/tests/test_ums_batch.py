"""ums_batch: peak VRAM via hw_auto, sem hardcode sdnq-uint8."""

from __future__ import annotations

import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

from gameassets.ums_batch import (
    motion3d_specs_from_items,
    resolve_paint3d_vram_opts,
    resolve_skymap2d_vram_opts,
    resolve_text2d_vram_opts,
    resolve_text2icon_vram_opts,
    resolve_text2sound_vram_opts,
    resolve_text3d_vram_opts,
    run_motion3d_wave_or_fallback,
    run_skymap2d_wave_or_fallback,
    run_terrain3d_wave_or_fallback,
    run_text2d_wave_or_fallback,
    run_text2icon_wave_or_fallback,
    run_text2sound_wave_or_fallback,
    run_texture2d_wave_or_fallback,
    shape_specs_from_items,
    text2d_specs_from_items,
)
from gameassets.ums_coord import FALLBACK_SUBPROCESS, UmsJobResult, UmsJobSpec


@contextmanager
def _temp_modules(modules: dict[str, types.ModuleType]) -> Iterator[None]:
    """Injeta módulos temporários sem poluir sys.modules após o teste."""
    saved: dict[str, types.ModuleType | None] = {name: sys.modules.get(name) for name in modules}
    try:
        sys.modules.update(modules)
        yield
    finally:
        for name, prev in saved.items():
            if prev is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prev


def _make_pkg(fullname: str, **attrs: object) -> dict[str, types.ModuleType]:
    """Cria cadeia de pacotes (a, a.b) com attrs no leaf."""
    parts = fullname.split(".")
    out: dict[str, types.ModuleType] = {}
    for i in range(1, len(parts) + 1):
        name = ".".join(parts[:i])
        out[name] = types.ModuleType(name)
    leaf = out[fullname]
    for k, v in attrs.items():
        setattr(leaf, k, v)
    return out


class TestResolveText3dVramOpts:
    def test_explicit_wins(self) -> None:
        preset, mem = resolve_text3d_vram_opts("sdnq-int4", False)
        assert preset == "sdnq-int4"
        assert mem is False

    def test_explicit_mem_false_clears_peak_pressure(self) -> None:
        preset, mem = resolve_text3d_vram_opts(None, False)
        assert preset is None
        assert mem is False

    def test_hw_auto_when_unset(self) -> None:
        hwp = MagicMock()
        hwp.sdnq_preset = "sdnq-int4"
        hwp.offload = False
        mods = _make_pkg(
            "text3d.hardware",
            hw_auto_enabled=lambda: True,
            detect_hardware_profile=lambda: hwp,
        )
        with _temp_modules(mods):
            preset, mem = resolve_text3d_vram_opts(None, None)
        assert preset == "sdnq-int4"
        assert mem is True

    def test_fallback_admit_safe(self) -> None:
        mods = _make_pkg(
            "text3d.hardware",
            hw_auto_enabled=lambda: False,
            detect_hardware_profile=lambda: MagicMock(),
        )
        with _temp_modules(mods):
            preset, mem = resolve_text3d_vram_opts(None, None)
        assert preset == "sdnq-int4"
        assert mem is True


class TestResolvePaint3dVramOpts:
    def test_hw_auto_fp16_high_vram(self) -> None:
        hwp = MagicMock()
        hwp.memory_efficient = False
        mods = _make_pkg(
            "paint3d.hardware",
            hw_auto_enabled=lambda: True,
            detect_hardware_profile=lambda: hwp,
        )
        with _temp_modules(mods):
            preset, mem = resolve_paint3d_vram_opts(None, None)
        assert preset is None
        assert mem is False

    def test_explicit_mem_eff(self) -> None:
        preset, mem = resolve_paint3d_vram_opts(True, None)
        assert preset == "sdnq-uint8"
        assert mem is True


class TestShapeSpecsHwAuto:
    def test_payload_uses_hw_auto_not_hardcoded_uint8(self, tmp_path: Path) -> None:
        img = tmp_path / "a.png"
        out = tmp_path / "a.glb"
        img.write_bytes(b"x")
        items = [{"id": "a", "image": str(img), "output": str(out)}]

        hwp = MagicMock()
        hwp.sdnq_preset = None
        hwp.offload = False
        fake_payload = {"sdnq_preset": "none", "memory_efficient": False, "output": str(out)}
        mock_build = MagicMock(return_value=fake_payload)

        hw_mods = _make_pkg(
            "text3d.hardware",
            hw_auto_enabled=lambda: True,
            detect_hardware_profile=lambda: hwp,
        )
        payload_mods = _make_pkg("text3d.ums_payload", build_generate_request=mock_build)
        with _temp_modules({**hw_mods, **payload_mods}):
            specs = shape_specs_from_items(items, manifest_dir=tmp_path)

        assert len(specs) == 1
        kwargs = mock_build.call_args.kwargs
        assert kwargs["memory_efficient"] is False
        assert kwargs["sdnq_preset"] is None


class TestResolveOptionalVramOpts:
    def test_text2d_hw_auto(self) -> None:
        hwp = MagicMock()
        hwp.quant_preset = "sdnq-int4"
        hwp.memory_efficient = True
        hwp.model_id = "black-forest-labs/FLUX.2-klein-4B"
        mods = {
            **_make_pkg(
                "text2d.hardware",
                hw_auto_enabled=lambda: True,
                detect_hardware_profile=lambda: hwp,
            ),
            **_make_pkg(
                "text2d.generator",
                LOW_VRAM_MODEL_ID="black-forest-labs/FLUX.2-klein-4B",
                model_footprint_key=lambda mid: "flux-klein-4b",
            ),
        }
        with _temp_modules(mods):
            quant, mem, mid, fp = resolve_text2d_vram_opts()
        assert quant == "sdnq-int4"
        assert mem is True
        assert fp == "flux-klein-4b"
        assert mid is not None

    def test_text2d_admit_safe_fallback(self) -> None:
        mods = {
            **_make_pkg("text2d.hardware", hw_auto_enabled=lambda: False),
            **_make_pkg(
                "text2d.generator",
                LOW_VRAM_MODEL_ID="m4b",
                model_footprint_key=lambda mid: "flux-klein-4b",
            ),
        }
        with _temp_modules(mods):
            quant, mem, _mid, fp = resolve_text2d_vram_opts()
        assert quant == "sdnq-uint8"
        assert mem is True
        assert fp == "flux-klein-4b"

    def test_text2icon_hw_auto(self) -> None:
        hwp = MagicMock()
        hwp.transformer_sdnq_preset = "sdnq-uint8"
        hwp.cpu_offload = True
        mods = _make_pkg(
            "text2icon.hardware",
            hw_auto_enabled=lambda: True,
            detect_hardware_profile=lambda: hwp,
        )
        with _temp_modules(mods):
            quant, mem = resolve_text2icon_vram_opts()
        assert quant == "sdnq-uint8"
        assert mem is True

    def test_skymap_admit_safe(self) -> None:
        mods = _make_pkg("skymap2d.hardware", hw_auto_enabled=lambda: False)
        with _temp_modules(mods):
            assert resolve_skymap2d_vram_opts() is True

    def test_text2sound_explicit_wins(self) -> None:
        assert resolve_text2sound_vram_opts(False) is False
        assert resolve_text2sound_vram_opts(True) is True


class TestText2dSpecsHwAuto:
    def test_payload_uses_hw_auto(self, tmp_path: Path) -> None:
        out = tmp_path / "a.png"
        items = [{"id": "a", "prompt": "hero", "output": str(out)}]
        mock_build = MagicMock(return_value={"output": str(out), "memory_efficient": True})
        hw_mods = {
            **_make_pkg(
                "text2d.hardware",
                hw_auto_enabled=lambda: True,
                detect_hardware_profile=lambda: MagicMock(
                    quant_preset="sdnq-uint8",
                    memory_efficient=True,
                    model_id="m4b",
                ),
            ),
            **_make_pkg(
                "text2d.generator",
                LOW_VRAM_MODEL_ID="m4b",
                model_footprint_key=lambda mid: "flux-klein-4b",
            ),
            **_make_pkg("text2d.ums_payload", build_generate_request=mock_build),
        }
        with _temp_modules(hw_mods):
            specs = text2d_specs_from_items(items, manifest_dir=tmp_path)
        assert len(specs) == 1
        assert mock_build.call_args.kwargs["memory_efficient"] is True
        assert mock_build.call_args.kwargs["quant_preset"] == "sdnq-uint8"


class TestOptionalWaveOrFallback:
    def test_no_ums_all_backends(self) -> None:
        items = [{"id": "a", "prompt": "x", "output": "/tmp/x.png"}]
        assert run_text2d_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert run_text2icon_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert run_texture2d_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert run_skymap2d_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert run_text2sound_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert run_motion3d_wave_or_fallback(items, manifest_dir=Path("."), no_ums=True) is None
        assert (
            run_terrain3d_wave_or_fallback([{"id": "t", "output": "/tmp/h.png"}], manifest_dir=Path("."), no_ums=True)
            is None
        )

    def test_text2d_wave_mocked(self, tmp_path: Path) -> None:
        items = [{"id": "a", "prompt": "p", "output": "o.png"}]
        fake_specs = [UmsJobSpec(asset_id="a", payload={"output": str(tmp_path / "o.png")})]
        fake_results = [UmsJobResult(asset_id="a", status="ok", output=str(tmp_path / "o.png"), seconds=1.0)]
        with (
            patch("gameassets.ums_batch.text2d_specs_from_items", return_value=fake_specs),
            patch("gameassets.ums_batch.run_gpu_wave", return_value=fake_results) as wave,
        ):
            out = run_text2d_wave_or_fallback(items, manifest_dir=tmp_path, no_ums=False)
            assert out is not None
            assert out[0]["status"] == "ok"
            assert wave.call_args.kwargs.get("preload") is False

    def test_texture2d_fallback_sentinel(self, tmp_path: Path) -> None:
        items = [{"id": "a", "prompt": "p", "output": "o.png"}]
        with (
            patch("gameassets.ums_batch.texture2d_specs_from_items", return_value=[MagicMock()]),
            patch("gameassets.ums_batch.run_gpu_wave", return_value=FALLBACK_SUBPROCESS),
        ):
            assert run_texture2d_wave_or_fallback(items, manifest_dir=tmp_path, no_ums=False) is None

    def test_terrain_wave_mocked(self, tmp_path: Path) -> None:
        items = [{"id": "terrain", "output": "h.png", "prompt": "hills"}]
        fake_specs = [UmsJobSpec(asset_id="terrain", payload={"output": str(tmp_path / "h.png")})]
        fake_results = [UmsJobResult(asset_id="terrain", status="ok", output=str(tmp_path / "h.png"))]
        with (
            patch("gameassets.ums_batch.terrain3d_specs_from_items", return_value=fake_specs),
            patch("gameassets.ums_batch.run_gpu_wave", return_value=fake_results),
        ):
            out = run_terrain3d_wave_or_fallback(items, manifest_dir=tmp_path, no_ums=False)
            assert out is not None
            assert out[0]["id"] == "terrain"

    def test_motion3d_wave_mocked(self, tmp_path: Path) -> None:
        items = [{"id": "walk", "prompt": "a person walks", "output": "walk.npz"}]
        fake_specs = [UmsJobSpec(asset_id="walk", payload={"output": str(tmp_path / "walk.npz")})]
        fake_results = [UmsJobResult(asset_id="walk", status="ok", output=str(tmp_path / "walk.npz"))]
        with (
            patch("gameassets.ums_batch.motion3d_specs_from_items", return_value=fake_specs),
            patch("gameassets.ums_batch.run_gpu_wave", return_value=fake_results),
        ):
            out = run_motion3d_wave_or_fallback(items, manifest_dir=tmp_path, no_ums=False)
            assert out is not None
            assert out[0]["id"] == "walk"


class TestMotion3dSpecs:
    def test_specs_from_items_mocked_payload(self, tmp_path: Path) -> None:
        items = [{"id": "w", "prompt": "walk", "output": "w.npz", "also_npz": True}]
        build = MagicMock(return_value={"output": "w.npz"})
        mods = _make_pkg("motion3d.ums_payload", build_generate_request=build)
        with _temp_modules(mods):
            specs = motion3d_specs_from_items(items, manifest_dir=tmp_path, quality="medium")
        assert len(specs) == 1
        assert specs[0].asset_id == "w"
        assert build.call_args.kwargs["also_npz"] is True
        assert build.call_args.kwargs["quality"] == "medium"

    def test_empty_items(self, tmp_path: Path) -> None:
        assert motion3d_specs_from_items([], manifest_dir=tmp_path) == []
