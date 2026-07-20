"""Testes do mapeamento UMS → KleinFluxGenerator."""

from __future__ import annotations

from text2d.ums_load import map_ums_load_kwargs


class TestMapUmsLoadKwargs:
    def test_sdnq_preset_to_quant_preset(self) -> None:
        out = map_ums_load_kwargs({"sdnq_preset": "sdnq-uint8"})
        assert out["quant_preset"] == "sdnq-uint8"
        assert "sdnq_preset" not in out

    def test_peak_memory_efficient_from_request(self) -> None:
        out = map_ums_load_kwargs({"memory_efficient": True})
        assert out["memory_efficient"] is True
        # Default OFF: compile frio não compensa fora de batch (opt-in no request).
        assert out["torch_compile"] is False
        assert out["channels_last"] is True

    def test_no_peak_defaults_false(self) -> None:
        out = map_ums_load_kwargs({})
        assert out["memory_efficient"] is False

    def test_explicit_compile_wins(self) -> None:
        out = map_ums_load_kwargs({"torch_compile": False, "memory_efficient": False})
        assert out["torch_compile"] is False

    def test_torch_compile_opt_in(self) -> None:
        out = map_ums_load_kwargs({"torch_compile": True})
        assert out["torch_compile"] is True
        assert out["memory_efficient"] is False

    def test_none_quant_cleared(self) -> None:
        out = map_ums_load_kwargs({"quant_preset": "none"})
        assert out["quant_preset"] is None
