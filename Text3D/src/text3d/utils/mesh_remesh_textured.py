"""
Isotropic remesh de GLB texturado com reprojeção de textura (bpy backend).

Re-malha para um número alvo de faces usando bpy voxel remesh
e re-projeta a textura original no novo layout UV via
transferência directa pixel-a-pixel (closest-point + bilinear sampling).
"""

from __future__ import annotations

import contextlib
import logging
import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from gamedev_shared.bpy_mesh import clear_scene, load_glb, save_glb

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class MeshData:
    """Lightweight mesh data container (no trimesh dependency)."""

    vertices: np.ndarray  # (N, 3) float64
    faces: np.ndarray  # (F, 3) int32
    uvs: np.ndarray | None = None  # per-vertex (N, 2) float64
    texture_image: np.ndarray | None = None  # (H, W, 3) uint8


# ---------------------------------------------------------------------------
# bpy helpers
# ---------------------------------------------------------------------------


def _join_objects(objects: list) -> object:
    """Join multiple bpy mesh objects into one."""
    import bpy

    if len(objects) <= 1:
        return objects[0] if objects else None
    bpy.ops.object.select_all(action="DESELECT")
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = objects[0]
    bpy.ops.object.join()
    return bpy.context.active_object


def _bpy_obj_to_arrays(obj) -> tuple[np.ndarray, np.ndarray]:
    """Extract vertices and faces from a bpy mesh object as numpy arrays.

    Triangulates the mesh first (GLTF export always triangulates, so this
    matches what the user sees in the GLB).
    """

    mesh = obj.data
    n_verts = len(mesh.vertices)

    verts = np.empty(n_verts * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", verts)
    verts = verts.reshape(n_verts, 3)

    # Triangulate — polygons can be quads/n-gons
    mesh.calc_loop_triangles()
    loop_tris = mesh.loop_triangles
    n_tris = len(loop_tris)
    faces = np.empty(n_tris * 3, dtype=np.int32)
    loop_tris.foreach_get("vertices", faces)
    faces = faces.reshape(n_tris, 3)

    return verts, faces


def _extract_source_data(obj) -> MeshData:
    """Extract vertices, faces, UVs, and texture from a bpy mesh object.

    Splits vertices at UV seams to produce per-vertex UVs (same convention as
    the old trimesh-based pipeline).
    """

    verts, faces = _bpy_obj_to_arrays(obj)
    mesh = obj.data
    mesh.calc_loop_triangles()
    loop_tris = mesh.loop_triangles
    n_tris = len(loop_tris)

    uvs = None
    texture_image = None

    # --- UVs (from loop_triangles for correctly triangulated data) ---
    uv_layer = mesh.uv_layers.active
    if uv_layer is not None:
        n_loops = len(mesh.loops)
        loop_uv_flat = np.empty(n_loops * 2, dtype=np.float64)
        uv_layer.data.foreach_get("uv", loop_uv_flat)
        loop_uvs = loop_uv_flat.reshape(n_loops, 2)

        tri_loops = np.empty(n_tris * 3, dtype=np.int32)
        loop_tris.foreach_get("loops", tri_loops)
        tri_loops = tri_loops.reshape(n_tris, 3)

        face_corner_uvs = loop_uvs[tri_loops].reshape(n_tris, 3, 2)

        corner_verts = faces.ravel()
        rounded_uvs = np.round(face_corner_uvs.reshape(-1, 2), 8)
        combined = np.column_stack([corner_verts.astype(np.float64), rounded_uvs])
        unique_combined, inverse = np.unique(combined, axis=0, return_inverse=True)

        verts = verts[unique_combined[:, 0].astype(np.int32)]
        faces = inverse.reshape(n_tris, 3).astype(np.int32)
        uvs = unique_combined[:, 1:3]

    # --- Texture image from material ---
    for mat_slot in obj.material_slots:
        mat = mat_slot.material
        if mat and mat.use_nodes:
            for node in mat.node_tree.nodes:
                if node.type == "TEX_IMAGE" and node.image:
                    bpy_img = node.image
                    w, h = bpy_img.size
                    if w > 0 and h > 0:
                        pixels = np.array(bpy_img.pixels[:]).reshape(h, w, 4)
                        texture_image = (pixels[:, :, :3] * 255).astype(np.uint8)
                    break
        if texture_image is not None:
            break

    return MeshData(vertices=verts, faces=faces, uvs=uvs, texture_image=texture_image)


# ---------------------------------------------------------------------------
# Geometry operations (bpy-based)
# ---------------------------------------------------------------------------


def _compute_surface_area(verts: np.ndarray, faces: np.ndarray) -> float:
    """Compute mesh surface area from vertices and face indices."""
    tri_verts = verts[faces]
    edge1 = tri_verts[:, 1] - tri_verts[:, 0]
    edge2 = tri_verts[:, 2] - tri_verts[:, 0]
    cross = np.cross(edge1, edge2)
    areas = 0.5 * np.sqrt(np.sum(cross**2, axis=1))
    return float(areas.sum())


def _bpy_remesh(obj, target_faces: int) -> None:
    """Apply voxel remesh to bpy mesh object, targeting ~target_faces.

    Computes voxel size from surface area and desired face count, then
    applies a single pass of bpy's voxel remesh modifier.
    """
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="OBJECT")

    verts, faces = _bpy_obj_to_arrays(obj)
    surface_area = _compute_surface_area(verts, faces)
    if surface_area < 1e-12:
        raise ValueError("Mesh com área de superfície zero")

    # Target edge length for desired face count
    targetlen = math.sqrt(4 * surface_area / (target_faces * math.sqrt(3)))

    mod = obj.modifiers.new(name="Remesh", type="REMESH")
    mod.mode = "VOXEL"
    mod.voxel_size = targetlen
    mod.use_smooth_shade = True
    bpy.ops.object.modifier_apply(modifier=mod.name)

    actual = len(obj.data.polygons)
    log.info("Voxel remesh: voxel_size=%.4f, target=%d, actual=%d", targetlen, target_faces, actual)


