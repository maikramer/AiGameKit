"""Corte horizontal de malha em stump + top (árvores / props destruíveis).

Usa ``bpy.ops.mesh.bisect`` no eixo vertical do Blender (Z-up). O export glTF
(``export_yup``) mapeia Blender Z → glTF Y, por isso ``cut_height`` em metros
acima da base corresponde ao Y do jogo.

Default: **só corte** (``cap=False``, ``use_fill=False``) — sem tampão, fill,
fuse ou UV retarget. Fecho do buraco fica para depois (``--cap`` / ``cap=True``).

Export multi-mesh via ``save_glb``.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import numpy as np

log = logging.getLogger(__name__)

# Corte default: min(0.8 m, 1/4 da altura) — acima das raízes, abaixo da copa.
DEFAULT_CUT_HEIGHT_MAX = 0.8
DEFAULT_CUT_HEIGHT_RATIO = 0.25
# Compat: testes / docs que ainda referem ``DEFAULT_CUT_HEIGHT``.
DEFAULT_CUT_HEIGHT = DEFAULT_CUT_HEIGHT_MAX
DEFAULT_STUMP_NAME = "Stump"
DEFAULT_TOP_NAME = "Top"
# Bevel off: colar/anel no corte — fecho seamless = tampão plano interior.
DEFAULT_BEVEL_SEGMENTS = 0
DEFAULT_BEVEL_PROFILE = 0.65
DEFAULT_BEVEL_RADIUS_FRAC = 0.04
# Resolução máscara 2D no plano de corte.
DEFAULT_SEAL_CELL = 0.015  # 1.5 cm
DEFAULT_SEAL_CLOSE_M = 0.03  # sela vãos ≤ ~3 cm na silhueta 2D
DEFAULT_SEAL_MAX_GRID = 384
# Clip banda (só puxar verts fora da silhueta).
DEFAULT_SEAL_BAND_CELLS = 3.0
# Pós-seal: folhas finas / micro-ilhas que “vazam” no plano de corte.
DEFAULT_LEAK_MAX_THICKNESS = 0.02  # m — bbox min-extent
DEFAULT_LEAK_MIN_SPAN = 0.06  # m — bbox max-extent (folha, não blob)
DEFAULT_LEAK_BAND_M = 0.08  # m — à volta do plano de corte
DEFAULT_LEAK_MICRO_FACES = 16
DEFAULT_LEAK_MICRO_VOL_FRAC = 0.001
# Fuse banda do corte: dissolve coplanar + fundir (só no fallback raster).
DEFAULT_FUSE_ANGLE_DEG = 10.0
DEFAULT_FUSE_WELD_M = 0.014
# Se após bisect-fill ainda há demasiadas boundary no plano → tampão grelha.
DEFAULT_BOUNDARY_FALLBACK = 64
# Fingerprint do algoritmo de split — resume invalida derivados se mudar.
SEAL_VERSION = "cut-only-v1"

# Blender Z-up == glTF Y-up após export.
_UP_AXIS = 2


@dataclass(frozen=True)
class SplitResult:
    """Resultado de ``split_glb_at_height``."""

    output: Path
    cut_y: float
    stump_faces: int
    top_faces: int
    stump_path: Path | None = None
    top_path: Path | None = None


def _join_mesh_objects(objects: list[Any]) -> Any:
    """Junta várias meshes bpy numa só (árvores do pipeline são single-mesh)."""
    import bpy

    if len(objects) <= 1:
        return objects[0] if objects else None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return bpy.context.active_object


def _duplicate_object(obj: Any, name: str) -> Any:
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.duplicate(linked=False)
    dup = bpy.context.active_object
    dup.name = name
    if dup.data is not None:
        dup.data.name = name
    return dup


def _bisect_keep_side(obj: Any, cut_up_world: float, *, keep_below: bool) -> None:
    """Bisect plano horizontal (Blender Z-up); ``keep_below=True`` → stump.

    Só corta — ``use_fill=False`` (sem tampão nativo; evita artefactos).
    """
    import bpy
    from mathutils import Vector

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)

    inv = obj.matrix_world.inverted()
    plane_co = inv @ Vector((0.0, 0.0, cut_up_world))
    plane_no = (inv.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    if plane_no.length < 1e-8:
        plane_no = Vector((0.0, 0.0, 1.0))

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.bisect(
        plane_co=plane_co[:],
        plane_no=plane_no[:],
        use_fill=False,
        clear_inner=not keep_below,
        clear_outer=keep_below,
        threshold=1e-5,
    )
    bpy.ops.object.mode_set(mode="OBJECT")


def _count_boundary_near_cut(obj: Any, cut_up: float, *, band: float = 0.08) -> int:
    """Arestas de fronteira com ambos os verts na banda do plano de corte."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    n = sum(1 for e in bm.edges if len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= band for v in e.verts))
    bm.free()
    return n


# ---------------------------------------------------------------------------
# Fecho voxel / morph 2D no plano de corte
# ---------------------------------------------------------------------------


def _topo_device() -> str:
    """Device p/ morph 2D: ``AIGAMEKIT_TOPOFIX_DEVICE`` ou cuda se disponível."""
    import os

    forced = (os.environ.get("AIGAMEKIT_TOPOFIX_DEVICE") or "").strip().lower()
    if forced in {"cpu", "cuda"}:
        if forced == "cuda":
            try:
                import torch

                if torch.cuda.is_available():
                    return "cuda"
            except Exception:
                pass
            return "cpu"
        return "cpu"
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _binary_dilate(mask: np.ndarray, iterations: int) -> np.ndarray:
    """Dilatação 3x3; torch CUDA quando disponível (mesmo contrato topology-fix)."""
    iters = max(0, int(iterations))
    if iters == 0:
        return mask.astype(bool, copy=True)
    device = _topo_device()
    if device == "cuda":
        try:
            import torch
            import torch.nn.functional as F

            t = torch.from_numpy(mask.astype(np.float32)).to(device)[None, None]
            for _ in range(iters):
                t = F.max_pool2d(t, kernel_size=3, stride=1, padding=1)
            return cast(np.ndarray, (t[0, 0] > 0.5).detach().cpu().numpy())
        except Exception as exc:
            log.debug("dilate GPU fallback CPU: %s", exc)
    out = mask.astype(bool, copy=True)
    for _ in range(iters):
        p = np.pad(out, 1, mode="constant", constant_values=False)
        out = (
            p[:-2, 1:-1]
            | p[2:, 1:-1]
            | p[1:-1, :-2]
            | p[1:-1, 2:]
            | p[:-2, :-2]
            | p[:-2, 2:]
            | p[2:, :-2]
            | p[2:, 2:]
            | p[1:-1, 1:-1]
        )
    return out


def _binary_erode(mask: np.ndarray, iterations: int) -> np.ndarray:
    return ~_binary_dilate(~mask.astype(bool), iterations)


def _binary_close(mask: np.ndarray, iterations: int) -> np.ndarray:
    return _binary_erode(_binary_dilate(mask, iterations), iterations)


def _binary_open(mask: np.ndarray, iterations: int) -> np.ndarray:
    return _binary_dilate(_binary_erode(mask, iterations), iterations)


def _flood_fill_exterior(solid_seed: np.ndarray) -> np.ndarray:
    """Células vazias alcançáveis a partir da borda (4-conectado)."""
    h, w = solid_seed.shape
    blocked = solid_seed.astype(bool)
    exterior = np.zeros((h, w), dtype=bool)
    q: deque[tuple[int, int]] = deque()

    def _try(i: int, j: int) -> None:
        if 0 <= i < h and 0 <= j < w and not blocked[i, j] and not exterior[i, j]:
            exterior[i, j] = True
            q.append((i, j))

    for i in range(h):
        _try(i, 0)
        _try(i, w - 1)
    for j in range(w):
        _try(0, j)
        _try(h - 1, j)

    while q:
        i, j = q.popleft()
        _try(i + 1, j)
        _try(i - 1, j)
        _try(i, j + 1)
        _try(i, j - 1)
    return exterior


def _stamp_segment(mask: np.ndarray, x0: float, y0: float, x1: float, y1: float, radius: int = 1) -> None:
    """Rasteriza segmento em grelha (células centrais) com espessura."""
    h, w = mask.shape
    steps = int(max(abs(x1 - x0), abs(y1 - y0), 1.0)) + 1
    for t in np.linspace(0.0, 1.0, steps):
        x = x0 + (x1 - x0) * t
        y = y0 + (y1 - y0) * t
        cx, cy = round(x), round(y)
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                i, j = cy + dy, cx + dx
                if 0 <= i < h and 0 <= j < w:
                    mask[i, j] = True


