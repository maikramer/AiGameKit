#!/usr/bin/env python3
"""Generate natural grass / plant / flower GLBs for simple-rpg vegetation.

Requires bpy (Animator3D / Blender 5.2+):

  Animator3D/.venv/bin/python \\
    VibeGame/examples/simple-rpg/scripts/generate_vegetation_glb.py

Outputs overwrite public/assets/meshes/vegetation/*.glb.

Author in Blender Z-up (+Z = height, feet at z≈0). Export uses export_yup=True so
glTF/Three.js receive Y-up (height on +Y) for wind and ground-align.

Docs: README_VEGETATION.md (this folder); engine recipe:
VibeGame/src/plugins/vegetation/context.md.
"""

from __future__ import annotations

import math
import random
import sys
from pathlib import Path

try:
    import bpy  # noqa: I001 - must load before bmesh (pip bpy)
    import bmesh
    from mathutils import Vector
except ImportError as e:
    raise SystemExit("bpy required. Run with Animator3D/.venv/bin/python (or Blender's Python).") from e

OUT_DIR = Path(__file__).resolve().parents[1] / "public" / "assets" / "meshes" / "vegetation"

# Meadow greens (Principled / glTF linear-ish)
GREEN_BASE = (0.14, 0.38, 0.09, 1.0)
GREEN_TIP = (0.52, 0.72, 0.24, 1.0)
GREEN_DARK = (0.06, 0.20, 0.05, 1.0)
GREEN_LEAF = (0.18, 0.46, 0.12, 1.0)


def _clear_scene() -> None:
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    for block in (bpy.data.meshes, bpy.data.materials, bpy.data.curves):
        for item in list(block):
            block.remove(item)


def _make_material(
    name: str,
    color: tuple[float, float, float, float],
    roughness: float = 0.85,
    *,
    use_vertex_color: bool = False,
) -> bpy.types.Material:
    mat = bpy.data.materials.new(name=name)
    mat.use_nodes = True
    mat.use_backface_culling = False
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.inputs["Base Color"].default_value = color
    bsdf.inputs["Roughness"].default_value = roughness
    if "Specular IOR Level" in bsdf.inputs:
        bsdf.inputs["Specular IOR Level"].default_value = 0.12
    elif "Specular" in bsdf.inputs:
        bsdf.inputs["Specular"].default_value = 0.12
    if use_vertex_color:
        # Must link Attribute→Base Color directly or glTF skips COLOR_0.
        attr = nodes.new("ShaderNodeAttribute")
        attr.attribute_name = "Color"
        attr.attribute_type = "GEOMETRY"
        links.new(attr.outputs["Color"], bsdf.inputs["Base Color"])
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])
    return mat


def _lerp_color(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    t: float,
) -> tuple[float, float, float, float]:
    t = max(0.0, min(1.0, t))
    return tuple(a[i] * (1.0 - t) + b[i] * t for i in range(4))  # type: ignore[return-value]


def _add_blade_strip(
    bm: bmesh.types.BMesh,
    *,
    height: float,
    width: float,
    bend: float,
    segments: int,
    lean_x: float,
    lean_y: float,
    tip_narrow: float,
    yaw: float,
    rng: random.Random,
) -> None:
    """Append one tapered/bent strip into ``bm`` (Blender Z-up: height on +Z)."""
    half_w = width * 0.5
    cy, sy = math.cos(yaw), math.sin(yaw)
    verts_l: list = []
    verts_r: list = []
    for i in range(segments + 1):
        t = i / segments
        z = height * t
        # Ease-in arc + soft S-curve (sign of bend chooses lean direction)
        ease = t * t * (3.0 - 2.0 * t)
        bend_amt = bend * ease
        s_curve = 0.22 * bend * math.sin(t * math.pi)
        local_x = lean_x * t + bend_amt + s_curve
        local_y = lean_y * t + bend_amt * 0.35 * math.sin(t * math.pi * 0.5)
        w = half_w * (1.0 - tip_narrow * t * t)
        j = rng.uniform(-width * 0.1, width * 0.1) * t
        # Width axis perpendicular to blade facing (ground XY)
        lx0, ly0 = local_x - w, local_y + j
        lx1, ly1 = local_x + w, local_y - j
        x0, y0 = lx0 * cy - ly0 * sy, lx0 * sy + ly0 * cy
        x1, y1 = lx1 * cy - ly1 * sy, lx1 * sy + ly1 * cy
        verts_l.append(bm.verts.new((x0, y0, z)))
        verts_r.append(bm.verts.new((x1, y1, z)))
    bm.verts.ensure_lookup_table()
    for i in range(segments):
        bm.faces.new((verts_l[i], verts_r[i], verts_r[i + 1], verts_l[i + 1]))