def _bpy_close_holes(obj) -> None:
    """Close holes and repair non-manifold geometry in bpy mesh."""
    import bpy

    bpy.context.view_layer.objects.active = obj

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")

    # Delete loose vertices/edges
    bpy.ops.mesh.delete_loose()

    # Dissolve degenerate faces
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.dissolve_degenerate(threshold=1e-6)

    # Fill holes (up to 30-sided boundary loops)
    bpy.ops.mesh.select_all(action="DESELECT")
    bpy.ops.mesh.select_non_manifold()
    bpy.ops.mesh.fill_holes(sides=30)

    # Remove duplicate vertices
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=1e-6)

    bpy.ops.object.mode_set(mode="OBJECT")


def _bpy_fix_normals(obj) -> None:
    """Make normals consistent (recalculate outside)."""
    import bpy

    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.normals_make_consistent(inside=False)
    bpy.ops.object.mode_set(mode="OBJECT")


def _bpy_post_remesh_repair(obj) -> None:
    """Post-remesh repair: close holes + fix normals."""
    _bpy_close_holes(obj)
    _bpy_fix_normals(obj)


# ---------------------------------------------------------------------------
# Texture / UV helpers (pure numpy — no trimesh)
# ---------------------------------------------------------------------------


def _sample_texture_at_uvs(tex: np.ndarray, uvs: np.ndarray) -> np.ndarray:
    """Amostra a textura nas coordenadas UV dadas (bilinear).

    Args:
        tex: Textura HxWx3 uint8.
        uvs: Array Nx2 com coordenadas UV (u, v) em [0, 1].

    Returns:
        Array Nx3 uint8 com cores amostradas.
    """
    h, w = tex.shape[:2]
    u = np.clip(uvs[:, 0], 0.0, 1.0)
    v = np.clip(uvs[:, 1], 0.0, 1.0)

    # Map UV to pixel coordinates (flip V because images are top-down)
    px = u * (w - 1)
    py = (1.0 - v) * (h - 1)

    # Bilinear interpolation
    x0 = np.floor(px).astype(np.int32)
    y0 = np.floor(py).astype(np.int32)
    x1 = np.minimum(x0 + 1, w - 1)
    y1 = np.minimum(y0 + 1, h - 1)

    fx = (px - x0).astype(np.float32)[:, np.newaxis]
    fy = (py - y0).astype(np.float32)[:, np.newaxis]

    c00 = tex[y0, x0].astype(np.float32)
    c10 = tex[y0, x1].astype(np.float32)
    c01 = tex[y1, x0].astype(np.float32)
    c11 = tex[y1, x1].astype(np.float32)

    colors = c00 * (1 - fx) * (1 - fy) + c10 * fx * (1 - fy) + c01 * (1 - fx) * fy + c11 * fx * fy
    return np.clip(colors, 0, 255).astype(np.uint8)


