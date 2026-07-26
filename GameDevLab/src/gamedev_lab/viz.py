"""Mesh debug visualizations — native bpy renders for mesh QA.

Modes (see :data:`VIZ_MODES`):

* ``normals`` — per-vertex normal → RGB color scale ((n+1)/2).
* ``normals-arrows`` — sampled arrow geometry along vertex normals.
* ``orientation`` — front faces blue, backfaces red (EEVEE ``Backfacing``).
* ``uv`` — UV_GRID checker texture on every material (distortion/seams).
* ``edges`` — boundary edges red, non-manifold orange, rest gray.
* ``weights`` — skin weights: dominant bone / influence count / unweighted,
  plus single-bone heatmap delegation to ``render_weight_heatmap``.

All modes accept ``--wireframe`` (black wireframe overlay) and embed a
Pillow legend/colorbar panel in each PNG.
"""

from __future__ import annotations

import colorsys
import json
from pathlib import Path
from typing import Any

from gamedev_lab.glb_import import import_glb
from gamedev_lab.renderer import (
    CAMERA_PRESETS,
    _add_camera,
    _auto_frame_camera,
    _remove_camera,
    _require_bpy,
    _setup_render,
    weight_to_color,
)

VIZ_MODES = ("normals", "normals-arrows", "orientation", "uv", "edges", "weights")
WEIGHT_VIEWS = ("dominant", "count", "unweighted", "bone")

DEFAULT_VIZ_VIEWS = "front,three_quarter,right,back"

# Influence-count palette caps: >MAX_INFLUENCES_HIGHLIGHT flagged magenta
# (GPU skinning is typically limited to 4 influences per vertex).
MAX_INFLUENCES_HIGHLIGHT = 4

GRAY = (0.5, 0.5, 0.5)
MAGENTA = (1.0, 0.0, 1.0)
BOUNDARY_RED = (1.0, 0.1, 0.1)
NONMANIFOLD_ORANGE = (1.0, 0.55, 0.0)

_WEIGHT_EPS = 1e-4


# ---------------------------------------------------------------------------
# Pure helpers (no bpy) — unit-testable
# ---------------------------------------------------------------------------


def normal_to_rgb(nx: float, ny: float, nz: float) -> tuple[float, float, float]:
    """Map a unit normal to RGB: channel = (component + 1) / 2, clamped."""

    def _c(v: float) -> float:
        return max(0.0, min(1.0, (float(v) + 1.0) * 0.5))

    return (_c(nx), _c(ny), _c(nz))


def bone_palette(names: list[str]) -> dict[str, tuple[float, float, float]]:
    """Deterministic categorical palette: golden-ratio hue walk over sorted names."""
    palette: dict[str, tuple[float, float, float]] = {}
    for i, name in enumerate(sorted(names)):
        hue = (i * 0.61803398875) % 1.0
        palette[name] = colorsys.hsv_to_rgb(hue, 0.75, 0.95)
    return palette


def influence_count_color(count: int) -> tuple[float, float, float]:
    """Color for the number of bone influences on a vertex.

    0 → gray (unweighted), 1..MAX → blue→red ramp, >MAX → magenta.
    """
    if count <= 0:
        return GRAY
    if count > MAX_INFLUENCES_HIGHLIGHT:
        return MAGENTA
    t = (count - 1) / max(1, MAX_INFLUENCES_HIGHLIGHT - 1)
    return weight_to_color(t)


def sample_indices(total: int, sample: int) -> list[int]:
    """Evenly-spaced deterministic subsample of ``range(total)``."""
    if total <= 0 or sample <= 0:
        return []
    if total <= sample:
        return list(range(total))
    step = total / sample
    return [min(int(i * step), total - 1) for i in range(sample)]


def _float_rgb_to_255(rgb: tuple[float, float, float]) -> tuple[int, int, int]:
    return tuple(round(max(0.0, min(1.0, c)) * 255) for c in rgb)  # type: ignore[return-value]