def _largest_mask_component(mask: np.ndarray) -> np.ndarray:
    """Mantém só a maior componente 4-conectada de células True."""
    if not mask.any():
        return mask
    h, w = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    best: list[tuple[int, int]] = []
    for y0 in range(h):
        for x0 in range(w):
            if not mask[y0, x0] or seen[y0, x0]:
                continue
            stack = [(y0, x0)]
            seen[y0, x0] = True
            comp: list[tuple[int, int]] = []
            while stack:
                y, x = stack.pop()
                comp.append((y, x))
                for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                    ny, nx = y + dy, x + dx
                    if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
            if len(comp) > len(best):
                best = comp
    out = np.zeros_like(mask)
    for y, x in best:
        out[y, x] = True
    return out


def _rasterize_cut_plane(
    obj: Any, cut_up: float, *, cell: float, max_grid: int
) -> tuple[np.ndarray, float, float, float]:
    """Máscara 2D da intersecção malha↔plano Z=cut_up.

    Só usa faces *quase verticais* (casca) — fins/horizontais no corte
    inchavam a silhueta e o tampão “vazava” para fora do tronco.

    Returns:
        ``(mask[row=y, col=x], origin_x, origin_y, cell)``
    """
    me = obj.data
    n_verts = len(me.vertices)
    if n_verts == 0 or len(me.polygons) == 0:
        return np.zeros((8, 8), dtype=bool), 0.0, 0.0, cell

    coords = np.empty(n_verts * 3, dtype=np.float64)
    me.vertices.foreach_get("co", coords)
    coords = cast(np.ndarray, coords.reshape(-1, 3))

    # Verts da casca vertical (|nz| baixo) na banda do corte.
    bark_vert = np.zeros(n_verts, dtype=bool)
    for poly in me.polygons:
        if abs(float(poly.normal.z)) >= 0.55:
            continue
        for vi in poly.vertices:
            bark_vert[vi] = True

    band = max(cell * 2.0, 0.03)
    near = bark_vert & (np.abs(coords[:, 2] - cut_up) <= band)
    if not near.any():
        near = np.abs(coords[:, 2] - cut_up) <= band
    if not near.any():
        near = bark_vert if bark_vert.any() else np.ones(n_verts, dtype=bool)

    xs = coords[near, 0]
    ys = coords[near, 1]
    pad = cell * 3.0
    min_x, max_x = float(xs.min()) - pad, float(xs.max()) + pad
    min_y, max_y = float(ys.min()) - pad, float(ys.max()) + pad
    span_x = max(max_x - min_x, cell)
    span_y = max(max_y - min_y, cell)
    cell_eff = max(cell, span_x / max_grid, span_y / max_grid)
    w = int(np.ceil(span_x / cell_eff)) + 1
    h = int(np.ceil(span_y / cell_eff)) + 1
    w = min(max(w, 8), max_grid)
    h = min(max(h, 8), max_grid)
    mask = np.zeros((h, w), dtype=bool)

    def _to_cell(x: float, y: float) -> tuple[float, float]:
        return (x - min_x) / cell_eff, (y - min_y) / cell_eff

    on_plane = bark_vert & (np.abs(coords[:, 2] - cut_up) <= max(cell_eff, 0.02))
    for x, y in coords[on_plane, :2]:
        cx, cy = _to_cell(float(x), float(y))
        _stamp_segment(mask, cx, cy, cx, cy, radius=1)

    # Intersecção aresta↔plano — só arestas de faces verticais.
    for poly in me.polygons:
        if abs(float(poly.normal.z)) >= 0.55:
            continue
        verts = list(poly.vertices)
        n = len(verts)
        for i in range(n):
            a, b = verts[i], verts[(i + 1) % n]
            za, zb = coords[a, 2], coords[b, 2]
            if (za - cut_up) * (zb - cut_up) > 0:
                continue
            if abs(zb - za) < 1e-12:
                if abs(za - cut_up) <= cell_eff:
                    ax, ay = _to_cell(float(coords[a, 0]), float(coords[a, 1]))
                    bx, by = _to_cell(float(coords[b, 0]), float(coords[b, 1]))
                    _stamp_segment(mask, ax, ay, bx, by, radius=1)
                continue
            t = (cut_up - za) / (zb - za)
            if t < -1e-6 or t > 1.0 + 1e-6:
                continue
            t = float(np.clip(t, 0.0, 1.0))
            px = coords[a, 0] + t * (coords[b, 0] - coords[a, 0])
            py = coords[a, 1] + t * (coords[b, 1] - coords[a, 1])
            cx, cy = _to_cell(float(px), float(py))
            _stamp_segment(mask, cx, cy, cx, cy, radius=1)

    # Não filtrar CC no anel cru (pode partir o perímetro); solidify trata.
    return mask, min_x, min_y, cell_eff


def _solidify_cut_mask(mask: np.ndarray, *, close_iters: int) -> np.ndarray:
    """Preenche *só o interior* da silhueta — sem dilate para fora das bordas.

    Morph-close sela rachas da casca; flood-fill do exterior define o disco
    interior. Sem dilate extra (esse era o “anel” a sair da casca).
    """
    if not mask.any():
        return mask
    closed = _binary_close(mask, max(1, close_iters))
    solid = ~_flood_fill_exterior(closed)
    # Open leve; se esvaziar, fica o fill cru. Depois maior CC.
    opened = _binary_open(solid, 1)
    if opened.any():
        solid = opened
    if not solid.any():
        solid = ~_flood_fill_exterior(_binary_dilate(mask, 1))
    solid = _largest_mask_component(solid)
    # Garantir que o anel da casca (mask) entra no keep — senão contain
    # apaga as paredes cujo XY cai na borda da grelha.
    return cast(np.ndarray, solid | mask)


def _snap_cap_to_bark_rim(
    obj: Any,
    cut_up: float,
    *,
    band: float,
    weld_dist: float,
) -> dict[str, int]:
    """Snap verts do tampão (só faces horiz) ao rebordo da casca + remove_doubles.

    O tampão em grelha nasce com verts nos centros das células — longe demais
    do anel do bisect para um weld fraco, fica ilha a flutuar no gap.
    """
    import bmesh
    from mathutils.kdtree import KDTree

    from aigamekit_shared.mesh_repair import remove_doubles

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    targets: list[Any] = []
    for v in bm.verts:
        if abs(v.co.z - cut_up) > band:
            continue
        if any(abs(f.normal.z) < 0.55 for f in v.link_faces):
            targets.append(v)
    if len(targets) < 3:
        bm.free()
        return {"snapped": 0, "welded": 0}

    kd = KDTree(len(targets))
    for i, v in enumerate(targets):
        kd.insert(v.co, i)
    kd.balance()

    snapped = 0
    max_dist = float(max(weld_dist * 2.5, 1e-4))
    for v in bm.verts:
        if abs(v.co.z - cut_up) > band:
            continue
        if not v.link_faces:
            continue
        # Só verts “de tampão”: todas as faces ligadas são horizontais.
        if any(abs(f.normal.z) < 0.55 for f in v.link_faces):
            continue
        # Só o *anel* do tampão (boundary) — interior não precisa de snap
        # e um weld global largo colapsa a casca.
        if not any(len(e.link_faces) == 1 for e in v.link_edges):
            continue
        _co, idx, dist = kd.find(v.co)
        if idx is None or dist > max_dist:
            continue
        t = targets[idx]
        v.co.x = t.co.x
        v.co.y = t.co.y
        v.co.z = float(cut_up)
        snapped += 1
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    # Weld curto: só funde pares já sobrepostos pelo snap.
    welded = remove_doubles(obj, float(max(min(weld_dist, 0.012), 1e-5)))
    return {"snapped": snapped, "welded": int(welded)}


