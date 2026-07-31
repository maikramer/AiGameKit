"""Verificação rápida de GLB pós-export (sem bpy).

Detecta falhas típicas da pipeline: sem NORMAL (flat), V/Tri≈3 (normais
per-face / split), mesh vazia, bounds inválidos, etc. Pensado para correr
em todo ``save_glb`` e noutros exporters.

Env:
    ``AIGAMEKIT_GLB_VERIFY`` — ``0`` desliga; default ligado.
    ``AIGAMEKIT_GLB_VERIFY_STRICT`` — ``1`` levanta ``GlbVerifyError`` em fail.
    ``AIGAMEKIT_GLB_VERIFY_MAX_VPT`` — teto V/Tri (default ``1.6``).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import struct
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# V/Tri ≈ 3 = um vértice por canto de triângulo (normais flat / split duro).
_FACETED_VPT = 2.5
_DEFAULT_MAX_VPT = 1.6

# Stages que exigem NORMAL no ficheiro (shape→lod texturado).
_STAGES_REQUIRE_NORMAL: frozenset[str] = frozenset(
    {
        "shape",
        "clean",
        "to_paint",
        "painted",
        "lod0",
        "lod1",
        "lod2",
        "rigged",
        "animated",
        "default",
    }
)
# Collision / helpers: geometria só POSITION OK.
_STAGES_OPTIONAL_NORMAL: frozenset[str] = frozenset({"collision", "helper", "debug"})


@dataclass
class GlbIssue:
    """Um achado de verificação."""

    code: str
    severity: str  # "fail" | "warn" | "info"
    message: str


@dataclass
class GlbVerifyResult:
    """Resultado de ``verify_glb``."""

    path: str
    ok: bool
    stage: str
    issues: list[GlbIssue] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    def fails(self) -> list[GlbIssue]:
        return [i for i in self.issues if i.severity == "fail"]

    def warns(self) -> list[GlbIssue]:
        return [i for i in self.issues if i.severity == "warn"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "ok": self.ok,
            "stage": self.stage,
            "issues": [asdict(i) for i in self.issues],
            "meta": self.meta,
        }


class GlbVerifyError(RuntimeError):
    """GLB falhou verificação estrita."""

    def __init__(self, result: GlbVerifyResult) -> None:
        self.result = result
        fails = "; ".join(f"{i.code}: {i.message}" for i in result.fails())
        super().__init__(f"GLB verify FAIL {result.path}: {fails}")


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def infer_stage_from_path(path: str | Path) -> str:
    """Inferir stage pelo sufixo do ficheiro (``*_shape.glb`` → ``shape``)."""
    stem = Path(path).stem.lower()
    for suf in (
        "rigged_animated",
        "animated",
        "rigged",
        "to_paint",
        "painted",
        "collision",
        "shape",
        "clean",
        "lod0",
        "lod1",
        "lod2",
    ):
        if stem.endswith("_" + suf) or stem.endswith(suf):
            if suf == "rigged_animated":
                return "animated"
            return suf
    return "default"


def extract_glb_meta(path: str | Path) -> dict[str, Any]:
    """Metadados leves do GLB (JSON chunk) — sem bpy.

    Returns:
        Dict com ``attributes_present``, ``v_per_tri``, counts, ``has_tangents``,
        ``has_normals``, ``has_uv``, ``world_bounds_y_min``, ou ``_error``.
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError as exc:
        return {"_error": f"read: {exc}"}

    if len(data) < 20 or data[:4] != b"glTF":
        return {"_error": "não é GLB"}

    json_len = struct.unpack_from("<I", data, 12)[0]
    try:
        chunk = json.loads(data[20 : 20 + json_len])
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return {"_error": f"json chunk: {exc}"}

    accessors = chunk.get("accessors") or []
    union: set[str] = set()
    total_v = 0
    total_i = 0
    y_min: float | None = None
    prim_count = 0
    for mesh in chunk.get("meshes") or []:
        for prim in mesh.get("primitives") or []:
            prim_count += 1
            attrs = prim.get("attributes") or {}
            union.update(attrs.keys())
            pos_idx = attrs.get("POSITION")
            if isinstance(pos_idx, int) and 0 <= pos_idx < len(accessors):
                acc = accessors[pos_idx]
                total_v += int(acc.get("count") or 0)
                amin = acc.get("min")
                if isinstance(amin, list) and len(amin) >= 2:
                    try:
                        yv = float(amin[1])
                        y_min = yv if y_min is None else min(y_min, yv)
                    except (TypeError, ValueError):
                        pass
            idx = prim.get("indices")
            if isinstance(idx, int) and 0 <= idx < len(accessors):
                total_i += int(accessors[idx].get("count") or 0)

    tris = total_i // 3 if total_i else 0
    v_per_tri = round(total_v / tris, 4) if tris > 0 else None
    return {
        "attributes_present": sorted(union),
        "has_normals": "NORMAL" in union,
        "has_tangents": "TANGENT" in union,
        "has_uv": "TEXCOORD_0" in union,
        "has_joints": "JOINTS_0" in union,
        "has_weights": "WEIGHTS_0" in union,
        "mesh_count": len(chunk.get("meshes") or []),
        "primitive_count": prim_count,
        "vertex_count_total": total_v,
        "triangle_count_total": tris,
        "v_per_tri": v_per_tri,
        "world_bounds_y_min": y_min,
        "extensions_used": list(chunk.get("extensionsUsed") or []),
        "byte_size": len(data),
    }