def make_legend(entries: list[tuple[str, tuple[float, float, float]]], *, title: str | None = None) -> Any:
    """Build a legend panel (PIL Image): color swatch + label per entry."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    swatch, pad, row_h = 12, 6, 16
    rows = len(entries) + (1 if title else 0)
    text_w = max(
        [int(font.getlength(label)) for label, _ in entries] + ([int(font.getlength(title))] if title else [0])
    )
    width = pad * 3 + swatch + text_w
    height = pad * 2 + rows * row_h
    img = Image.new("RGBA", (width, height), (255, 255, 255, 210))
    draw = ImageDraw.Draw(img)
    y = pad
    if title:
        draw.text((pad, y), title, fill=(0, 0, 0, 255), font=font)
        y += row_h
    for label, rgb in entries:
        draw.rectangle([pad, y + 2, pad + swatch, y + 2 + swatch], fill=(*_float_rgb_to_255(rgb), 255))
        draw.text((pad * 2 + swatch, y), label, fill=(0, 0, 0, 255), font=font)
        y += row_h
    return img


def make_colorbar(
    stops: list[tuple[float, float, float]],
    *,
    labels: tuple[str, str] = ("0", "1"),
    title: str | None = None,
    width: int = 180,
    height: int = 14,
) -> Any:
    """Build a horizontal gradient colorbar panel (PIL Image)."""
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.load_default()
    pad, text_h = 6, 14
    rows_h = (text_h if title else 0) + height + text_h
    img = Image.new("RGBA", (width + pad * 2, rows_h + pad * 2), (255, 255, 255, 210))
    draw = ImageDraw.Draw(img)
    y = pad
    if title:
        draw.text((pad, y), title, fill=(0, 0, 0, 255), font=font)
        y += text_h
    n = len(stops)
    for x in range(width):
        t = x / max(1, width - 1)
        pos = t * (n - 1)
        i = min(int(pos), n - 2)
        f = pos - i
        rgb = tuple(stops[i][c] * (1 - f) + stops[i + 1][c] * f for c in range(3))
        draw.line([(pad + x, y), (pad + x, y + height)], fill=(*_float_rgb_to_255(rgb), 255))  # type: ignore[arg-type]
    y += height
    draw.text((pad, y), labels[0], fill=(0, 0, 0, 255), font=font)
    right_w = int(font.getlength(labels[1]))
    draw.text((pad + width - right_w, y), labels[1], fill=(0, 0, 0, 255), font=font)
    return img


def attach_panel(png_path: str | Path, panel: Any) -> None:
    """Paste a legend/colorbar panel into the bottom-left corner of a PNG."""
    from PIL import Image

    path = Path(png_path)
    base = Image.open(path).convert("RGBA")
    margin = 8
    pos = (margin, base.height - panel.height - margin)
    base.alpha_composite(panel, dest=pos)
    base.save(path)


# ---------------------------------------------------------------------------
# bpy scene preparation per mode
# ---------------------------------------------------------------------------


def _scene_meshes() -> list[Any]:
    bpy = _require_bpy()
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def _paint_vertex_colors(obj: Any, colors: list[tuple[float, float, float]]) -> None:
    """Write per-vertex RGB into a ``VizColors`` loop color layer."""
    mesh = obj.data
    layer = mesh.vertex_colors.get("VizColors")
    if layer is None:
        layer = mesh.vertex_colors.new(name="VizColors")
    mesh.vertex_colors.active = layer
    data = layer.data
    for li, loop in enumerate(mesh.loops):
        r, g, b = colors[loop.vertex_index]
        data[li].color = (r, g, b, 1.0)
    mesh.update()


def _scene_extent() -> float:
    """Max world-space bbox extent across all meshes (fallback 1.0)."""
    bpy = _require_bpy()
    from mathutils import Vector

    coords = []
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            coords.extend(obj.matrix_world @ Vector(c) for c in obj.bound_box)
    if not coords:
        return 1.0
    mins = [min(v[i] for v in coords) for i in range(3)]
    maxs = [max(v[i] for v in coords) for i in range(3)]
    return max(maxs[i] - mins[i] for i in range(3)) or 1.0


def _prepare_normals(*, world_space: bool) -> dict[str, Any]:
    """Paint vertex colors from vertex normals ((n+1)/2)."""
    for obj in _scene_meshes():
        nm = obj.matrix_world.inverted_safe().transposed().to_3x3() if world_space else None
        colors = []
        for v in obj.data.vertices:
            n = (nm @ v.normal).normalized() if nm is not None else v.normal
            colors.append(normal_to_rgb(n.x, n.y, n.z))
        _paint_vertex_colors(obj, colors)
    return {"world_space": world_space}


def _build_normal_arrows(*, sample: int, arrow_length: float | None) -> dict[str, Any]:
    """Create one arrow (4-sided pyramid) mesh along sampled vertex normals."""
    bpy = _require_bpy()
    from mathutils import Vector

    extent = _scene_extent()
    length = arrow_length if arrow_length else extent * 0.03
    half_w = length * 0.12

    # Evaluated depsgraph mesh: rigged GLBs render in armature pose, so arrows
    # must sample the deformed geometry, not the rest-pose vertices.
    deps = bpy.context.evaluated_depsgraph_get()
    points: list[tuple[Vector, Vector]] = []
    for obj in _scene_meshes():
        obj_eval = obj.evaluated_get(deps)
        mesh_eval = obj_eval.to_mesh()
        mw = obj_eval.matrix_world
        nm = mw.inverted_safe().transposed().to_3x3()
        for v in mesh_eval.vertices:
            points.append((mw @ v.co, (nm @ v.normal).normalized()))
        obj_eval.to_mesh_clear()

    picked = [points[i] for i in sample_indices(len(points), sample)]

    verts: list[tuple[float, float, float]] = []
    faces: list[tuple[int, ...]] = []
    up = Vector((0.0, 1.0, 0.0))
    alt = Vector((1.0, 0.0, 0.0))
    for pos, n in picked:
        axis = up if abs(n.dot(up)) < 0.95 else alt
        u = n.cross(axis).normalized() * half_w
        w = n.cross(u).normalized() * half_w
        base = len(verts)
        apex = pos + n * length
        verts.extend(
            [
                tuple(pos + u),
                tuple(pos + w),
                tuple(pos - u),
                tuple(pos - w),
                tuple(apex),
            ]
        )
        faces.extend(
            [
                (base, base + 1, base + 4),
                (base + 1, base + 2, base + 4),
                (base + 2, base + 3, base + 4),
                (base + 3, base, base + 4),
                (base, base + 3, base + 2, base + 1),
            ]
        )

    mesh = bpy.data.meshes.new("VizNormalArrows")
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    arrows = bpy.data.objects.new("VizNormalArrows", mesh)
    bpy.context.scene.collection.objects.link(arrows)

    mat = bpy.data.materials.new("VizArrowGreen")
    mat.diffuse_color = (0.0, 1.0, 0.1, 1.0)
    if not mat.use_nodes:
        mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.0, 1.0, 0.1, 1.0)
    arrows.data.materials.append(mat)
    return {"arrows": len(picked), "arrow_length": length, "total_vertices": len(points)}


def _apply_orientation_material() -> dict[str, Any]:
    """Override every material: front faces blue, backfaces red (EEVEE)."""
    bpy = _require_bpy()

    mat = bpy.data.materials.new("VizOrientation")
    if not mat.use_nodes:
        mat.use_nodes = True
    nt = mat.node_tree
    nt.nodes.clear()
    out = nt.nodes.new("ShaderNodeOutputMaterial")
    mix = nt.nodes.new("ShaderNodeMixShader")
    geom = nt.nodes.new("ShaderNodeNewGeometry")
    front = nt.nodes.new("ShaderNodeEmission")
    front.inputs["Color"].default_value = (0.05, 0.25, 1.0, 1.0)
    back = nt.nodes.new("ShaderNodeEmission")
    back.inputs["Color"].default_value = (1.0, 0.05, 0.05, 1.0)
    nt.links.new(geom.outputs["Backfacing"], mix.inputs[0])
    nt.links.new(front.outputs["Emission"], mix.inputs[1])
    nt.links.new(back.outputs["Emission"], mix.inputs[2])
    nt.links.new(mix.outputs["Shader"], out.inputs["Surface"])
    mat.use_backface_culling = False

    for obj in _scene_meshes():
        obj.data.materials.clear()
        obj.data.materials.append(mat)

    return {"flipped_face_estimate": _estimate_flipped_faces()}


def _estimate_flipped_faces() -> dict[str, Any]:
    """Estimate inconsistently-oriented faces via ``recalc_face_normals`` diff."""
    import bmesh

    total = 0
    flipped = 0
    for obj in _scene_meshes():
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        # Weld the analysis clone: glTF seam-splits fragment islands and
        # inflate the recalc-based estimate (render mesh stays untouched).
        bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-4)
        before = [f.normal.copy() for f in bm.faces]
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        for f, n_before in zip(bm.faces, before, strict=True):
            total += 1
            if n_before.length > 1e-9 and f.normal.dot(n_before) < 0:
                flipped += 1
        bm.free()
    pct = round(100.0 * flipped / total, 2) if total else 0.0
    return {"total_faces": total, "flipped_faces": flipped, "flipped_pct": pct}


def _apply_uv_checker() -> dict[str, Any]:
    """Wire a generated UV_GRID image into every material's Base Color."""
    bpy = _require_bpy()

    img = bpy.data.images.new("VizUVChecker", 1024, 1024)
    img.generated_type = "UV_GRID"

    materials_patched = 0
    meshes_without_uv: list[str] = []
    for obj in _scene_meshes():
        if not obj.data.uv_layers:
            meshes_without_uv.append(obj.name)
        if not obj.data.materials:
            obj.data.materials.append(bpy.data.materials.new(f"VizUV_{obj.name}"))
        for mat in obj.data.materials:
            if mat is None:
                continue
            if not mat.use_nodes:
                mat.use_nodes = True
            nt = mat.node_tree
            bsdf = next((n for n in nt.nodes if n.type == "BSDF_PRINCIPLED"), None)
            if bsdf is None:
                bsdf = nt.nodes.new("ShaderNodeBsdfPrincipled")
                out = next((n for n in nt.nodes if n.type == "OUTPUT_MATERIAL"), None)
                if out is None:
                    out = nt.nodes.new("ShaderNodeOutputMaterial")
                nt.links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
            tex = nt.nodes.new("ShaderNodeTexImage")
            tex.image = img
            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            nt.nodes.active = tex
            materials_patched += 1
    return {"materials_patched": materials_patched, "meshes_without_uv": meshes_without_uv}