def _bridge_cap_to_bark(
    obj: Any,
    cut_up: float,
    *,
    band: float,
    max_bridge_dist: float = 0.05,
    weld_dist: float = DEFAULT_FUSE_WELD_M,
) -> dict[str, int]:
    """Ponte robusta cap↔casca: fecha gaps onde o snap falha.

    O snap só do anel deixa o cap a flutuar quando a casca Hunyuan é
    irregular (fendas, bifurcações, raízes a atravessar o corte). Aqui:

    1. Flatten de toda a banda do corte para z=cut_up (anti-Z-fight);
    2. **Edges de bridge explícitos**: para cada boundary vert do cap, criar
       uma aresta até ao boundary vert da casca mais próximo (KDTree em XY);
    3. ``triangle_fill`` sobre o conjunto combinado (cap + casca + bridges)
       — os bridges transformam as duas boundaries desconexas num anel
       conjunto que o fill fecha com triângulos;
    4. ``remove_doubles`` para fundir overlaps.

    Não colapsa a casca: só liga o cap a ela, não mexe em verts fora da banda.
    """
    import bmesh
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    from aigamekit_shared.mesh_repair import remove_doubles

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # 1) Flatten: verts do cap (faces horiz) na banda → z=cut_up.
    flattened = 0
    for v in bm.verts:
        if abs(v.co.z - cut_up) > band:
            continue
        if any(abs(f.normal.z) >= 0.65 for f in v.link_faces):
            v.co.z = float(cut_up)
            flattened += 1

    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()

    # 2) Snap leve do anel do cap à casca (XY, ≤ max_bridge_dist).
    bark_verts = [
        v for v in bm.verts if abs(v.co.z - cut_up) <= band * 1.5 and any(abs(f.normal.z) < 0.55 for f in v.link_faces)
    ]
    snapped = 0
    if len(bark_verts) >= 3:
        kd_snap = KDTree(len(bark_verts))
        for i, v in enumerate(bark_verts):
            kd_snap.insert(Vector((v.co.x, v.co.y, cut_up)), i)
        kd_snap.balance()
        for v in bm.verts:
            if abs(v.co.z - cut_up) > band:
                continue
            if not v.link_faces or any(abs(f.normal.z) < 0.55 for f in v.link_faces):
                continue
            if not any(len(e.link_faces) == 1 for e in v.link_edges):
                continue
            _co, idx, dist = kd_snap.find(Vector((v.co.x, v.co.y, cut_up)))
            if idx is None or dist > max_bridge_dist:
                continue
            t = bark_verts[idx]
            v.co.x = t.co.x
            v.co.y = t.co.y
            v.co.z = float(cut_up)
            snapped += 1
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()

    # 3) Edges de bridge explícitos: cap boundary → bark boundary mais próximo.
    bark_boundary = [
        v
        for v in bm.verts
        if v.is_valid
        and abs(v.co.z - cut_up) <= band
        and any(len(e.link_faces) == 1 for e in v.link_edges)
        and any(abs(f.normal.z) < 0.55 for f in v.link_faces)
    ]
    cap_boundary = [
        v
        for v in bm.verts
        if v.is_valid
        and abs(v.co.z - cut_up) <= band
        and any(len(e.link_faces) == 1 for e in v.link_edges)
        and all(abs(f.normal.z) >= 0.65 for f in v.link_faces)
    ]
    bridges = 0
    if bark_boundary and cap_boundary:
        kd_bridge = KDTree(len(bark_boundary))
        for i, v in enumerate(bark_boundary):
            kd_bridge.insert(Vector((v.co.x, v.co.y, cut_up)), i)
        kd_bridge.balance()
        for cv in cap_boundary:
            _co, idx, dist = kd_bridge.find(Vector((cv.co.x, cv.co.y, cut_up)))
            if idx is None or dist > max_bridge_dist * 3.0:
                continue
            bv = bark_boundary[idx]
            # Só criar se não existir já.
            if any(e.other_vert(cv) == bv for e in cv.link_edges):
                continue
            try:
                bm.edges.new((cv, bv))
                bridges += 1
            except Exception:
                pass
        bm.edges.ensure_lookup_table()

    # 4) triangle_fill sobre todas as boundaries combinadas (cap + bark + bridges).
    fill_edges = [
        e
        for e in bm.edges
        if e.is_valid and len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= band for v in e.verts)
    ]
    filled = 0
    if fill_edges:
        try:
            ret = bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=fill_edges)
            filled = sum(1 for g in ret.get("geom", []) if hasattr(g, "loops") and len(g.loops) > 0)
        except Exception as exc:
            log.debug("bridge triangle_fill falhou: %s", exc)
        # Fallback: holes_fill para o que sobrar.
        bm.edges.ensure_lookup_table()
        remaining = [
            e
            for e in bm.edges
            if e.is_valid and len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= band for v in e.verts)
        ]
        if remaining:
            try:
                ret = bmesh.ops.holes_fill(bm, edges=remaining, sides=0)
                filled += len(ret.get("faces", []))
            except Exception as exc:
                log.debug("bridge holes_fill falhou: %s", exc)

    # 5) Re-flatten faces novas do fill (triangle_fill pode criar em 3D).
    bm.faces.ensure_lookup_table()
    for f in bm.faces:
        if not f.is_valid or abs(f.normal.z) < 0.55:
            continue
        c = f.calc_center_median()
        if abs(c.z - cut_up) > 0.005:
            for v in f.verts:
                if abs(v.co.z - cut_up) <= band:
                    v.co.z = float(cut_up)

    # 6) Segunda passagem: re-bridge + re-fill para fechar gaps remanescentes.
    bark_boundary_2 = [
        v
        for v in bm.verts
        if v.is_valid
        and abs(v.co.z - cut_up) <= band
        and any(len(e.link_faces) == 1 for e in v.link_edges)
        and any(abs(f.normal.z) < 0.55 for f in v.link_faces)
    ]
    cap_boundary_2 = [
        v
        for v in bm.verts
        if v.is_valid
        and abs(v.co.z - cut_up) <= band
        and any(len(e.link_faces) == 1 for e in v.link_edges)
        and all(abs(f.normal.z) >= 0.65 for f in v.link_faces)
    ]
    if bark_boundary_2 and cap_boundary_2:
        kd2 = KDTree(len(bark_boundary_2))
        for i, v in enumerate(bark_boundary_2):
            kd2.insert(Vector((v.co.x, v.co.y, cut_up)), i)
        kd2.balance()
        for cv in cap_boundary_2:
            _co, idx, dist = kd2.find(Vector((cv.co.x, cv.co.y, cut_up)))
            if idx is None or dist > max_bridge_dist * 3.0:
                continue
            bv = bark_boundary_2[idx]
            if any(e.other_vert(cv) == bv for e in cv.link_edges):
                continue
            try:
                bm.edges.new((cv, bv))
                bridges += 1
            except Exception:
                pass
        bm.edges.ensure_lookup_table()
        fill_edges_2 = [
            e
            for e in bm.edges
            if e.is_valid and len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= band for v in e.verts)
        ]
        if fill_edges_2:
            try:
                ret = bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=fill_edges_2)
                filled += sum(1 for g in ret.get("geom", []) if hasattr(g, "loops") and len(g.loops) > 0)
            except Exception:
                pass
        # Re-flatten faces novas da 2ª passagem.
        bm.faces.ensure_lookup_table()
        for f in bm.faces:
            if not f.is_valid or abs(f.normal.z) < 0.55:
                continue
            c = f.calc_center_median()
            if abs(c.z - cut_up) > 0.005:
                for v in f.verts:
                    if abs(v.co.z - cut_up) <= band:
                        v.co.z = float(cut_up)

    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    welded = int(remove_doubles(obj, float(max(min(weld_dist, 0.012), 1e-5))))
    return {"snapped": snapped, "flattened": flattened, "bridges": bridges, "filled": filled, "welded": welded}


