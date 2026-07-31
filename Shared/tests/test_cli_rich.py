"""Testes para aigamekit_shared.cli_rich."""

from aigamekit_shared.cli_rich import setup_rich_click, setup_rich_click_module


class TestSetupRichClick:
    def test_returns_bool(self):
        result = setup_rich_click(
            header="[bold]Test[/bold]",
            footer="[dim]footer[/dim]",
        )
        assert isinstance(result, bool)

    def test_returns_true_if_rich_click_available(self):
        try:
            import rich_click  # noqa: F401

            has_rc = True
        except ImportError:
            has_rc = False

        result = setup_rich_click(header="H", footer="F")
        assert result == has_rc


class TestSetupRichClickModule:
    def test_returns_tuple(self):
        click_mod, rich_ok = setup_rich_click_module(header="H", footer="F")
        assert hasattr(click_mod, "group")
        assert isinstance(rich_ok, bool)
        assert rich_ok == setup_rich_click(header="H", footer="F")