def _prepare_edges() -> dict[str, Any]:
    """Paint boundary (red) and non-manifold (orange) edge vertices.

    glTF export splits vertices along UV/normal seams, turning every seam
    into a fake boundary after import. A short weld (same approach as
    ``cut_review``) restores shared topology so only real holes show up.
    """
    import bmesh

    from gamedev_shared.mesh_repair import remove_doubles

    boundary_total = 0
    nonmanifold_total = 0
    for obj in _scene_meshes():
        remove_doubles(obj, 1e-4)
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        colors: list[tuple[float, float, float]] = [GRAY] * len(bm.verts)
        for e in bm.edges:
            nf = len(e.link_faces)
            if nf == 1:
                boundary_total += 1
                for v in e.verts:
                    colors[v.index] = BOUNDARY_RED
            elif nf > 2:
                nonmanifold_total += 1
                for v in e.verts:
                    colors[v.index] = NONMANIFOLD_ORANGE
        bm.free()
        _paint_vertex_colors(obj, colors)
    return {"boundary_edges": boundary_total, "nonmanifold_edges": nonmanifold_total}


def _prepare_weights(view: str) -> dict[str, Any]:
    """Paint skin-weight visualization; returns stats + legend entries."""
    meshes = _scene_meshes()
    skinned = [o for o in meshes if o.vertex_groups]
    if not skinned:
        raise ValueError("GLB sem vertex groups — modo weights requer mesh rigada")

    stats: dict[str, Any] = {"view": view, "meshes": [o.name for o in skinned]}
    if view == "dominant":
        all_names = sorted({vg.name for o in skinned for vg in o.vertex_groups})
        palette = bone_palette(all_names)
        dominance: dict[str, int] = dict.fromkeys(all_names, 0)
        for obj in skinned:
            group_names = {vg.index: vg.name for vg in obj.vertex_groups}
            colors = []
            for v in obj.data.vertices:
                best_name, best_w = None, _WEIGHT_EPS
                for g in v.groups:
                    if g.weight > best_w:
                        best_w = g.weight
                        best_name = group_names.get(g.group)
                if best_name is None:
                    colors.append(GRAY)
                else:
                    dominance[best_name] += 1
                    colors.append(palette[best_name])
            _paint_vertex_colors(obj, colors)
        top = sorted(dominance.items(), key=lambda kv: -kv[1])
        stats["bones"] = len(all_names)
        stats["dominance_top"] = top[:16]
        stats["legend"] = [(name, palette[name]) for name, cnt in top[:16] if cnt > 0]
    elif view == "count":
        max_count = 0
        over_limit = 0
        for obj in skinned:
            colors = []
            for v in obj.data.vertices:
                cnt = sum(1 for g in v.groups if g.weight > _WEIGHT_EPS)
                max_count = max(max_count, cnt)
                if cnt > MAX_INFLUENCES_HIGHLIGHT:
                    over_limit += 1
                colors.append(influence_count_color(cnt))
            _paint_vertex_colors(obj, colors)
        stats["max_influences"] = max_count
        stats["verts_over_limit"] = over_limit
        stats["legend"] = (
            [("0 (sem peso)", GRAY)]
            + [(str(i), influence_count_color(i)) for i in range(1, MAX_INFLUENCES_HIGHLIGHT + 1)]
            + [(f">{MAX_INFLUENCES_HIGHLIGHT}", MAGENTA)]
        )
    elif view == "unweighted":
        light_gray = (0.75, 0.75, 0.75)
        unweighted = 0
        total = 0
        for obj in skinned:
            colors = []
            for v in obj.data.vertices:
                total += 1
                if sum(g.weight for g in v.groups) < 1e-6:
                    unweighted += 1
                    colors.append(MAGENTA)
                else:
                    colors.append(light_gray)
            _paint_vertex_colors(obj, colors)
        stats["unweighted_vertices"] = unweighted
        stats["total_vertices"] = total
        stats["legend"] = [("sem peso", MAGENTA), ("com peso", light_gray)]
    else:
        raise ValueError(f"weights-view inválida: {view!r} (usa {WEIGHT_VIEWS})")
    return stats


