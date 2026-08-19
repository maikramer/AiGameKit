class TestPreferFp8Preset:
    """hw-auto: uint8 → fp8 quando o hardware suporta (mesma VRAM, melhor qualidade)."""

    def test_switches_uint8_to_fp8_when_supported(self) -> None:
        from aigamekit_shared.vramd_load import prefer_fp8_preset

        assert prefer_fp8_preset("sdnq-uint8", fp8_supported=True) == "sdnq-fp8"
        assert prefer_fp8_preset("sdnq-int8", fp8_supported=True) == "sdnq-fp8"
        assert prefer_fp8_preset("uint8", fp8_supported=True) == "sdnq-fp8"

    def test_keeps_uint8_without_fp8_hardware(self) -> None:
        from aigamekit_shared.vramd_load import prefer_fp8_preset

        assert prefer_fp8_preset("sdnq-uint8", fp8_supported=False) == "sdnq-uint8"

    def test_other_presets_untouched(self) -> None:
        from aigamekit_shared.vramd_load import prefer_fp8_preset

        assert prefer_fp8_preset("sdnq-int4", fp8_supported=True) == "sdnq-int4"
        assert prefer_fp8_preset("none", fp8_supported=True) == "none"
        assert prefer_fp8_preset(None, fp8_supported=True) is None

    def test_probes_hardware_when_not_given(self, monkeypatch) -> None:
        from aigamekit_shared import vramd_load

        monkeypatch.setattr("aigamekit_shared.gpu.supports_fp8", lambda device=0: True)
        assert vramd_load.prefer_fp8_preset("sdnq-uint8") == "sdnq-fp8"
