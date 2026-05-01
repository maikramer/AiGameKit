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

import contextlib
import os
from io import StringIO
from typing import Any

import bpy
import cv2
import numpy as np


def _safe_extract_attribute(obj: Any, attr_path: str, default: Any = None) -> Any:
    """Extract nested attribute safely from object. Handles bpy Object (.data.vertices)."""
    try:
        # bpy Object: route .vertices → obj.data.vertices, .faces → obj.data.polygons
        if hasattr(obj, "data") and hasattr(obj, "type"):
            if attr_path == "vertices":
                verts = obj.data.vertices
                n = len(verts)
                co = np.zeros(n * 3, dtype=np.float32)
                verts.foreach_get("co", co)
                return co.reshape(n, 3)
            if attr_path == "faces":
                polys = obj.data.polygons
                triangles = np.zeros(len(polys) * 3, dtype=np.int32)
                polys.foreach_get("vertices", triangles)
                return triangles.reshape(-1, 3)
            if attr_path == "visual.uv":
                uv_layer = obj.data.uv_layers.active
                if uv_layer is not None:
                    n_loops = len(uv_layer.data)
                    uv_flat = np.zeros(n_loops * 2, dtype=np.float32)
                    uv_layer.data.foreach_get("uv", uv_flat)
                    per_loop_uv = uv_flat.reshape(n_loops, 2)
                    loop_verts = np.zeros(n_loops, dtype=np.int32)
                    obj.data.loops.foreach_get("vertex_index", loop_verts)
                    n_verts = len(obj.data.vertices)
                    vtx_uvs = np.zeros((n_verts, 2), dtype=np.float32)
                    np.add.at(vtx_uvs, (loop_verts, slice(None)), per_loop_uv)
                    counts = np.bincount(loop_verts, minlength=n_verts).clip(min=1)
                    vtx_uvs /= counts.reshape(-1, 1)
                    return vtx_uvs
                return default
        for attr in attr_path.split("."):
            obj = getattr(obj, attr)
        return obj
    except AttributeError:
        return default


def _convert_to_numpy(data: Any, dtype: np.dtype) -> np.ndarray | None:
    """Convert data to numpy array with specified dtype, handling None values."""
    if data is None:
        return None
    return np.asarray(data, dtype=dtype)


def load_mesh(mesh):
    """Load mesh data including vertices, faces, UV coordinates and texture."""
    # Extract vertex positions and face indices
    vtx_pos = _safe_extract_attribute(mesh, "vertices")
    pos_idx = _safe_extract_attribute(mesh, "faces")

    # Extract UV coordinates (reusing face indices for UV indices)
    vtx_uv = _safe_extract_attribute(mesh, "visual.uv")
    uv_idx = pos_idx  # Reuse face indices for UV mapping

    # Convert to numpy arrays with appropriate dtypes
    vtx_pos = _convert_to_numpy(vtx_pos, np.float32)
    pos_idx = _convert_to_numpy(pos_idx, np.int32)
    vtx_uv = _convert_to_numpy(vtx_uv, np.float32)
    uv_idx = _convert_to_numpy(uv_idx, np.int32)

    texture_data = None
    return vtx_pos, pos_idx, vtx_uv, uv_idx, texture_data


def _get_base_path_and_name(mesh_path: str) -> tuple[str, str]:
    """Get base path without extension and mesh name."""
    base_path = os.path.splitext(mesh_path)[0]
    name = os.path.basename(base_path)
    return base_path, name


def _save_texture_map(
    texture: np.ndarray,
    base_path: str,
    suffix: str = "",
    image_format: str = ".jpg",
    color_convert: int | None = None,
) -> str:
    """Save texture map with optional color conversion."""
    path = f"{base_path}{suffix}{image_format}"
    processed_texture = (texture * 255).astype(np.uint8)

    if color_convert is not None:
        processed_texture = cv2.cvtColor(processed_texture, color_convert)
        cv2.imwrite(path, processed_texture)
    else:
        cv2.imwrite(path, processed_texture[..., ::-1])  # RGB to BGR

    return os.path.basename(path)


def _write_mtl_properties(f, properties: dict[str, Any]):
    """Write material properties to MTL file."""
    for key, value in properties.items():
        if isinstance(value, (list, tuple)):
            f.write(f"{key} {' '.join(map(str, value))}\n")
        else:
            f.write(f"{key} {value}\n")


def _create_obj_content(
    vtx_pos: np.ndarray, vtx_uv: np.ndarray, pos_idx: np.ndarray, uv_idx: np.ndarray, name: str
) -> str:
    """Create OBJ file content."""
    buffer = StringIO()

    # Write header and vertices
    buffer.write(f"mtllib {name}.mtl\no {name}\n")
    np.savetxt(buffer, vtx_pos, fmt="v %.6f %.6f %.6f")
    np.savetxt(buffer, vtx_uv, fmt="vt %.6f %.6f")
    buffer.write("s 0\nusemtl Material\n")

    # Write faces
    pos_idx_plus1 = pos_idx + 1
    uv_idx_plus1 = uv_idx + 1
    face_format = np.frompyfunc(lambda *x: f"{int(x[0])}/{int(x[1])}", 2, 1)
    faces = face_format(pos_idx_plus1, uv_idx_plus1)
    face_strings = [f"f {' '.join(face)}" for face in faces]
    buffer.write("\n".join(face_strings) + "\n")

    return buffer.getvalue()


