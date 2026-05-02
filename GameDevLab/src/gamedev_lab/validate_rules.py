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


def evaluate_inspect_rules(
    inspect: dict[str, Any],
    rules: dict[str, Any],
    *,
    category: str | None = None,
) -> tuple[bool, list[str], dict[str, Any]]:
    """
    Avalia regras contra o dict de inspect.

    Regras suportadas (todas opcionais):
      mesh_totals.vertex_count: { min, max }
      mesh_totals.face_count: { min, max }
      mesh_totals.v_per_tri: { max }   — pega ``glb_meta.v_per_tri``
      world_bounds.max_extent: { min, max }
      world_bounds.size: [ { min, max }, ... ] por eixo
      armatures: lista por índice; cada item pode ter bone_count: { min, max }
      bones_contain: lista de nomes que devem existir em algum armature
      actions_min: número mínimo de actions
      meshes_min: número mínimo de meshes
      attributes_required: [POSITION, NORMAL, TEXCOORD_0, TANGENT]
      texture_format: ktx2 | png | jpeg | any  (nas imagens)
      compression: meshopt | draco | none      (extensionsUsed)
      origin.y_min: { near: 0.0, tol: 0.01 }
      face_count.max_per_category: { humanoid: 38400, weapon: 7200, ... }
        — usa ``category`` (kwarg) para selecionar o limite efetivo.
    """
    failures: list[str] = []
    details: dict[str, Any] = {"rules_applied": []}
    glb_meta = inspect.get("glb_meta") or {}

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
        if "v_per_tri" in mt:
            vpt = glb_meta.get("v_per_tri")
            _check_min_max(vpt, mt["v_per_tri"], "mesh_totals.v_per_tri", failures)
            details["rules_applied"].append("mesh_totals.v_per_tri")

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

    attrs_req = rules.get("attributes_required")
    if isinstance(attrs_req, list) and attrs_req:
        present = set(glb_meta.get("attributes_present") or [])
        missing = [a for a in attrs_req if a not in present]
        if missing:
            failures.append(f"attributes_required: faltam {missing}")
        details["rules_applied"].append("attributes_required")

    texture_format = rules.get("texture_format")
    if texture_format and texture_format != "any":
        wanted = str(texture_format).lower()
        mimes = glb_meta.get("texture_mime_types") or []
        if not mimes:
            details["rules_applied"].append("texture_format(no_textures)")
        else:
            wanted_full = {
                "ktx2": "image/ktx2",
                "png": "image/png",
                "jpeg": "image/jpeg",
                "jpg": "image/jpeg",
                "webp": "image/webp",
            }.get(wanted, wanted)
            non_match = [m for m in mimes if m != wanted_full]
            if non_match:
                failures.append(f"texture_format: esperado {wanted_full}, encontrado {non_match}")
            details["rules_applied"].append("texture_format")

    compression = rules.get("compression")
    if compression and compression != "any":
        wanted = str(compression).lower()
        ext_used = glb_meta.get("extensions_used") or []
        if wanted == "meshopt":
            if "EXT_meshopt_compression" not in ext_used:
                failures.append("compression: EXT_meshopt_compression ausente")
            details["rules_applied"].append("compression(meshopt)")
        elif wanted == "draco":
            if "KHR_draco_mesh_compression" not in ext_used:
                failures.append("compression: KHR_draco_mesh_compression ausente")
            details["rules_applied"].append("compression(draco)")
        elif wanted == "none":
            for unwanted in ("EXT_meshopt_compression", "KHR_draco_mesh_compression"):
                if unwanted in ext_used:
                    failures.append(f"compression: {unwanted} presente (esperado none)")
            details["rules_applied"].append("compression(none)")

    origin_rule = rules.get("origin")
    if isinstance(origin_rule, dict) and "y_min" in origin_rule:
        spec = origin_rule["y_min"] or {}
        near = float(spec.get("near", 0.0))
        tol = float(spec.get("tol", 0.01))
        # Preferir glb_meta.world_bounds_y_min; fallback ao inspect existente
        y = glb_meta.get("world_bounds_y_min")
        if y is None:
            y = (inspect.get("world_bounds") or {}).get("min", [None, None, None])[1]
        if y is None:
            failures.append("origin.y_min: valor não disponível no inspect")
        elif abs(float(y) - near) > tol:
            failures.append(f"origin.y_min: {y} fora de {near}±{tol}")
        details["rules_applied"].append("origin.y_min")

    fc_rule = rules.get("face_count")
    if isinstance(fc_rule, dict):
        per_cat = fc_rule.get("max_per_category")
        if isinstance(per_cat, dict) and category and category in per_cat:
            limit = int(per_cat[category])
            fc = inspect.get("mesh_totals", {}).get("face_count")
            if fc is not None and int(fc) > limit:
                failures.append(f"face_count.max_per_category[{category}]: {fc} > {limit}")
            details["rules_applied"].append(f"face_count.max_per_category[{category}]")

    ok = len(failures) == 0
    details["failures"] = failures
    details["passed"] = ok
    return ok, failures, details