def _strip_horizontals_near_cut(obj: Any, cut_up: float, *, band: float, nz_min: float = 0.72) -> int:
    """Apaga faces quase horizontais na banda do corte (caps/leaks/prateleiras)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    doomed = [f for f in bm.faces if abs(f.normal.z) >= nz_min and abs(f.calc_center_median().z - cut_up) <= band]
    n = len(doomed)
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        loose = [v for v in bm.verts if not v.link_faces]
        if loose:
            bmesh.ops.delete(bm, geom=loose, context="VERTS")
        bm.to_mesh(obj.data)
        obj.data.update()
    bm.free()
    return n


def _cell_inside(mask: np.ndarray, origin_x: float, origin_y: float, cell: float, x: float, y: float) -> bool:
    """True se (x,y) cai numa célula True da máscara 2D."""
    h, w = mask.shape
    cx = int(np.floor((x - origin_x) / cell))
    cy = int(np.floor((y - origin_y) / cell))
    return 0 <= cy < h and 0 <= cx < w and bool(mask[cy, cx])


def _contain_cut_band_to_volume(
    obj: Any,
    allowed: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    cell: float,
    cut_up: float,
    band_half: float,
    strip_horizontal: bool = True,
    delete_outside_verts: bool = True,
) -> dict[str, int]:
    """Contém a banda do corte ao volume 2D — **apaga** o que sai (não puxa).

    Puxar verts criava prateleiras/folhas no gap. Aqui:

    1. Apaga verts na banda com XY fora de ``allowed`` (se ``delete_outside_verts``);
    2. Apaga faces com centro fora de ``allowed`` (qualquer orientação);
    3. Opcional: apaga faces quase horizontais na banda (caps/leaks velhos).
    """
    import bmesh

    h, w = allowed.shape
    if h < 2 or w < 2 or not allowed.any():
        return {"verts_deleted": 0, "faces_deleted": 0, "horiz_stripped": 0}

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    z_pad = float(band_half)

    n_verts = 0
    if delete_outside_verts:
        outside_verts = [
            v
            for v in bm.verts
            if abs(v.co.z - cut_up) <= z_pad
            and not _cell_inside(allowed, origin_x, origin_y, cell, float(v.co.x), float(v.co.y))
        ]
        n_verts = len(outside_verts)
        if outside_verts:
            bmesh.ops.delete(bm, geom=outside_verts, context="VERTS")
            bm.faces.ensure_lookup_table()
            bm.verts.ensure_lookup_table()

    doomed_faces: list[Any] = []
    horiz: list[Any] = []
    for f in bm.faces:
        if not f.is_valid:
            continue
        c = f.calc_center_median()
        if abs(c.z - cut_up) > z_pad:
            continue
        # Faces fora do volume (só quando estamos a conter de verdade).
        if delete_outside_verts and not _cell_inside(allowed, origin_x, origin_y, cell, float(c.x), float(c.y)):
            doomed_faces.append(f)
            continue
        if delete_outside_verts:
            n_out = sum(
                1
                for v in f.verts
                if abs(v.co.z - cut_up) <= z_pad
                and not _cell_inside(allowed, origin_x, origin_y, cell, float(v.co.x), float(v.co.y))
            )
            if n_out >= max(2, (len(f.verts) + 1) // 2):
                doomed_faces.append(f)
                continue
        if strip_horizontal and abs(f.normal.z) >= 0.72:
            if delete_outside_verts:
                # Pré-cap: limpar todas as horizontais na banda.
                horiz.append(f)
            else:
                # Pós-cap: horizontais com centro/maioria fora do allowed.
                n_out = sum(
                    1
                    for v in f.verts
                    if not _cell_inside(allowed, origin_x, origin_y, cell, float(v.co.x), float(v.co.y))
                )
                if n_out >= max(1, len(f.verts) // 2) or not _cell_inside(
                    allowed, origin_x, origin_y, cell, float(c.x), float(c.y)
                ):
                    horiz.append(f)

    n_faces = len(doomed_faces)
    n_horiz = 0
    if doomed_faces:
        bmesh.ops.delete(bm, geom=doomed_faces, context="FACES")
        bm.faces.ensure_lookup_table()
    if strip_horizontal and horiz:
        horiz_ok = [f for f in horiz if f.is_valid]
        n_horiz = len(horiz_ok)
        if horiz_ok:
            bmesh.ops.delete(bm, geom=horiz_ok, context="FACES")

    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return {"verts_deleted": n_verts, "faces_deleted": n_faces, "horiz_stripped": n_horiz}


def _clip_band_to_silhouette(
    obj: Any,
    allowed: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    cell: float,
    cut_up: float,
    band_half: float,
) -> dict[str, int]:
    """Compat: delega para contain-by-delete (já não puxa verts)."""
    r = _contain_cut_band_to_volume(
        obj,
        allowed,
        origin_x=origin_x,
        origin_y=origin_y,
        cell=cell,
        cut_up=cut_up,
        band_half=band_half,
        strip_horizontal=False,
    )
    return {"pulled": 0, "faces_deleted": r["faces_deleted"] + r["verts_deleted"]}


def _mask_to_flat_cap_object(
    mask: np.ndarray,
    *,
    origin_x: float,
    origin_y: float,
    cell: float,
    z: float,
    keep_below: bool,
    name: str,
) -> Any | None:
    """Tampão *plano* no Z do corte (só faces interiores — sem paredes/anel)."""
    import bmesh
    import bpy

    if not mask.any():
        return None

    h, w = mask.shape
    corner: dict[tuple[int, int], Any] = {}
    bm = bmesh.new()

    def _vert(ix: int, iy: int) -> Any:
        key = (ix, iy)
        if key in corner:
            return corner[key]
        v = bm.verts.new((origin_x + ix * cell, origin_y + iy * cell, z))
        corner[key] = v
        return v

    faces = 0
    for iy in range(h):
        for ix in range(w):
            if not mask[iy, ix]:
                continue
            v00 = _vert(ix, iy)
            v10 = _vert(ix + 1, iy)
            v11 = _vert(ix + 1, iy + 1)
            v01 = _vert(ix, iy + 1)
            # Stump (keep_below): normal +Z. Top: normal -Z.
            order = (v00, v10, v11, v01) if keep_below else (v00, v01, v11, v10)
            try:
                f = bm.faces.new(order)
                f.smooth = True
                faces += 1
            except Exception:
                try:
                    f = bm.faces.new(tuple(reversed(order)))
                    f.smooth = True
                    faces += 1
                except Exception:
                    pass

    if faces == 0:
        bm.free()
        return None

    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def _join_two(a: Any, b: Any) -> Any:
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    a.select_set(True)
    b.select_set(True)
    bpy.context.view_layer.objects.active = a
    bpy.ops.object.join()
    return bpy.context.active_object


def _voxel_remesh(obj: Any, voxel_size: float) -> None:
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")
    mod = obj.modifiers.new("CutSealVoxel", "REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = float(max(1e-4, voxel_size))
    mod.use_smooth_shade = True
    bpy.ops.object.modifier_apply(modifier=mod.name)


def _separate_cut_band(obj: Any, cut_up: float, band_half: float) -> tuple[Any, Any | None]:
    """Separa faces na banda do corte → ``(band, far_or_None)``.

    ``far`` fica com o tronco fora da banda (sem solidify/voxel → sem espessura
    axial). Se quase tudo está na banda, ``far`` é ``None``.
    """
    import bmesh
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    n_faces = len(bm.faces)
    if n_faces == 0:
        bm.free()
        return obj, None

    band_count = 0
    for f in bm.faces:
        zs = [v.co.z for v in f.verts]
        in_band = any(abs(z - cut_up) <= band_half for z in zs) or (min(zs) - 1e-6 <= cut_up <= max(zs) + 1e-6)
        f.select = bool(in_band)
        if in_band:
            band_count += 1
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    if band_count == 0 or band_count >= n_faces:
        return obj, None

    before = {o.as_pointer() for o in bpy.context.scene.objects}
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")

    new_objs = [o for o in bpy.context.scene.objects if o.as_pointer() not in before]
    if not new_objs:
        new_objs = [o for o in bpy.context.selected_objects if o != obj and o.type == "MESH"]
    if not new_objs:
        return obj, None

    # separate(SELECTED): faces seleccionadas → objecto novo; original = far.
    band = new_objs[0]
    far = obj
    base_name = far.name
    if len(far.data.polygons) == 0:
        # Nada fora da banda — descartar far vazio; morph no band.
        bpy.ops.object.select_all(action="DESELECT")
        far.select_set(True)
        bpy.ops.object.delete()
        band.name = base_name
        if band.data is not None:
            band.data.name = base_name
        return band, None
    band.name = f"{base_name}_band"
    if band.data is not None:
        band.data.name = f"{base_name}_band"
    return band, far


def _morph_close_band(obj: Any, *, distance: float, voxel_size: float) -> dict[str, int | float]:
    """Morph-close leve só na banda (mesmo espírito topology-fix, escala local).

    solidify fino → voxel → dilate → voxel → erode → voxel. Distância pequena
    (~célula) funde tampão↔casca sem engrossar o tronco longe do corte.
    """
    from aigamekit_shared.mesh_repair import morphological_close

    return morphological_close(
        obj,
        distance=float(max(1e-4, distance)),
        voxel_size=float(max(1e-4, voxel_size)),
        wall_thickness=float(max(1e-4, min(distance, voxel_size * 1.2))),
        max_grid_axis=DEFAULT_SEAL_MAX_GRID,
    )


def _patch_cut_plane_holes(obj: Any, cut_up: float, *, tol: float) -> int:
    """Último recurso: holes_fill / triangle_fill nas boundary do plano de corte."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    edges = [
        e
        for e in bm.edges
        if e.is_valid and len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= tol for v in e.verts)
    ]
    if not edges:
        bm.free()
        return 0
    filled = 0
    try:
        ret = bmesh.ops.holes_fill(bm, edges=edges, sides=0)
        filled += len(ret.get("faces", []))
    except Exception:
        pass
    # Re-colectar (holes_fill pode ter parcialmente fechado).
    bm.edges.ensure_lookup_table()
    edges = [
        e
        for e in bm.edges
        if e.is_valid and len(e.link_faces) == 1 and all(abs(v.co.z - cut_up) <= tol for v in e.verts)
    ]
    if edges:
        try:
            ret = bmesh.ops.triangle_fill(bm, use_beauty=True, use_dissolve=False, edges=edges)
            filled += len(ret.get("geom", []))
        except Exception:
            pass
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return filled


