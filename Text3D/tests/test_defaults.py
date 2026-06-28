"""Testes para ``text3d.defaults`` — precedência de env vars e overrides.

Os setters mutam estado global de módulo; um fixture autouse faz reset antes
e depois de cada teste para evitar vazamento entre testes.
"""

from __future__ import annotations

import math

import pytest

from text3d import defaults

_ROT_ENVS = ("TEXT3D_EXPORT_ROTATION_X_RAD", "TEXT3D_EXPORT_ROTATION_X_DEG", "TEXT3D_EXPORT_ORIGIN")


@pytest.fixture(autouse=True)
def _reset_module_globals() -> None:
    """Reseta overrides globais antes/depois de cada teste.

    Necessario porque os setters mutam variaveis de modulo persistentes; sem
    reset um override esquecido vazaria e quebraria outros testes.
    """
    defaults.set_export_rotation_x_rad_override(None)
    defaults.set_export_origin_override(None)
    yield
    defaults.set_export_rotation_x_rad_override(None)
    defaults.set_export_origin_override(None)


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    for var in _ROT_ENVS:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


class TestGetExportRotationXRad:
    def test_default_is_half_pi(self, clean_env: pytest.MonkeyPatch) -> None:
        assert defaults.get_export_rotation_x_rad() == pytest.approx(math.pi / 2.0)

    def test_rad_env_used(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "1.25")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(1.25)

    def test_rad_env_beats_deg_env(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "0.7")
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "90")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(0.7)

    def test_deg_env_converted_to_radians(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "180")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(math.pi)

    def test_deg_env_zero(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "0")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(0.0)

    def test_empty_rad_env_skipped(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "   ")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(math.pi / 2.0)

    def test_empty_deg_env_skipped(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "")
        assert defaults.get_export_rotation_x_rad() == pytest.approx(math.pi / 2.0)

    def test_override_beats_everything(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "0.7")
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "180")
        defaults.set_export_rotation_x_rad_override(0.3)
        assert defaults.get_export_rotation_x_rad() == pytest.approx(0.3)

    def test_override_returns_float(self, clean_env: pytest.MonkeyPatch) -> None:
        defaults.set_export_rotation_x_rad_override(1)
        result = defaults.get_export_rotation_x_rad()
        assert isinstance(result, float)
        assert result == pytest.approx(1.0)

    def test_invalid_rad_env_raises_value_error(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_RAD", "not-a-number")
        with pytest.raises(ValueError):
            defaults.get_export_rotation_x_rad()

    def test_invalid_deg_env_raises_value_error(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ROTATION_X_DEG", "xyz")
        with pytest.raises(ValueError):
            defaults.get_export_rotation_x_rad()


class TestSetExportRotationXRadOverride:
    def test_sets_and_clears(self, clean_env: pytest.MonkeyPatch) -> None:
        defaults.set_export_rotation_x_rad_override(0.5)
        assert defaults.get_export_rotation_x_rad() == pytest.approx(0.5)
        defaults.set_export_rotation_x_rad_override(None)
        assert defaults.get_export_rotation_x_rad() == pytest.approx(math.pi / 2.0)

    def test_negative_override(self, clean_env: pytest.MonkeyPatch) -> None:
        defaults.set_export_rotation_x_rad_override(-1.0)
        assert defaults.get_export_rotation_x_rad() == pytest.approx(-1.0)


class TestGetExportOrigin:
    def test_default_is_feet(self, clean_env: pytest.MonkeyPatch) -> None:
        assert defaults.get_export_origin() == "feet"

    @pytest.mark.parametrize("value", ["feet", "center", "none"])
    def test_valid_env_values(self, clean_env: pytest.MonkeyPatch, value: str) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", value)
        assert defaults.get_export_origin() == value

    def test_env_is_lowercased(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", "CENTER")
        assert defaults.get_export_origin() == "center"

    def test_env_is_stripped(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", "  none  ")
        assert defaults.get_export_origin() == "none"

    def test_invalid_env_falls_back_to_default(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", "banana")
        assert defaults.get_export_origin() == "feet"

    def test_empty_env_falls_back_to_default(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", "")
        assert defaults.get_export_origin() == "feet"

    def test_override_beats_env(self, clean_env: pytest.MonkeyPatch) -> None:
        clean_env.setenv("TEXT3D_EXPORT_ORIGIN", "center")
        defaults.set_export_origin_override("none")
        assert defaults.get_export_origin() == "none"


class TestSetExportOriginOverride:
    @pytest.mark.parametrize("value", ["feet", "center", "none"])
    def test_valid_values_accepted(self, clean_env: pytest.MonkeyPatch, value: str) -> None:
        defaults.set_export_origin_override(value)
        assert defaults.get_export_origin() == value

    def test_none_clears_override(self, clean_env: pytest.MonkeyPatch) -> None:
        defaults.set_export_origin_override("center")
        defaults.set_export_origin_override(None)
        assert defaults.get_export_origin() == "feet"

    def test_invalid_value_raises_value_error(self, clean_env: pytest.MonkeyPatch) -> None:
        with pytest.raises(ValueError):
            defaults.set_export_origin_override("banana")

    def test_invalid_value_does_not_mutate_state(self, clean_env: pytest.MonkeyPatch) -> None:
        defaults.set_export_origin_override("center")
        with pytest.raises(ValueError):
            defaults.set_export_origin_override("invalid")
        assert defaults.get_export_origin() == "center"