def _blade_mesh(
    *,
    height: float,
    width: float,
    bend: float,
    segments: int,
    lean_x: float,
    lean_y: float,
    tip_narrow: float,
    rng: random.Random,
    cross: bool = True,
    hue_shift: float = 0.0,
) -> bpy.types.Mesh:
    """Tapered bent blade; optional cross-card for volume from all angles."""
    bm = bmesh.new()
    yaw0 = rng.uniform(0, math.tau)
    _add_blade_strip(
        bm,
        height=height,
        width=width,
        bend=bend,
        segments=segments,
        lean_x=lean_x,
        lean_y=lean_y,
        tip_narrow=tip_narrow,
        yaw=yaw0,
        rng=rng,
    )
    if cross:
        _add_blade_strip(
            bm,
            height=height * rng.uniform(0.92, 1.0),
            width=width * 0.85,
            bend=bend * 0.9,
            segments=segments,
            lean_x=lean_x * 0.7,
            lean_y=lean_y * 0.7,
            tip_narrow=tip_narrow,
            yaw=yaw0 + math.pi * 0.5 + rng.uniform(-0.15, 0.15),
            rng=rng,
        )
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("blade")
    bm.to_mesh(mesh)
    bm.free()

    # Vertex colors: darker base → brighter tip (+ per-blade hue)
    base = _lerp_color(GREEN_DARK, GREEN_BASE, 0.35 + hue_shift * 0.2)
    tip = _lerp_color(GREEN_TIP, (0.55, 0.70, 0.28, 1.0), 0.3 + hue_shift * 0.25)
    mesh.color_attributes.new(name="Color", type="BYTE_COLOR", domain="CORNER")
    color_attr = mesh.color_attributes["Color"]
    for poly in mesh.polygons:
        for li in poly.loop_indices:
            vi = mesh.loops[li].vertex_index
            z = mesh.vertices[vi].co.z
            t = max(0.0, min(1.0, z / max(height, 1e-6)))
            color_attr.data[li].color = _lerp_color(base, tip, t**0.8)
    return mesh


def _join_objects(objects: list[bpy.types.Object], name: str) -> bpy.types.Object:
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    joined = bpy.context.view_layer.objects.active
    joined.name = name
    if joined.data:
        joined.data.name = name
    return joined


def _origin_to_feet(obj: bpy.types.Object) -> None:
    """Put origin at ground center (Blender Z-up: xy centroid, z=min → feet)."""
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    coords = [obj.matrix_world @ v.co for v in obj.data.vertices]
    min_z = min(c.z for c in coords)
    cx = sum(c.x for c in coords) / len(coords)
    cy = sum(c.y for c in coords) / len(coords)
    for v in obj.data.vertices:
        v.co.x -= cx
        v.co.y -= cy
        v.co.z -= min_z
    obj.location = (0.0, 0.0, 0.0)
    obj.data.update()


def _shade_smooth(obj: bpy.types.Object) -> None:
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.shade_smooth()