def _transfer_uvs_and_materials(src: Any, dst: Any) -> None:
    """Copia materials + UVs (nearest face) da doadora pré-remesh."""
    import bpy

    dst.data.materials.clear()
    for mat in src.data.materials:
        dst.data.materials.append(mat)
    if not dst.data.uv_layers:
        dst.data.uv_layers.new(name="UVMap")

    bpy.ops.object.select_all(action="DESELECT")
    dst.select_set(True)
    bpy.context.view_layer.objects.active = dst
    mod = dst.modifiers.new("CutSealUV", "DATA_TRANSFER")
    mod.object = src
    mod.use_loop_data = True
    mod.data_types_loops = {"UV"}
    # Polyinterp: UVs contínuas nas faces laterais da casca.
    mod.loop_mapping = "POLYINTERP_NEAREST"
    try:
        bpy.ops.object.modifier_apply(modifier=mod.name)
    except Exception as exc:
        log.warning("DATA_TRANSFER UV falhou (%s); a tentar NEAREST_POLYNOR", exc)
        if "CutSealUV" in dst.modifiers:
            dst.modifiers.remove(dst.modifiers["CutSealUV"])
        mod = dst.modifiers.new("CutSealUV", "DATA_TRANSFER")
        mod.object = src
        mod.use_loop_data = True
        mod.data_types_loops = {"UV"}
        mod.loop_mapping = "NEAREST_POLYNOR"
        bpy.ops.object.modifier_apply(modifier=mod.name)


def _retarget_cut_face_uvs_to_bark(obj: Any, src: Any, cut_up: float, *, tol: float) -> int:
    """Faces horizontais no corte: UV da casca vertical mais próxima (anti-verde)."""
    import bmesh
    from mathutils import Vector
    from mathutils.kdtree import KDTree

    # Amostrar loops de casca na doadora (|nz| baixo, perto do corte).
    bm_src = bmesh.new()
    bm_src.from_mesh(src.data)
    uv_src = bm_src.loops.layers.uv.active
    if uv_src is None and bm_src.loops.layers.uv:
        uv_src = bm_src.loops.layers.uv[0]
    bark_pts: list[Vector] = []
    bark_uvs: list[Vector] = []
    if uv_src is not None:
        for f in bm_src.faces:
            if abs(f.normal.z) > 0.55:
                continue
            z = sum(v.co.z for v in f.verts) / len(f.verts)
            if abs(z - cut_up) > tol * 4.0:
                continue
            for lp in f.loops:
                bark_pts.append(lp.vert.co.copy())
                bark_uvs.append(lp[uv_src].uv.copy())
    bm_src.free()

    if len(bark_pts) < 3:
        return 0

    kd = KDTree(len(bark_pts))
    for i, p in enumerate(bark_pts):
        kd.insert(p, i)
    kd.balance()

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    uv = bm.loops.layers.uv.active
    if uv is None and bm.loops.layers.uv:
        uv = bm.loops.layers.uv[0]
    if uv is None:
        bm.free()
        return 0

    fixed = 0
    for f in bm.faces:
        if abs(f.normal.z) < 0.75:
            continue
        z = sum(v.co.z for v in f.verts) / len(f.verts)
        if abs(z - cut_up) > tol * 3.0:
            continue
        for lp in f.loops:
            # Procurar casca no mesmo XY (ignorar Z do query → project to cut).
            q = Vector((lp.vert.co.x, lp.vert.co.y, cut_up))
            _co, idx, _dist = kd.find(q)
            if idx is None:
                continue
            lp[uv].uv = bark_uvs[idx].copy()
            fixed += 1
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return fixed


