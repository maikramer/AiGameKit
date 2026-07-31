"""Testes do validation.py — validate_prompt/dimensions/params parameterizados."""

from __future__ import annotations

from aigamekit_shared.validation import format_timestamp, validate_dimensions, validate_params, validate_prompt


class TestValidatePrompt:
    def test_empty_prompt_invalid(self) -> None:
        ok, err = validate_prompt("")
        assert not ok
        assert "vazio" in err

    def test_whitespace_prompt_invalid(self) -> None:
        ok, _ = validate_prompt("   ")
        assert not ok

    def test_valid_prompt(self) -> None:
        ok, err = validate_prompt("a beautiful castle")
        assert ok
        assert err is None

    def test_too_long_prompt(self) -> None:
        ok, err = validate_prompt("x" * 600, max_length=500)
        assert not ok
        assert "500" in err

    def test_text2icon_uses_max_1000(self) -> None:
        """Text2Icon permite prompts até 1000 chars (Sana tem contexto maior)."""
        ok, _ = validate_prompt("x" * 800, max_length=1000)
        assert ok


class TestValidateDimensions:
    def test_valid_dims(self) -> None:
        ok, err = validate_dimensions(1024, 1024)
        assert ok
        assert err is None

    def test_too_small(self) -> None:
        ok, err = validate_dimensions(128, 1024)
        assert not ok
        assert "256" in err

    def test_too_large(self) -> None:
        ok, err = validate_dimensions(4096, 1024)
        assert not ok
        assert "2048" in err

    def test_not_multiple_of_8(self) -> None:
        # 1001 não é múltiplo de 8 (1001 / 8 = 125.125).
        ok, err = validate_dimensions(1001, 1001)
        assert not ok
        assert "múltiplos" in err

    def test_skymap2d_wider_max(self) -> None:
        """Skymap2D permite até 4096 de largura (panorama equirect)."""
        ok, _ = validate_dimensions(4096, 2048, max_width=4096, max_height=2048)
        assert ok

    def test_warn_ratio_no_logger(self) -> None:
        """warn_ratio sem logger não levanta."""
        ok, _ = validate_dimensions(1024, 1024, warn_ratio=2.0, logger=None)
        assert ok

    def test_warn_ratio_with_logger(self) -> None:
        """warn_ratio com logger emite warning se ratio diverge."""
        calls: list[str] = []

        class FakeLogger:
            def warning(self, msg: str) -> None:
                calls.append(msg)

        validate_dimensions(1024, 1024, warn_ratio=2.0, logger=FakeLogger())
        assert len(calls) == 1
        assert "2.0:1" in calls[0]


class TestValidateParams:
    def test_defaults_valid(self) -> None:
        ok, err = validate_params({"guidance_scale": 7.5, "num_inference_steps": 50, "width": 1024, "height": 1024})
        assert ok
        assert err is None

    def test_guidance_too_low(self) -> None:
        ok, err = validate_params({"guidance_scale": 0.5})
        assert not ok
        assert "1.0" in err

    def test_steps_too_few_default(self) -> None:
        ok, err = validate_params({"num_inference_steps": 5})
        assert not ok
        assert "10" in err

    def test_text2icon_allows_1_step(self) -> None:
        """Sana Sprint gera em 1-4 passos; min_steps=1."""
        ok, _ = validate_params({"num_inference_steps": 1, "width": 512, "height": 512}, min_steps=1)
        assert ok

    def test_missing_params_uses_defaults(self) -> None:
        ok, _ = validate_params({})
        assert ok


class TestFormatTimestamp:
    def test_formats_correctly(self) -> None:
        # Timestamp fixo para teste determinístico (epoch = 1970 UTC; pode variar por TZ).
        result = format_timestamp(0)
        # O formato deve ter HH:MM:SS.
        assert ":" in result
        # E o ano (1970 UTC, pode ser 1969 nalgumas timezones).
        assert "1970" in result or "1969" in result
