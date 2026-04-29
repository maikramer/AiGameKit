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

import numpy as np
import xatlas


def mesh_uv_wrap(mesh_objects):
    import bpy

    if isinstance(mesh_objects, list):
        if len(mesh_objects) > 1:
            bpy.ops.object.select_all(action="DESELECT")
            for obj in mesh_objects:
                obj.select_set(True)
            bpy.context.view_layer.objects.active = mesh_objects[0]
            bpy.ops.object.join()
            mesh_obj = bpy.context.active_object
        else:
            mesh_obj = mesh_objects[0]
    else:
        mesh_obj = mesh_objects

    mesh = mesh_obj.data

    verts = np.array([tuple(v.co) for v in mesh.vertices], dtype=np.float32)
    faces = np.array([list(p.vertices) for p in mesh.polygons], dtype=np.int32)

    if len(faces) > 500_000_000:
        raise ValueError("The mesh has more than 500,000,000 faces, which is not supported.")

    vmapping, indices, uvs = xatlas.parametrize(verts, faces)

    new_verts = verts[vmapping]

    mesh.clear_geometry()
    mesh.from_pydata(new_verts.tolist(), [], indices.tolist())

    uv_layer = mesh.uv_layers.new(name="UVMap") if not mesh.uv_layers else mesh.uv_layers[0]
    # Bulk-set UVs via Blender's foreach_set — O(1) vs O(n) Python loop
    loop_vert_indices = np.zeros(len(mesh.loops), dtype=np.int32)
    mesh.loops.foreach_get("vertex_index", loop_vert_indices)
    flat_uvs = uvs[loop_vert_indices].ravel().astype(np.float32)
    uv_layer.data.foreach_set("uv", flat_uvs)

    mesh.update()

    return mesh_obj