def build_grass_tuft(
    *,
    name: str,
    blade_count: int,
    height_range: tuple[float, float],
    width_range: tuple[float, float],
    radius: float,
    bend_range: tuple[float, float],
    segments: int,
    seed: int,
    tip_narrow: float = 0.72,
    mat_color: tuple[float, float, float, float] = GREEN_BASE,
    cross_blades: bool = True,
) -> bpy.types.Object:
    rng = random.Random(seed)
    mat = _make_material(f"{name}_mat", mat_color, use_vertex_color=True)
    objs: list[bpy.types.Object] = []
    for i in range(blade_count):
        h = rng.uniform(*height_range)
        w = rng.uniform(*width_range)
        # Signed bend so arcs lean both ways
        bend = rng.uniform(*bend_range) * rng.choice((-1.0, 1.0))
        # Cluster denser near center; keep blades mostly upright
        ang = rng.uniform(0, math.tau)
        dist = radius * (rng.random() ** 0.7)
        ox = math.cos(ang) * dist
        oy = math.sin(ang) * dist
        # Mild outward lean from clump center (ground XY)
        lean_x = ox * 0.45 + rng.uniform(-0.03, 0.03) * h
        lean_y = oy * 0.45 + rng.uniform(-0.03, 0.03) * h
        mesh = _blade_mesh(
            height=h,
            width=w,
            bend=bend,
            segments=segments,
            lean_x=lean_x,
            lean_y=lean_y,
            tip_narrow=tip_narrow,
            rng=rng,
            cross=cross_blades,
            hue_shift=rng.uniform(-0.2, 0.45),
        )
        obj = bpy.data.objects.new(f"{name}_blade_{i}", mesh)
        bpy.context.collection.objects.link(obj)
        obj.location = (ox, oy, 0.0)
        obj.data.materials.append(mat)
        objs.append(obj)
    joined = _join_objects(objs, name)
    _origin_to_feet(joined)
    _shade_smooth(joined)
    return joined


def build_broadleaf(
    *,
    name: str,
    leaf_count: int,
    height: float,
    leaf_len: float,
    leaf_width: float,
    seed: int,
    color: tuple[float, float, float, float] = GREEN_LEAF,
) -> bpy.types.Object:
    """Low cluster of broader leaves (plant_flat*)."""
    rng = random.Random(seed)
    mat = _make_material(f"{name}_mat", color, roughness=0.78)
    objs: list[bpy.types.Object] = []
    for i in range(leaf_count):
        bm = bmesh.new()
        # Leaf card: ellipse-ish strip growing +Z, leaning outward on XY
        segs = 5
        ang = (i / leaf_count) * math.tau + rng.uniform(-0.2, 0.2)
        lean = rng.uniform(0.35, 0.75)
        verts_l, verts_r = [], []
        for s in range(segs + 1):
            t = s / segs
            z = height * 0.15 + leaf_len * t * 0.55 + t * height * 0.35
            radial = leaf_len * t * lean
            x = math.cos(ang) * radial
            y = math.sin(ang) * radial
            flare = math.sin(t * math.pi) ** 0.7
            half = leaf_width * 0.5 * (0.15 + 0.85 * flare)
            side = Vector((-math.sin(ang), math.cos(ang), 0.0)) * half
            verts_l.append(bm.verts.new((x - side.x, y - side.y, z)))
            verts_r.append(bm.verts.new((x + side.x, y + side.y, z)))
        bm.verts.ensure_lookup_table()
        for s in range(segs):
            bm.faces.new((verts_l[s], verts_r[s], verts_r[s + 1], verts_l[s + 1]))
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        mesh = bpy.data.meshes.new(f"{name}_leaf_{i}")
        bm.to_mesh(mesh)
        bm.free()
        obj = bpy.data.objects.new(f"{name}_leaf_{i}", mesh)
        bpy.context.collection.objects.link(obj)
        obj.data.materials.append(mat)
        objs.append(obj)
    joined = _join_objects(objs, name)
    _origin_to_feet(joined)
    _shade_smooth(joined)
    return joined


