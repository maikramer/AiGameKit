"""Testes do mapeamento UMS → KleinFluxGenerator."""

from __future__ import annotations

from text2d.ums_load import map_ums_load_kwargs


class TestMapUmsLoadKwargs:
    def test_sdnq_preset_to_quant_preset(self) -> None:
        out = map_ums_load_kwargs({"sdnq_preset": "sdnq-uint8"}, low_vram=False)
        assert out["quant_preset"] == "sdnq-uint8"
        assert "sdnq_preset" not in out

    def test_low_vram_defaults_mem_eff(self) -> None:
        out = map_ums_load_kwargs({}, low_vram=True)
        assert out["memory_efficient"] is True
        assert out["torch_compile"] is True
        assert out["channels_last"] is True

    def test_explicit_compile_wins(self) -> None:
        out = map_ums_load_kwargs({"torch_compile": False, "memory_efficient": False}, low_vram=True)
        assert out["torch_compile"] is False
        assert out["memory_efficient"] is False

    def test_none_quant_cleared(self) -> None:
        out = map_ums_load_kwargs({"quant_preset": "none"}, low_vram=False)
        assert out["quant_preset"] is None
