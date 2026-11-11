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

O A-pose oficial tem as pernas **na vertical** (tornozelos a ±0.083 vs ancas a
±0.080). O Hunyuan lê o controlo de pose como estrutura e as coxas fundem-se no
marching cubes: no pool Vael (2026-10-30) o vão interior aos joelhos media 0-2 cm
e as pernas saíam como uma coluna. A segunda saída
(``omni_apose_nofingers_spread``) roda a cadeia da perna ±10° no plano XY à volta
da anca — o mesmo ângulo que o ``derive_openhand_apose.py`` usa — mantendo
braços e tronco intactos.

Não precisa bpy. Os **valores** vêm do ``omni_apose_bone.txt`` upstream (o
``omni_apose_bone.json`` empacotado é só points — ``start == end`` em todos os
ossos — e serve apenas de ordem/nomes); a saída fica com segmentos reais nos
dois formatos. Idempotente.

Usage::

    python Text3D/scripts/derive_omni_apose_nofingers.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

OMNI_DIR = Path(__file__).resolve().parents[1] / "src" / "text3d" / "data" / "omni"
SRC_JSON = OMNI_DIR / "omni_apose_bone.json"
SRC_TXT = OMNI_DIR / "omni_apose_bone.txt"
OUT_JSON = OMNI_DIR / "omni_apose_nofingers_bone.json"
OUT_TXT = OMNI_DIR / "omni_apose_nofingers_bone.txt"
OUT_SPREAD_JSON = OMNI_DIR / "omni_apose_nofingers_spread_bone.json"
OUT_SPREAD_TXT = OMNI_DIR / "omni_apose_nofingers_spread_bone.txt"

# Falanges removidas (as duas mãos). O ``Wrist.*`` mantém-se.
_FINGER_PREFIXES = ("thumb", "index", "middle", "ring", "pinky")

# Abertura das pernas (±graus no plano XY, à volta da anca). Mesmo valor do
# ``derive_openhand_apose.py``: 10° abre um vão claro sem pose de espacate.
APOSE_LEG_SPREAD_DEG = 10.0

# No esqueleto oficial: Foot = anca→joelho, Knee = joelho→tornozelo,
# Ankle = tornozelo→bico do pé (nomes do upstream, não a anatomia).
_LEG_BASES = ("Foot", "Knee", "Ankle")


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


def _rot_xy(point: list[float], pivot: np.ndarray, deg: float) -> list[float]:
    """Roda ``point`` no plano XY (sobre Z) à volta de ``pivot``."""
    a = math.radians(deg)
    rel = np.asarray(point, dtype=np.float64) - pivot
    x = rel[0] * math.cos(a) - rel[1] * math.sin(a)
    y = rel[0] * math.sin(a) + rel[1] * math.cos(a)
    return [float(pivot[0] + x), float(pivot[1] + y), float(point[2])]


def spread_legs(
    bones: dict[str, list[list[float]]],
    spread_deg: float = APOSE_LEG_SPREAD_DEG,
) -> dict[str, list[list[float]]]:
    """Abre as pernas ±``spread_deg`` à volta de cada anca (plano XY).

    ``R`` está em -X e ``L`` em +X: para afastar, R roda para -X (ângulo
    negativo) e L para +X (positivo). A cadeia inteira (anca→joelho→tornozelo→
    bico) roda solidária, por isso o joelho não dobra.
    """
    out = {name: [list(seg[0]), list(seg[1])] for name, seg in bones.items()}
    for side, sign in (("R", -1.0), ("L", 1.0)):
        hip_name = f"Foot.{side}"
        if hip_name not in out:
            continue
        pivot = np.asarray(out[hip_name][0], dtype=np.float64)
        angle = sign * abs(spread_deg)
        for base in _LEG_BASES:
            name = f"{base}.{side}"
            if name not in out:
                continue
            start, end = out[name]
            out[name] = [_rot_xy(start, pivot, angle), _rot_xy(end, pivot, angle)]
    return out


def _ankle_gap(bones: dict[str, list[list[float]]]) -> float | None:
    """Distância em X entre as pontas dos tornozelos (None se faltar um lado)."""
    xs = []
    for side in ("R", "L"):
        ankle = bones.get(f"Ankle.{side}")
        if not ankle:
            return None
        xs.append(ankle[1][0])
    return abs(xs[1] - xs[0])


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

    spread = spread_legs(bones)
    OUT_SPREAD_JSON.write_text(json.dumps(spread, indent=4) + "\n", encoding="utf-8")
    write_txt(spread, OUT_SPREAD_TXT)

    gap_before = _ankle_gap(bones)
    gap_after = _ankle_gap(spread)
    print(f"{SRC_TXT.name}: {len(names)} ossos → {len(kept)} (removidos {removed} de dedos)")
    if gap_before is not None and gap_after is not None:
        print(
            f"pernas: tornozelos {gap_before:.3f} → {gap_after:.3f} "
            f"(±{APOSE_LEG_SPREAD_DEG:g}°)"
        )
    print(f"escritos: {OUT_JSON.name}, {OUT_TXT.name}")
    print(f"escritos: {OUT_SPREAD_JSON.name}, {OUT_SPREAD_TXT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