def _add_wireframe_overlay() -> None:
    """Duplicate meshes with a thin black Wireframe modifier on top."""
    bpy = _require_bpy()

    thickness = max(_scene_extent() * 0.0015, 1e-4)
    mat = bpy.data.materials.new("VizWireBlack")
    mat.diffuse_color = (0.0, 0.0, 0.0, 1.0)
    if not mat.use_nodes:
        mat.use_nodes = True
    bsdf = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf is not None:
        bsdf.inputs["Base Color"].default_value = (0.0, 0.0, 0.0, 1.0)

    for obj in list(_scene_meshes()):
        if obj.name.startswith("Viz"):
            continue
        dup = obj.copy()
        dup.data = obj.data.copy()
        dup.name = f"VizWire_{obj.name}"
        # Keep existing modifiers (ARMATURE deform) so the wireframe follows
        # the posed mesh; WIREFRAME appended last applies to deformed geometry.
        mod = dup.modifiers.new("VizWire", "WIREFRAME")
        mod.thickness = thickness
        mod.use_replace = True
        # Even offset scales by 1/sin(angle) — explodes to +-32k on the
        # degenerate/sliver geometry these debug views exist to expose.
        mod.use_even_offset = False
        mod.use_relative_offset = False
        mod.use_boundary = True
        dup.data.materials.clear()
        dup.data.materials.append(mat)
        with_colors = dup.data.vertex_colors.get("VizColors")
        if with_colors is not None:
            dup.data.vertex_colors.remove(with_colors)
        bpy.context.scene.collection.objects.link(dup)


