"""Testes dos cli_helpers — env_bool, apply_quality_defaults, try_ums_delegation."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

from gamedev_shared.cli_helpers import apply_quality_defaults, env_bool, try_ums_delegation


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
            ctx, "texture2d", "medium", {"width": "width", "steps": "steps"},
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
                ctx, "texture2d", "medium", {"width": "width", "steps": "steps"},
            )
            assert resolved == {"width": 1024, "steps": 28}


class TestTryUmsDelegation:
    """try_ums_delegation: delega no UMS e retorna se handled."""

    def test_returns_false_when_no_output(self) -> None:
        console = MagicMock()
        result = try_ums_delegation("text2icon", {}, t_start=time.time(), noun="Ícone", console=console)
        assert result is False

    def test_returns_false_when_ums_down(self) -> None:
        """Se o UMS não está ativo, delegate_to_ums retorna None → False."""
        console = MagicMock()
        with patch("gamedev_shared.cli_helpers.delegate_to_ums", return_value=None):
            result = try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )
        assert result is False

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
        with patch(
            "gamedev_shared.cli_helpers.delegate_to_ums",
            return_value={"status": "error", "error": "backend down"},
        ):
            result = try_ums_delegation(
                "text2icon", {"output": "/tmp/x.png"}, t_start=time.time(), noun="Ícone", console=console
            )
        assert result is False
