"""Testes unitários para ``reweight_back_props_to_spine`` (acessórios dorsais rígidos)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np
from rigging3d.back_prop_weights import reweight_back_props_to_spine


def _write_synthetic_humanoid_glb(path: Path, verts: np.ndarray, joints: np.ndarray, weights: np.ndarray) -> None:
    """Cria GLB mínimo válido com esqueleto humanoid (pelvis/spine_03/clavicle/neck/Head)."""
    joint_defs = [
        ("pelvis", [0.0, 0.86, 0.04]),
        ("spine_03", [0.0, 1.15, 0.03]),
        ("clavicle_l", [0.06, 1.25, 0.02]),
        ("clavicle_r", [-0.06, 1.25, 0.02]),
        ("upperarm_r", [-0.17, 1.18, 0.02]),
        ("neck_01", [0.0, 1.28, 0.02]),
        ("Head", [0.0, 1.34, 0.03]),
    ]
    ibms = []
    nodes = [{"name": "Mesh", "mesh": 0, "skin": 0}]
    joint_indices = []
    for name, pos in joint_defs:
        joint_indices.append(len(nodes))
        nodes.append({"name": name, "translation": pos})
        m = np.eye(4, dtype=np.float32)
        m[:3, 3] = pos
        ibms.append(np.linalg.inv(m).T)  # glTF column-major

    pos_b = verts.astype(np.float32).tobytes()
    j_b = joints.astype(np.uint8).tobytes()
    w_b = weights.astype(np.float32).tobytes()
    ibm_b = np.stack(ibms, axis=0).astype(np.float32).tobytes()

    blob = bytearray()

    def add_bv(data: bytes) -> tuple[int, int]:
        while len(blob) % 4:
            blob.append(0)
        off = len(blob)
        blob.extend(data)
        return off, len(data)

    p_off, p_len = add_bv(pos_b)
    j_off, j_len = add_bv(j_b)
    w_off, w_len = add_bv(w_b)
    i_off, i_len = add_bv(ibm_b)
    while len(blob) % 4:
        blob.append(0)

    n_v = len(verts)
    g = {
        "asset": {"version": "2.0"},
        "nodes": nodes,
        "meshes": [
            {
                "primitives": [
                    {
                        "attributes": {"POSITION": 0, "JOINTS_0": 1, "WEIGHTS_0": 2},
                    }
                ]
            }
        ],
        "skins": [{"joints": joint_indices, "inverseBindMatrices": 3}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": p_off, "byteLength": p_len},
            {"buffer": 0, "byteOffset": j_off, "byteLength": j_len},
            {"buffer": 0, "byteOffset": w_off, "byteLength": w_len},
            {"buffer": 0, "byteOffset": i_off, "byteLength": i_len},
        ],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n_v, "type": "VEC3"},
            {"bufferView": 1, "componentType": 5121, "count": n_v, "type": "VEC4"},
            {"bufferView": 2, "componentType": 5126, "count": n_v, "type": "VEC4"},
            {"bufferView": 3, "componentType": 5126, "count": len(joint_defs), "type": "MAT4"},
        ],
        "buffers": [{"byteLength": len(blob)}],
    }
    js = json.dumps(g).encode("utf-8")
    while len(js) % 4:
        js += b" "
    total = 12 + 8 + len(js) + 8 + len(blob)
    out = (
        struct.pack("<III", 0x46546C67, 2, total)
        + struct.pack("<II", len(js), 0x4E4F534A)
        + js
        + struct.pack("<II", len(blob), 0x004E4942)
        + bytes(blob)
    )
    path.write_bytes(out)


def _read_skin_weights(path: Path) -> tuple[np.ndarray, np.ndarray]:
    raw = path.read_bytes()
    c0_len, _ = struct.unpack_from("<II", raw, 12)
    g = json.loads(raw[20 : 20 + c0_len])
    c1_len, _ = struct.unpack_from("<II", raw, 20 + c0_len)
    blob = raw[28 + c0_len : 28 + c0_len + c1_len]
    bv1, bv2 = g["bufferViews"][1], g["bufferViews"][2]
    n = g["accessors"][1]["count"]
    j = np.frombuffer(blob, dtype=np.uint8, count=n * 4, offset=bv1["byteOffset"]).reshape((n, 4))
    w = np.frombuffer(blob, dtype=np.float32, count=n * 4, offset=bv2["byteOffset"]).reshape((n, 4))
    return j, w


def test_reweight_back_props_moves_map_tube_to_spine_03_and_keeps_arm_intact(tmp_path: Path) -> None:
    # Índices: 0=pelvis, 1=spine_03, 2=clavicle_l, 3=clavicle_r, 4=upperarm_r, 5=neck_01, 6=Head
    verts = np.array(
        [
            [0.0, 0.0, 0.18],  # 0: ponta do pé (+Z = frente)
            [0.0, 1.55, 0.02],  # 1: topo da cabeça (define altura H = 1.55)
            [-0.18, 1.44, -0.14],  # 2: topo do tubo de mapa atrás do ombro direito (errado em clavicle_r)
            [-0.12, 1.24, -0.18],  # 3: meio do tubo atrás das costas (errado em clavicle_r)
            [-0.14, 1.24, 0.01],  # 4: ombro direito anatómico real (deve ficar em clavicle_r!)
            [-0.45, 1.05, -0.02],  # 5: antebraço/braço direito lateral (deve ficar em upperarm_r!)
            [0.0, 1.46, -0.02],  # 6: cabeça real (deve ficar em Head!)
        ],
        dtype=np.float32,
    )
    joints = np.array(
        [
            [0, 0, 0, 0],
            [6, 0, 0, 0],
            [3, 0, 0, 0],  # clavicle_r
            [3, 1, 0, 0],  # clavicle_r + spine_03
            [3, 0, 0, 0],  # clavicle_r (real shoulder)
            [4, 0, 0, 0],  # upperarm_r (real arm)
            [6, 0, 0, 0],  # Head (real head)
        ],
        dtype=np.uint8,
    )
    weights = np.array(
        [
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [0.8, 0.2, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 0.0, 0.0],
        ],
        dtype=np.float32,
    )

    glb = tmp_path / "hero_rigged.glb"
    _write_synthetic_humanoid_glb(glb, verts, joints, weights)

    changed = reweight_back_props_to_spine(glb)
    assert changed == 2  # Apenas vértices 2 e 3 (tubo de mapa)!

    j_after, w_after = _read_skin_weights(glb)
    # Vértices 2 e 3 (tubo de mapa) → 100% spine_03 (índice 1)
    assert j_after[2, 0] == 1 and np.isclose(w_after[2, 0], 1.0, atol=1e-4)
    assert j_after[3, 0] == 1 and np.isclose(w_after[3, 0], 1.0, atol=1e-4)
    # Ombro real (4), braço real (5) e cabeça real (6) intocados!
    assert j_after[4, 0] == 3 and np.isclose(w_after[4, 0], 1.0, atol=1e-4)
    assert j_after[5, 0] == 4 and np.isclose(w_after[5, 0], 1.0, atol=1e-4)
    assert j_after[6, 0] == 6 and np.isclose(w_after[6, 0], 1.0, atol=1e-4)