def _stem_center_at(t: float, *, height: float, bend: float, bx: float, by: float) -> Vector:
    """Centerline point on bent stem (t in 0..1, Blender Z-up)."""
    t = max(0.0, min(1.0, t))
    return Vector((bend * (t * t) * bx, bend * (t * t) * by, height * t))


def _stem_mesh(
    *,
    height: float,
    radius_base: float,
    radius_tip: float,
    segments: int,
    sides: int,
    bend: float,
    bx: float,
    by: float,
) -> tuple[bpy.types.Mesh, Vector]:
    """Tapered stem along +Z. Returns (mesh, tip center on centerline)."""
    bm = bmesh.new()
    rings: list[list] = []
    for i in range(segments + 1):
        t = i / segments
        c = _stem_center_at(t, height=height, bend=bend, bx=bx, by=by)
        r = radius_base * (1.0 - t) + radius_tip * t
        ring = []
        for s in range(sides):
            a = (s / sides) * math.tau
            ring.append(bm.verts.new((c.x + math.cos(a) * r, c.y + math.sin(a) * r, c.z)))
        rings.append(ring)
    bm.verts.ensure_lookup_table()
    for i in range(segments):
        for s in range(sides):
            s2 = (s + 1) % sides
            bm.faces.new((rings[i][s], rings[i][s2], rings[i + 1][s2], rings[i + 1][s]))
    tip = _stem_center_at(1.0, height=height, bend=bend, bx=bx, by=by)
    tip_v = bm.verts.new((tip.x, tip.y, tip.z + radius_tip * 0.25))
    for s in range(sides):
        bm.faces.new((rings[-1][s], rings[-1][(s + 1) % sides], tip_v))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("stem")
    bm.to_mesh(mesh)
    bm.free()
    return mesh, tip