# ---------------------------------------------------------------------------
# Legend panels per mode
# ---------------------------------------------------------------------------


def _panel_for_mode(mode: str, weights_view: str, metrics: dict[str, Any]) -> Any | None:
    # PIL's default bitmap font cannot draw accents/arrows — ASCII-only labels.
    if mode == "normals":
        return make_legend(
            [("+X", normal_to_rgb(1, 0, 0)), ("+Y", normal_to_rgb(0, 1, 0)), ("+Z", normal_to_rgb(0, 0, 1))],
            title="normal -> RGB",
        )
    if mode == "edges":
        return make_legend(
            [
                (f"boundary ({metrics.get('boundary_edges', 0)})", BOUNDARY_RED),
                (f"non-manifold ({metrics.get('nonmanifold_edges', 0)})", NONMANIFOLD_ORANGE),
            ],
            title="edges",
        )
    if mode == "orientation":
        est = metrics.get("flipped_face_estimate", {})
        return make_legend(
            [("frente", (0.05, 0.25, 1.0)), (f"backface ({est.get('flipped_pct', 0)}% est.)", (1.0, 0.05, 0.05))],
            title="orientacao",
        )
    if mode == "weights":
        legend = metrics.get("legend") or []
        if legend:
            title = {"dominant": "bone dominante", "count": "influencias", "unweighted": "pesos"}.get(weights_view)
            return make_legend([(str(label), tuple(rgb)) for label, rgb in legend], title=title)
    return None


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


