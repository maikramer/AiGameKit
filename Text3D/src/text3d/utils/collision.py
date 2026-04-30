"""Geração de mesh de colisão (convex hull simplificado) para física em engines de jogo."""

from __future__ import annotations

from pathlib import Path


def generate_collision_mesh(
    input_path: Path,
    output_path: Path,
    *,
    max_faces: int = 300,
    convex_hull: bool = True,
) -> Path:
    """Generate a simplified convex-hull collision mesh from any GLB/OBJ/PLY.

    Produces a **minimal** GLB — only geometry (vertices + faces), no
    materials, textures, UVs, or normals.  Suitable for physics engines.

    Pipeline (bpy native):
    1. Load mesh via bpy
    2. Compute convex hull
    3. Decimate to ``max_faces``
    4. Strip all materials/UVs/normals
    5. Export minimal GLB

    Args:
        input_path: Source mesh file.
        output_path: Destination GLB (geometry only).
        max_faces: Target face count (minimum 4).
        convex_hull: Compute convex hull before simplification.

    Returns:
        Path to the written collision GLB.

    Raises:
        ValueError: Mesh has fewer than 4 faces.
    """
    import bpy

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(Path(input_path)))

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError("No mesh objects found in input file")
    obj = mesh_objs[0]

    n = len(obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); collision não aplicável.")

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)

    if convex_hull:
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.convex_hull()
        bpy.ops.object.mode_set(mode="OBJECT")

    target = max(4, max_faces)
    if current > target:
        ratio = target / current
        mod = obj.modifiers.new("CollisionDecimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)

    mesh = obj.data
    mesh.materials.clear()
    for uv in list(mesh.uv_layers):
        mesh.uv_layers.remove(uv)
    for attr in list(getattr(mesh, "color_attributes", [])):
        mesh.color_attributes.remove(attr)
    # Remove custom normals so export doesn't include them
    mesh.use_auto_smooth = False
    bpy.ops.mesh.customdata_custom_splitnormals_clear()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    bpy.ops.export_scene.gltf(
        filepath=str(output_path),
        use_selection=True,
        export_apply=True,
        export_normals=False,
        export_uvs=False,
        export_materials="NONE",
        export_animations=False,
        export_skins=False,
        export_morph=False,
    )

    clear_scene()
    return output_path