def _dilate_texture(tex: np.ndarray, filled: np.ndarray, padding: int) -> np.ndarray:
    """Dilata a textura preenchendo pixels vazios adjacentes com a cor do vizinho mais próximo.

    Args:
        tex: Textura HxWx3 uint8 (parcialmente preenchida).
        filled: Máscara booleana HxW indicando pixels já preenchidos.
        padding: Número de pixels para expandir as bordas das ilhas UV.

    Returns:
        Textura HxWx3 uint8 com as ilhas dilatadas de `padding` pixels.
    """
    if padding <= 0:
        return tex

    result = tex.copy()
    result_filled = filled.copy()

    for _ in range(padding):
        # Para cada iteração, expandir a fronteira em 1 pixel
        # Encontrar pixels vazios adjacentes a pixels preenchidos
        padded = np.pad(result_filled, 1, mode="constant", constant_values=False)
        # Um pixel vazio torna-se preenchido se algum vizinho (4-connectivity) está preenchido
        neighbors = (
            padded[:-2, 1:-1]  # acima
            | padded[2:, 1:-1]  # abaixo
            | padded[1:-1, :-2]  # esquerda
            | padded[1:-1, 2:]  # direita
        )
        new_frontier = neighbors & ~result_filled

        if not new_frontier.any():
            break

        # Para cada pixel novo na fronteira, copiar a cor do vizinho preenchido mais próximo
        ys, xs = np.where(new_frontier)
        for y, x in zip(ys, xs, strict=True):
            # Verificar vizinhos 4-connected, copiar o primeiro preenchido
            for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                ny, nx = y + dy, x + dx
                if 0 <= ny < result.shape[0] and 0 <= nx < result.shape[1] and result_filled[ny, nx]:
                    result[y, x] = result[ny, nx]
                    break

        result_filled = result_filled | new_frontier

    return result


def _closest_point_kdtree(
    source_verts: np.ndarray,
    source_faces: np.ndarray,
    query_points: np.ndarray,
    k_candidates: int = 32,
    batch_size: int = 50_000,
) -> tuple[np.ndarray, np.ndarray]:
    """CPU closest-point using scipy KDTree on face centroids + vectorised fine search."""
    from scipy.spatial import cKDTree

    sv = np.ascontiguousarray(source_verts, dtype=np.float32)
    centroids = sv[source_faces].mean(axis=1)
    tree = cKDTree(centroids)

    n_queries = len(query_points)
    all_closest: list[np.ndarray] = []
    all_face_ids: list[np.ndarray] = []

    for start in range(0, n_queries, batch_size):
        end = min(start + batch_size, n_queries)
        chunk = np.ascontiguousarray(query_points[start:end], dtype=np.float32)
        b = len(chunk)

        _, topk_idx = tree.query(chunk, k=k_candidates)
        if topk_idx.ndim == 1:
            topk_idx = topk_idx[:, np.newaxis]

        cand_tris = sv[source_faces[topk_idx]]
        q = chunk[:, np.newaxis, :]

        A = cand_tris[:, :, 0]
        B_v = cand_tris[:, :, 1]
        C_v = cand_tris[:, :, 2]
        AB = B_v - A
        AC = C_v - A
        AP = q - A
        d00 = np.einsum("...i,...i", AB, AB)
        d01 = np.einsum("...i,...i", AB, AC)
        d11 = np.einsum("...i,...i", AC, AC)
        d20 = np.einsum("...i,...i", AP, AB)
        d21 = np.einsum("...i,...i", AP, AC)
        denom = d00 * d11 - d01 * d01
        denom = np.where(np.abs(denom) < 1e-6, np.float32(1.0), denom)
        bv = (d11 * d20 - d01 * d21) / denom
        bw = (d00 * d21 - d01 * d20) / denom
        bu = np.clip(np.float32(1.0) - bv - bw, np.float32(0.0), np.float32(1.0))
        total = bu + bv + bw
        total = np.where(total < np.float32(1e-6), np.float32(1.0), total)
        bu /= total
        bv /= total
        bw /= total
        closest = bu[..., np.newaxis] * A + bv[..., np.newaxis] * B_v + bw[..., np.newaxis] * C_v
        diff = q - closest
        dist_sq = np.einsum("...i,...i", diff, diff)
        best_local = dist_sq.argmin(axis=1)
        batch_idx = np.arange(b)
        all_closest.append(closest[batch_idx, best_local].astype(np.float64))
        all_face_ids.append(topk_idx[batch_idx, best_local])

    return np.concatenate(all_closest), np.concatenate(all_face_ids)


