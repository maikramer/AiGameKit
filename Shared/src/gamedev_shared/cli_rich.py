"""Configuração parametrizada de rich-click para CLIs do monorepo GameDev."""

from __future__ import annotations

from typing import Any


def setup_rich_click(
    header: str,
    footer: str,
    *,
    tool: str | None = None,
    use_rich_markup: bool = True,
    group_arguments_options: bool = True,
    show_metavars_column: bool = True,
) -> bool:
    """Configura rich-click com header/footer personalizados.

    Retorna ``True`` se rich-click estiver disponível e configurado,
    ``False`` caso contrário (graceful degradation para Click puro).

    Deve ser chamado **antes** de importar Click nos módulos CLI::

        from gamedev_shared.cli_rich import setup_rich_click

        RICH_CLICK = setup_rich_click(
            header="[bold cyan]Text2D[/bold cyan] — FLUX.2 Klein",
            footer="[dim]Docs: README[/dim]",
            tool="text2d",
        )

        if RICH_CLICK:
            import rich_click as click
        else:
            import click

    Args:
        tool: Nome da tool para ficheiro de log (``~/.cache/gamedev/logs/<tool>-…``).
    """
    if tool:
        from gamedev_shared.logging import configure_logging

        configure_logging(tool)

    try:
        import rich_click.rich_click as _rc

        # rich-click>=1.9: usar TEXT_MARKUP / OPTIONS_TABLE_COLUMN_TYPES em vez de
        # USE_RICH_MARKUP / SHOW_METAVARS_COLUMN (evita PendingDeprecationWarning).
        _rc.TEXT_MARKUP = "rich" if use_rich_markup else "ansi"
        _rc.GROUP_ARGUMENTS_OPTIONS = group_arguments_options
        if not show_metavars_column:
            _rc.OPTIONS_TABLE_COLUMN_TYPES = [
                "required",
                "opt_short",
                "opt_long",
                "help",
            ]
        _rc.HEADER_TEXT = header
        _rc.FOOTER_TEXT = footer
        return True
    except ImportError:
        return False


def setup_rich_click_module(
    header: str,
    footer: str,
    *,
    tool: str | None = None,
    use_rich_markup: bool = True,
    group_arguments_options: bool = True,
    show_metavars_column: bool = True,
) -> tuple[Any, bool]:
    """Configura rich-click se existir e devolve ``(módulo click, usou_rich_click)``."""
    ok = setup_rich_click(
        header,
        footer,
        tool=tool,
        use_rich_markup=use_rich_markup,
        group_arguments_options=group_arguments_options,
        show_metavars_column=show_metavars_column,
    )
    if ok:
        import rich_click as click

        return click, True
    import click as click_std

    return click_std, False
