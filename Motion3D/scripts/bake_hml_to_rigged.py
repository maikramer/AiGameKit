#!/usr/bin/env python3
"""Bake HumanML3D NPZ onto SkinTokens rigged GLB via Animator3D retarget.

Thin wrapper around ``motion3d.apply_rigged`` (same as CLI ``apply-rigged``).

Usage:
  python scripts/bake_hml_to_rigged.py walk.npz hero_rigged.glb -o hero_walk.glb
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("npz", type=Path)
    ap.add_argument("rigged", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--clip", default="walk")
    ap.add_argument("--profile", default="hml22")
    ap.add_argument("--keep-source", type=Path, default=None)
    args = ap.parse_args()

    from motion3d.apply_rigged import apply_npz_to_rigged

    try:
        res = apply_npz_to_rigged(
            args.npz,
            args.rigged,
            args.output,
            clip_name=args.clip,
            profile_name=args.profile,
            keep_source=args.keep_source,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise
    print(res["output"])
    print(res["retarget"])


if __name__ == "__main__":
    main()
