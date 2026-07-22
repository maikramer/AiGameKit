"""Corte horizontal de malha em stump + top (árvores / props destruíveis).

Usa ``bpy.ops.mesh.bisect`` no eixo vertical do Blender (Z-up). O export glTF
(``export_yup``) mapeia Blender Z → glTF Y, por isso ``cut_height`` em metros
acima da base corresponde ao Y do jogo.

Fecho do corte (cascas Hunyuan abertas — fill topológico falha):

1. Rasteriza intersecção malha↔plano → máscara 2D;
2. Morph close 2D + flood-fill → **só interior** da silhueta;
3. Tampão *plano* no Z do corte (erode 1 célula, sem paredes/bevel/morph 3D);
4. Clip verts que saíram da silhueta; UV de casca nas faces do corte.

Sem bevel / morph voxel 3D — esses criavam o “colar” na casca.

Export multi-mesh via ``save_glb``.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_CUT_HEIGHT = 0.6
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
    """Bisect plano horizontal (Blender Z-up); ``keep_below=True`` → stump."""
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


# ---------------------------------------------------------------------------
# Fecho voxel / morph 2D no plano de corte
# ---------------------------------------------------------------------------


def _topo_device() -> str:
    """Device p/ morph 2D: ``GAMEDEV_TOPOFIX_DEVICE`` ou cuda se disponível."""
    import os

    forced = (os.environ.get("GAMEDEV_TOPOFIX_DEVICE") or "").strip().lower()
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
    """Dilatação 3×3; torch CUDA quando disponível (mesmo contrato topology-fix)."""
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
            return (t[0, 0] > 0.5).detach().cpu().numpy()
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
        cx, cy = int(round(x)), int(round(y))
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                i, j = cy + dy, cx + dx
                if 0 <= i < h and 0 <= j < w:
                    mask[i, j] = True


def _rasterize_cut_plane(
    obj: Any, cut_up: float, *, cell: float, max_grid: int
) -> tuple[np.ndarray, float, float, float]:
    """Máscara 2D da intersecção malha↔plano Z=cut_up.

    Returns:
        ``(mask[row=y, col=x], origin_x, origin_y, cell)``
    """
    me = obj.data
    n_verts = len(me.vertices)
    if n_verts == 0 or len(me.polygons) == 0:
        return np.zeros((8, 8), dtype=bool), 0.0, 0.0, cell

    coords = np.empty(n_verts * 3, dtype=np.float64)
    me.vertices.foreach_get("co", coords)
    coords = coords.reshape(-1, 3)

    # Banda: faces que cruzam o plano ou verts perto dele.
    band = max(cell * 2.0, 0.03)
    near = np.abs(coords[:, 2] - cut_up) <= band
    if not near.any():
        # fallback: todos os verts (bisect deveria ter criado verts no plano)
        near = np.ones(n_verts, dtype=bool)

    xs = coords[near, 0]
    ys = coords[near, 1]
    pad = cell * 3.0
    min_x, max_x = float(xs.min()) - pad, float(xs.max()) + pad
    min_y, max_y = float(ys.min()) - pad, float(ys.max()) + pad
    span_x = max(max_x - min_x, cell)
    span_y = max(max_y - min_y, cell)
    # Cap de grelha.
    cell_eff = max(cell, span_x / max_grid, span_y / max_grid)
    w = int(np.ceil(span_x / cell_eff)) + 1
    h = int(np.ceil(span_y / cell_eff)) + 1
    w = min(max(w, 8), max_grid)
    h = min(max(h, 8), max_grid)
    mask = np.zeros((h, w), dtype=bool)

    def _to_cell(x: float, y: float) -> tuple[float, float]:
        return (x - min_x) / cell_eff, (y - min_y) / cell_eff

    # Stamp verts no plano (radius=1 liga o anel da casca sem blob convexo).
    on_plane = np.abs(coords[:, 2] - cut_up) <= max(cell_eff, 0.02)
    for x, y in coords[on_plane, :2]:
        cx, cy = _to_cell(float(x), float(y))
        _stamp_segment(mask, cx, cy, cx, cy, radius=1)

    # Intersecção aresta↔plano.
    for poly in me.polygons:
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
    solid = _binary_open(solid, 1)
    if not solid.any():
        solid = ~_flood_fill_exterior(_binary_dilate(mask, 1))
    return solid


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
    """Puxa/apaga geometria da banda que saiu da silhueta 2D (só interior)."""
    import bmesh

    h, w = allowed.shape
    if h < 2 or w < 2 or not allowed.any():
        return {"pulled": 0, "faces_deleted": 0}

    # Prefixo: lista de células permitidas p/ nearest.
    ay, ax = np.nonzero(allowed)
    allow_pts = np.stack([ax.astype(np.float64), ay.astype(np.float64)], axis=1)

    def _nearest_cell(ix: float, iy: float) -> tuple[int, int]:
        d = (allow_pts[:, 0] - ix) ** 2 + (allow_pts[:, 1] - iy) ** 2
        j = int(np.argmin(d))
        return int(allow_pts[j, 0]), int(allow_pts[j, 1])

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.verts.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    pulled = 0
    z_pad = band_half * 1.6
    for v in bm.verts:
        if abs(v.co.z - cut_up) > z_pad:
            continue
        ix = (v.co.x - origin_x) / cell
        iy = (v.co.y - origin_y) / cell
        cx, cy = int(round(ix)), int(round(iy))
        if 0 <= cy < h and 0 <= cx < w and allowed[cy, cx]:
            continue
        nx, ny = _nearest_cell(ix, iy)
        # Puxar para o centro da célula permitida (fica *dentro* da borda).
        v.co.x = origin_x + (nx + 0.5) * cell
        v.co.y = origin_y + (ny + 0.5) * cell
        pulled += 1

    # Só apagar faces de tampão (|nz| alto) ainda fora — casca vertical já puxada.
    doomed: list[Any] = []
    for f in bm.faces:
        if abs(f.normal.z) < 0.75:
            continue
        c = f.calc_center_median()
        if abs(c.z - cut_up) > z_pad:
            continue
        ix = (c.x - origin_x) / cell
        iy = (c.y - origin_y) / cell
        # 2×2 células à volta (anti-alias da grelha).
        inside = False
        for dy in (0, 1):
            for dx in (0, 1):
                cx, cy = int(np.floor(ix)) + dx, int(np.floor(iy)) + dy
                if 0 <= cy < h and 0 <= cx < w and allowed[cy, cx]:
                    inside = True
                    break
            if inside:
                break
        if not inside:
            doomed.append(f)
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")
    loose = [v for v in bm.verts if not v.link_faces and abs(v.co.z - cut_up) <= z_pad]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="VERTS")
    bm.to_mesh(obj.data)
    obj.data.update()
    bm.free()
    return {"pulled": pulled, "faces_deleted": len(doomed)}


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
    from gamedev_shared.mesh_repair import morphological_close

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
) -> tuple[Any, dict[str, int | float]]:
    """Fecha o corte de forma seamless: tampão plano interior, sem bevel/morph 3D.

    Returns:
        ``(obj_final, stats)``.
    """
    import bpy

    from gamedev_shared.bpy_mesh import apply_smooth_by_angle
    from gamedev_shared.mesh_repair import (
        count_boundary_edges_fast,
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
        "device": 1.0 if _topo_device() == "cuda" else 0.0,
        "clip_pulled": 0.0,
        "clip_faces_deleted": 0.0,
    }
    _ = bevel_offset  # API estável; bevel desligado por omissão.

    uv_src = _duplicate_object(obj, f"{obj.name}_uvsrc")
    uv_src.hide_set(True)

    mask, ox, oy, cell_eff = _rasterize_cut_plane(obj, cut_up, cell=cell, max_grid=DEFAULT_SEAL_MAX_GRID)
    close_iters = max(1, int(round(close_m / max(cell_eff, 1e-6))))
    solid = _solidify_cut_mask(mask, close_iters=close_iters)
    stats["mask_cells"] = int(mask.sum())
    stats["solid_cells"] = int(solid.sum())

    # Tampão plano *dentro* da silhueta (erode 1 — nunca pastas bordas).
    cap_mask = _binary_erode(solid, 1)
    if not cap_mask.any():
        cap_mask = solid
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

    # Soldar tampão ao rebordo do bisect (mesmo plano Z).
    remove_doubles(obj, max(cell_eff * 0.35, 1e-4))

    # Puxar qualquer vert na banda que tenha saído da silhueta.
    band_half = cell_eff * DEFAULT_SEAL_BAND_CELLS
    allowed_clip = solid  # estrito: sem dilate para fora
    clip = _clip_band_to_silhouette(
        obj,
        allowed_clip,
        origin_x=ox,
        origin_y=oy,
        cell=cell_eff,
        cut_up=cut_up,
        band_half=band_half,
    )
    stats["clip_pulled"] = float(clip["pulled"])
    stats["clip_faces_deleted"] = float(clip["faces_deleted"])
    remove_doubles(obj, max(1e-5, cell_eff * 0.2))

    _transfer_uvs_and_materials(uv_src, obj)
    stats["uv_bark_fixed"] = _retarget_cut_face_uvs_to_bark(obj, uv_src, cut_up, tol=max(0.04, cell_eff * 2.0))

    # Bevel opcional (default 0) — evita colar na casca.
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

    _patch_cut_plane_holes(obj, cut_up, tol=max(0.05, cell_eff * 2.5))
    remove_doubles(obj, max(1e-5, cell_eff * 0.2))

    triangulate(obj)
    normals_consistent(obj, inside=False)
    apply_smooth_by_angle(obj, 60.0)
    stats["boundary_after"] = count_boundary_edges_fast(obj)

    bpy.ops.object.select_all(action="DESELECT")
    uv_src.hide_set(False)
    uv_src.select_set(True)
    bpy.ops.object.delete()

    log.info(
        "seal_cut %s: mask=%d solid=%d flat_cap gpu2d=%s clip_pull=%d clip_del=%d uv_bark=%d bevel=%d boundary=%d",
        obj.name,
        stats["mask_cells"],
        stats["solid_cells"],
        "cuda" if stats["device"] else "cpu",
        int(stats["clip_pulled"]),
        int(stats["clip_faces_deleted"]),
        stats["uv_bark_fixed"],
        stats["bevel_faces"],
        stats["boundary_after"],
    )
    return obj, stats


def split_mesh_object_at_height(
    obj: Any,
    cut_y_world: float,
    *,
    cap: bool = True,
    bevel_offset: float | None = None,
    bevel_segments: int = DEFAULT_BEVEL_SEGMENTS,
    bevel_profile: float = DEFAULT_BEVEL_PROFILE,
    stump_name: str = DEFAULT_STUMP_NAME,
    top_name: str = DEFAULT_TOP_NAME,
) -> tuple[Any, Any]:
    """Parte *obj* num plano horizontal à altura ``cut_y_world`` (Blender Z).

    Em bpy o up é Z; após ``save_glb`` isso vira Y no glTF/jogo.
    Com ``cap=True`` fecha stump e top com fecho voxel + chanfro suave.

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
    Default: ``DEFAULT_CUT_HEIGHT`` metros acima de ``bbox_min_up``.
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
    else:
        h = DEFAULT_CUT_HEIGHT if cut_height is None else float(cut_height)
        if h <= 0.0:
            raise ValueError(f"cut_height deve ser > 0; recebido {h}")
        if h >= height:
            raise ValueError(f"cut_height ({h}) >= altura da malha ({height:.4f})")
        cut = float(bbox_min_up) + h

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
    cap: bool = True,
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
        cut_height: Metros acima da base (pés). Default 0.6.
        cut_ratio: Fracção da altura AABB (alternativa a ``cut_height``).
        cap: Fechar corte com fecho voxel (silhueta 2D + remesh) + chanfro.
        bevel_offset: Largura do chanfro (metros); None = auto.
        bevel_segments: Segmentos do bevel (suavidade do chanfro).
        bevel_profile: Profile do bevel (0.5=recto, ~0.65=arredondado).
        stump_name / top_name: Nomes dos objectos exportados.
        split_files: Se True, também escreve ``{stem}_stump.glb`` e ``{stem}_top.glb``.

    Returns:
        ``SplitResult`` com paths e contagens de faces.
    """
    from gamedev_shared.bpy_mesh import clear_scene, get_bounds, load_glb, save_glb

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
