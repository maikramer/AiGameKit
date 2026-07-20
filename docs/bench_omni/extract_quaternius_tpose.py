#!/usr/bin/env python3
"""Extract Quaternius UAL ``A_TPose`` bones → Omni pose ``.txt`` / ``.json``.

Requires ``bpy`` (Animator3D / Text3D / Rigging3D venv) and cached Quaternius pack
(``gamedev_shared.quaternius_fetch.fetch_quaternius_pack``).

Usage::

    Animator3D/.venv/bin/python docs/bench_omni/extract_quaternius_tpose.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).resolve().parent
OUT_TXT = OUT_DIR / "quaternius_tpose_bone.txt"
OUT_JSON = OUT_DIR / "quaternius_tpose_bone.json"

MAJOR = [
    "pelvis",
    "spine_01",
    "spine_02",
    "spine_03",
    "neck_01",
    "Head",
    "clavicle_l",
    "upperarm_l",
    "lowerarm_l",
    "hand_l",
    "clavicle_r",
    "upperarm_r",
    "lowerarm_r",
    "hand_r",
    "thigh_l",
    "calf_l",
    "foot_l",
    "ball_l",
    "thigh_r",
    "calf_r",
    "foot_r",
    "ball_r",
]
FINGER_KEEP = (
    "index_01",
    "middle_01",
    "ring_01",
    "pinky_01",
    "thumb_01",
    "thumb_02",
    "index_02",
    "middle_02",
)


def _z_up_to_y_up(p: np.ndarray) -> np.ndarray:
    """Map Quaternius/UE Z-up → Omni Y-up: (x,y,z) → (x,z,-y)."""
    return np.array([p[0], p[2], -p[1]], dtype=np.float64)


def main() -> int:
    import bpy

    from gamedev_shared.quaternius_fetch import fetch_quaternius_pack

    pack = fetch_quaternius_pack(on_status=print)
    glb = pack.glb

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(glb))
    arm = next((o for o in bpy.context.scene.objects if o.type == "ARMATURE"), None)
    if arm is None:
        print("ERROR: no armature", file=sys.stderr)
        return 1
    tpose = next((a for a in bpy.data.actions if a.name == "A_TPose"), None)
    if tpose is None:
        print("ERROR: A_TPose action missing", file=sys.stderr)
        return 1

    bpy.context.view_layer.objects.active = arm
    bpy.ops.object.mode_set(mode="POSE")
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = tpose
    bpy.context.scene.frame_set(int(tpose.frame_range[0]))
    bpy.context.view_layer.update()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    arm_eval = arm.evaluated_get(depsgraph)
    aw = arm_eval.matrix_world

    segments: list[tuple[str, np.ndarray, np.ndarray]] = []
    for pb in arm_eval.pose.bones:
        nl = pb.name.lower()
        if nl == "root" or "leaf" in nl:
            continue
        head = _z_up_to_y_up(np.array((aw @ pb.head)[:3], dtype=np.float64))
        tail = _z_up_to_y_up(np.array((aw @ pb.tail)[:3], dtype=np.float64))
        segments.append((pb.name, head, tail))

    pts = np.stack([np.concatenate([h, t]) for _, h, t in segments], axis=0)
    coords = pts.reshape(-1, 3)
    center = (coords.max(0) + coords.min(0)) / 2.0
    extent = float((coords.max(0) - coords.min(0)).max())
    scale = 1.98 / extent if extent > 0 else 1.0
    pts6 = pts.copy()
    pts6[:, 0:3] = (pts[:, 0:3] - center) * scale
    pts6[:, 3:6] = (pts[:, 3:6] - center) * scale

    name_to_row = {n: i for i, (n, _, _) in enumerate(segments)}
    selected = [n for n in MAJOR if n in name_to_row]
    for n, _, _ in segments:
        if n in selected:
            continue
        base = n.replace("_l", "").replace("_r", "")
        if any(base.startswith(fk) or fk in base for fk in FINGER_KEEP):
            selected.append(n)
    for n, _, _ in segments:
        if len(selected) >= 51:
            break
        if n not in selected:
            selected.append(n)
    selected = selected[:51]

    idx = [name_to_row[n] for n in selected]
    out = pts6[idx]
    np.savetxt(OUT_TXT, out, fmt="%.18e")
    meta = {
        "_meta": {
            "source": str(glb),
            "action": "A_TPose",
            "coords": "Y-up normalized, Omni pose txt (head+tail xyz)",
            "n_bones": len(selected),
        },
        **{n: [out[i, :3].tolist(), out[i, 3:].tolist()] for i, n in enumerate(selected)},
    }
    OUT_JSON.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"wrote {OUT_TXT} ({len(selected)} bones)")
    print(f"Y span [{out[:, 1].min():.3f}, {out[:, 1].max():.3f}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