def verify_glb(
    path: str | Path,
    *,
    stage: str | None = None,
    require_normals: bool | None = None,
    require_uv: bool | None = None,
    require_tangents: bool | None = None,
    max_v_per_tri: float | None = None,
) -> GlbVerifyResult:
    """Verifica saúde do GLB.

    Args:
        path: Ficheiro GLB.
        stage: ``shape``/``clean``/``painted``/``lod0``/… — infere do path se None.
        require_normals: Override; default por stage.
        require_uv: Override; default True só para ``painted``/``lod*``.
        require_tangents: Override; default False (só warn se NORMAL_MAP esperado).
        max_v_per_tri: Teto; default env ou ``1.6``. Fail duro se ≥ ``2.5``.
    """
    p = Path(path)
    st = (stage or infer_stage_from_path(p)).lower()
    issues: list[GlbIssue] = []

    if not p.is_file():
        issues.append(GlbIssue("MISSING", "fail", f"ficheiro inexistente: {p}"))
        return GlbVerifyResult(str(p), False, st, issues, {})

    meta = extract_glb_meta(p)
    if meta.get("_error"):
        issues.append(GlbIssue("PARSE", "fail", str(meta["_error"])))
        return GlbVerifyResult(str(p), False, st, issues, meta)

    if int(meta.get("mesh_count") or 0) < 1 or int(meta.get("primitive_count") or 0) < 1:
        issues.append(GlbIssue("EMPTY_MESH", "fail", "GLB sem meshes/primitives"))
    verts = int(meta.get("vertex_count_total") or 0)
    tris = int(meta.get("triangle_count_total") or 0)
    if verts <= 0 or tris <= 0:
        issues.append(GlbIssue("EMPTY_GEO", "fail", f"geometria vazia V={verts} T={tris}"))

    if require_normals is None:
        require_normals = st in _STAGES_REQUIRE_NORMAL and st not in _STAGES_OPTIONAL_NORMAL
    if require_uv is None:
        require_uv = st in {"painted", "lod0", "lod1", "lod2"}
    if require_tangents is None:
        require_tangents = False
    if max_v_per_tri is None:
        try:
            max_v_per_tri = float(os.environ.get("AIGAMEKIT_GLB_VERIFY_MAX_VPT", _DEFAULT_MAX_VPT))
        except ValueError:
            max_v_per_tri = _DEFAULT_MAX_VPT

    has_n = bool(meta.get("has_normals"))
    has_uv = bool(meta.get("has_uv"))
    has_t = bool(meta.get("has_tangents"))
    vpt = meta.get("v_per_tri")

    if require_normals and not has_n:
        issues.append(
            GlbIssue(
                "NO_NORMAL",
                "fail",
                "sem atributo NORMAL — viewer flat / re-export V/Tri≈3",
            )
        )
    if not require_normals and not has_n and st not in _STAGES_OPTIONAL_NORMAL:
        issues.append(GlbIssue("NO_NORMAL", "warn", "sem NORMAL (stage não exige)"))

    if require_uv and not has_uv:
        issues.append(GlbIssue("NO_UV", "fail", "sem TEXCOORD_0 (paint/LOD texturado)"))

    if require_tangents and not has_t:
        issues.append(GlbIssue("NO_TANGENT", "fail", "sem TANGENT (normal-map esperado)"))
    elif has_uv and has_n and not has_t and st in {"painted", "lod0"}:
        issues.append(
            GlbIssue(
                "NO_TANGENT",
                "warn",
                "painted/lod0 sem TANGENT — normal maps podem ter seams",
            )
        )

    if isinstance(vpt, (int, float)):
        if vpt >= _FACETED_VPT:
            issues.append(
                GlbIssue(
                    "FACETED_VPT3",
                    "fail",
                    f"v_per_tri={vpt} (≥{_FACETED_VPT}) — normais per-face / split duro",
                )
            )
        elif vpt > max_v_per_tri:
            issues.append(
                GlbIssue(
                    "HIGH_VPT",
                    "warn",
                    f"v_per_tri={vpt} > {max_v_per_tri} — possível split excessivo",
                )
            )
        elif vpt < 0.25 and tris > 100:
            issues.append(
                GlbIssue(
                    "LOW_VPT",
                    "warn",
                    f"v_per_tri={vpt} muito baixo — índices/contagens suspeitas",
                )
            )

    y_min = meta.get("world_bounds_y_min")
    if isinstance(y_min, (int, float)) and st in {"shape", "clean", "to_paint", "painted", "lod0"} and y_min < -0.5:
        issues.append(
            GlbIssue(
                "ORIGIN_Y",
                "warn",
                f"world_bounds_y_min={y_min:.4f} — pés longe de y=0?",
            )
        )

    byte_size = int(meta.get("byte_size") or 0)
    if byte_size > 0 and byte_size < 200:
        issues.append(GlbIssue("TINY_FILE", "fail", f"ficheiro minúsculo ({byte_size} B)"))

    ok = not any(i.severity == "fail" for i in issues)
    return GlbVerifyResult(str(p), ok, st, issues, meta)