def render_viz(
    glb_path: str | Path,
    output_dir: str | Path,
    mode: str,
    *,
    views: str = DEFAULT_VIZ_VIEWS,
    resolution: int = 512,
    engine: str = "workbench",
    ortho: bool = False,
    transparent_film: bool = True,
    sample: int = 2000,
    arrow_length: float | None = None,
    bone: str | None = None,
    weights_view: str = "dominant",
    wireframe: bool = False,
    world_space: bool = False,
) -> dict[str, Any]:
    """Render a mesh-debug visualization of a GLB (native bpy).

    Args:
        glb_path: Input GLB/GLTF (KTX2/meshopt decoded automatically).
        output_dir: Directory for PNGs + ``viz_report.json``.
        mode: One of :data:`VIZ_MODES`.
        views: Comma-separated camera preset names.
        resolution: Square render resolution.
        engine: ``workbench`` or ``eevee`` (``orientation`` forces eevee).
        ortho: Orthographic camera.
        transparent_film: Transparent background.
        sample: Max arrows for ``normals-arrows``.
        arrow_length: Arrow length in meters (default: 3% of bbox extent).
        bone: Bone name → delegates to single-bone heatmap (weights mode).
        weights_view: ``dominant`` | ``count`` | ``unweighted`` (weights mode).
        wireframe: Add black wireframe overlay.
        world_space: Use world-space normals (normals mode).

    Returns:
        Report dict with ``mode``, ``metrics``, ``screenshots``, settings.
    """
    if mode not in VIZ_MODES:
        raise ValueError(f"modo inválido: {mode!r} (usa {VIZ_MODES})")

    glb_path = Path(glb_path).expanduser().resolve()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if mode == "weights" and (bone or weights_view == "bone"):
        if not bone:
            raise ValueError("weights-view 'bone' requer --bone <nome>")
        from gamedev_lab.renderer import render_weight_heatmap

        report = render_weight_heatmap(
            glb_path,
            output_dir,
            bone,
            views=views,
            resolution=resolution,
            engine=engine,
            ortho=ortho,
            transparent_film=transparent_film,
        )
        colorbar = make_colorbar([weight_to_color(t / 10) for t in range(11)], labels=("0", "1"), title=f"peso: {bone}")
        for shot in report.get("weight_heatmap", {}).get("screenshots", []):
            attach_panel(shot["path"], colorbar)
        return report

    from gamedev_shared.bpy_mesh import clear_scene

    bpy = _require_bpy()
    clear_scene()
    import_glb(glb_path)

    metrics: dict[str, Any]
    color_type: str | None = None
    if mode == "normals":
        metrics = _prepare_normals(world_space=world_space)
        color_type = "VERTEX"
    elif mode == "normals-arrows":
        metrics = _build_normal_arrows(sample=sample, arrow_length=arrow_length)
    elif mode == "orientation":
        engine = "eevee"
        metrics = _apply_orientation_material()
    elif mode == "uv":
        metrics = _apply_uv_checker()
        color_type = "TEXTURE"
    elif mode == "edges":
        metrics = _prepare_edges()
        color_type = "VERTEX"
    else:  # weights (dominant/count/unweighted)
        metrics = _prepare_weights(weights_view)
        color_type = "VERTEX"

    if wireframe:
        _add_wireframe_overlay()

    _setup_render(resolution, engine=engine, film_transparent=transparent_film)
    scene = bpy.context.scene
    if color_type and scene.render.engine == "BLENDER_WORKBENCH":
        scene.display.shading.color_type = color_type

    view_names = [v.strip() for v in views.split(",") if v.strip()]
    panel = _panel_for_mode(mode, weights_view, metrics)

    screenshots: list[dict[str, Any]] = []
    for view_name in view_names:
        preset = CAMERA_PRESETS.get(view_name)
        if preset is None:
            continue
        loc, target = preset
        camera = _add_camera(loc, target, ortho=ortho)
        _auto_frame_camera(camera)
        out_path = output_dir / f"{mode.replace('-', '_')}_{view_name}.png"
        scene.render.filepath = str(out_path)
        bpy.ops.render.render(write_still=True)
        if panel is not None:
            attach_panel(out_path, panel)
        screenshots.append({"view": view_name, "path": str(out_path)})
        _remove_camera(camera)

    metrics.pop("legend", None)
    report: dict[str, Any] = {
        "input": str(glb_path),
        "mode": mode,
        "metrics": metrics,
        "screenshots": screenshots,
        "render_settings": {
            "views": view_names,
            "resolution": resolution,
            "engine": engine,
            "ortho": ortho,
            "film_transparent": transparent_film,
            "wireframe": wireframe,
        },
    }
    if mode == "weights":
        report["render_settings"]["weights_view"] = weights_view
    if mode == "normals-arrows":
        report["render_settings"]["sample"] = sample

    report_path = output_dir / "viz_report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str) + "\n")
    report["report_path"] = str(report_path)
    return report
