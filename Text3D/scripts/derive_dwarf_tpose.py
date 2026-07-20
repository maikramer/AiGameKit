#!/usr/bin/env python3
"""Deriva T-pose Omni para anões / humanoides baixos (chibi) a partir do Quaternius.

Ajusta proporções face ao adulto:
- pernas + torso mais curtos
- cabeça maior (relativa ao pescoço)
- ombros mais baixos e braços um pouco mais abertos (limpa cabeça grande)

Não precisa bpy — lê ``quaternius_tpose_bone.json`` empacotado.

Usage::

    Text3D/.venv/bin/python Text3D/scripts/derive_dwarf_tpose.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

OMNI_DIR = Path(__file__).resolve().parents[1] / "src" / "text3d" / "data" / "omni"
SRC_JSON = OMNI_DIR / "quaternius_tpose_bone.json"
OUT_JSON = OMNI_DIR / "quaternius_tpose_dwarf_bone.json"
OUT_TXT = OMNI_DIR / "quaternius_tpose_dwarf_bone.txt"

# Bones do tronco/cabeça vs cadeia de braço.
HEAD_BONES = frozenset({"Head", "neck_01"})
ARM_MARKERS = (
    "clavicle",
    "upperarm",
    "lowerarm",
    "hand",
    "thumb",
    "index",
    "middle",
    "ring",
    "pinky",
)


def _is_arm(name: str) -> bool:
    nl = name.lower()
    return any(m in nl for m in ARM_MARKERS)


def _renorm(pts6: np.ndarray, *, target_extent: float = 1.98) -> np.ndarray:
    coords = pts6.reshape(-1, 3)
    center = (coords.max(0) + coords.min(0)) / 2.0
    extent = float((coords.max(0) - coords.min(0)).max())
    scale = target_extent / extent if extent > 0 else 1.0
    out = pts6.copy()
    out[:, 0:3] = (pts6[:, 0:3] - center) * scale
    out[:, 3:6] = (pts6[:, 3:6] - center) * scale
    return out


def derive(
    bones: dict[str, list],
    *,
    body_y_scale: float = 0.72,
    head_y_scale: float = 1.28,
    arm_drop: float = 0.10,
    arm_x_scale: float = 1.14,
) -> tuple[list[str], np.ndarray]:
    """Aplica morph anão/chibi. Devolve ordem de bones + pts ``(N,6)``."""
    names = [n for n in bones if not n.startswith("_")]
    if "neck_01" not in bones or "Head" not in bones:
        raise KeyError("JSON Quaternius sem neck_01/Head")

    neck_y = float(bones["neck_01"][0][1])
    pts = np.zeros((len(names), 6), dtype=np.float64)
    for i, name in enumerate(names):
        h, t = bones[name]
        pts[i, :3] = h
        pts[i, 3:] = t

    for i, name in enumerate(names):
        for col in (0, 3):  # head xyz, tail xyz
            y = pts[i, col + 1]
            x = pts[i, col]
            z = pts[i, col + 2]
            if name in HEAD_BONES or (name == "Head"):
                # Escala cabeça para cima a partir do pescoço.
                y = neck_y + (y - neck_y) * head_y_scale
            else:
                # Comprime corpo abaixo do pescoço em direcção aos pés.
                y = neck_y + (y - neck_y) * body_y_scale
            if _is_arm(name):
                y -= arm_drop
                x *= arm_x_scale
            pts[i, col] = x
            pts[i, col + 1] = y
            pts[i, col + 2] = z

    pts = _renorm(pts)
    return names, pts


def main() -> int:
    if not SRC_JSON.is_file():
        print(f"ERROR: missing {SRC_JSON}", file=sys.stderr)
        return 1
    doc = json.loads(SRC_JSON.read_text(encoding="utf-8"))
    bones = {k: v for k, v in doc.items() if not k.startswith("_")}
    names, pts = derive(bones)

    np.savetxt(OUT_TXT, pts, fmt="%.18e")
    meta = {
        "_meta": {
            "source": "derived from quaternius_tpose_bone.json",
            "preset": "quaternius-tpose-dwarf",
            "coords": "Y-up normalized [-0.99,0.99], Omni pose txt (head xyz + tail xyz)",
            "n_bones": len(names),
            "morph": {
                "body_y_scale": 0.72,
                "head_y_scale": 1.28,
                "arm_drop": 0.10,
                "arm_x_scale": 1.14,
            },
            "use_for": "dwarf / chibi / big-head short humanoids (goblin, bogling, …)",
        },
        **{n: [pts[i, :3].tolist(), pts[i, 3:].tolist()] for i, n in enumerate(names)},
    }
    OUT_JSON.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    print(f"wrote {OUT_TXT} ({len(names)} bones)")
    print(f"wrote {OUT_JSON}")
    print(f"Y span [{pts[:, 1].min():.3f}, {pts[:, 1].max():.3f}]")
    print(f"X span [{pts[:, 0].min():.3f}, {pts[:, 0].max():.3f}]")
    # Sanity vs adult
    adult = np.loadtxt(OMNI_DIR / "quaternius_tpose_bone.txt")
    print(
        f"adult Y=[{adult[:, 1].min():.3f},{adult[:, 1].max():.3f}] X=[{adult[:, 0].min():.3f},{adult[:, 0].max():.3f}]"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
