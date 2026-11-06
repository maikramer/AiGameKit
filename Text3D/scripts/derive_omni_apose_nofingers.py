#!/usr/bin/env python3
"""Deriva o A-pose oficial do Omni **sem ossos de dedo** (``omni_apose_nofingers``).

Contexto (medido 2026-10-29, pool Vael): o esqueleto oficial do Hunyuan3D-Omni
(``demos/pose/a_pose_bone.txt``, 51 ossos) traz, por mão, ``Wrist`` + 5 dedos x
3 falanges. Isso resolveu os braços (-40° rectos, validado no hero) mas
**partiu as mãos**: os dedos do esqueleto abrem em profundidade (Z, palma virada
para o corpo) enquanto as refs do Text2D saem com as palmas viradas para a
câmara e os dedos abertos no plano da imagem (o hint de prompt
``palms facing the thighs`` é ignorado). O modelo passa a ter duas mãos — uma da
imagem, outra do esqueleto — a 90° uma da outra: daí as mãos «balão» e as mãos
com 7-9 dedos (``npc_guard`` @384 com 8 dedos numa mão).

Sem ossos de dedo a mão forma-se **só a partir da imagem** e o esqueleto continua
a mandar nos ombros/cotovelos/pulsos — a mesma conclusão já registada em
``derive_openhand_apose.py`` (esqueleto canónico = sem dedos). O ``Wrist`` fica:
é o único osso que ancora o bico da mão.

Não precisa bpy. Os **valores** vêm do ``omni_apose_bone.txt`` upstream (o
``omni_apose_bone.json`` empacotado é só points — ``start == end`` em todos os
ossos — e serve apenas de ordem/nomes); a saída fica com segmentos reais nos
dois formatos. Idempotente.

Usage::

    python Text3D/scripts/derive_omni_apose_nofingers.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

OMNI_DIR = Path(__file__).resolve().parents[1] / "src" / "text3d" / "data" / "omni"
SRC_JSON = OMNI_DIR / "omni_apose_bone.json"
SRC_TXT = OMNI_DIR / "omni_apose_bone.txt"
OUT_JSON = OMNI_DIR / "omni_apose_nofingers_bone.json"
OUT_TXT = OMNI_DIR / "omni_apose_nofingers_bone.txt"

# Falanges removidas (as duas mãos). O ``Wrist.*`` mantém-se.
_FINGER_PREFIXES = ("thumb", "index", "middle", "ring", "pinky")


def is_finger(name: str) -> bool:
    """``Thumb0.R``/``Index3.L``/... → True (o ``Wrist`` não é dedo)."""
    return name.strip().lower().startswith(_FINGER_PREFIXES)


def write_txt(bones: dict[str, list[list[float]]], path: Path) -> None:
    """Um osso por linha, 6 floats (start xyz + end xyz) — formato ``pose_tensor``."""
    lines = []
    for _name, (start, end) in bones.items():
        vals = [*start, *end]
        if len(vals) != 6:
            raise ValueError(f"osso com {len(vals)} floats (esperado 6): {_name}")
        lines.append(" ".join(f"{float(v):.18e}" for v in vals))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    names = list(json.loads(SRC_JSON.read_text(encoding="utf-8")))
    segs = np.loadtxt(SRC_TXT)
    if segs.shape != (len(names), 6):
        raise SystemExit(f"{SRC_TXT.name}: {segs.shape} não casa com {len(names)} nomes x 6")
    kept = [(name, segs[i]) for i, name in enumerate(names) if not is_finger(name)]
    removed = len(names) - len(kept)
    if removed == 0:
        raise SystemExit(f"nada removido — {SRC_TXT.name} já não tem dedos?")
    bones = {name: [seg[:3].tolist(), seg[3:].tolist()] for name, seg in kept}
    OUT_JSON.write_text(json.dumps(bones, indent=4) + "\n", encoding="utf-8")
    write_txt(bones, OUT_TXT)
    print(f"{SRC_TXT.name}: {len(names)} ossos → {len(kept)} (removidos {removed} de dedos)")
    print(f"escritos: {OUT_JSON.name}, {OUT_TXT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