def save_obj_mesh(mesh_path, vtx_pos, pos_idx, vtx_uv, uv_idx, texture, metallic=None, roughness=None, normal=None):
    """Save mesh as OBJ file with textures and material."""
    # Convert inputs to numpy arrays
    vtx_pos = _convert_to_numpy(vtx_pos, np.float32)
    vtx_uv = _convert_to_numpy(vtx_uv, np.float32)
    pos_idx = _convert_to_numpy(pos_idx, np.int32)
    uv_idx = _convert_to_numpy(uv_idx, np.int32)

    base_path, name = _get_base_path_and_name(mesh_path)

    # Create and save OBJ content
    obj_content = _create_obj_content(vtx_pos, vtx_uv, pos_idx, uv_idx, name)
    with open(mesh_path, "w") as obj_file:
        obj_file.write(obj_content)

    # Save texture maps
    texture_maps = {}
    texture_maps["diffuse"] = _save_texture_map(texture, base_path)

    if metallic is not None:
        texture_maps["metallic"] = _save_texture_map(metallic, base_path, "_metallic", color_convert=cv2.COLOR_RGB2GRAY)
    if roughness is not None:
        texture_maps["roughness"] = _save_texture_map(
            roughness, base_path, "_roughness", color_convert=cv2.COLOR_RGB2GRAY
        )
    if normal is not None:
        texture_maps["normal"] = _save_texture_map(normal, base_path, "_normal")

    # Create MTL file
    _create_mtl_file(base_path, texture_maps, metallic is not None)


def _create_mtl_file(base_path: str, texture_maps: dict[str, str], is_pbr: bool):
    """Create MTL material file."""
    mtl_path = f"{base_path}.mtl"

    with open(mtl_path, "w") as f:
        f.write("newmtl Material\n")

        if is_pbr:
            # PBR material properties
            properties = {
                "Kd": [0.800, 0.800, 0.800],
                "Ke": [0.000, 0.000, 0.000],
                "Ni": 1.500,
                "d": 1.0,
                "illum": 2,
                "map_Kd": texture_maps["diffuse"],
            }
            _write_mtl_properties(f, properties)

            # Additional PBR maps
            map_configs = [("metallic", "map_Pm"), ("roughness", "map_Pr"), ("normal", "map_Bump -bm 1.0")]

            for texture_key, mtl_key in map_configs:
                if texture_key in texture_maps:
                    f.write(f"{mtl_key} {texture_maps[texture_key]}\n")
        else:
            # Standard material properties
            properties = {
                "Ns": 250.000000,
                "Ka": [0.200, 0.200, 0.200],
                "Kd": [0.800, 0.800, 0.800],
                "Ks": [0.500, 0.500, 0.500],
                "Ke": [0.000, 0.000, 0.000],
                "Ni": 1.500,
                "d": 1.0,
                "illum": 3,
                "map_Kd": texture_maps["diffuse"],
            }
            _write_mtl_properties(f, properties)


def _dilate_texture_at_seams(texture_np, vtx_uv, uv_idx, dilation_pixels=4):
    """Dilate texture at UV island boundaries to prevent visible seam cracks.

    Rasterizes UV triangles into a mask, then dilates painted regions outward
    so GPU sampling near seam edges picks up correct color instead of background.
    """
    h, w = texture_np.shape[:2]
    uv_triangles = vtx_uv[uv_idx]
    coverage = np.zeros((h, w), dtype=np.uint8)
    pts = (uv_triangles * [w, h]).astype(np.float32)
    pts[:, :, 1] = np.clip(h - pts[:, :, 1], 0, h - 1)
    for tri in pts:
        cv2.fillConvexPoly(coverage, tri.astype(np.int32), 255)
    kernel = np.ones((dilation_pixels * 2 + 1, dilation_pixels * 2 + 1), np.uint8)
    dilated = cv2.dilate(coverage, kernel, iterations=1)
    border_mask = (dilated > 0) & (coverage == 0)
    if not border_mask.any():
        return texture_np
    if texture_np.ndim == 3:
        filled = np.zeros_like(texture_np)
        for c in range(texture_np.shape[2]):
            filled[:, :, c] = cv2.dilate(texture_np[:, :, c], kernel, iterations=1)
        texture_np[border_mask] = filled[border_mask]
    else:
        filled = cv2.dilate(texture_np, kernel, iterations=1)
        texture_np[border_mask] = filled[border_mask]
    return texture_np


