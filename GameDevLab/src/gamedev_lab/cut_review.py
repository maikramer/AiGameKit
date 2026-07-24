"""Review rápido do fecho de corte (stump/top) — zoom + métricas."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from gamedev_lab.glb_import import import_glb


def _require_bpy():
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required for cut-review. Install with: pip install bpy") from None


def _cut_band_metrics(obj: Any, cut_z: float, *, band: float = 0.1) -> dict[str, Any]:
    """Métricas do plano de corte num objecto bpy (Z-up)."""
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(obj.data)
    boundary_near = 0
    open_loops = 0
    horiz_faces = 0
    uv_on_cut = False
    uv_lay = bm.loops.layers.uv.active if bm.loops.layers.uv else None

    # Só arestas de fronteira *quase horizontais* no plano (= buracos no
    # tampão). Costuras verticais da casca aberta (Hunyuan) não contam.
    for e in bm.edges:
        if len(e.link_faces) != 1:
            continue
        if not all(abs(v.co.z - cut_z) <= band for v in e.verts):
            continue
        d = e.verts[1].co - e.verts[0].co
        length = d.length
        if length < 1e-9:
            continue
        if abs(d.z) / length < 0.35:  # edge ~horizontal
            boundary_near += 1

    # Heurística: cada loop fechado típico tem ≥3 edges.
    open_loops = math.ceil(boundary_near / 3.0) if boundary_near else 0

    for f in bm.faces:
        z_avg = sum(v.co.z for v in f.verts) / max(len(f.verts), 1)
        if abs(f.normal.z) > 0.8 and abs(z_avg - cut_z) <= band:
            horiz_faces += 1
            if uv_lay is not None:
                uvs = [loop[uv_lay].uv[:] for loop in f.loops]
                if any(abs(u) > 1e-6 or abs(v) > 1e-6 for u, v in uvs):
                    uv_on_cut = True

    bm.free()
    return {
        "boundary_edges_near_cut": boundary_near,
        "open_loops_near_cut": open_loops,
        "horizontal_faces_on_cut": horiz_faces,
        "uv_on_cut_faces": uv_on_cut,
        "name": obj.name,
        "faces": len(obj.data.polygons),
    }


def review_cut(
    glb_path: str | Path,
    output_dir: str | Path,
    *,
    cut_height: float | None = None,
    resolution: int = 512,
    engine: str = "workbench",
    band: float = 0.12,
) -> dict[str, Any]:
    """Screenshots zoom na banda do corte + JSON de métricas.

    Args:
        glb_path: GLB stump, top, ou composição Stump+Top.
        output_dir: Pasta para PNGs + ``cut_review.json``.
        cut_height: Altura do corte (Blender Z / metros). Default: mid-height
            dos bounds, ou 0.8 se altura > 3 m.
        resolution: Resolução px.
        engine: ``workbench`` | ``eevee``.
        band: Meia-largura da banda de métricas (m).
    """
    from gamedev_lab.renderer import _add_camera, _remove_camera, _setup_render
    from gamedev_shared.bpy_mesh import clear_scene, get_bounds
    from gamedev_shared.mesh_split import default_cut_height_m, resolve_cut_y

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        raise ValueError(f"Sem meshes em {glb_path}")

    # glTF splita verts por UV/normal — boundary pós-import é falso positivo.
    # Weld curto restaura topologia partilhada antes das métricas.
    from gamedev_shared.mesh_repair import remove_doubles

    for obj in meshes:
        remove_doubles(obj, 1e-4)

    # Bounds globais (world).
    mins = [float("inf")] * 3
    maxs = [float("-inf")] * 3
    for obj in meshes:
        mn, mx = get_bounds(obj)
        for i in range(3):
            mins[i] = min(mins[i], float(mn[i]))
            maxs[i] = max(maxs[i], float(mx[i]))

    height = maxs[2] - mins[2]
    if cut_height is None:
        try:
            cut_z = resolve_cut_y(mins[2], maxs[2])
        except Exception:
            cut_z = mins[2] + float(default_cut_height_m(height))
    else:
        cut_z = float(cut_height)
        # Se o caller passou altura relativa à base (glTF Y), alinhar a world Z.
        if cut_z < mins[2] - 0.05 or cut_z > maxs[2] + 0.05:
            cut_z = mins[2] + float(cut_height)

    metrics_per_mesh = [_cut_band_metrics(o, cut_z, band=band) for o in meshes]
    totals = {
        "boundary_edges_near_cut": sum(m["boundary_edges_near_cut"] for m in metrics_per_mesh),
        "open_loops_near_cut": sum(m["open_loops_near_cut"] for m in metrics_per_mesh),
        "horizontal_faces_on_cut": sum(m["horizontal_faces_on_cut"] for m in metrics_per_mesh),
        "uv_on_cut_faces": any(m["uv_on_cut_faces"] for m in metrics_per_mesh),
    }

    # Câmaras focadas na banda do corte (não full-body).
    cx = 0.5 * (mins[0] + maxs[0])
    cy = 0.5 * (mins[1] + maxs[1])
    span_xy = max(maxs[0] - mins[0], maxs[1] - mins[1], 0.4)
    dist = max(span_xy * 1.8, 0.6)
    target = (cx, cy, cut_z)
    views = {
        "cut_front": ((cx, cy - dist, cut_z + span_xy * 0.15), target),
        "cut_side": ((cx + dist, cy, cut_z + span_xy * 0.15), target),
        "cut_three_quarter": ((cx + dist * 0.7, cy - dist * 0.7, cut_z + span_xy * 0.25), target),
        "cut_top": ((cx, cy - 0.05, cut_z + dist * 0.9), target),
    }

    _setup_render(resolution, engine=engine, film_transparent=True)
    screenshots: list[dict[str, Any]] = []
    for view_name, (loc, tgt) in views.items():
        camera = _add_camera(loc, tgt, ortho=True)
        camera.data.ortho_scale = max(span_xy * 1.6, band * 8.0)
        out_path = output_dir / f"{view_name}.png"
        bpy.context.scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        screenshots.append({"view": view_name, "path": str(out_path)})
        _remove_camera(camera)

    report: dict[str, Any] = {
        "glb": str(glb_path),
        "cut_height": cut_z,
        "band": band,
        "bounds_min": mins,
        "bounds_max": maxs,
        "meshes": metrics_per_mesh,
        **totals,
        "screenshots": screenshots,
        "ok": totals["boundary_edges_near_cut"] <= 16 and totals["horizontal_faces_on_cut"] >= 1,
    }
    (output_dir / "cut_review.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    clear_scene()
    return report
