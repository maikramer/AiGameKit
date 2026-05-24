"""Localização da raiz do monorepo GameDev."""

from __future__ import annotations

from pathlib import Path


def _walk_monorepo_root(start: Path) -> Path | None:
    """Percorre ascendentes; devolve raiz se ``.git`` e ``Shared/`` existirem."""
    current = start
    for _ in range(10):
        if (current / ".git").exists() and (current / "Shared").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


def try_find_monorepo_root(start: Path | None = None) -> Path | None:
    """Como :func:`find_monorepo_root`, mas devolve ``None`` se não encontrar."""
    if start is None:
        start = Path(__file__).resolve()
    return _walk_monorepo_root(start)


def find_monorepo_root(start: Path | None = None) -> Path:
    """Encontra a raiz do monorepo (``.git`` + ``Shared/``)."""
    if start is None:
        start = Path(__file__).resolve()
    found = _walk_monorepo_root(start)
    if found is not None:
        return found
    msg = (
        "Raiz do monorepo GameDev não encontrada. "
        "Execute a partir de dentro do repositório."
    )
    raise FileNotFoundError(msg)
