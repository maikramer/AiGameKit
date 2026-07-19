"""Testes do mapeamento kwargs UMS → HunyuanTextTo3DGenerator."""

from __future__ import annotations

from text3d.ums_load import map_ums_load_kwargs


class TestMapUmsLoadKwargs:
    def test_torch_compile_alias(self) -> None:
        kw = map_ums_load_kwargs({"torch_compile": True, "torch_compile_mode": "reduce-overhead"}, low_vram=False)
        assert kw["compile_models"] is True
        assert kw["compile_mode"] == "reduce-overhead"
        assert "torch_compile" not in kw

    def test_quant_mode_to_sdnq(self) -> None:
        kw = map_ums_load_kwargs({"quant_mode": "sdnq-int4"}, low_vram=False)
        assert kw["sdnq_preset"] == "sdnq-int4"
        assert "quant_mode" not in kw

    def test_low_vram_defaults(self) -> None:
        kw = map_ums_load_kwargs({}, low_vram=True)
        assert kw["offload"] is True
        assert kw["allow_group_offload"] is True
        assert kw["sdnq_preset"] == "sdnq-int4"
        assert kw["volume_decoder"] == "flashvdm"

    def test_explicit_sdnq_not_overridden(self) -> None:
        kw = map_ums_load_kwargs({"sdnq_preset": "sdnq-int8", "memory_efficient": True}, low_vram=False)
        assert kw["sdnq_preset"] == "sdnq-int8"

    def test_drops_legacy_subfolder(self) -> None:
        kw = map_ums_load_kwargs({"hunyuan_subfolder": "hunyuan3d-dit-v2-1"}, low_vram=False)
        assert "hunyuan_subfolder" not in kw
