"""Testes dos cli_helpers — env_bool, apply_quality_defaults, try_ums_delegation."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from gamedev_shared.cli_helpers import (
    apply_quality_defaults,
    env_bool,
    legacy_server_allowed,
    needed_mib_for_backend,
    try_ums_delegation,
    with_ums_peak_opts,
)


class TestEnvBool:
    """env_bool: env var tem precedência sobre CLI."""

    def test_env_false_overrides_cli_true(self) -> None:
        with patch.dict("os.environ", {"MY_FLAG": "0"}):
            assert env_bool("MY_FLAG", True) is False

    def test_env_true_overrides_cli_false(self) -> None:
        with patch.dict("os.environ", {"MY_FLAG": "1"}):
            assert env_bool("MY_FLAG", False) is True

    def test_no_env_uses_cli(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert env_bool("MY_FLAG", True) is True
            assert env_bool("MY_FLAG", False) is False

    def test_env_variants(self) -> None:
        for true_val in ("1", "true", "yes", "on", "TRUE", "Yes"):
            with patch.dict("os.environ", {"X": true_val}):
                assert env_bool("X", False) is True
        for false_val in ("0", "false", "no", "off", "FALSE", "No"):
            with patch.dict("os.environ", {"X": false_val}):
                assert env_bool("X", True) is False


class TestApplyQualityDefaults:
    """apply_quality_defaults: resolve defaults só quando user não explicitou."""

    def test_returns_empty_when_all_user_set(self) -> None:
        """Se o user explicitou todos os params, não deve haver overrides."""
        ctx = MagicMock()
        src = MagicMock()
        src.DEFAULT = "DEFAULT"
        ctx.get_parameter_source.return_value = "COMMANDLINE"  # user explicitou

        resolved = apply_quality_defaults(
            ctx,
            "texture2d",
            "medium",
            {"width": "width", "steps": "steps"},
        )
        assert resolved == {}

    def test_returns_overrides_when_user_didnt_set(self) -> None:
        """Se o user não explicitou, o QualityEngine preenche."""
        import click.core

        ctx = MagicMock()
        ctx.get_parameter_source.return_value = click.core.ParameterSource.DEFAULT  # user não explicitou

        mock_qresolved = MagicMock()
        mock_qresolved.params = {"width": 1024, "steps": 28}

        with patch("gamedev_shared.quality.QualityEngine") as mock_qe_class:
            mock_qe_class.return_value.resolve.return_value = mock_qresolved
            resolved = apply_quality_defaults(
                ctx,
                "texture2d",
                "medium",
                {"width": "width", "steps": "steps"},
            )
            assert resolved == {"width": 1024, "steps": 28}


class TestTryUmsDelegation:
    """try_ums_delegation: delega no UMS e retorna se handled."""

    def test_returns_false_when_no_output(self) -> None:
        console = MagicMock()
        result = try_ums_delegation("text2icon", {}, t_start=time.time(), noun="Ícone", console=console)
        assert result is False

    def test_returns_false_when_ums_down(self) -> None:
        """Se o UMS não está ativo, delegate_to_ums retorna None → False + mensagem."""
        console = MagicMock()
        with (
            patch("gamedev_shared.model_server.is_ums_running", return_value=False),
            patch("gamedev_shared.cli_helpers.delegate_to_ums", return_value=None),
        ):
            result = try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )
        assert result is False
        printed = " ".join(str(c) for c in console.print.call_args_list)
        assert "indisponível" in printed or "fallback in-process" in printed

    def test_returns_true_on_success(self, tmp_path) -> None:
        """Se o UMS responde ok, imprime e retorna True."""

        # Criar ficheiro fake para format_bytes funcionar.
        fake_output = tmp_path / "result.png"
        fake_output.write_bytes(b"\x89PNG fake")

        console = MagicMock()
        with patch(
            "gamedev_shared.cli_helpers.delegate_to_ums",
            return_value={"status": "ok", "output": str(fake_output), "seed": 42},
        ):
            result = try_ums_delegation(
                "text2icon", {"output": str(fake_output)}, t_start=time.time(), noun="Ícone", console=console
            )
        assert result is True
        # Verificar que o console imprimiu algo.
        assert console.print.call_count >= 2

    def test_returns_false_on_ums_error(self) -> None:
        console = MagicMock()
        with (
            patch(
                "gamedev_shared.cli_helpers.delegate_to_ums",
                return_value={"status": "error", "error": "backend down"},
            ),
            patch("gamedev_shared.model_server.is_ums_running", return_value=True),
            patch("gamedev_shared.model_server.ums_is_busy", return_value=False),
        ):
            result = try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )
        assert result is False

    def test_raises_when_none_but_ums_still_running(self) -> None:
        """Timeout com UMS up → sem fallback in-process (GPU pode estar ocupada)."""
        import click

        console = MagicMock()
        with (
            patch("gamedev_shared.cli_helpers.delegate_to_ums", return_value=None),
            patch("gamedev_shared.model_server.is_ums_running", return_value=True),
            patch("gamedev_shared.model_server.fetch_ums_queue_snapshot", return_value=None),
            pytest.raises(click.ClickException, match="sem resposta"),
        ):
            try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )

    def test_raises_on_error_when_ums_busy(self) -> None:
        import click

        console = MagicMock()
        with (
            patch(
                "gamedev_shared.cli_helpers.delegate_to_ums",
                return_value={"status": "error", "error_code": "BACKEND_FAIL", "error": "boom"},
            ),
            patch("gamedev_shared.model_server.is_ums_running", return_value=True),
            patch("gamedev_shared.model_server.ums_is_busy", return_value=True),
            pytest.raises(click.ClickException, match="UMS ocupado"),
        ):
            try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )

    def test_raises_on_vram_insufficient(self) -> None:
        """VRAM_INSUFFICIENT nunca faz fallback in-process (evita OOM)."""
        import click

        console = MagicMock()
        with (
            patch(
                "gamedev_shared.cli_helpers.delegate_to_ums",
                return_value={
                    "status": "error",
                    "error_code": "VRAM_INSUFFICIENT",
                    "error": "peak 6553 > free 5657",
                    "hint": "evict other backends",
                },
            ),
            pytest.raises(click.ClickException, match="VRAM_INSUFFICIENT"),
        ):
            try_ums_delegation(
                "paint3d",
                {"output": "/tmp/x.glb"},
                t_start=time.time(),
                noun="Mesh",
                console=console,
            )

    def test_returns_false_when_disabled(self) -> None:
        console = MagicMock()
        with patch("gamedev_shared.cli_helpers.delegate_to_ums") as mock_delegate:
            result = try_ums_delegation(
                "text2icon",
                {"output": "/tmp/x.png"},
                t_start=time.time(),
                noun="Ícone",
                console=console,
                enabled=False,
            )
        assert result is False
        mock_delegate.assert_not_called()

    def test_raises_on_queue_full(self) -> None:
        import click

        console = MagicMock()
        with (
            patch(
                "gamedev_shared.cli_helpers.delegate_to_ums",
                return_value={
                    "status": "queue_full",
                    "queue_depth": 8,
                    "max_depth": 8,
                    "error": "queue_full",
                },
            ),
            pytest.raises(click.ClickException, match="fila cheia"),
        ):
            try_ums_delegation(
                "text2icon",
                {"output": "/tmp/x.png"},
                t_start=time.time(),
                noun="Ícone",
                console=console,
            )

    def test_passes_priority_to_delegate(self, tmp_path) -> None:
        fake_output = tmp_path / "result.png"
        fake_output.write_bytes(b"\x89PNG fake")

        console = MagicMock()
        with patch(
            "gamedev_shared.cli_helpers.delegate_to_ums",
            return_value={"status": "ok", "output": str(fake_output)},
        ) as mock_delegate:
            try_ums_delegation(
                "text2icon",
                {"output": str(fake_output)},
                t_start=time.time(),
                noun="Ícone",
                console=console,
                priority="batch",
            )
        mock_delegate.assert_called_once()
        assert mock_delegate.call_args.kwargs.get("priority") == "batch"


class TestWithUmsPeakOpts:
    """with_ums_peak_opts: sinais honestos de pico VRAM para admit UMS."""

    def test_paint_mem_eff_default_sdnq(self) -> None:
        out = with_ums_peak_opts({}, backend="paint3d", memory_efficient=True)
        assert out["memory_efficient"] is True
        assert out["sdnq_preset"] == "sdnq-uint8"

    def test_text2d_quant_preset_maps_to_sdnq(self) -> None:
        out = with_ums_peak_opts({}, backend="text2d", quant_preset="sdnq-int4")
        assert out["quant_preset"] == "sdnq-int4"
        assert out["sdnq_preset"] == "sdnq-int4"

    def test_explicit_none_preset(self) -> None:
        out = with_ums_peak_opts(
            {},
            backend="text3d",
            memory_efficient=True,
            sdnq_preset="none",
        )
        assert out["sdnq_preset"] == "none"

    def test_skymap_mem_eff_forces_none_sdnq(self) -> None:
        out = with_ums_peak_opts({}, backend="skymap2d", memory_efficient=True)
        assert out["memory_efficient"] is True
        assert out["sdnq_preset"] == "none"

    def test_part3d_mem_eff_default_sdnq(self) -> None:
        out = with_ums_peak_opts({}, backend="part3d", memory_efficient=True)
        assert out["sdnq_preset"] == "sdnq-uint8"

    def test_texture2d_passthrough_no_forced_sdnq(self) -> None:
        out = with_ums_peak_opts({"prompt": "x"}, backend="texture2d")
        assert out["prompt"] == "x"
        assert "sdnq_preset" not in out

    def test_terrain3d_passthrough(self) -> None:
        out = with_ums_peak_opts({"size": 512}, backend="terrain3d")
        assert out["size"] == 512


class TestLegacyServerAllowed:
    def test_default_off(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            assert legacy_server_allowed() is False

    def test_opt_in(self) -> None:
        with patch.dict("os.environ", {"GAMEDEV_ALLOW_LEGACY_SERVER": "1"}):
            assert legacy_server_allowed() is True


class TestNeededMibForBackend:
    def test_text3d_int4_smaller_than_fp16(self) -> None:
        fp16 = needed_mib_for_backend("text3d", quant_mode="none")
        int4 = needed_mib_for_backend("text3d", quant_mode="sdnq-int4")
        assert int4 < fp16
        assert int4 >= 512

    def test_texture2d_fallback(self) -> None:
        assert needed_mib_for_backend("texture2d") == 2500

    def test_terrain3d_fallback(self) -> None:
        assert needed_mib_for_backend("terrain3d") == 6000

    def test_mem_eff_infers_quant(self) -> None:
        plain = needed_mib_for_backend("paint3d", memory_efficient=False)
        mem = needed_mib_for_backend("paint3d", memory_efficient=True)
        assert mem < plain
