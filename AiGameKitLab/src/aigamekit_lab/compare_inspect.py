"""Diff estruturado entre dois JSONs de inspect GLB."""

from __future__ import annotations

from typing import Any


def _num(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def diff_inspect(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    """Gera resumo numérico e texto para CI / diff_report."""
    out: dict[str, Any] = {
        "summary": "",
        "mesh_totals_delta": {},
        "world_bounds_delta": {},
        "armatures": [],
        "bones": {"only_a": [], "only_b": [], "common_count": 0},
        "animations": {"count_a": 0, "count_b": 0},
    }

    mta = a.get("mesh_totals") or {}
    mtb = b.get("mesh_totals") or {}
    for key in ("vertex_count", "face_count"):
        va, vb = _num(mta.get(key)), _num(mtb.get(key))
        if va is not None or vb is not None:
            out["mesh_totals_delta"][key] = {
                "a": mta.get(key),
                "b": mtb.get(key),
                "delta": (vb - va) if va is not None and vb is not None else None,
            }

    wba = a.get("world_bounds") or {}
    wbb = b.get("world_bounds") or {}
    for key in ("max_extent",):
        va, vb = _num(wba.get(key)), _num(wbb.get(key))
        if va is not None or vb is not None:
            delta = (vb - va) if va is not None and vb is not None else None
            out["world_bounds_delta"][key] = {
                "a": wba.get(key),
                "b": wbb.get(key),
                "delta": delta,
            }
    sza = wba.get("size")
    szb = wbb.get("size")
    if isinstance(sza, list) and isinstance(szb, list) and len(sza) == len(szb):
        out["world_bounds_delta"]["size"] = [
            {
                "axis": i,
                "a": sza[i],
                "b": szb[i],
                "delta": (
                    _num(szb[i]) - _num(sza[i]) if _num(sza[i]) is not None and _num(szb[i]) is not None else None
                ),
            }
            for i in range(len(sza))
        ]

    arms_a = a.get("armatures") or []
    arms_b = b.get("armatures") or []
    out["armatures"] = []
    n = max(len(arms_a), len(arms_b))
    for i in range(n):
        aa = arms_a[i] if i < len(arms_a) else {}
        bb = arms_b[i] if i < len(arms_b) else {}
        bones_a = aa.get("bones") or aa.get("bones_sample") or []
        bones_b_list = bb.get("bones") or bb.get("bones_sample") or []
        set_a = set(str(x) for x in bones_a)
        set_b = set(str(x) for x in bones_b_list)
        out["armatures"].append(
            {
                "index": i,
                "bone_count": {"a": aa.get("bone_count"), "b": bb.get("bone_count")},
                "bones_intersection": len(set_a & set_b),
                "bones_only_a": sorted(set_a - set_b)[:64],
                "bones_only_b": sorted(set_b - set_a)[:64],
            }
        )

    # União global de ossos (primeiro armature com lista completa)
    all_a: set[str] = set()
    all_b: set[str] = set()
    for arm in arms_a:
        for x in arm.get("bones") or arm.get("bones_sample") or []:
            all_a.add(str(x))
    for arm in arms_b:
        for x in arm.get("bones") or arm.get("bones_sample") or []:
            all_b.add(str(x))
    out["bones"]["only_a"] = sorted(all_a - all_b)[:128]
    out["bones"]["only_b"] = sorted(all_b - all_a)[:128]
    out["bones"]["common_count"] = len(all_a & all_b)

    act_a = a.get("actions") or []
    act_b = b.get("actions") or []
    out["animations"]["count_a"] = len(act_a) if isinstance(act_a, list) else 0
    out["animations"]["count_b"] = len(act_b) if isinstance(act_b, list) else 0

    parts = []
    d_vert = out["mesh_totals_delta"].get("vertex_count", {}).get("delta")
    if d_vert is not None:
        parts.append(f"Δvértices={d_vert:+.0f}")
    d_ext = out["world_bounds_delta"].get("max_extent", {}).get("delta")
    if d_ext is not None:
        parts.append(f"Δmax_extent={d_ext:+.4f}")
    parts.append(f"ossos_comuns={out['bones']['common_count']}")
    out["summary"] = "; ".join(parts) if parts else "sem deltas numéricos simples"

    return out
