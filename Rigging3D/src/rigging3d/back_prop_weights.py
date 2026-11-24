"""Pós-processamento de skin weights para acessórios rígidos nas costas de humanoides.

Modelos neurais de skinning (ex.: SkinTokens) tendem a atribuir 90–100% de peso de
``clavicle_l``/``clavicle_r`` (ou ``upperarm_*``) a tubos de mapa, aljavas, mochilas e
capas que sobressaem atrás dos ombros ou acima da clavícula. Quando o personagem sai da
A-pose para ``idle``/``walk`` e baixa os ombros, o topo do acessório entorta para dentro
da cabeça/pescoço.

:func:`reweight_back_props_to_spine` transfere suavemente os pesos de ombro/braço dessas
regiões (atrás do plano dorsal do tronco ou acima da linha clavícula→cabeça) para
``spine_03``, mantendo o acessório preso de forma rígida ao dorso sem afetar braços nem
ombros anatómicos.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

import numpy as np

_DTYPE_BY_COMP = {
    5120: np.int8,
    5121: np.uint8,
    5122: np.int16,
    5123: np.uint16,
    5125: np.uint32,
    5126: np.float32,
}
_COUNT_BY_TYPE = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

_SHOULDER_BONES = frozenset({"clavicle_l", "clavicle_r", "upperarm_l", "upperarm_r"})


def _read_accessor(g: dict, blob: bytes | bytearray, acc_idx: int) -> tuple[np.ndarray, int, int, np.dtype, int]:
    acc = g["accessors"][acc_idx]
    bv = g["bufferViews"][acc["bufferView"]]
    off = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
    stride = bv.get("byteStride")
    dt = np.dtype(_DTYPE_BY_COMP[acc["componentType"]])
    k = _COUNT_BY_TYPE[acc["type"]]
    count = int(acc["count"])
    if stride is not None and stride != dt.itemsize * k:
        raise ValueError("Interleaved bufferView não suportado em reweight_back_props_to_spine")
    arr = np.frombuffer(blob, dtype=dt, count=count * k, offset=off)
    if k > 1:
        arr = arr.reshape((count, k))
    return arr, off, count * k * dt.itemsize, dt, k


def _smoothstep(edge0: float, edge1: float, x: np.ndarray) -> np.ndarray:
    if edge1 <= edge0:
        return (x >= edge1).astype(np.float32)
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return (t * t * (3.0 - 2.0 * t)).astype(np.float32)


def reweight_back_props_to_spine(glb_path: Path) -> int:
    """Transfere pesos de ombro/braço em acessórios dorsais/acima do ombro para ``spine_03``.

    Reescreve o GLB in-place apenas quando encontra vértices afetados.
    Devolve o número de vértices ajustados (0 se esqueleto não for humanoid ou nada a fazer).
    """
    if not glb_path.is_file() or glb_path.stat().st_size < 20:
        return 0

    raw = glb_path.read_bytes()
    magic, version, _total = struct.unpack_from("<III", raw, 0)
    if magic != 0x46546C67:
        return 0
    c0_len, c0_type = struct.unpack_from("<II", raw, 12)
    if c0_type != 0x4E4F534A:
        return 0
    g = json.loads(raw[20 : 20 + c0_len])
    bin_header_off = 20 + c0_len
    if bin_header_off + 8 > len(raw):
        return 0
    c1_len, c1_type = struct.unpack_from("<II", raw, bin_header_off)
    if c1_type != 0x004E4942:
        return 0
    blob = bytearray(raw[bin_header_off + 8 : bin_header_off + 8 + c1_len])

    skins = g.get("skins") or []
    meshes = g.get("meshes") or []
    nodes = g.get("nodes") or []
    if not skins or not meshes:
        return 0

    skin = skins[0]
    joints = skin.get("joints") or []
    if not joints or "inverseBindMatrices" not in skin:
        return 0

    joint_names = [nodes[ni].get("name", "") for ni in joints]
    name_to_ji = {name: ji for ji, name in enumerate(joint_names)}

    spine_name = next((n for n in ("spine_03", "UpperChest", "chest", "spine_02") if n in name_to_ji), None)
    if spine_name is None:
        return 0
    if not any(n in name_to_ji for n in ("clavicle_l", "clavicle_r")):
        return 0
    if "neck_01" not in name_to_ji or "Head" not in name_to_ji:
        return 0

    spine_ji = name_to_ji[spine_name]
    shoulder_jis = {name_to_ji[n] for n in _SHOULDER_BONES if n in name_to_ji}
    if not shoulder_jis:
        return 0

    try:
        ibms_raw, *_ = _read_accessor(g, blob, skin["inverseBindMatrices"])
    except (KeyError, ValueError):
        return 0
    ibms = ibms_raw.reshape((-1, 4, 4)).astype(np.float64)

    joint_pos: dict[str, np.ndarray] = {}
    for ji, jn in enumerate(joint_names):
        if jn in (spine_name, "neck_01", "Head", "clavicle_l", "clavicle_r", "pelvis"):
            try:
                inv = np.linalg.inv(ibms[ji].T)
                joint_pos[jn] = inv[:3, 3].astype(np.float32)
            except np.linalg.LinAlgError:
                return 0

    spine_p = joint_pos[spine_name]
    neck_p = joint_pos["neck_01"]
    head_p = joint_pos["Head"]

    # Altura do personagem a partir das primitivas do mesh principal
    all_y_min = float("inf")
    all_y_max = float("-inf")
    all_foot_z: list[np.ndarray] = []
    for mesh in meshes:
        for prim in mesh.get("primitives") or []:
            attrs = prim.get("attributes") or {}
            if "POSITION" not in attrs:
                continue
            try:
                pos_arr, *_ = _read_accessor(g, blob, attrs["POSITION"])
            except (KeyError, ValueError):
                continue
            if pos_arr.size == 0:
                continue
            all_y_min = min(all_y_min, float(pos_arr[:, 1].min()))
            all_y_max = max(all_y_max, float(pos_arr[:, 1].max()))
            foot_mask = pos_arr[:, 1] < (all_y_min + 0.08)
            if foot_mask.any():
                all_foot_z.append(pos_arr[foot_mask, 2])

    height = all_y_max - all_y_min
    if not np.isfinite(height) or height < 0.25:
        return 0

    # Sinal do eixo "costas" (glTF canónico: frente = +Z, costas = -Z; confirma via dedos dos pés)
    back_sign = -1.0
    if all_foot_z:
        fz = np.concatenate(all_foot_z)
        # Dedos projetam-se mais para a frente do que o calcanhar para trás em relação à pélvis/coluna
        pelvis_z = float(joint_pos.get("pelvis", spine_p)[2])
        front_reach = float(fz.max()) - pelvis_z
        back_reach = pelvis_z - float(fz.min())
        if back_reach > front_reach * 1.15:
            back_sign = 1.0

    spine_x = float(spine_p[0])
    spine_z = float(spine_p[2])
    neck_y = float(neck_p[1])
    head_y = float(head_p[1])

    # Banda lateral do torso/ombros (não toca mãos nem braços abertos longe do tronco)
    max_lat = 0.24 * height
    # Faixa vertical onde acessórios de costas (tubo, aljava, mochila, capa) podem tocar clavículas
    min_back_y = float(spine_p[1]) - 0.14 * height

    total_changed = 0
    for mesh in meshes:
        for prim in mesh.get("primitives") or []:
            attrs = prim.get("attributes") or {}
            if not all(k in attrs for k in ("POSITION", "JOINTS_0", "WEIGHTS_0")):
                continue
            try:
                pos, *_ = _read_accessor(g, blob, attrs["POSITION"])
                j0, j_off, j_bytes, j_dt, _ = _read_accessor(g, blob, attrs["JOINTS_0"])
                w0, w_off, w_bytes, w_dt, _ = _read_accessor(g, blob, attrs["WEIGHTS_0"])
            except (KeyError, ValueError):
                continue
            if w_dt != np.float32 or pos.shape[0] != j0.shape[0] or pos.shape[0] != w0.shape[0]:
                continue

            j_arr = j0.copy()
            w_arr = w0.copy()

            # Distância para trás a partir da coluna dorsal (positivo = atrás das costas)
            back_dist = (pos[:, 2] - spine_z) * back_sign
            lat_dist = np.abs(pos[:, 0] - spine_x)
            y = pos[:, 1]

            in_lat = lat_dist <= max_lat
            in_vert = y >= min_back_y

            # 1) Profundidade dorsal: claramente atrás das escápulas (ombro real acaba ~0.075*H)
            t_back = _smoothstep(0.078 * height, 0.105 * height, back_dist) * in_vert.astype(np.float32)

            # 2) Saliência acima da clavícula/pescoço: nenhum ombro/braço real em A-pose sobe acima de Head.y
            hi_lo = neck_y + 0.014 * height
            hi_hi = max(hi_lo + 0.012 * height, head_y + 0.004 * height)
            t_high = _smoothstep(hi_lo, hi_hi, y)

            t = np.maximum(t_back, t_high) * in_lat.astype(np.float32)

            is_sh = np.isin(j_arr, list(shoulder_jis))
            sh_sum = (w_arr * is_sh).sum(axis=1)
            candidates = np.where((t > 1e-3) & (sh_sum > 1e-4))[0]
            if candidates.size == 0:
                continue

            for vi in candidates:
                alpha = float(t[vi])
                js = j_arr[vi]
                ws = w_arr[vi]
                transferred = 0.0
                for s in range(4):
                    if int(js[s]) in shoulder_jis and ws[s] > 0.0:
                        delta = float(ws[s]) * alpha
                        ws[s] = float(ws[s]) - delta
                        transferred += delta
                if transferred <= 1e-6:
                    continue

                # Soma o peso transferido em spine_ji (reutilizando slot existente ou substituindo o menor)
                existing = np.where(js == spine_ji)[0]
                if existing.size > 0:
                    ws[int(existing[0])] += transferred
                else:
                    min_s = int(np.argmin(ws))
                    js[min_s] = spine_ji
                    ws[min_s] = transferred

                # Ordena decrescente por peso e renormaliza para soma == 1.0
                order = np.argsort(-ws)
                js[:] = js[order]
                ws[:] = np.maximum(ws[order], 0.0)
                s_tot = float(ws.sum())
                if s_tot > 1e-8:
                    ws /= s_tot
                else:
                    js[:] = [spine_ji, 0, 0, 0]
                    ws[:] = [1.0, 0.0, 0.0, 0.0]

                j_arr[vi] = js
                w_arr[vi] = ws
                total_changed += 1

            blob[j_off : j_off + j_bytes] = j_arr.astype(j_dt, copy=False).tobytes()
            blob[w_off : w_off + w_bytes] = w_arr.astype(w_dt, copy=False).tobytes()

    if total_changed == 0:
        return 0

    out_raw = bytearray(raw)
    out_raw[bin_header_off + 8 : bin_header_off + 8 + c1_len] = blob
    glb_path.write_bytes(bytes(out_raw))
    return total_changed