def _weld_seam_vertices(vtx_pos, pos_idx, tolerance=1e-4):
    """Snap UV-seam duplicate vertices to shared positions.

    Groups vertices within *tolerance* and snaps all to centroid.
    Preserves face structure and UV coordinates — only moves positions.
    """
    from collections import defaultdict

    groups = defaultdict(list)
    for i, v in enumerate(vtx_pos):
        key = tuple(np.round(v / tolerance) * tolerance)
        groups[key].append(i)

    moved = 0
    for key, indices in groups.items():
        if len(indices) < 2:
            continue
        centroid = vtx_pos[indices].mean(axis=0)
        for idx in indices:
            vtx_pos[idx] = centroid
        moved += len(indices)
    return moved


def _save_glb_mesh_bpy(
    mesh_path, vtx_pos, pos_idx, vtx_uv, uv_idx, texture, metallic=None, roughness=None, normal=None
):
    """Export mesh as GLB via bpy — create mesh from arrays, apply texture, export.

    No scene switching, no EDIT mode, no vertex merge.  Keeps the pipeline
    context intact and avoids the hangs caused by ``bpy.ops.object.mode_set``
    with large meshes in headless bpy.
    """
    import logging
    import tempfile
    import time as _time

    _log = logging.getLogger("paint3d.save_glb")
    _t0 = _time.time()

    # 1. Save texture to temp PNG
    texture_uint8 = (texture * 255).astype(np.uint8)
    texture_uint8 = _dilate_texture_at_seams(texture_uint8, vtx_uv, uv_idx, dilation_pixels=4)
    tmp_fd, tmp_tex_path = tempfile.mkstemp(suffix=".png")
    os.close(tmp_fd)
    try:
        cv2.imwrite(tmp_tex_path, texture_uint8[..., ::-1])
        _log.info("texture: %dx%d (%.1fs)", texture_uint8.shape[1], texture_uint8.shape[0], _time.time() - _t0)

        # 2. Clear current scene objects (no scene switching, no factory settings)
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

        # 3. Create mesh
        mesh = bpy.data.meshes.new("Mesh")
        mesh.vertices.add(len(vtx_pos))
        mesh.vertices.foreach_set("co", vtx_pos.ravel())

        num_faces = len(pos_idx)
        mesh.loops.add(pos_idx.size)
        mesh.loops.foreach_set("vertex_index", pos_idx.ravel())
        mesh.polygons.add(num_faces)
        loop_start = np.arange(num_faces, dtype=np.int32) * pos_idx.shape[1]
        mesh.polygons.foreach_set("loop_start", loop_start)
        mesh.polygons.foreach_set("loop_total", np.full(num_faces, pos_idx.shape[1], dtype=np.int32))

        # 4. UV layer
        uv_layer = mesh.uv_layers.new(name="UVMap")
        flat_uvs = vtx_uv[uv_idx.ravel()].ravel().astype(np.float32)
        uv_layer.data.foreach_set("uv", flat_uvs)

        # 5. Material + texture
        mat = bpy.data.materials.new(name="Material")
        mat.use_nodes = True
        bsdf = mat.node_tree.nodes.get("Principled BSDF")
        tex_node = mat.node_tree.nodes.new("ShaderNodeTexImage")
        tex_node.image = bpy.data.images.load(tmp_tex_path)
        mat.node_tree.links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
        if metallic is not None:
            bsdf.inputs["Metallic"].default_value = 1.0
            bsdf.inputs["Roughness"].default_value = 1.0
        mesh.materials.append(mat)

        # 6. Link + export
        obj = bpy.data.objects.new("Mesh", mesh)
        bpy.context.scene.collection.objects.link(obj)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        _log.info("exporting %d verts, %d faces → %s", len(vtx_pos), num_faces, mesh_path)
        with contextlib.redirect_stdout(StringIO()):
            bpy.ops.export_scene.gltf(
                filepath=mesh_path,
                use_active_scene=True,
                export_normals=False,
                export_image_format="JPEG",
            )
        _log.info("done (%.1fs) — %d bytes", _time.time() - _t0, os.path.getsize(mesh_path))

        # 7. Cleanup scene
        for obj in list(bpy.context.scene.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
    finally:
        with contextlib.suppress(OSError):
            os.remove(tmp_tex_path)


def save_glb_mesh(mesh_path, vtx_pos, pos_idx, vtx_uv, uv_idx, texture, metallic=None, roughness=None, normal=None):
    """Save mesh as GLB with embedded PBR textures (bpy-only).

    Applies seam-aware texture dilation and vertex welding to prevent visible cracks
    at UV island boundaries.
    """
    _save_glb_mesh_bpy(mesh_path, vtx_pos, pos_idx, vtx_uv, uv_idx, texture, metallic, roughness, normal)


def save_mesh(mesh_path, vtx_pos, pos_idx, vtx_uv, uv_idx, texture, metallic=None, roughness=None, normal=None):
    if mesh_path.endswith(".glb"):
        save_glb_mesh(
            mesh_path,
            vtx_pos,
            pos_idx,
            vtx_uv,
            uv_idx,
            texture,
            metallic=metallic,
            roughness=roughness,
            normal=normal,
        )
    else:
        save_obj_mesh(
            mesh_path,
            vtx_pos,
            pos_idx,
            vtx_uv,
            uv_idx,
            texture,
            metallic=metallic,
            roughness=roughness,
            normal=normal,
        )
