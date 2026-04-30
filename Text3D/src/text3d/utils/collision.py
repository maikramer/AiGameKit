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
    """Generate a simplified collision mesh from any GLB/OBJ/PLY.

    Pipeline (bpy native):
    1. Load mesh via bpy
    2. Optionally compute convex hull
    3. Aggressive quadric decimation to ``max_faces``
    4. Export as GLB

    Args:
        input_path: Source mesh file.
        output_path: Destination GLB.
        max_faces: Target face count (minimum 4).
        convex_hull: Compute convex hull before simplification.

    Returns:
        Path to the written collision GLB.

    Raises:
        ValueError: Mesh has fewer than 4 faces.
    """
    import bpy
    from gamedev_shared.bpy_mesh import clear_scene, save_glb

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(Path(input_path)))

    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError("No mesh objects found in input file")
    obj = mesh_objs[0]

    n = len(obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); collision não aplicável.")

    if convex_hull:
        # Select the mesh object and add convex hull modifier
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.mode_set(mode="OBJECT")
        bpy.ops.object.select_all(action="DESELECT")
        obj.select_set(True)
        bpy.ops.object.mode_set(mode="EDIT")
        bpy.ops.mesh.select_all(action="SELECT")
        bpy.ops.mesh.convex_hull()
        bpy.ops.object.mode_set(mode="OBJECT")

    # Decimate to target face count
    target = max(4, max_faces)
    current = len(obj.data.polygons)
    if current > target:
        ratio = target / current
        mod = obj.modifiers.new("CollisionDecimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.modifier_apply(modifier=mod.name)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    save_glb([obj], output_path)
    clear_scene()
    return output_path
