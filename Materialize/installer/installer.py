#!/usr/bin/env python3
"""Redireciona para o instalador Clified do monorepo."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_repo = Path(__file__).resolve().parents[1]
_monorepo = _repo.parent
_install_sh = _monorepo / "install.sh"


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Materialize CLI — instalador via Clified")
    parser.add_argument(
        "action",
        nargs="?",
        default="install",
        choices=["install", "uninstall", "reinstall"],
    )
    args = parser.parse_args()
    if not _install_sh.is_file():
        print("install.sh do monorepo não encontrado.", file=sys.stderr)
        return 1
    cmd = [str(_install_sh), "materialize", "--action", args.action]
    return subprocess.call(cmd, cwd=_monorepo)


if __name__ == "__main__":
    sys.exit(main())
