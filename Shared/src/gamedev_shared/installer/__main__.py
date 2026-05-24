"""Permite ``python -m gamedev_shared.installer`` (delega ao Clified)."""

from __future__ import annotations

from .unified import main

if __name__ == "__main__":
    raise SystemExit(main())