def post_save_verify(
    path: str | Path,
    *,
    stage: str | None = None,
    require_normals: bool | None = None,
    require_uv: bool | None = None,
    require_tangents: bool | None = None,
    max_v_per_tri: float | None = None,
    log_trace: bool = True,
) -> GlbVerifyResult | None:
    """Hook pós-``save_glb``: verifica, loga, opcionalmente falha.

    Returns:
        Resultado, ou ``None`` se verificação desligada via env.
    """
    if not _truthy("AIGAMEKIT_GLB_VERIFY", default=True):
        return None

    result = verify_glb(
        path,
        stage=stage,
        require_normals=require_normals,
        require_uv=require_uv,
        require_tangents=require_tangents,
        max_v_per_tri=max_v_per_tri,
    )

    for issue in result.issues:
        msg = f"glb_verify [{result.stage}] {Path(result.path).name}: {issue.code} — {issue.message}"
        if issue.severity == "fail":
            log.error(msg)
        elif issue.severity == "warn":
            log.warning(msg)
        else:
            log.info(msg)

    if result.ok and not result.issues:
        log.debug(
            "glb_verify OK [%s] %s vpt=%s attrs=%s",
            result.stage,
            Path(result.path).name,
            result.meta.get("v_per_tri"),
            result.meta.get("attributes_present"),
        )
    elif result.ok:
        log.info(
            "glb_verify OK-with-warns [%s] %s issues=%s",
            result.stage,
            Path(result.path).name,
            [i.code for i in result.warns()],
        )

    if log_trace:
        with contextlib.suppress(Exception):
            from aigamekit_shared.pipeline_trace import trace_event

            trace_event(
                "glb_verify",
                path=str(path),
                stage=result.stage,
                ok=result.ok,
                issues=[asdict(i) for i in result.issues],
                meta={
                    k: result.meta.get(k)
                    for k in (
                        "v_per_tri",
                        "has_normals",
                        "has_tangents",
                        "has_uv",
                        "vertex_count_total",
                        "triangle_count_total",
                        "byte_size",
                    )
                },
            )

    if not result.ok and _truthy("AIGAMEKIT_GLB_VERIFY_STRICT", default=False):
        raise GlbVerifyError(result)
    return result
