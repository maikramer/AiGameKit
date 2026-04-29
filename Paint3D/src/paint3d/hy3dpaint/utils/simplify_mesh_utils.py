# Hunyuan 3D is licensed under the TENCENT HUNYUAN NON-COMMERCIAL LICENSE AGREEMENT
# except for the third-party components listed below.
# Hunyuan 3D does not impose any additional limitations beyond what is outlined
# in the repsective licenses of these third-party components.
# Users must comply with all terms and conditions of original licenses of these third-party
# components and must ensure that the usage of the third party components adheres to
# all relevant laws and regulations.

# For avoidance of doubts, Hunyuan 3D means the large language models and
# their software and algorithms, including trained model weights, parameters (including
# optimizer states), machine-learning model code, inference-enabling code, training-enabling code,
# fine-tuning enabling code and other elements of the foregoing made publicly available
# by Tencent in accordance with TENCENT HUNYUAN COMMUNITY LICENSE AGREEMENT.

from __future__ import annotations

from gamedev_shared.bpy_mesh import load_glb, save_glb


def remesh_mesh(mesh_path: str, remesh_path: str) -> None:
    mesh_simplify_trimesh(mesh_path, remesh_path)


def mesh_simplify_trimesh(inputpath: str, outputpath: str, target_count: int = 40000) -> None:
    """Simplify mesh to *target_count* faces using bpy Decimate modifier."""
    import bpy

    mesh_objs = load_glb(inputpath)
    if not mesh_objs:
        raise RuntimeError(f"No mesh objects found in {inputpath}")

    # Pick the largest mesh by face count
    mesh_obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    current_faces = len(mesh_obj.data.polygons)

    if current_faces > target_count:
        ratio = target_count / current_faces
        mod = mesh_obj.modifiers.new("Decimate", "DECIMATE")
        mod.decimate_type = "COLLAPSE"
        mod.ratio = ratio
        bpy.context.view_layer.objects.active = mesh_obj
        bpy.ops.object.modifier_apply(modifier=mod.name)

    save_glb(mesh_obj, outputpath)
