"""GLB com cor por vértice → difuso (UV) + Materialize → GLB PBR (metallic-roughness glTF)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import xatlas
from PIL import Image

from gamedev_shared.bpy_mesh import clear_scene, create_mesh_from_arrays

from .utils.mesh_io import load_mesh_trimesh, save_glb


def _rasterize_vertex_colors_to_texture(
    uvs: np.ndarray,
    faces: np.ndarray,
    vertex_rgb: np.ndarray,
    size: int,
) -> np.ndarray:
    """
    ``uvs`` (N,2) [0,1], ``vertex_rgb`` (N,3) linear 0–1.
    Devolve ``(H,W,3)`` uint8 sRGB por pixel.
    """
    h = w = int(size)
    acc = np.zeros((h, w, 3), dtype=np.float64)
    cnt = np.zeros((h, w, 1), dtype=np.float64)

    for fi in range(len(faces)):
        tri = faces[fi]
        uvt = uvs[tri].astype(np.float64)
        col = vertex_rgb[tri].astype(np.float64)
        xs = np.array([u * (w - 1) for u in uvt[:, 0]], dtype=np.float64)
        ys = np.array([(1.0 - vv) * (h - 1) for vv in uvt[:, 1]], dtype=np.float64)
        minx = int(np.floor(np.clip(np.min(xs), 0, w - 1)))
        maxx = int(np.ceil(np.clip(np.max(xs), 0, w - 1)))
        miny = int(np.floor(np.clip(np.min(ys), 0, h - 1)))
        maxy = int(np.ceil(np.clip(np.max(ys), 0, h - 1)))
        v0x, v0y = xs[0], ys[0]
        v1x, v1y = xs[1], ys[1]
        v2x, v2y = xs[2], ys[2]
        denom = ((v1y - v2y) * (v0x - v2x) + (v2x - v1x) * (v0y - v2y))
        if abs(denom) < 1e-12:
            continue
        for py in range(miny, maxy + 1):
            for px in range(minx, maxx + 1):
                px_f = float(px) + 0.5
                py_f = float(py) + 0.5
                a = ((v1y - v2y) * (px_f - v2x) + (v2x - v1x) * (py_f - v2y)) / denom
                b = ((v2y - v0y) * (px_f - v2x) + (v0x - v2x) * (py_f - v2y)) / denom
                c = 1.0 - a - b
                if a >= -1e-8 and b >= -1e-8 and c >= -1e-8:
                    rgb_lin = col[0] * a + col[1] * b + col[2] * c
                    acc[py, px] += rgb_lin
                    cnt[py, px, 0] += 1.0

    mask = cnt[..., 0] > 0.5
    out_lin = np.zeros_like(acc)
    out_lin[mask] = acc[mask] / cnt[mask]

    hole = ~mask
    if hole.any():
        base = np.clip(out_lin, 0.0, 1.0)
        u8 = np.zeros((h, w, 3), dtype=np.uint8)
        for ch in range(3):
            lin = base[..., ch]
            mx = 1.0 if lin.max() <= 0 else lin.max()
            u8[..., ch] = (np.clip(lin / mx, 0, 1) * 255).astype(np.uint8)
        mask_u8 = (hole.astype(np.uint8) * 255)
        u8_bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
        inp = cv2.inpaint(u8_bgr, mask_u8, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        u8_rgb = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).astype(np.float64) / 255.0
        out_lin = np.where(hole[..., None], u8_rgb, out_lin)

    out_srgb = np.clip(out_lin, 0, 1)
    out_srgb = np.where(
        out_srgb <= 0.0031308,
        12.92 * out_srgb,
        1.055 * np.power(out_srgb, 1.0 / 2.4) - 0.055,
    )
    return (np.clip(out_srgb * 255.0, 0, 255)).astype(np.uint8)


def _extract_vertex_colors(obj: Any) -> np.ndarray:
    """Extrai ``(N, 3)`` vertex colors em [0,1] de um objecto bpy."""
    mesh = obj.data
    if hasattr(mesh, "color_attributes") and mesh.color_attributes.active is not None:
        col_attr = mesh.color_attributes.active
    elif hasattr(mesh, "vertex_colors") and mesh.vertex_colors.active is not None:
        col_attr = mesh.vertex_colors.active
    else:
        raise ValueError("A mesh precisa de vertex_colors (GLB cor por vértice).")

    n_verts = len(mesh.vertices)
    rgb = np.zeros((n_verts, 3), dtype=np.float64)
    for loop in mesh.loops:
        vi = loop.vertex_index
        c = col_attr.data[loop.index].color
        rgb[vi] = [c[0], c[1], c[2]]
    return rgb


def _unwrap_vertex_colors(obj: Any) -> tuple[Any, np.ndarray, np.ndarray, np.ndarray]:
    """xatlas unwrap + cores nos novos vértices.

    Returns:
        new_obj: bpy object com UV layer.
        rgb_new: ``(N, 3)`` cores por vértice.
        uvs: ``(N, 2)`` coordenadas UV.
        faces: ``(M, 3)`` índices de faces.
    """
    mesh = obj.data
    rgb = _extract_vertex_colors(obj)

    verts = np.array([tuple(v.co) for v in mesh.vertices], dtype=np.float64)
    faces_np = np.array([tuple(p.vertices) for p in mesh.polygons], dtype=np.int64)

    vmapping, indices, uvs = xatlas.parametrize(verts, faces_np)
    verts_new = verts[vmapping]
    rgb_new = rgb[vmapping]

    clear_scene()
    new_obj = create_mesh_from_arrays(verts_new, indices, name="Unwrapped")

    new_mesh = new_obj.data
    uv_layer = new_mesh.uv_layers.new(name="UVMap")
    loop_vert_indices = np.zeros(len(new_mesh.loops), dtype=np.int32)
    new_mesh.loops.foreach_get("vertex_index", loop_vert_indices)
    flat_uvs = uvs[loop_vert_indices].ravel().astype(np.float32)
    uv_layer.data.foreach_set("uv", flat_uvs)

    return new_obj, rgb_new, uvs, indices


def _pil_to_bpy_image(pil_img: Image.Image, name: str) -> Any:
    """Cria ``bpy.data.images`` a partir de uma PIL Image."""
    import bpy

    img = pil_img.convert("RGBA")
    w, h = img.size
    pixels = np.array(img, dtype=np.float32) / 255.0
    bpy_img = bpy.data.images.new(name, width=w, height=h)
    bpy_img.pixels[:] = pixels.flatten()
    bpy_img.pack()
    return bpy_img


def _mesh_with_pbr_textures(
    obj: Any,
    diffuse_rgb_u8: np.ndarray,
    normal_pil: Image.Image,
    metallic_pil: Image.Image,
    smoothness_pil: Image.Image,
    ao_pil: Image.Image,
    *,
    double_sided: bool = True,
) -> Any:
    """Cria material Principled BSDF com texturas PBR e atribui ao objecto bpy."""
    import bpy

    h, w = diffuse_rgb_u8.shape[:2]
    diffuse_pil = Image.fromarray(diffuse_rgb_u8, mode="RGB")

    mt = np.array(metallic_pil.convert("L"), dtype=np.float32) / 255.0
    sm = np.array(smoothness_pil.convert("L"), dtype=np.float32) / 255.0
    ao = np.array(ao_pil.convert("L"), dtype=np.float32) / 255.0
    rough = np.clip(1.0 - sm, 0.0, 1.0)

    orm = np.zeros((h, w, 3), dtype=np.uint8)
    orm[:, :, 0] = (ao * 255.0).astype(np.uint8)
    orm[:, :, 1] = (rough * 255.0).astype(np.uint8)
    orm[:, :, 2] = (mt * 255.0).astype(np.uint8)
    orm_pil = Image.fromarray(orm, mode="RGB")

    diff_bpy = _pil_to_bpy_image(diffuse_pil, "Diffuse")
    norm_bpy = _pil_to_bpy_image(normal_pil.convert("RGB"), "Normal")
    orm_bpy = _pil_to_bpy_image(orm_pil, "ORM")

    mat = bpy.data.materials.new(name="PBRMaterial")
    mat.use_nodes = True
    mat.use_backface_culling = not double_sided

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    nodes.clear()

    bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    bsdf.location = (0, 0)

    output = nodes.new("ShaderNodeOutputMaterial")
    output.location = (300, 0)
    links.new(bsdf.outputs["BSDF"], output.inputs["Surface"])

    diff_tex = nodes.new("ShaderNodeTexImage")
    diff_tex.image = diff_bpy
    diff_tex.location = (-600, 200)
    links.new(diff_tex.outputs["Color"], bsdf.inputs["Base Color"])

    norm_tex = nodes.new("ShaderNodeTexImage")
    norm_tex.image = norm_bpy
    norm_tex.location = (-600, -300)
    normal_map_node = nodes.new("ShaderNodeNormalMap")
    normal_map_node.location = (-300, -300)
    links.new(norm_tex.outputs["Color"], normal_map_node.inputs["Color"])
    links.new(normal_map_node.outputs["Normal"], bsdf.inputs["Normal"])

    orm_tex = nodes.new("ShaderNodeTexImage")
    orm_tex.image = orm_bpy
    orm_tex.location = (-600, 0)
    sep = nodes.new("ShaderNodeSeparateColor")
    sep.location = (-300, 0)
    links.new(orm_tex.outputs["Color"], sep.inputs["Color"])
    links.new(sep.outputs["G"], bsdf.inputs["Roughness"])
    links.new(sep.outputs["B"], bsdf.inputs["Metallic"])

    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)

    return obj


def vertex_color_glb_to_pbr_glb(
    glb_in: str | Path,
    glb_out: str | Path,
    *,
    texture_size: int = 1024,
    materialize_bin: str = "materialize",
    materialize_preset: str = "default",
    verbose: bool = False,
) -> Path:
    """
    Converte GLB só com cor por vértice num GLB glTF 2.0 PBR com texturas geradas pelo Materialize.

    1) Unwrap UV (xatlas) + raster do difuso a partir das cores de vértice.
    2) ``materialize`` no PNG difuso (height, normal, metallic, smoothness, AO, …).
    3) Embute baseColor, normal, ORM (R=occlusion, G=roughness, B=metallic) no GLB.
    """
    glb_in = Path(glb_in).resolve()
    glb_out = Path(glb_out).resolve()
    base = glb_in.stem

    objs = load_mesh_trimesh(glb_in)
    if not objs:
        raise ValueError(f"Sem mesh objects em {glb_in}")

    obj_u, vrgb, uvs, faces = _unwrap_vertex_colors(objs[0])

    tex_u8 = _rasterize_vertex_colors_to_texture(uvs, faces, vrgb, texture_size)

    with tempfile.TemporaryDirectory(prefix="paint3d_vpbr_") as tmp:
        td = Path(tmp)
        diffuse_path = td / f"{base}_diffuse.png"
        Image.fromarray(tex_u8, mode="RGB").save(diffuse_path)

        cmd = [
            materialize_bin,
            str(diffuse_path),
            "-o",
            str(td),
            "-p",
            materialize_preset,
            "-f",
            "png",
        ]
        if verbose:
            cmd.append("-v")
        subprocess.run(cmd, check=True, capture_output=not verbose)
        stem_diff = diffuse_path.stem
        p_normal = td / f"{stem_diff}_normal.png"
        p_metallic = td / f"{stem_diff}_metallic.png"
        p_smooth = td / f"{stem_diff}_smoothness.png"
        p_ao = td / f"{stem_diff}_ao.png"
        for p in (p_normal, p_metallic, p_smooth, p_ao):
            if not p.is_file():
                raise FileNotFoundError(f"Materialize não gerou: {p}")

        n_img = Image.open(p_normal).convert("RGB")
        m_img = Image.open(p_metallic).convert("L")
        s_img = Image.open(p_smooth).convert("L")
        ao_img = Image.open(p_ao).convert("L")

        tw, th = tex_u8.shape[1], tex_u8.shape[0]
        if n_img.size != (tw, th):
            n_img = n_img.resize((tw, th), Image.Resampling.LANCZOS)
        if m_img.size != (tw, th):
            m_img = m_img.resize((tw, th), Image.Resampling.LANCZOS)
        if s_img.size != (tw, th):
            s_img = s_img.resize((tw, th), Image.Resampling.LANCZOS)
        if ao_img.size != (tw, th):
            ao_img = ao_img.resize((tw, th), Image.Resampling.LANCZOS)

        mesh_pbr = _mesh_with_pbr_textures(
            obj_u,
            tex_u8,
            n_img,
            m_img,
            s_img,
            ao_img,
        )
        save_glb([mesh_pbr], glb_out)

    return glb_out