def _petal_mesh(
    *,
    head: Vector,
    yaw: float,
    length: float,
    width: float,
    lift: float,
    cup: float,
    segments: int,
) -> bpy.types.Mesh:
    """Cupped petal card from flower head origin (Blender Z-up)."""
    bm = bmesh.new()
    cy, sy = math.cos(yaw), math.sin(yaw)
    verts_l: list = []
    verts_r: list = []
    for i in range(segments + 1):
        t = i / segments
        radial = 0.012 + length * t
        flare = math.sin(t * math.pi) ** 0.65
        half = width * 0.5 * (0.12 + 0.88 * flare)
        z = head.z + 0.004 + lift * math.sin(t * math.pi * 0.85) - 0.01 * (t**2)
        mid_x = head.x + radial * cy
        mid_y = head.y + radial * sy
        side_x, side_y = -sy * half, cy * half
        z_edge = z - cup * flare
        verts_l.append(bm.verts.new((mid_x - side_x, mid_y - side_y, z_edge)))
        verts_r.append(bm.verts.new((mid_x + side_x, mid_y + side_y, z_edge)))
    bm.verts.ensure_lookup_table()
    for i in range(segments):
        bm.faces.new((verts_l[i], verts_r[i], verts_r[i + 1], verts_l[i + 1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("petal")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def _leaf_on_stem(
    *,
    attach: Vector,
    yaw: float,
    length: float,
    width: float,
) -> bpy.types.Mesh:
    """Small stem leaf from attach point on stem centerline."""
    bm = bmesh.new()
    cy, sy = math.cos(yaw), math.sin(yaw)
    segs = 4
    verts_l, verts_r = [], []
    for i in range(segs + 1):
        t = i / segs
        radial = length * t
        flare = math.sin(t * math.pi) ** 0.7
        half = width * 0.5 * (0.15 + 0.85 * flare)
        hz = attach.z + length * 0.35 * t
        mx = attach.x + radial * cy
        my = attach.y + radial * sy
        sx, sy_ = -sy * half, cy * half
        verts_l.append(bm.verts.new((mx - sx, my - sy_, hz)))
        verts_r.append(bm.verts.new((mx + sx, my + sy_, hz)))
    bm.verts.ensure_lookup_table()
    for i in range(segs):
        bm.faces.new((verts_l[i], verts_r[i], verts_r[i + 1], verts_l[i + 1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    mesh = bpy.data.meshes.new("leaf")
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def build_flower(
    *,
    name: str,
    petal_color: tuple[float, float, float, float],
    stem_height: float,
    seed: int,
) -> bpy.types.Object:
    rng = random.Random(seed)
    stem_mat = _make_material(f"{name}_stem", GREEN_DARK, roughness=0.9)
    leaf_mat = _make_material(f"{name}_leaf", GREEN_LEAF, roughness=0.82)
    petal_mat = _make_material(f"{name}_petal", petal_color, roughness=0.48)
    center_mat = _make_material(f"{name}_center", (0.92, 0.78, 0.18, 1.0), roughness=0.55)
    objs: list[bpy.types.Object] = []

    bend = rng.uniform(0.012, 0.028)
    bend_dir = rng.uniform(0, math.tau)
    bx, by = math.cos(bend_dir), math.sin(bend_dir)
    stem_mesh, tip = _stem_mesh(
        height=stem_height,
        radius_base=0.007,
        radius_tip=0.0035,
        segments=6,
        sides=6,
        bend=bend,
        bx=bx,
        by=by,
    )
    stem = bpy.data.objects.new(f"{name}_stem", stem_mesh)
    bpy.context.collection.objects.link(stem)
    stem.data.materials.append(stem_mat)
    objs.append(stem)

    # 1-2 small leaves on bent centerline
    for k in range(rng.randint(1, 2)):
        t_leaf = rng.uniform(0.28, 0.55)
        attach = _stem_center_at(t_leaf, height=stem_height, bend=bend, bx=bx, by=by)
        leaf = bpy.data.objects.new(
            f"{name}_leaf_{k}",
            _leaf_on_stem(
                attach=attach,
                yaw=rng.uniform(0, math.tau),
                length=rng.uniform(0.04, 0.065),
                width=rng.uniform(0.014, 0.022),
            ),
        )
        bpy.context.collection.objects.link(leaf)
        leaf.data.materials.append(leaf_mat)
        objs.append(leaf)

    petal_n = rng.choice((5, 6, 7))
    petal_len = rng.uniform(0.055, 0.075)
    petal_w = rng.uniform(0.028, 0.038)
    for i in range(petal_n):
        yaw = (i / petal_n) * math.tau + rng.uniform(-0.08, 0.08)
        pmesh = _petal_mesh(
            head=tip,
            yaw=yaw,
            length=petal_len * rng.uniform(0.9, 1.08),
            width=petal_w * rng.uniform(0.9, 1.1),
            lift=rng.uniform(0.018, 0.03),
            cup=rng.uniform(0.004, 0.01),
            segments=5,
        )
        pobj = bpy.data.objects.new(f"{name}_petal_{i}", pmesh)
        bpy.context.collection.objects.link(pobj)
        pobj.data.materials.append(petal_mat)
        objs.append(pobj)

    # Center sits on stem tip
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=10,
        radius=0.014,
        depth=0.006,
        location=(tip.x, tip.y, tip.z + 0.003),
    )
    disk = bpy.context.active_object
    disk.name = f"{name}_disk"
    disk.data.materials.append(center_mat)
    objs.append(disk)
    bpy.ops.mesh.primitive_uv_sphere_add(
        segments=10, ring_count=6, radius=0.012, location=(tip.x, tip.y, tip.z + 0.009)
    )
    dome = bpy.context.active_object
    dome.name = f"{name}_center"
    dome.data.materials.append(center_mat)
    objs.append(dome)

    joined = _join_objects(objs, name)
    _origin_to_feet(joined)
    _shade_smooth(joined)
    return joined


def _export_glb(obj: bpy.types.Object, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    kwargs = {
        "filepath": str(path),
        "check_existing": False,
        "export_format": "GLB",
        "use_selection": True,
        "export_apply": True,
        "export_texcoords": True,
        "export_normals": True,
        "export_materials": "EXPORT",
        "export_yup": True,
        "export_vertex_color": "MATERIAL",
        "export_all_vertex_colors": True,
    }
    bpy.ops.export_scene.gltf(**kwargs)
    print(f"Wrote {path} ({path.stat().st_size} bytes)")


def generate_all(out_dir: Path = OUT_DIR) -> None:
    specs: list[tuple[str, object]] = [
        (
            "grass.glb",
            lambda: build_grass_tuft(
                name="grass",
                blade_count=22,
                height_range=(0.16, 0.30),
                width_range=(0.010, 0.018),
                radius=0.07,
                bend_range=(0.025, 0.07),
                segments=8,
                seed=41,
                tip_narrow=0.82,
            ),
        ),
        (
            "grass_large.glb",
            lambda: build_grass_tuft(
                name="grass_large",
                blade_count=36,
                height_range=(0.26, 0.44),
                width_range=(0.012, 0.022),
                radius=0.10,
                bend_range=(0.03, 0.09),
                segments=9,
                seed=42,
                tip_narrow=0.85,
            ),
        ),
        (
            "grass_leafs.glb",
            lambda: build_grass_tuft(
                name="grass_leafs",
                blade_count=12,
                height_range=(0.13, 0.24),
                width_range=(0.028, 0.046),
                radius=0.08,
                bend_range=(0.015, 0.04),
                segments=7,
                seed=43,
                tip_narrow=0.62,
                mat_color=GREEN_LEAF,
                cross_blades=False,
            ),
        ),
        (
            "grass_leafsLarge.glb",
            lambda: build_grass_tuft(
                name="grass_leafsLarge",
                blade_count=20,
                height_range=(0.20, 0.38),
                width_range=(0.032, 0.052),
                radius=0.11,
                bend_range=(0.02, 0.055),
                segments=8,
                seed=44,
                tip_narrow=0.58,
                mat_color=GREEN_LEAF,
                cross_blades=False,
            ),
        ),
        (
            "plant_flatShort.glb",
            lambda: build_broadleaf(
                name="plant_flatShort",
                leaf_count=7,
                height=0.10,
                leaf_len=0.12,
                leaf_width=0.05,
                seed=45,
            ),
        ),
        (
            "plant_flatTall.glb",
            lambda: build_broadleaf(
                name="plant_flatTall",
                leaf_count=8,
                height=0.18,
                leaf_len=0.16,
                leaf_width=0.055,
                seed=46,
            ),
        ),
        (
            "flower_yellowA.glb",
            lambda: build_flower(
                name="flower_yellowA",
                petal_color=(0.95, 0.82, 0.10, 1.0),
                stem_height=0.22,
                seed=47,
            ),
        ),
        (
            "flower_purpleA.glb",
            lambda: build_flower(
                name="flower_purpleA",
                petal_color=(0.62, 0.28, 0.82, 1.0),
                stem_height=0.24,
                seed=48,
            ),
        ),
        (
            "flower_redA.glb",
            lambda: build_flower(
                name="flower_redA",
                petal_color=(0.88, 0.16, 0.18, 1.0),
                stem_height=0.20,
                seed=49,
            ),
        ),
    ]

    for filename, builder in specs:
        _clear_scene()
        obj = builder()
        _export_glb(obj, out_dir / filename)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # When launched via `blender --python`, args after `--` are forwarded.
    if "--" in argv:
        argv = argv[argv.index("--") + 1 :]
    out = OUT_DIR
    if argv:
        out = Path(argv[0]).resolve()
    generate_all(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
