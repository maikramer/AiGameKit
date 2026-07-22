#!/usr/bin/env python3
"""Deriva variantes de pose Omni: T-pose mínima (sem dedos) e A-pose.

Estado canónico (review simple-rpg 2026-07, hero lab): o esqueleto de
condicionamento tem **22 ossos** — coluna (pelvis→spine_03), pescoço/cabeça,
braços até ao pulso (clavicle→hand) e pernas (thigh→ball). **Sem ossos de
dedos**: com dedos, o modelo gera dedos esquisitos/deformados; sem eles, a mão
forma-se a partir da imagem (cartoon = mão simples/mitene, que é o estilo alvo)
com o pulso guiado pelo osso ``hand_*``.

Intervenções deste script (idempotente — corre as 3 de uma vez):

1. **Strip de dedos** — remove index/middle/ring/pinky/thumb do T-pose base
   (adulto e dwarf), ficando ossos 0-21 (tronco+membros).
2. **A-pose** (`quaternius_apose_bone.txt`) — cadeia de braço rodada -45° à
   volta do ombro (glenohumeral = head de ``upperarm_*``) sobre a base já
   mínima. Para humanoides musculados/gordos (merchant/ogre): a T-pose
   horizontal estica músculo/gordura.
3. **Leg spread** — pernas abertas ~10° à volta do hip (head de ``thigh_*``)
   para haver gap claro entre coxas (evita webbing / pernas fundidas no MC).

Não precisa bpy — lê ``quaternius_tpose_bone.json`` empacotado (fonte
editável; o ``.txt`` é regenerado a partir do JSON).

NOTA: o fingerprint Omni usa o NOME do preset, não o conteúdo do ficheiro —
mudar o esqueleto NÃO invalida shapes existentes (sem regen forçada).

Usage::

    Text3D/.venv/bin/python Text3D/scripts/derive_openhand_apose.py
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np

OMNI_DIR = Path(__file__).resolve().parents[1] / "src" / "text3d" / "data" / "omni"
SRC_JSON = OMNI_DIR / "quaternius_tpose_bone.json"
DWARF_JSON = OMNI_DIR / "quaternius_tpose_dwarf_bone.json"
APOSE_JSON = OMNI_DIR / "quaternius_apose_bone.json"
APOSE_DWARF_JSON = OMNI_DIR / "quaternius_apose_dwarf_bone.json"

# Ângulo de descida dos braços na A-pose (graus abaixo da horizontal).
APOSE_ARM_ANGLE_DEG = 45.0
# Abertura das pernas na A-pose (graus para fora no plano XY). Demasiado
# pequeno → MC funde coxas; demasiado grande → pose estranha / pés demasiado
# afastados. ~10° dá gap leve sem caricatura.
APOSE_LEG_SPREAD_DEG = 10.0

# Dedos a remover do esqueleto (ficam 22 ossos: 0-21 da numeração original).
FINGER_NAMES = ("index", "middle", "ring", "pinky", "thumb")
ARM_CHAIN = ("upperarm", "lowerarm", "hand")
LEG_CHAIN = ("thigh", "calf", "foot", "ball")


def _load_bones(path: Path) -> dict[str, np.ndarray]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    meta = raw.pop("_meta", {})
    bones = {name: np.asarray(seg, dtype=np.float64) for name, seg in raw.items()}
    bones["_meta"] = meta  # type: ignore[assignment]
    return bones


def _write_bones(bones: dict[str, np.ndarray], json_path: Path, *, note: str) -> None:
    meta = dict(bones.get("_meta") or {})
    meta["derived_by"] = f"scripts/derive_openhand_apose.py ({note})"
    out: dict[str, object] = {"_meta": meta}
    txt_lines: list[str] = []
    for name, seg in bones.items():
        if name == "_meta":
            continue
        out[name] = [[float(v) for v in seg[0]], [float(v) for v in seg[1]]]
        txt_lines.append(" ".join(f"{v:.18e}" for v in (*seg[0], *seg[1])))
    json_path.write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    json_path.with_suffix(".txt").write_text("\n".join(txt_lines) + "\n", encoding="utf-8")


def strip_fingers(bones: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    """Remove ossos de dedos — fica tronco+membros (22 ossos)."""
    out = {name: seg for name, seg in bones.items() if name == "_meta" or not name.startswith(FINGER_NAMES)}
    removed = len(bones) - len(out)
    out["_meta"] = dict(out.get("_meta") or {})
    out["_meta"]["n_bones"] = len(out) - 1
    print(f"  strip: {removed} ossos de dedos removidos → {len(out) - 1} ossos")
    return out


def _rot_xy(point: np.ndarray, pivot: np.ndarray, deg: float) -> np.ndarray:
    """Roda ``point`` no plano XY (sobre Z) à volta de ``pivot``."""
    a = math.radians(deg)
    rel = point - pivot
    x = rel[0] * math.cos(a) - rel[1] * math.sin(a)
    y = rel[0] * math.sin(a) + rel[1] * math.cos(a)
    return np.array([pivot[0] + x, pivot[1] + y, pivot[2] + rel[2]])


def apose_arms(bones: dict[str, np.ndarray], angle_deg: float = APOSE_ARM_ANGLE_DEG) -> dict[str, np.ndarray]:
    """Roda a cadeia de braço (upperarm→hand) para A-pose."""
    out = dict(bones)
    for side in ("_l", "_r"):
        upper = f"upperarm{side}"
        if upper not in out:
            continue
        pivot = out[upper][0].copy()
        # _l (+X) desce com rotação negativa sobre Z; _r (-X) com positiva.
        angle = -abs(angle_deg) if side == "_l" else abs(angle_deg)
        chain = [n for n in out if n != "_meta" and n.endswith(side) and n.startswith(ARM_CHAIN)]
        for name in chain:
            seg = out[name]
            out[name] = np.stack([_rot_xy(seg[0], pivot, angle), _rot_xy(seg[1], pivot, angle)])
    return out


def apose_legs(
    bones: dict[str, np.ndarray],
    spread_deg: float = APOSE_LEG_SPREAD_DEG,
) -> dict[str, np.ndarray]:
    """Abre pernas (thigh→ball) à volta do hip — gap entre coxas no condicionamento Omni."""
    out = dict(bones)
    for side in ("_l", "_r"):
        thigh = f"thigh{side}"
        if thigh not in out:
            continue
        pivot = out[thigh][0].copy()
        # Outward no plano XY: _l (+X) → +ângulo; _r (−X) → −ângulo.
        angle = abs(spread_deg) if side == "_l" else -abs(spread_deg)
        chain = [n for n in out if n != "_meta" and n.endswith(side) and n.startswith(LEG_CHAIN)]
        for name in chain:
            seg = out[name]
            out[name] = np.stack([_rot_xy(seg[0], pivot, angle), _rot_xy(seg[1], pivot, angle)])
    return out


def _report(bones: dict[str, np.ndarray], label: str) -> None:
    names = [n for n in bones if n != "_meta"]
    ys = [bones[f"hand_{s}"][1][1] for s in ("l", "r") if f"hand_{s}" in bones]
    feet_x = [bones[f"foot_{s}"][1][0] for s in ("l", "r") if f"foot_{s}" in bones]
    extra = ""
    if ys:
        extra += f", mãos y≈{min(ys):+.3f}..{max(ys):+.3f}"
    if len(feet_x) == 2:
        extra += f", pés x≈{min(feet_x):+.3f}..{max(feet_x):+.3f} (gap={max(feet_x) - min(feet_x):.3f})"
    print(f"  {label}: {len(names)} ossos{extra}")


def main() -> int:
    base = _load_bones(SRC_JSON)
    _report(base, "base (antes)")

    # 1) T-pose base mínima (sem dedos) — sobrescreve os ficheiros base.
    minimal = strip_fingers(base)
    _write_bones(minimal, SRC_JSON, note="esqueleto mínimo 22 ossos (sem dedos)")
    print(f"  → {SRC_JSON.name} + .txt (T-pose mínima)")

    # 2) Dwarf: mesmo strip (layout idêntico).
    if DWARF_JSON.is_file():
        dwarf = strip_fingers(_load_bones(DWARF_JSON))
        _write_bones(dwarf, DWARF_JSON, note="esqueleto mínimo 22 ossos (sem dedos)")
        print(f"  → {DWARF_JSON.name} + .txt (dwarf mínimo)")

    # 3) A-pose (braços -45° + pernas abertas) sobre a base mínima.
    apose = apose_legs(apose_arms(minimal))
    _report(apose, "A-pose")
    _write_bones(
        apose,
        APOSE_JSON,
        note=(f"A-pose braços -{APOSE_ARM_ANGLE_DEG:g}°, pernas ±{APOSE_LEG_SPREAD_DEG:g}°, sem dedos"),
    )
    print(f"  → {APOSE_JSON.name} + .txt (A-pose mínima)")

    # 4) A-pose dwarf — mesma rotação sobre o dwarf mínimo.
    if DWARF_JSON.is_file():
        apose_dwarf = apose_legs(apose_arms(_load_bones(DWARF_JSON)))
        _report(apose_dwarf, "A-pose dwarf")
        _write_bones(
            apose_dwarf,
            APOSE_DWARF_JSON,
            note=(f"A-pose dwarf braços -{APOSE_ARM_ANGLE_DEG:g}°, pernas ±{APOSE_LEG_SPREAD_DEG:g}°, sem dedos"),
        )
        print(f"  → {APOSE_DWARF_JSON.name} + .txt (A-pose dwarf mínima)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
