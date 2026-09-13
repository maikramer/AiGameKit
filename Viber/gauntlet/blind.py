#!/usr/bin/env python3
"""Anonimiza pares A/B (nosso vs ref) para crítica cega.

Uso: python3 blind.py <round> <nosso.png> <ref.(png|jpg)>
Cria gauntlet/critique/round<N>/img_A.(png|jpg) e img_B.(png|jpg) com ordem
sorteada; guarda o mapa em gauntlet/critique/round<N>/key.json (o crítico
nunca lê a key).
"""

from __future__ import annotations

import json
import random
import shutil
import sys
from pathlib import Path

GAUNTLET = Path(__file__).resolve().parent


def main() -> None:
    round_n, ours, ref = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3])
    out = GAUNTLET / "critique" / f"round{round_n}"
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(f"gauntlet-{round_n}-{ours.stem}")
    pair = [("img_A", ours), ("img_B", ref)]
    rng.shuffle(pair)
    key = {}
    for name, src in pair:
        shutil.copy(src, out / f"{name}{src.suffix.lower()}")
        key[name] = "ours" if src == ours else "botw"
    (out / "key.json").write_text(json.dumps(key, indent=2))
    print(f"round{round_n}: img_A={key['img_A']} img_B={key['img_B']} -> {out}")


if __name__ == "__main__":
    main()