def _closest_point_batch(
    source_verts: np.ndarray,
    source_faces: np.ndarray,
    query_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Closest-point query using scipy KDTree (CPU)."""
    return _closest_point_kdtree(
        source_verts=source_verts,
        source_faces=source_faces,
        query_points=query_points,
    )


def _uv_unwrap(vertices: np.ndarray, faces: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """UV unwrap com xatlas. Retorna (vmapping, indices, uvs)."""
    import xatlas

    v = np.ascontiguousarray(vertices, dtype=np.float32)
    f = np.ascontiguousarray(faces, dtype=np.int32)
    vmapping, indices, uvs = xatlas.parametrize(v, f)
    return vmapping, indices, uvs


def _transfer_texture_direct(
    source_verts: np.ndarray,
    source_faces: np.ndarray,
    source_tex: np.ndarray,
    source_uvs: np.ndarray,
    remeshed_verts: np.ndarray,
    remeshed_faces: np.ndarray,
    new_uvs: np.ndarray,
    texture_size: int,
    padding: int = 16,
) -> np.ndarray:
    """Transferência directa pixel-a-pixel da textura fonte para o novo atlas UV.

    Para cada pixel no novo atlas: rasteriza o triângulo UV correspondente,
    calcula a posição 3D na mesh remeshed, encontra o ponto mais próximo na
    mesh fonte, e amostra a textura fonte via UV interpoladas com bilinear.

    Args:
        source_verts: Vértices da mesh fonte (Nx3 float64).
        source_faces: Faces da mesh fonte (Fx3 int).
        source_tex: Textura fonte HxWx3 uint8.
        source_uvs: UVs da mesh fonte (Nx2 float64).
        remeshed_verts: Vértices da mesh remeshed (Mx3 float64).
        remeshed_faces: Faces da mesh remeshed (Fx3 int).
        new_uvs: UVs do novo atlas (Mx2 float64, do xatlas).
        texture_size: Resolução da textura de saída (quadrada).
        padding: Pixels de dilatação nas fronteiras das ilhas UV.

    Returns:
        Textura texture_size x texture_size x 3 uint8.
    """
    h, w = texture_size, texture_size
    tex = np.zeros((h, w, 3), dtype=np.uint8)
    filled = np.zeros((h, w), dtype=bool)

    # Converter UVs para coordenadas pixel
    px_u = new_uvs[:, 0] * (w - 1)
    px_v = (1.0 - new_uvs[:, 1]) * (h - 1)

    # Phase 1: Rasterizar todas as faces no espaço UV e colectar
    # (pixel_x, pixel_y, face_idx, bary_u, bary_v, bary_w)
    all_px: list[int] = []
    all_py: list[int] = []
    all_bary: list[np.ndarray] = []
    all_fi: list[int] = []

    log.info("Rasterizando %d faces no espaço UV...", len(remeshed_faces))

    for _fi, face in enumerate(remeshed_faces):
        tri_x = np.array([px_u[face[0]], px_u[face[1]], px_u[face[2]]])
        tri_y = np.array([px_v[face[0]], px_v[face[1]], px_v[face[2]]])

        x_min = max(0, int(np.floor(tri_x.min())) - 1)
        x_max = min(w - 1, int(np.ceil(tri_x.max())) + 1)
        y_min = max(0, int(np.floor(tri_y.min())) - 1)
        y_max = min(h - 1, int(np.ceil(tri_y.max())) + 1)

        if x_max <= x_min or y_max <= y_min:
            continue

        # Edge vectors do triângulo no espaço pixel
        v0x, v0y = tri_x[1] - tri_x[0], tri_y[1] - tri_y[0]
        v1x, v1y = tri_x[2] - tri_x[0], tri_y[2] - tri_y[0]

        d00 = v0x * v0x + v0y * v0y
        d01 = v0x * v1x + v0y * v1y
        d11 = v1x * v1x + v1y * v1y

        denom = d00 * d11 - d01 * d01
        if abs(denom) < 1e-12:
            continue

        inv_denom = 1.0 / denom

        # Gerar grelha de pixels na bounding box
        ys = np.arange(y_min, y_max + 1, dtype=np.float64)
        xs = np.arange(x_min, x_max + 1, dtype=np.float64)
        xx, yy = np.meshgrid(xs, ys)
        pts_x = xx.ravel()
        pts_y = yy.ravel()

        # Coordenadas baricêntricas
        v2x = pts_x - tri_x[0]
        v2y = pts_y - tri_y[0]

        d20 = v2x * v0x + v2y * v0y
        d21 = v2x * v1x + v2y * v1y

        bary_v = (d11 * d20 - d01 * d21) * inv_denom
        bary_w = (d00 * d21 - d01 * d20) * inv_denom
        bary_u = 1.0 - bary_v - bary_w

        inside = (bary_u >= -1e-6) & (bary_v >= -1e-6) & (bary_w >= -1e-6)
        if not inside.any():
            continue

        idx_inside = np.where(inside)[0]
        all_px.append(pts_x[idx_inside].astype(np.int32))
        all_py.append(pts_y[idx_inside].astype(np.int32))
        all_bary.append(np.stack([bary_u[idx_inside], bary_v[idx_inside], bary_w[idx_inside]], axis=-1))
        all_fi.append(_fi)

    if not all_px:
        log.warning("Nenhum pixel rasterizado; textura de saída ficará vazia.")
        return tex

    pixel_x = np.concatenate(all_px)
    pixel_y = np.concatenate(all_py)
    bary_coords = np.concatenate(all_bary, axis=0)  # (N, 3)
    pixel_face = np.concatenate([np.full(len(all_px[i]), fi, dtype=np.int32) for i, fi in enumerate(all_fi)])

    n_pixels = len(pixel_x)
    log.info("Rasterização: %d pixels preenchidos.", n_pixels)

    # Phase 2: Converter coords baricêntricas em posições 3D na mesh remeshed
    tri_verts = remeshed_verts[remeshed_faces[pixel_face]]  # (N, 3, 3)
    positions_3d = (
        bary_coords[:, 0:1] * tri_verts[:, 0]
        + bary_coords[:, 1:2] * tri_verts[:, 1]
        + bary_coords[:, 2:3] * tri_verts[:, 2]
    )  # (N, 3)

    log.info("Consultando closest_point na mesh fonte (%d pontos)...", n_pixels)

    closest_pts, src_face_ids = _closest_point_batch(source_verts, source_faces, positions_3d)

    log.info("Interpolando UVs fonte e amostrando textura...")

    # Phase 4: Coords baricêntricas na face fonte e interpolação de UVs
    src_tri = source_verts[source_faces[src_face_ids]]  # (N, 3, 3)

    sv0 = src_tri[:, 1] - src_tri[:, 0]
    sv1 = src_tri[:, 2] - src_tri[:, 0]
    sv2 = closest_pts - src_tri[:, 0]

    sd00 = np.sum(sv0 * sv0, axis=1)
    sd01 = np.sum(sv0 * sv1, axis=1)
    sd11 = np.sum(sv1 * sv1, axis=1)
    sd20 = np.sum(sv2 * sv0, axis=1)
    sd21 = np.sum(sv2 * sv1, axis=1)

    s_denom = sd00 * sd11 - sd01 * sd01
    s_denom = np.where(np.abs(s_denom) < 1e-12, 1.0, s_denom)
    s_bary_v = (sd11 * sd20 - sd01 * sd21) / s_denom
    s_bary_w = (sd00 * sd21 - sd01 * sd20) / s_denom
    s_bary_u = 1.0 - s_bary_v - s_bary_w

    s_bary_u = np.clip(s_bary_u, 0.0, 1.0)
    s_bary_v = np.clip(s_bary_v, 0.0, 1.0)
    s_bary_w = np.clip(s_bary_w, 0.0, 1.0)

    # Interpolar UVs fonte
    src_uv_tri = source_uvs[source_faces[src_face_ids]]  # (N, 3, 2)
    interp_uv = (
        s_bary_u[:, np.newaxis] * src_uv_tri[:, 0]
        + s_bary_v[:, np.newaxis] * src_uv_tri[:, 1]
        + s_bary_w[:, np.newaxis] * src_uv_tri[:, 2]
    )

    # Phase 5: Bilinear sample da textura fonte
    colors = _sample_texture_at_uvs(source_tex, interp_uv)

    # Escrever na textura de saída (último write ganha para overlapping pixels)
    pixel_y_clamped = np.clip(pixel_y, 0, h - 1)
    pixel_x_clamped = np.clip(pixel_x, 0, w - 1)
    tex[pixel_y_clamped, pixel_x_clamped] = colors
    filled[pixel_y_clamped, pixel_x_clamped] = True

    log.info("Amostragem completa. Pixels preenchidos: %d / %d.", filled.sum(), h * w)

    # Phase 6: Dilatação para preencher fronteiras de ilhas UV
    if padding > 0:
        log.info("Dilatando fronteiras UV (%d pixels)...", padding)
        tex = _dilate_texture(tex, filled, padding)

    return tex


# ---------------------------------------------------------------------------
# bpy mesh creation from numpy arrays
# ---------------------------------------------------------------------------


def _build_textured_bpy_mesh(
    verts: np.ndarray,
    faces: np.ndarray,
    uvs: np.ndarray,
    baked_tex: np.ndarray,
) -> tuple[object, str]:
    """Create a bpy mesh with vertices, faces, UVs, and a baked texture material.

    Returns (bpy_object, temp_image_path) — caller should unlink/delete temp file
    after saving.
    """
    import os

    import bpy
    from PIL import Image as PILImage

    n_verts = len(verts)
    n_faces = len(faces)

    clear_scene()

    # --- Create mesh data ---
    mesh = bpy.data.meshes.new(name="Remeshed")
    mesh.vertices.add(n_verts)
    mesh.vertices.foreach_set("co", verts.astype(np.float32).ravel())

    mesh.loops.add(n_faces * 3)
    mesh.polygons.add(n_faces)
    mesh.loops.foreach_set("vertex_index", faces.astype(np.int32).ravel())
    loop_starts = np.arange(n_faces, dtype=np.int32) * 3
    mesh.polygons.foreach_set("loop_start", loop_starts)
    loop_totals = np.full(n_faces, 3, dtype=np.int32)
    mesh.polygons.foreach_set("loop_total", loop_totals)
    mesh.update()

    # --- Object ---
    obj = bpy.data.objects.new(name="Remeshed", object_data=mesh)
    bpy.context.collection.objects.link(obj)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    # --- UVs (per-loop from per-vertex UVs) ---
    uv_layer = mesh.uv_layers.new(name="UVMap")
    loop_vert_indices = faces.ravel()
    loop_uv_values = uvs[loop_vert_indices].astype(np.float32)
    uv_layer.data.foreach_set("uv", loop_uv_values.ravel())

    mesh.update()

    # --- Material with baked texture ---
    # Save baked texture to temp PNG
    temp_fd, temp_path = tempfile.mkstemp(suffix=".png", prefix="baked_tex_")
    os.close(temp_fd)
    baked_img = PILImage.fromarray(baked_tex, mode="RGB")
    baked_img.save(temp_path)

    mat = bpy.data.materials.new(name="BakedMaterial")
    mat.use_nodes = True
    mat.use_backface_culling = False  # doubleSided = True

    nodes = mat.node_tree.nodes
    links = mat.node_tree.links

    # Image texture node
    tex_node = nodes.new("ShaderNodeTexImage")
    bpy_image = bpy.data.images.load(temp_path)
    bpy_image.colorspace_settings.name = "sRGB"
    tex_node.image = bpy_image

    # Connect to Principled BSDF
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")
    links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

    mesh.materials.append(mat)

    return obj, temp_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def remesh_geometry_only_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
) -> Path:
    """Carrega GLB, aplica isotropic remesh (só geometria) e guarda resultado.

    Uses bpy voxel remesh modifier for topology regularisation.

    Args:
        path_in: Caminho do GLB de entrada.
        path_out: Caminho do GLB de saída.
        target_faces: Número alvo de faces.

    Returns:
        Path do ficheiro escrito.
    """
    path_in = Path(path_in)
    path_out = Path(path_out)

    objs = load_glb(path_in)
    if not objs:
        raise ValueError(f"Mesh vazia: {path_in}")
    obj = _join_objects(objs)

    n = len(obj.data.polygons)
    log.info("remesh_geometry_only_glb: %d faces → ~%d", n, target_faces)

    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); remesh não aplicável.")

    _bpy_remesh(obj, target_faces)
    log.info("Voxel remesh: %d → %d faces", n, len(obj.data.polygons))

    _bpy_post_remesh_repair(obj)
    log.info("Post-remesh repair: %d faces, %d vertices", len(obj.data.polygons), len(obj.data.vertices))

    path_out.parent.mkdir(parents=True, exist_ok=True)
    save_glb(obj, path_out)
    return path_out


def remesh_with_texture_reprojection(
    source_data: MeshData,
    target_faces: int,
    texture_size: int = 2048,
) -> MeshData:
    """Remesh com reprojeção de textura via transferência directa pixel-a-pixel.

    Takes source mesh data, applies remeshing, UV unwrapping, and texture
    reprojection to produce a new MeshData with baked texture.

    Args:
        source_data: Source mesh data (vertices, faces, UVs, texture).
        target_faces: Número alvo de faces após remesh.
        texture_size: Resolução da textura de saída (default 2048).

    Returns:
        New MeshData with remeshed geometry and reprojeted texture.

    Raises:
        ValueError: Mesh sem textura/UV ou com geometria inválida.
    """
    if source_data.uvs is None:
        raise ValueError("Mesh de entrada não tem UVs.")
    if source_data.texture_image is None:
        raise ValueError("Mesh de entrada não tem textura.")

    source_tex = source_data.texture_image
    source_uvs = np.array(source_data.uvs, dtype=np.float64)
    source_verts = source_data.vertices
    source_faces = source_data.faces

    src_h, src_w = source_tex.shape[:2]
    effective_size = max(src_h, src_w) if texture_size == 2048 else texture_size

    n = len(source_faces)
    log.info(
        "Mesh original: %d faces, %d vertices, textura %dx%d",
        n,
        len(source_verts),
        src_w,
        src_h,
    )

    # Step 1: UV unwrap
    log.info("UV unwrap com xatlas...")
    vmapping, indices, uvs = _uv_unwrap(source_verts, source_faces)

    remapped_verts = source_verts[vmapping]
    remapped_faces = indices

    # Step 2: Direct per-pixel texture transfer
    log.info("Transferência directa pixel-a-pixel (%dx%d)...", effective_size, effective_size)
    baked_tex = _transfer_texture_direct(
        source_verts=source_verts,
        source_faces=source_faces,
        source_tex=source_tex,
        source_uvs=source_uvs,
        remeshed_verts=remapped_verts,
        remeshed_faces=remapped_faces,
        new_uvs=uvs,
        texture_size=effective_size,
        padding=4,
    )

    log.info("Resultado final: %d faces, %d vertices", len(remapped_faces), len(remapped_verts))

    return MeshData(
        vertices=remapped_verts,
        faces=remapped_faces,
        uvs=uvs,
        texture_image=baked_tex,
    )


def remesh_textured_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
    texture_size: int = 2048,
) -> Path:
    """Simplifica GLB texturado preservando UVs e textura.

    Pipeline:
    1. Merge by distance (0.0001) — fecha micro-rachaduras
    2. Decimate geometry (ratio = target_faces / current_faces)
    3. Downscale texture
    4. Export

    Args:
        path_in: Caminho do GLB de entrada.
        path_out: Caminho do GLB de saída.
        target_faces: Número alvo de faces.
        texture_size: Resolução da textura de saída.

    Returns:
        Path do ficheiro escrito.
    """
    import bpy
    import numpy as np
    from mathutils.kdtree import KDTree

    path_in = Path(path_in)
    path_out = Path(path_out)
    path_out.parent.mkdir(parents=True, exist_ok=True)

    clear_scene()
    bpy.ops.import_scene.gltf(filepath=str(path_in))
    mesh_objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not mesh_objs:
        raise ValueError(f"Mesh vazia: {path_in}")
    obj = max(mesh_objs, key=lambda o: len(o.data.polygons))
    n = len(obj.data.polygons)
    if n < 4:
        raise ValueError(f"Mesh com poucas faces ({n}); remesh não aplicável.")
    log.info("Original: %d faces", n)

    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)

    mesh = obj.data
    saved_n = len(mesh.vertices)
    saved_pos = np.empty((saved_n, 3), dtype=np.float32)
    saved_nrm = np.empty((saved_n, 3), dtype=np.float32)
    for i, v in enumerate(mesh.vertices):
        saved_pos[i] = v.co
        saved_nrm[i] = v.normal

    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="SELECT")
    bpy.ops.mesh.remove_doubles(threshold=0.0001, use_sharp_edge_from_normals=False)
    bpy.ops.object.mode_set(mode="OBJECT")
    log.info("Após merge by distance: %d faces", len(obj.data.polygons))

    ratio = target_faces / len(obj.data.polygons)
    mod = obj.modifiers.new("Decimate", "DECIMATE")
    mod.decimate_type = "COLLAPSE"
    mod.ratio = ratio
    bpy.ops.object.modifier_apply(modifier=mod.name)
    log.info("Após decimate: %d faces (ratio=%.4f)", len(obj.data.polygons), ratio)

    kdt = KDTree(len(saved_pos))
    for i, p in enumerate(saved_pos):
        kdt.insert(p.tolist(), i)
    kdt.balance()
    K = min(4, len(saved_pos))
    new_normals = np.empty((len(mesh.vertices), 3), dtype=np.float64)
    for i, v in enumerate(mesh.vertices):
        total_w = 0.0
        normal = np.zeros(3, dtype=np.float64)
        for _co, idx, dist in kdt.find_n(v.co, K):
            w = 1.0 / (dist * dist + 1e-10)
            normal += w * saved_nrm[idx]
            total_w += w
        if total_w > 0:
            normal /= total_w
        length = np.linalg.norm(normal)
        if length > 1e-8:
            normal /= length
        new_normals[i] = normal
    loop_normals = np.empty((len(mesh.loops), 3), dtype=np.float32)
    for loop in mesh.loops:
        loop_normals[loop.index] = new_normals[loop.vertex_index]
    with contextlib.suppress(AttributeError):
        mesh.normals_split_custom_set(loop_normals)

    if texture_size and obj.data.materials and obj.data.materials[0].use_nodes:
        for node in obj.data.materials[0].node_tree.nodes:
            if node.type == "TEX_IMAGE" and node.image:
                w, h = node.image.size[0], node.image.size[1]
                if max(w, h) != texture_size:
                    node.image.scale(texture_size, texture_size)
                break

    n_final = len(obj.data.polygons)
    bpy.ops.object.select_all(action="DESELECT")
    obj.select_set(True)

    # Also select armature if present (preserve rig + animations)
    arm_objs = [o for o in bpy.context.scene.objects if o.type == "ARMATURE"]
    for a in arm_objs:
        a.select_set(True)
    has_armature = bool(arm_objs)

    bpy.context.view_layer.objects.active = arm_objs[0] if has_armature else obj
    bpy.ops.export_scene.gltf(
        filepath=str(path_out),
        use_selection=True,
        export_apply=True,
        export_normals=True,
        export_texcoords=True,
        export_animations=has_armature,
        export_skins=has_armature,
        export_materials="EXPORT",
        export_image_format="AUTO",
    )

    clear_scene()
    log.info("Resultado: %s (%d faces)", path_out, n_final)
    return path_out
