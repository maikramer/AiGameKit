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


def _node_matrix(node: dict[str, Any]) -> list[list[float]]:
    """Matriz 4x4 (linha-maior) de um nó glTF, de ``matrix`` ou TRS."""
    m = node.get("matrix")
    if isinstance(m, list) and len(m) == 16:
        # glTF guarda coluna-maior.
        return [[float(m[c * 4 + r]) for c in range(4)] for r in range(4)]

    t = node.get("translation") or [0.0, 0.0, 0.0]
    r = node.get("rotation") or [0.0, 0.0, 0.0, 1.0]  # quaternião xyzw
    s = node.get("scale") or [1.0, 1.0, 1.0]
    x, y, z, w = (float(v) for v in r)
    rot = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    return [
        [rot[i][0] * float(s[0]), rot[i][1] * float(s[1]), rot[i][2] * float(s[2]), float(t[i])] for i in range(3)
    ] + [[0.0, 0.0, 0.0, 1.0]]


def _mat_mul(a: list[list[float]], b: list[list[float]]) -> list[list[float]]:
    return [[sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def _xform(m: list[list[float]], p: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3] for i in range(3))  # type: ignore[return-value]


# glTF: accessors com ``normalized`` guardam inteiros que mapeiam para [-1,1]
# (com sinal) ou [0,1]. É assim que o KHR_mesh_quantization guarda POSITION.
_NORMALIZED_DIVISOR: dict[int, float] = {
    5120: 127.0,  # BYTE
    5121: 255.0,  # UNSIGNED_BYTE
    5122: 32767.0,  # SHORT
    5123: 65535.0,  # UNSIGNED_SHORT
}


def _dequantize(acc: dict[str, Any], values: list[float]) -> list[float]:
    """Aplica a normalização do accessor (no-op em floats)."""
    if not acc.get("normalized"):
        return values
    div = _NORMALIZED_DIVISOR.get(int(acc.get("componentType") or 0))
    if not div:
        return values
    signed = int(acc.get("componentType") or 0) in (5120, 5122)
    return [max(v / div, -1.0) if signed else v / div for v in values]


def glb_world_bounds(path: str | Path) -> tuple[list[float], list[float]] | None:
    """AABB em espaço-mundo de um GLB, compondo as transforms dos nós (sem bpy).

    Os ``min``/``max`` dos accessors estão em espaço **local** e, com
    ``KHR_mesh_quantization``, em unidades **inteiras**: ler o accessor
    directamente devolve ``-32767`` em vez de metros (todo o lod0 finalizado do
    projecto). A escala/offset da dequantização vive na TRS do nó, portanto
    compor a hierarquia resolve os dois problemas de uma vez.

    Skins: os bounds de uma malha skinned estão em espaço de bind; sem avaliar
    os joints o resultado é aproximado (suficiente para verificar origem e
    alinhamento colisão↔visual).

    Returns:
        ``(min_xyz, max_xyz)`` em unidades de mundo, ou ``None`` se o ficheiro
        não tiver geometria legível.
    """
    p = Path(path)
    try:
        data = p.read_bytes()
    except OSError:
        return None
    if len(data) < 20 or data[:4] != b"glTF":
        return None
    json_len = struct.unpack_from("<I", data, 12)[0]
    try:
        chunk = json.loads(data[20 : 20 + json_len])
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None

    accessors = chunk.get("accessors") or []
    meshes = chunk.get("meshes") or []
    nodes = chunk.get("nodes") or []
    if not nodes or not meshes:
        return None

    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    found = False

    def visit(idx: int, parent: list[list[float]], depth: int = 0) -> None:
        nonlocal found
        if depth > 64 or not (0 <= idx < len(nodes)):
            return
        node = nodes[idx]
        world = _mat_mul(parent, _node_matrix(node))
        mesh_idx = node.get("mesh")
        if isinstance(mesh_idx, int) and 0 <= mesh_idx < len(meshes):
            for prim in meshes[mesh_idx].get("primitives") or []:
                pos = (prim.get("attributes") or {}).get("POSITION")
                if not isinstance(pos, int) or not (0 <= pos < len(accessors)):
                    continue
                acc = accessors[pos]
                amin, amax = acc.get("min"), acc.get("max")
                if not (isinstance(amin, list) and isinstance(amax, list) and len(amin) == 3 == len(amax)):
                    continue
                amin = _dequantize(acc, [float(v) for v in amin])
                amax = _dequantize(acc, [float(v) for v in amax])
                for cx in (amin[0], amax[0]):
                    for cy in (amin[1], amax[1]):
                        for cz in (amin[2], amax[2]):
                            w = _xform(world, (cx, cy, cz))
                            for i in range(3):
                                lo[i] = min(lo[i], w[i])
                                hi[i] = max(hi[i], w[i])
                            found = True
        for child in node.get("children") or []:
            visit(int(child), world, depth + 1)

    identity = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    scenes = chunk.get("scenes") or []
    scene_idx = chunk.get("scene", 0)
    roots = scenes[scene_idx].get("nodes") if 0 <= scene_idx < len(scenes) else None
    for root in roots if roots else range(len(nodes)):
        visit(int(root), identity)
    return ([round(v, 6) for v in lo], [round(v, 6) for v in hi]) if found else None


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
    # Bounds a sério: com transforms de nó compostas e dequantização implícita.
    # O ``y_min`` cru dos accessors dava -32767 em qualquer GLB quantizado
    # (KHR_mesh_quantization) e ignorava rotações/escalas de nó nos restantes.
    world = glb_world_bounds(p)
    if world is not None:
        y_min = world[0][1]
    return {
        "attributes_present": sorted(union),
        "has_normals": "NORMAL" in union,
        "has_tangents": "TANGENT" in union,
        "has_uv": "TEXCOORD_0" in union,
        "has_joints": "JOINTS_0" in union,
        "has_weights": "WEIGHTS_0" in union,
        "mesh_count": len(chunk.get("meshes") or []),
        "skin_count": len(chunk.get("skins") or []),
        "primitive_count": prim_count,
        "vertex_count_total": total_v,
        "triangle_count_total": tris,
        "v_per_tri": v_per_tri,
        "world_bounds_y_min": y_min,
        "world_bounds_min": world[0] if world else None,
        "world_bounds_max": world[1] if world else None,
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
