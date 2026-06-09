"""Seamless rock texturing + GLB export via Blender (bpy).

The trimesh path textures rocks with 2D noise sampled through an atlas UV, so
texture content jumps across UV-island boundaries — faint but visible seams.
This module instead builds a procedural material driven by **object-space**
coordinates (and geometry pointiness for cavities), so the signal is coherent
in 3D, then *bakes* it to UV images with a bake margin that floods island
gutters. The result has no texture seams, and the GLB is exported with smooth
normals + MikkTSpace tangents so the normal map renders cleanly too.

bpy ships transitively via ``gamedev-shared``; callers fall back to the
trimesh exporter when it is unavailable.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    import numpy as np

    from rocks3d.defaults import RockPreset


def bpy_available() -> bool:
    """Whether bpy can be imported in the current environment."""
    try:
        import bpy  # noqa: F401
    except ImportError:
        return False
    return True


def _hex_rgb(hex_str: str) -> tuple[float, float, float]:
    h = hex_str.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def _build_rock_material(preset: RockPreset, seed: int):
    """Create a procedural, object-space rock material (seamless in 3D)."""
    import bpy

    low = _hex_rgb(preset.color_range[0])
    high = _hex_rgb(preset.color_range[1])
    jitter = (seed % 997) * 0.013  # decorrelate noise between rocks

    mat = bpy.data.materials.new("rock_proc")
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()

    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    texco = nodes.new("ShaderNodeTexCoord")
    mapping = nodes.new("ShaderNodeMapping")
    mapping.inputs["Location"].default_value = (jitter, jitter * 2, jitter * 3)
    links.new(texco.outputs["Object"], mapping.inputs["Vector"])

    # --- base colour: low/mid-frequency object-space noise between two colours
    noise = nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 2.5
    noise.inputs["Detail"].default_value = 6.0
    links.new(mapping.outputs["Vector"], noise.inputs["Vector"])
    ramp = nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*low, 1.0)
    ramp.color_ramp.elements[1].color = (*high, 1.0)
    links.new(noise.outputs["Fac"], ramp.inputs["Fac"])

    # --- cavity darkening from geometry pointiness (3D-coherent, seamless)
    geo = nodes.new("ShaderNodeNewGeometry")
    cav = nodes.new("ShaderNodeValToRGB")
    cav.color_ramp.elements[0].position = 0.35
    cav.color_ramp.elements[0].color = (0.45, 0.45, 0.45, 1.0)
    cav.color_ramp.elements[1].position = 0.6
    cav.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)
    links.new(geo.outputs["Pointiness"], cav.inputs["Fac"])
    mix = nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.blend_type = "MULTIPLY"
    mix.inputs["Factor"].default_value = 1.0
    links.new(ramp.outputs["Color"], mix.inputs[6])  # A (color)
    links.new(cav.outputs["Color"], mix.inputs[7])  # B (color)
    links.new(mix.outputs[2], bsdf.inputs["Base Color"])

    # --- fine surface bump (object-space high-freq noise) -> normal
    bump_noise = nodes.new("ShaderNodeTexNoise")
    bump_noise.inputs["Scale"].default_value = 28.0
    bump_noise.inputs["Detail"].default_value = 8.0
    links.new(mapping.outputs["Vector"], bump_noise.inputs["Vector"])
    bump = nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.25
    links.new(bump_noise.outputs["Fac"], bump.inputs["Height"])
    links.new(bump.outputs["Normal"], bsdf.inputs["Normal"])

    # --- roughness: high, with a touch of variation
    rough_noise = nodes.new("ShaderNodeTexNoise")
    rough_noise.inputs["Scale"].default_value = 12.0
    links.new(mapping.outputs["Vector"], rough_noise.inputs["Vector"])
    rramp = nodes.new("ShaderNodeValToRGB")
    rramp.color_ramp.elements[0].color = (0.78, 0.78, 0.78, 1.0)
    rramp.color_ramp.elements[1].color = (0.95, 0.95, 0.95, 1.0)
    links.new(rough_noise.outputs["Fac"], rramp.inputs["Fac"])
    links.new(rramp.outputs["Color"], bsdf.inputs["Roughness"])

    return mat


def _bake_pass(obj, mat, image, bake_type: str, *, samples: int, margin: int, color_space: str):
    """Bake one pass of *mat* into *image* for *obj*."""
    import bpy

    # Active image texture node = bake target.
    nt = mat.node_tree
    tex_node = nt.nodes.new("ShaderNodeTexImage")
    tex_node.image = image
    image.colorspace_settings.name = color_space
    nt.nodes.active = tex_node

    scene = bpy.context.scene
    scene.cycles.samples = samples
    scene.render.bake.margin = margin
    scene.render.bake.use_clear = True

    kwargs = {}
    if bake_type == "DIFFUSE":
        scene.render.bake.use_pass_direct = False
        scene.render.bake.use_pass_indirect = False
        kwargs["pass_filter"] = {"COLOR"}
    bpy.ops.object.bake(type=bake_type, **kwargs)

    nt.nodes.remove(tex_node)


def bake_and_export(
    vertices: np.ndarray,
    faces: np.ndarray,
    vertex_normals: np.ndarray,
    preset: RockPreset,
    output_path: Path,
    *,
    seed: int = 0,
    resolution: int = 1024,
    margin: int = 16,
    bake_ao: bool = True,
) -> Path:
    """Texture a rock with a seamless object-space bake and export a GLB.

    Args:
        vertices: ``(N, 3)`` vertex positions.
        faces: ``(M, 3)`` triangle indices.
        vertex_normals: ``(N, 3)`` smooth normals (seam-consistent).
        preset: Rock preset (provides the colour range).
        output_path: Destination ``.glb`` path.
        seed: Decorrelates the procedural noise between rocks.
        resolution: Baked texture resolution.
        margin: Bake margin in pixels (floods UV-island gutters → no seams).
        bake_ao: Whether to bake an ambient-occlusion map.

    Returns:
        *output_path*.
    """
    import bpy
    import numpy as np

    from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays

    clear_scene()
    bpy.context.scene.render.engine = "CYCLES"
    bpy.context.scene.cycles.device = "CPU"

    # Sit the rock on y=0 like the trimesh exporter does.
    verts = np.asarray(vertices, dtype=np.float64).copy()
    verts[:, 1] -= verts[:, 1].min()

    obj = create_mesh_from_arrays(verts, np.asarray(faces), name="rock")
    mesh = obj.data

    # Smooth shading with our seam-consistent normals.
    for poly in mesh.polygons:
        poly.use_smooth = True
    with contextlib.suppress(RuntimeError, AttributeError):
        mesh.normals_split_custom_set_from_vertices([tuple(map(float, n)) for n in np.asarray(vertex_normals)])

    # UV unwrap with an island margin so the bake margin has room.
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.uv.smart_project(angle_limit=1.15, island_margin=0.02)
    bpy.ops.object.mode_set(mode="OBJECT")

    mat = _build_rock_material(preset, seed)
    mesh.materials.append(mat)

    def _img(name, is_data):
        img = bpy.data.images.new(name, resolution, resolution, alpha=False, float_buffer=False, is_data=is_data)
        return img

    albedo = _img("rock_albedo", False)
    normal = _img("rock_normal", True)
    rough = _img("rock_rough", True)

    _bake_pass(obj, mat, albedo, "DIFFUSE", samples=1, margin=margin, color_space="sRGB")
    _bake_pass(obj, mat, normal, "NORMAL", samples=1, margin=margin, color_space="Non-Color")
    _bake_pass(obj, mat, rough, "ROUGHNESS", samples=1, margin=margin, color_space="Non-Color")
    if bake_ao:
        ao = _img("rock_ao", True)
        _bake_pass(obj, mat, ao, "AO", samples=16, margin=margin, color_space="Non-Color")
        # Fold occlusion into the albedo image so it survives glTF export as a
        # single baseColorTexture (a node-mix would not export as a texture).
        _multiply_into(albedo, ao)
        bpy.data.images.remove(ao)

    # Replace the procedural material with a clean baked Principled material.
    baked = _baked_material(albedo, normal, rough)
    mesh.materials.clear()
    mesh.materials.append(baked)

    # Tangents for the normal map; export normals + tangents.
    with contextlib.suppress(RuntimeError):
        mesh.calc_tangents()

    output_path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj
    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        export_format="GLB",
        use_selection=True,
        export_normals=True,
        export_tangents=True,
        export_texcoords=True,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )
    return output_path


def _multiply_into(target, factor) -> None:
    """In-place ``target.rgb *= factor.rgb`` on two same-size bpy images."""
    import numpy as np

    n = len(target.pixels)
    t = np.empty(n, dtype=np.float32)
    f = np.empty(n, dtype=np.float32)
    target.pixels.foreach_get(t)
    factor.pixels.foreach_get(f)
    t = t.reshape(-1, 4)
    f = f.reshape(-1, 4)
    t[:, :3] *= f[:, :3]  # keep alpha
    target.pixels.foreach_set(t.reshape(-1))
    target.update()


def _baked_material(albedo, normal, rough):
    """Clean Principled material wired to the baked image textures."""
    import bpy

    mat = bpy.data.materials.new("rock")
    mat.use_nodes = True
    nt = mat.node_tree
    nodes, links = nt.nodes, nt.links
    nodes.clear()
    out = nodes.new("ShaderNodeOutputMaterial")
    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(bsdf.outputs["BSDF"], out.inputs["Surface"])

    tex_a = nodes.new("ShaderNodeTexImage")
    tex_a.image = albedo
    links.new(tex_a.outputs["Color"], bsdf.inputs["Base Color"])

    tex_r = nodes.new("ShaderNodeTexImage")
    tex_r.image = rough
    links.new(tex_r.outputs["Color"], bsdf.inputs["Roughness"])
    bsdf.inputs["Metallic"].default_value = 0.0

    tex_n = nodes.new("ShaderNodeTexImage")
    tex_n.image = normal
    nmap = nodes.new("ShaderNodeNormalMap")
    links.new(tex_n.outputs["Color"], nmap.inputs["Color"])
    links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])

    return mat
