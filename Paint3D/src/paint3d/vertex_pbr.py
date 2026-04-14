"""GLB com cor por vértice → difuso (UV) + Materialize → GLB PBR (metallic-roughness glTF)."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np
import trimesh
import xatlas
from PIL import Image

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
    # Acumular em RGB linear e contar sobreposições (média simples)
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

    # Buracos: inpainting OpenCV sobre máscara de buracos
    hole = ~mask
    if hole.any():
        base = np.clip(out_lin, 0.0, 1.0)
        # linear → uint8 para inpaint (aproximação)
        u8 = np.zeros((h, w, 3), dtype=np.uint8)
        for ch in range(3):
            lin = base[..., ch]
            mx = 1.0 if lin.max() <= 0 else lin.max()
            u8[..., ch] = (np.clip(lin / mx, 0, 1) * 255).astype(np.uint8)
        mask_u8 = (hole.astype(np.uint8) * 255)
        u8_bgr = cv2.cvtColor(u8, cv2.COLOR_RGB2BGR)
        inp = cv2.inpaint(u8_bgr, mask_u8, inpaintRadius=3, flags=cv2.INPAINT_TELEA)
        u8_rgb = cv2.cvtColor(inp, cv2.COLOR_BGR2RGB).astype(np.float64) / 255.0
        # Mesclar: só buracos
        out_lin = np.where(hole[..., None], u8_rgb, out_lin)

    # Linear → sRGB (display / glTF baseColor típico)
    out_srgb = np.clip(out_lin, 0, 1)
    out_srgb = np.where(
        out_srgb <= 0.0031308,
        12.92 * out_srgb,
        1.055 * np.power(out_srgb, 1.0 / 2.4) - 0.055,
    )
    return (np.clip(out_srgb * 255.0, 0, 255)).astype(np.uint8)


def _unwrap_vertex_colors(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, np.ndarray]:
    """xatlas + cores nos novos vértices. Devolve mesh com UV em ``mesh.visual.uv``."""
    if not hasattr(mesh.visual, "vertex_colors") or mesh.visual.vertex_colors is None:
        raise ValueError("A mesh precisa de vertex_colors (GLB cor por vértice).")
    rgba = mesh.visual.vertex_colors
    rgb = rgba[:, :3].astype(np.float64) / 255.0

    vmapping, indices, uvs = xatlas.parametrize(mesh.vertices, mesh.faces)
    verts_new = mesh.vertices[vmapping]
    rgb_new = rgb[vmapping]

    m2 = trimesh.Trimesh(vertices=verts_new, faces=indices, process=False)
    m2.visual = trimesh.visual.texture.TextureVisuals(uv=uvs.astype(np.float64))
    return m2, rgb_new


def _mesh_with_pbr_textures(
    mesh: trimesh.Trimesh,
    uvs: np.ndarray,
    diffuse_rgb_u8: np.ndarray,
    normal_pil: Image.Image,
    metallic_pil: Image.Image,
    smoothness_pil: Image.Image,
    ao_pil: Image.Image,
    *,
    double_sided: bool = True,
) -> trimesh.Trimesh:
    """Define TextureVisuals + PBRMaterial (ORM + oclusão em R)."""
    from trimesh.visual.material import PBRMaterial

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

    mat = PBRMaterial(
        baseColorTexture=diffuse_pil,
        normalTexture=normal_pil.convert("RGB"),
        metallicRoughnessTexture=orm_pil,
        occlusionTexture=Image.fromarray((ao * 255).astype(np.uint8), mode="L"),
        metallicFactor=1.0,
        roughnessFactor=1.0,
        doubleSided=double_sided,
    )

    vis = trimesh.visual.texture.TextureVisuals(uv=uvs, material=mat, image=diffuse_pil)
    out = mesh.copy()
    out.visual = vis
    return out


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

    mesh0 = load_mesh_trimesh(glb_in)
    mesh_u, vrgb = _unwrap_vertex_colors(mesh0)
    uvs = mesh_u.visual.uv
    assert uvs is not None
    faces = mesh_u.faces

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

        # Redimensionar mapas PBR ao tamanho do difuso se Materialize alterou resolução
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
            mesh_u,
            uvs,
            tex_u8,
            n_img,
            m_img,
            s_img,
            ao_img,
        )
        save_glb(mesh_pbr, glb_out)

    return glb_out
