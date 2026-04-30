"""Validação declarativa de GLB (via inspect nativo) contra regras YAML/JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml


def load_rules_file(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw) if path.suffix.lower() in {".yaml", ".yml"} else json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Regras devem ser um objeto na raiz (dict).")
    return data


def _check_min_max(
    value: float | int | None,
    spec: dict[str, Any],
    path: str,
    failures: list[str],
) -> None:
    if value is None:
        failures.append(f"{path}: valor ausente (None)")
        return
    try:
        v = float(value)
    except (TypeError, ValueError):
        failures.append(f"{path}: não numérico ({value!r})")
        return
    if "min" in spec and v < float(spec["min"]):
        failures.append(f"{path}: {v} < min ({spec['min']})")
    if "max" in spec and v > float(spec["max"]):
        failures.append(f"{path}: {v} > max ({spec['max']})")


def evaluate_inspect_rules(inspect: dict[str, Any], rules: dict[str, Any]) -> tuple[bool, list[str], dict[str, Any]]:
    """
    Avalia regras contra o dict de inspect.

    Regras suportadas (todas opcionais):
      mesh_totals.vertex_count: { min, max }
      mesh_totals.face_count: { min, max }
      world_bounds.max_extent: { min, max }  (ou size[*] individual)
      world_bounds.size: [ { min, max }, ... ] por eixo
      armatures: lista por índice; cada item pode ter bone_count: { min, max }
      bones_contain: lista de nomes que devem existir em algum armature
      actions_min: número mínimo de actions (animações)
    """
    failures: list[str] = []
    details: dict[str, Any] = {"rules_applied": []}

    mt = rules.get("mesh_totals") or {}
    if isinstance(mt, dict):
        vc = inspect.get("mesh_totals", {}).get("vertex_count")
        if "vertex_count" in mt:
            _check_min_max(vc, mt["vertex_count"], "mesh_totals.vertex_count", failures)
            details["rules_applied"].append("mesh_totals.vertex_count")
        fc = inspect.get("mesh_totals", {}).get("face_count")
        if "face_count" in mt:
            _check_min_max(fc, mt["face_count"], "mesh_totals.face_count", failures)
            details["rules_applied"].append("mesh_totals.face_count")

    wb = rules.get("world_bounds") or {}
    if isinstance(wb, dict):
        wb_in = inspect.get("world_bounds") or {}
        if "max_extent" in wb:
            me = wb_in.get("max_extent")
            _check_min_max(me, wb["max_extent"], "world_bounds.max_extent", failures)
            details["rules_applied"].append("world_bounds.max_extent")
        sz_spec = wb.get("size")
        if isinstance(sz_spec, list) and "size" in wb_in:
            sz_val = wb_in.get("size")
            if isinstance(sz_val, list) and len(sz_val) == len(sz_spec):
                for i, (axis_spec, axis_val) in enumerate(zip(sz_spec, sz_val, strict=False)):
                    if isinstance(axis_spec, dict):
                        _check_min_max(axis_val, axis_spec, f"world_bounds.size[{i}]", failures)
                details["rules_applied"].append("world_bounds.size")

    arms_rule = rules.get("armatures")
    if isinstance(arms_rule, list):
        arms_in = inspect.get("armatures") or []
        for i, ar_spec in enumerate(arms_rule):
            if not isinstance(ar_spec, dict):
                continue
            if i >= len(arms_in):
                failures.append(f"armatures[{i}]: modelo tem só {len(arms_in)} armature(s)")
                continue
            bc_spec = ar_spec.get("bone_count")
            if isinstance(bc_spec, dict):
                bc = arms_in[i].get("bone_count")
                _check_min_max(bc, bc_spec, f"armatures[{i}].bone_count", failures)
            details["rules_applied"].append(f"armatures[{i}]")

    bones_need = rules.get("bones_contain")
    if isinstance(bones_need, list) and bones_need:
        arms_in = inspect.get("armatures") or []
        all_bones: set[str] = set()
        for arm in arms_in:
            for b in arm.get("bones") or []:
                all_bones.add(str(b))
            for b in arm.get("bones_sample") or []:
                all_bones.add(str(b))
        for name in bones_need:
            if name not in all_bones:
                failures.append(f"bones_contain: osso ausente {name!r}")
        details["rules_applied"].append("bones_contain")

    amin = rules.get("actions_min")
    if amin is not None:
        actions = inspect.get("actions") or []
        n = len(actions) if isinstance(actions, list) else 0
        if n < int(amin):
            failures.append(f"actions: {n} < actions_min ({amin})")
        details["rules_applied"].append("actions_min")

    meshes_min = rules.get("meshes_min")
    if meshes_min is not None:
        meshes = inspect.get("meshes") or []
        n = len(meshes) if isinstance(meshes, list) else 0
        if n < int(meshes_min):
            failures.append(f"meshes: {n} < meshes_min ({meshes_min})")
        details["rules_applied"].append("meshes_min")

    ok = len(failures) == 0
    details["failures"] = failures
    details["passed"] = ok
    return ok, failures, details