def _soft_bevel_cut_rim(
    obj: Any,
    cut_up: float,
    *,
    offset: float,
    segments: int,
    profile: float,
    tol: float,
) -> int:
    """Bevel suave nas arestas cap↔casca (se manifold)."""
    import bmesh

    if segments <= 0 or offset <= 1e-6:
        return 0
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    rim = []
    for e in bm.edges:
        if len(e.link_faces) != 2:
            continue
        if not all(abs(v.co.z - cut_up) < tol * 2.0 for v in e.verts):
            continue
        na = abs(e.link_faces[0].normal.z)
        nb = abs(e.link_faces[1].normal.z)
        if (na > 0.55) ^ (nb > 0.55):
            rim.append(e)
    if not rim:
        bm.free()
        return 0
    lengths = sorted(e.calc_length() for e in rim if e.calc_length() > 1e-8)
    if lengths:
        p25 = lengths[max(0, len(lengths) // 4)]
        offset = float(min(offset, max(0.006, p25 * 0.3)))
    try:
        ret = bmesh.ops.bevel(
            bm,
            geom=rim,
            offset=offset,
            offset_type="OFFSET",
            segments=max(1, segments),
            profile=float(np.clip(profile, 0.0, 1.0)),
            affect="EDGES",
            clamp_overlap=True,
        )
        n = len(ret.get("faces", []))
    except Exception as exc:
        log.warning("bevel pós-seal falhou: %s", exc)
        n = 0
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return n


def _cleanup_cut_leak_geometry(
    obj: Any,
    cut_up: float,
    *,
    max_thickness: float = DEFAULT_LEAK_MAX_THICKNESS,
    min_span: float = DEFAULT_LEAK_MIN_SPAN,
    band: float = DEFAULT_LEAK_BAND_M,
    micro_faces: int = DEFAULT_LEAK_MICRO_FACES,
    micro_vol_frac: float = DEFAULT_LEAK_MICRO_VOL_FRAC,
) -> dict[str, int]:
    """Remove folhas finas / micro-ilhas que vazam no plano de corte.

    Heurística (por componente conexo de faces):

    - **thin sheet near cut**: bbox intersecta a banda do corte, espessura
      (min-extent) ≤ ``max_thickness`` e span (max-extent) ≥ ``min_span``;
    - **micro debris**: poucas faces e volume bbox ≪ componente principal.

    Nunca apaga o componente de maior volume (corpo da malha). Folhagem longe
    do corte não é tocada pela regra thin.
    """
    import bmesh

    me = obj.data
    bm = bmesh.new()
    bm.from_mesh(me)
    bm.faces.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    if not bm.faces:
        bm.free()
        return {"thin_faces": 0, "debris_faces": 0, "components_dropped": 0}

    parent = list(range(len(bm.faces)))

    def _find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def _union(a: int, b: int) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            parent[rb] = ra

    for e in bm.edges:
        linked = [f.index for f in e.link_faces]
        for i in range(1, len(linked)):
            _union(linked[0], linked[i])

    comps: dict[int, list[Any]] = {}
    for f in bm.faces:
        comps.setdefault(_find(f.index), []).append(f)

    scored: list[tuple[list[Any], float, float, float, float, float, float]] = []
    for faces in comps.values():
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        for f in faces:
            for v in f.verts:
                xs.append(float(v.co.x))
                ys.append(float(v.co.y))
                zs.append(float(v.co.z))
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        dz = max(zs) - min(zs)
        vol = max(dx, 1e-9) * max(dy, 1e-9) * max(dz, 1e-9)
        scored.append((faces, vol, dx, dy, dz, min(zs), max(zs)))

    max_vol = max(s[1] for s in scored)
    doomed: list[Any] = []
    thin_n = 0
    debris_n = 0
    dropped = 0
    for faces, vol, dx, dy, dz, z0, z1 in scored:
        if vol >= max_vol * 0.99:
            continue
        n = len(faces)
        near = z0 <= cut_up + band and z1 >= cut_up - band
        min_e = min(dx, dy, dz)
        max_e = max(dx, dy, dz)
        thin = max_thickness > 0 and near and min_e <= max_thickness and max_e >= min_span
        debris = (
            micro_faces > 0 and micro_vol_frac > 0 and n <= micro_faces and vol < max(1e-8, micro_vol_frac * max_vol)
        )
        if not (thin or debris):
            continue
        doomed.extend(faces)
        dropped += 1
        if thin:
            thin_n += n
        else:
            debris_n += n

    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
        orphans = [v for v in bm.verts if not v.link_faces]
        if orphans:
            bmesh.ops.delete(bm, geom=orphans, context="VERTS")
        bm.to_mesh(me)
        me.update()
    bm.free()
    return {"thin_faces": thin_n, "debris_faces": debris_n, "components_dropped": dropped}


def _fuse_cut_band(
    obj: Any,
    cut_up: float,
    *,
    band: float,
    angle_deg: float = DEFAULT_FUSE_ANGLE_DEG,
    weld_dist: float = DEFAULT_FUSE_WELD_M,
) -> dict[str, int]:
    """Dissolve arestas coplanares / curtas na banda do corte e funde verts.

    Mata prateleiras/Z-fight: achata Z das faces horiz no plano, dissolve
    arestas entre faces quase coplanares, remove degeneradas, ``remove_doubles``.
    """
    import math

    import bmesh

    from aigamekit_shared.mesh_repair import remove_doubles

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()

    # 1) Achatar verts de faces horiz no plano de corte (anti Z-fight / shelves).
    flattened = 0
    for v in bm.verts:
        if abs(v.co.z - cut_up) > band:
            continue
        if any(abs(f.normal.z) >= 0.65 for f in v.link_faces):
            v.co.z = float(cut_up)
            flattened += 1

    # 2) Dissolve arestas entre faces quase coplanares na banda.
    cos_a = math.cos(math.radians(float(angle_deg)))
    coplanar = [
        e
        for e in bm.edges
        if len(e.link_faces) == 2
        and all(abs(v.co.z - cut_up) <= band for v in e.verts)
        and float(e.link_faces[0].normal.dot(e.link_faces[1].normal)) >= cos_a
    ]
    dissolved = 0
    if coplanar:
        bmesh.ops.dissolve_edges(bm, edges=coplanar, use_verts=True, use_face_split=False)
        dissolved = len(coplanar)
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()

    # 3) Dissolve arestas muito curtas na banda.
    short_lim = float(max(weld_dist * 0.75, 1e-4))
    short = [
        e
        for e in bm.edges
        if e.is_valid and all(abs(v.co.z - cut_up) <= band for v in e.verts) and e.calc_length() < short_lim
    ]
    if short:
        bmesh.ops.dissolve_edges(bm, edges=short, use_verts=True, use_face_split=False)
        dissolved += len(short)
        bm.faces.ensure_lookup_table()

    # 4) Apagar faces degeneradas / slivers extremos na banda.
    doomed = []
    for f in bm.faces:
        if not f.is_valid:
            continue
        c = f.calc_center_median()
        if abs(c.z - cut_up) > band:
            continue
        area = float(f.calc_area())
        if area < 1e-10:
            doomed.append(f)
            continue
        longest = max((e.calc_length() for e in f.edges), default=0.0)
        if longest * longest / area > 120.0:
            doomed.append(f)
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
    loose = [v for v in bm.verts if not v.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")

    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()

    welded = int(remove_doubles(obj, float(max(weld_dist, 1e-5))))
    return {"flattened": flattened, "dissolved_edges": dissolved, "welded": welded}


def _post_seal_cleanup(
    obj: Any,
    cut_up: float,
    *,
    cell: float,
    aggressive: bool = False,
) -> dict[str, int | float]:
    """Limpeza pós-fecho.

    Default (``aggressive=False``): dissolve + weld curto + re-patch — preserva UV.
    ``aggressive=True`` (fallback raster): fuse banda + leak islands.
    """
    from aigamekit_shared.mesh_repair import (
        delete_loose,
        dissolve_degenerate,
        remove_doubles,
        remove_sliver_faces,
    )

    band = max(DEFAULT_LEAK_BAND_M, cell * 5.0)
    weld = max(DEFAULT_FUSE_WELD_M, cell * 0.9)
    dissolved = 0
    flattened = 0
    leak_thin = 0.0
    leak_debris = 0.0
    leak_comps = 0.0
    welded_extra = 0

    if aggressive:
        fuse = _fuse_cut_band(
            obj,
            cut_up,
            band=band,
            angle_deg=DEFAULT_FUSE_ANGLE_DEG,
            weld_dist=weld,
        )
        dissolved += int(fuse["dissolved_edges"])
        flattened += int(fuse["flattened"])
        welded_extra += int(fuse["welded"])
        leak = _cleanup_cut_leak_geometry(
            obj,
            cut_up,
            max_thickness=max(DEFAULT_LEAK_MAX_THICKNESS, cell * 1.5),
            min_span=DEFAULT_LEAK_MIN_SPAN,
            band=band,
            micro_faces=8,
            micro_vol_frac=1e-4,
        )
        leak_thin = float(leak["thin_faces"])
        leak_debris = float(leak["debris_faces"])
        leak_comps = float(leak["components_dropped"])
        fuse2 = _fuse_cut_band(obj, cut_up, band=band, angle_deg=DEFAULT_FUSE_ANGLE_DEG, weld_dist=weld)
        dissolved += int(fuse2["dissolved_edges"])
        flattened += int(fuse2["flattened"])
        welded_extra += int(fuse2["welded"])

    slivers = remove_sliver_faces(obj, max_aspect=50.0, max_removal_ratio=0.20)
    dissolve_degenerate(obj, threshold=max(1e-6, cell * 0.08))
    delete_loose(obj)
    filled = _patch_cut_plane_holes(obj, cut_up, tol=max(0.05, cell * 2.5))
    welded = remove_doubles(obj, max(1e-5, cell * 0.35))
    return {
        "leak_thin": leak_thin,
        "leak_debris": leak_debris,
        "leak_comps": leak_comps,
        "slivers": float(slivers),
        "welded": float(welded + welded_extra),
        "refilled": float(filled),
        "dissolved_edges": float(dissolved),
        "flattened": float(flattened),
    }


def _seal_raster_cap_fallback(
    obj: Any,
    cut_up: float,
    *,
    keep_below: bool,
    cell: float,
    close_m: float,
    stats: dict[str, int | float],
) -> Any:
    """Tampão grelha 2D + contain/bridge — só quando bisect-fill deixa buracos grandes."""
    from aigamekit_shared.mesh_repair import remove_doubles

    band_pre = max(cell * DEFAULT_SEAL_BAND_CELLS, 0.05)
    stats["horiz_stripped"] = float(stats.get("horiz_stripped", 0)) + float(
        _strip_horizontals_near_cut(obj, cut_up, band=band_pre)
    )

    mask, ox, oy, cell_eff = _rasterize_cut_plane(obj, cut_up, cell=cell, max_grid=DEFAULT_SEAL_MAX_GRID)
    close_iters = max(1, round(close_m / max(cell_eff, 1e-6)))
    solid = _solidify_cut_mask(mask, close_iters=close_iters)
    stats["mask_cells"] = int(mask.sum())
    stats["solid_cells"] = int(solid.sum())

    cap_mask = _binary_erode(solid, 1)
    if not cap_mask.any():
        cap_mask = solid

    band_half = cell_eff * DEFAULT_SEAL_BAND_CELLS
    pre = _contain_cut_band_to_volume(
        obj,
        solid,
        origin_x=ox,
        origin_y=oy,
        cell=cell_eff,
        cut_up=cut_up,
        band_half=band_half,
        strip_horizontal=False,
    )
    stats["clip_faces_deleted"] = float(
        float(stats.get("clip_faces_deleted", 0)) + pre["faces_deleted"] + pre["verts_deleted"]
    )

    _patch_cut_plane_holes(obj, cut_up, tol=max(0.05, cell_eff * 2.5))
    remove_doubles(obj, max(cell_eff * 0.35, 1e-4))

    weld_dist = max(cell_eff * 1.1, 0.016)
    cap = _mask_to_flat_cap_object(
        cap_mask,
        origin_x=ox,
        origin_y=oy,
        cell=cell_eff,
        z=float(cut_up),
        keep_below=keep_below,
        name=f"{obj.name}_cap",
    )
    if cap is not None:
        obj = _join_two(obj, cap)
    bridge = _bridge_cap_to_bark(
        obj,
        cut_up,
        band=band_half * 1.5,
        max_bridge_dist=max(cell_eff * 4.0, 0.05),
        weld_dist=weld_dist,
    )
    stats["clip_pulled"] = float(bridge["snapped"])
    stats["bridge_filled"] = float(bridge["filled"])

    bleed = _contain_cut_band_to_volume(
        obj,
        cap_mask,
        origin_x=ox,
        origin_y=oy,
        cell=cell_eff,
        cut_up=cut_up,
        band_half=band_half,
        strip_horizontal=True,
        delete_outside_verts=False,
    )
    stats["clip_faces_deleted"] = float(stats["clip_faces_deleted"]) + float(bleed["faces_deleted"])
    stats["horiz_stripped"] = float(stats["horiz_stripped"]) + float(bleed["horiz_stripped"])
    if bleed["horiz_stripped"] > 0:
        cap2 = _mask_to_flat_cap_object(
            cap_mask,
            origin_x=ox,
            origin_y=oy,
            cell=cell_eff,
            z=float(cut_up),
            keep_below=keep_below,
            name=f"{obj.name}_cap2",
        )
        if cap2 is not None:
            obj = _join_two(obj, cap2)
        bridge2 = _bridge_cap_to_bark(
            obj,
            cut_up,
            band=band_half * 1.5,
            max_bridge_dist=max(cell_eff * 4.0, 0.05),
            weld_dist=weld_dist,
        )
        stats["clip_pulled"] = float(stats["clip_pulled"]) + float(bridge2["snapped"])
        stats["bridge_filled"] = float(stats["bridge_filled"]) + float(bridge2["filled"])

    leak = _cleanup_cut_leak_geometry(
        obj,
        cut_up,
        max_thickness=max(DEFAULT_LEAK_MAX_THICKNESS, cell_eff * 1.5),
        min_span=DEFAULT_LEAK_MIN_SPAN,
        band=max(DEFAULT_LEAK_BAND_M, cell_eff * 4.0),
        micro_faces=0,
        micro_vol_frac=0.0,
    )
    stats["leak_thin"] = float(leak["thin_faces"])
    stats["leak_debris"] = float(leak["debris_faces"])
    if leak["thin_faces"] > 0:
        _patch_cut_plane_holes(obj, cut_up, tol=max(0.05, cell_eff * 2.5))
        remove_doubles(obj, max(cell_eff * 0.35, 1e-4))
    stats["seal_mode"] = 1.0  # raster fallback
    return obj


def _seal_and_bevel_cut(
    obj: Any,
    cut_up: float,
    *,
    keep_below: bool,
    bevel_offset: float | None = None,
    bevel_segments: int = DEFAULT_BEVEL_SEGMENTS,
    bevel_profile: float = DEFAULT_BEVEL_PROFILE,
    cell: float = DEFAULT_SEAL_CELL,
    close_m: float = DEFAULT_SEAL_CLOSE_M,
    boundary_fallback: int = DEFAULT_BOUNDARY_FALLBACK,
) -> tuple[Any, dict[str, int | float]]:
    """Fecha o corte: bisect-fill nativo + UV casca; raster só se boundary alto.

    Returns:
        ``(obj_final, stats)``.
    """
    import bpy

    from aigamekit_shared.bpy_mesh import apply_smooth_by_angle
    from aigamekit_shared.mesh_repair import (
        count_boundary_edges_fast,
        delete_loose,
        dissolve_degenerate,
        normals_consistent,
        remove_doubles,
        triangulate,
    )

    stats: dict[str, int | float] = {
        "mask_cells": 0,
        "solid_cells": 0,
        "uv_bark_fixed": 0,
        "bevel_faces": 0,
        "boundary_after": -1,
        "boundary_near_cut": -1,
        "device": 1.0 if _topo_device() == "cuda" else 0.0,
        "clip_pulled": 0.0,
        "clip_faces_deleted": 0.0,
        "horiz_stripped": 0.0,
        "leak_thin": 0.0,
        "leak_debris": 0.0,
        "slivers": 0.0,
        "internal_shell": 0.0,
        "dissolved_edges": 0.0,
        "flattened": 0.0,
        "bridge_filled": 0.0,
        "morph_closed": 0.0,
        "seal_mode": 0.0,  # 0=bisect-fill, 1=raster fallback
        "holes_patched": 0.0,
    }
    _ = bevel_offset  # API estável; bevel desligado por omissão.

    # Snapshot UV antes de qualquer limpeza pós-bisect.
    uv_src = _duplicate_object(obj, f"{obj.name}_uvsrc")
    uv_src.hide_set(True)

    cell_eff = float(cell)
    # 1) Pós-bisect leve no rebordo (não fuse agressivo — destrói UV).
    dissolve_degenerate(obj, threshold=max(1e-6, cell_eff * 0.08))
    remove_doubles(obj, max(1e-5, cell_eff * 0.25))
    delete_loose(obj)
    stats["holes_patched"] = float(_patch_cut_plane_holes(obj, cut_up, tol=max(0.05, cell_eff * 2.5)))
    remove_doubles(obj, max(1e-5, cell_eff * 0.25))

    near = _count_boundary_near_cut(obj, cut_up, band=max(0.08, cell_eff * 4.0))
    stats["boundary_near_cut"] = float(near)

    # 2) Fallback raster só se o fill nativo deixou buracos grandes.
    if near > int(boundary_fallback):
        log.warning(
            "seal_cut %s: boundary_near_cut=%d > %d — fallback raster-cap",
            obj.name,
            near,
            boundary_fallback,
        )
        obj = _seal_raster_cap_fallback(
            obj,
            cut_up,
            keep_below=keep_below,
            cell=cell,
            close_m=close_m,
            stats=stats,
        )
        cell_eff = float(cell)  # raster may refine; keep nominal for UV tol

    # 3) UV: transfer + faces do fill → casca vertical.
    _transfer_uvs_and_materials(uv_src, obj)
    stats["uv_bark_fixed"] = _retarget_cut_face_uvs_to_bark(obj, uv_src, cut_up, tol=max(0.04, cell_eff * 2.0))

    if int(bevel_segments) > 0:
        radius = 0.5 * max(float(obj.dimensions.x), float(obj.dimensions.y), cell_eff * 4.0)
        off = bevel_offset
        if off is None:
            off = max(0.006, radius * DEFAULT_BEVEL_RADIUS_FRAC)
        off = float(min(float(off), cell_eff * 0.8))
        stats["bevel_faces"] = _soft_bevel_cut_rim(
            obj,
            cut_up,
            offset=off,
            segments=int(bevel_segments),
            profile=float(bevel_profile),
            tol=max(0.04, cell_eff * 2.0),
        )

    aggressive = float(stats.get("seal_mode", 0)) >= 1.0
    post = _post_seal_cleanup(obj, cut_up, cell=cell_eff, aggressive=aggressive)
    stats["leak_thin"] = float(stats["leak_thin"]) + float(post["leak_thin"])
    stats["leak_debris"] = float(stats["leak_debris"]) + float(post["leak_debris"])
    stats["slivers"] = post["slivers"]
    stats["dissolved_edges"] = float(post.get("dissolved_edges", 0))
    stats["flattened"] = float(post.get("flattened", 0))

    triangulate(obj)
    normals_consistent(obj, inside=False)
    apply_smooth_by_angle(obj, 60.0)
    stats["boundary_after"] = count_boundary_edges_fast(obj)
    stats["boundary_near_cut"] = float(_count_boundary_near_cut(obj, cut_up, band=max(0.08, cell_eff * 4.0)))

    bpy.ops.object.select_all(action="DESELECT")
    uv_src.hide_set(False)
    uv_src.select_set(True)
    bpy.ops.object.delete()

    log.info(
        "seal_cut %s: mode=%s holes=%d near_cut=%d uv_bark=%d mask=%d solid=%d leak_thin=%d slivers=%d boundary=%d",
        obj.name,
        "raster" if aggressive else "bisect-fill",
        int(stats["holes_patched"]),
        int(stats["boundary_near_cut"]),
        stats["uv_bark_fixed"],
        stats["mask_cells"],
        stats["solid_cells"],
        int(stats["leak_thin"]),
        int(stats["slivers"]),
        stats["boundary_after"],
    )
    return obj, stats


def split_mesh_object_at_height(
    obj: Any,
    cut_y_world: float,
    *,
    cap: bool = False,
    bevel_offset: float | None = None,
    bevel_segments: int = DEFAULT_BEVEL_SEGMENTS,
    bevel_profile: float = DEFAULT_BEVEL_PROFILE,
    stump_name: str = DEFAULT_STUMP_NAME,
    top_name: str = DEFAULT_TOP_NAME,
) -> tuple[Any, Any]:
    """Parte *obj* num plano horizontal à altura ``cut_y_world`` (Blender Z).

    Em bpy o up é Z; após ``save_glb`` isso vira Y no glTF/jogo.
    Default ``cap=False``: só bisect (buraco aberto no corte).
    ``cap=True`` activa o fecho legado (experimental — pode criar artefactos).

    Returns:
        ``(stump, top)`` — dois objectos bpy novos; o original é removido.
    """
    import bpy

    if obj is None or getattr(obj, "type", None) != "MESH":
        raise ValueError("split_mesh_object_at_height requer um objecto MESH")
    if len(obj.data.polygons) < 4:
        raise ValueError(f"Mesh com poucas faces ({len(obj.data.polygons)}); split não aplicável")

    stump = _duplicate_object(obj, stump_name)
    top = _duplicate_object(obj, top_name)

    _bisect_keep_side(stump, cut_y_world, keep_below=True)
    _bisect_keep_side(top, cut_y_world, keep_below=False)

    if cap:
        stump, _ = _seal_and_bevel_cut(
            stump,
            cut_y_world,
            keep_below=True,
            bevel_offset=bevel_offset,
            bevel_segments=bevel_segments,
            bevel_profile=bevel_profile,
        )
        top, _ = _seal_and_bevel_cut(
            top,
            cut_y_world,
            keep_below=False,
            bevel_offset=bevel_offset,
            bevel_segments=bevel_segments,
            bevel_profile=bevel_profile,
        )

    stump.name = stump_name
    top.name = top_name
    if stump.data is not None:
        stump.data.name = stump_name
    if top.data is not None:
        top.data.name = top_name

    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.ops.object.delete()

    if len(stump.data.polygons) < 1:
        raise ValueError("Stump ficou sem faces — cut-height fora da malha?")
    if len(top.data.polygons) < 1:
        raise ValueError("Top ficou sem faces — cut-height fora da malha?")

    return stump, top


def default_cut_height_m(mesh_height: float) -> float:
    """Altura de corte default: ``min(0.8 m, 1/4 da altura da malha)``."""
    h = float(mesh_height)
    if h <= 1e-8:
        raise ValueError("altura da malha nula")
    cut = min(DEFAULT_CUT_HEIGHT_MAX, h * DEFAULT_CUT_HEIGHT_RATIO)
    # Guarda: deixar margem aos extremos (≥1% / 1 cm).
    max_ok = h * 0.49
    min_ok = min(0.01, h * 0.05)
    return float(max(min_ok, min(cut, max_ok)))


def resolve_cut_y(
    bbox_min_up: float,
    bbox_max_up: float,
    *,
    cut_height: float | None = None,
    cut_ratio: float | None = None,
) -> float:
    """Resolve a coordenada do plano de corte a partir da base da AABB (eixo up).

    Em bpy o eixo up é Z; o valor exportado corresponde ao Y do glTF.
    ``cut_height`` (metros) e ``cut_ratio`` (0-1) sao mutuamente exclusivos.
    Default: ``min(0.8 m, altura/4)`` acima de ``bbox_min_up``.
    """
    height = float(bbox_max_up) - float(bbox_min_up)
    if height <= 1e-8:
        raise ValueError("AABB com altura nula; impossível partir a malha")

    if cut_height is not None and cut_ratio is not None:
        raise ValueError("Use cut_height ou cut_ratio, não ambos")

    if cut_ratio is not None:
        ratio = float(cut_ratio)
        if not 0.0 < ratio < 1.0:
            raise ValueError(f"cut_ratio deve estar em (0, 1); recebido {ratio}")
        cut = float(bbox_min_up) + ratio * height
    elif cut_height is not None:
        h = float(cut_height)
        if h <= 0.0:
            raise ValueError(f"cut_height deve ser > 0; recebido {h}")
        if h >= height:
            raise ValueError(f"cut_height ({h}) >= altura da malha ({height:.4f})")
        cut = float(bbox_min_up) + h
    else:
        cut = float(bbox_min_up) + default_cut_height_m(height)

    eps = max(1e-4, height * 0.01)
    if cut <= bbox_min_up + eps or cut >= bbox_max_up - eps:
        raise ValueError(
            f"Plano de corte up={cut:.4f} demasiado perto dos extremos [{bbox_min_up:.4f}, {bbox_max_up:.4f}]"
        )
    return cut


def split_glb_at_height(
    path_in: str | Path,
    path_out: str | Path,
    *,
    cut_height: float | None = None,
    cut_ratio: float | None = None,
    cap: bool = False,
    bevel_offset: float | None = None,
    bevel_segments: int = DEFAULT_BEVEL_SEGMENTS,
    bevel_profile: float = DEFAULT_BEVEL_PROFILE,
    stump_name: str = DEFAULT_STUMP_NAME,
    top_name: str = DEFAULT_TOP_NAME,
    split_files: bool = False,
) -> SplitResult:
    """Carrega GLB, parte na altura pedida, exporta composição Stump+Top.

    Args:
        path_in: GLB de entrada (tipicamente single-mesh).
        path_out: GLB multi-mesh de saída.
        cut_height: Metros acima da base (pés). Default ``min(0.8, altura/4)``.
        cut_ratio: Fracção da altura AABB (alternativa a ``cut_height``).
        cap: Se True, tenta fechar o corte (legado/experimental). Default False.
        bevel_offset: Largura do chanfro (metros); None = auto. Só com ``cap``.
        bevel_segments: Segmentos do bevel. Só com ``cap``.
        bevel_profile: Profile do bevel. Só com ``cap``.
        stump_name / top_name: Nomes dos objectos exportados.
        split_files: Se True, também escreve ``{stem}_stump.glb`` e ``{stem}_top.glb``.

    Returns:
        ``SplitResult`` com paths e contagens de faces.
    """
    from aigamekit_shared.bpy_mesh import clear_scene, get_bounds, load_glb, save_glb

    path_in = Path(path_in)
    path_out = Path(path_out)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    if path_out.exists() and path_out.resolve() != path_in.resolve() and path_out.stat().st_nlink > 1:
        path_out.unlink()
        log.info("Hardlink quebrado em %s (nlink>1)", path_out.name)

    objs = load_glb(path_in)
    mesh_objs = [o for o in objs if getattr(o, "type", None) == "MESH"]
    if not mesh_objs:
        raise ValueError(f"Mesh vazia: {path_in}")

    obj = _join_mesh_objects(mesh_objs)
    bmin, bmax = get_bounds(obj)
    cut_up = resolve_cut_y(
        bmin[_UP_AXIS],
        bmax[_UP_AXIS],
        cut_height=cut_height,
        cut_ratio=cut_ratio,
    )
    height = bmax[_UP_AXIS] - bmin[_UP_AXIS]
    log.info(
        "split_glb_at_height: %s → cut_up=%.4f (height=%.4f, blender_Z)",
        path_in.name,
        cut_up,
        height,
    )

    stump, top = split_mesh_object_at_height(
        obj,
        cut_up,
        cap=cap,
        bevel_offset=bevel_offset,
        bevel_segments=bevel_segments,
        bevel_profile=bevel_profile,
        stump_name=stump_name,
        top_name=top_name,
    )
    stump_faces = len(stump.data.polygons)
    top_faces = len(top.data.polygons)

    write_path = path_out
    tmp_path: Path | None = None
    if path_out.resolve() == path_in.resolve():
        tmp_path = path_out.with_name(f"{path_out.stem}.split_tmp{path_out.suffix}")
        write_path = tmp_path

    save_glb([stump, top], write_path, export_apply=True)
    if tmp_path is not None:
        tmp_path.replace(path_out)

    stump_path: Path | None = None
    top_path: Path | None = None
    if split_files:
        stem = path_out.stem
        stump_path = path_out.with_name(f"{stem}_stump{path_out.suffix}")
        top_path = path_out.with_name(f"{stem}_top{path_out.suffix}")
        save_glb([stump], stump_path, export_apply=True)
        save_glb([top], top_path, export_apply=True)

    clear_scene()
    log.info(
        "Resultado: %s (stump=%d faces, top=%d faces)%s",
        path_out,
        stump_faces,
        top_faces,
        f"; split-files={stump_path.name},{top_path.name}" if stump_path and top_path else "",
    )
    return SplitResult(
        output=path_out,
        cut_y=cut_up,
        stump_faces=stump_faces,
        top_faces=top_faces,
        stump_path=stump_path,
        top_path=top_path,
    )
