"""
Isotropic remesh de GLB texturado com reprojeção de textura.

Re-malha para um número alvo de faces usando isotropic explicit remeshing
(pymeshlab) e re-projeta a textura original no novo layout UV via
transferência directa pixel-a-pixel (closest-point + bilinear sampling).
"""

from __future__ import annotations

import logging
import math
import tempfile
from pathlib import Path

import numpy as np
import trimesh
from trimesh.visual.texture import TextureVisuals

from .export import _export_glb_with_normals

log = logging.getLogger(__name__)


def _load_single_mesh(path: Path) -> trimesh.Trimesh:
    loaded = trimesh.load(str(path), force=None)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise ValueError(f"Mesh vazia: {path}")
        if len(loaded.geometry) == 1:
            return next(iter(loaded.geometry.values())).copy()
        return trimesh.util.concatenate([g.copy() for g in loaded.geometry.values()])
    if isinstance(loaded, trimesh.Trimesh):
        return loaded.copy()
    raise TypeError(f"Formato não suportado: {type(loaded)}")


def _has_uv_texture_image(mesh: trimesh.Trimesh) -> bool:
    v = mesh.visual
    if not isinstance(v, TextureVisuals):
        return False
    if getattr(v, "uv", None) is None:
        return False
    mat = getattr(v, "material", None)
    if mat is None:
        return False
    img = getattr(mat, "image", None)
    if img is None and hasattr(mat, "baseColorTexture"):
        img = mat.baseColorTexture
    return img is not None


def _get_texture_image(mesh: trimesh.Trimesh) -> np.ndarray:
    """Retorna a imagem de textura como array numpy HxWx3 uint8."""
    v = mesh.visual
    mat = v.material
    img = getattr(mat, "image", None)
    if img is None and hasattr(mat, "baseColorTexture"):
        img = mat.baseColorTexture
    if img is None:
        raise ValueError("Mesh sem imagem de textura")
    return np.array(img.convert("RGB"), dtype=np.uint8)


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


def _post_remesh_repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Repara rachaduras e artefactos de topologia introduzidos pelo remesh isotrópico.

    O ``meshing_isotropic_explicit_remeshing`` cria boundary edges em regiões
    onde edge-splits/collapses abrem pequenos buracos.  Esta função:
    1. Fecha buracos via pymeshlab (close_holes + non-manifold repair)
    2. Fallback: fecha buracos restantes via trimesh.fill_holes()
    3. Remove debris (componentes isolados < 1% diagonal)
    """
    result = _pymeshlab_close_holes(mesh)
    result = _trimesh_close_remaining(result)
    return result


def _pymeshlab_close_holes(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    import contextlib

    try:
        import pymeshlab
    except ImportError:
        return mesh

    with tempfile.TemporaryDirectory(prefix="t3d_repair_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        mesh.export(in_ply)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)

        # Repair non-manifold edges FIRST — close_holes requires 2-manifold input
        ms.meshing_repair_non_manifold_edges()
        ms.meshing_repair_non_manifold_vertices()
        with contextlib.suppress(pymeshlab.pmeshlab.PyMeshLabException):
            ms.meshing_close_holes(maxholesize=30)
        ms.meshing_remove_connected_component_by_diameter(
            mincomponentdiag=pymeshlab.PercentageValue(1),
        )
        ms.meshing_remove_duplicate_faces()
        ms.meshing_remove_duplicate_vertices()
        ms.meshing_remove_unreferenced_vertices()

        ms.save_current_mesh(out_ply)
        result = trimesh.load(out_ply, force="mesh")
        if isinstance(result, trimesh.Trimesh) and len(result.faces) > 0:
            return result
    return mesh


def _trimesh_close_remaining(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    import contextlib

    import trimesh.repair as trimesh_repair

    m = mesh.copy()
    trimesh_repair.fix_normals(m, multibody=True)
    with contextlib.suppress(Exception):
        m.fill_holes()
    return m


def _isotropic_remesh(mesh: trimesh.Trimesh, target_faces: int) -> trimesh.Trimesh:
    """Isotropic explicit remeshing via pymeshlab para atingir ~target_faces.

    Calcula targetlen a partir da área da superfície e do número alvo de faces.
    """
    import pymeshlab

    surface_area = float(mesh.area)
    if surface_area < 1e-12:
        raise ValueError("Mesh com área de superfície zero")

    # Target edge length for desired face count
    # Each equilateral triangle has area = sqrt(3)/4 * edge^2
    # target_faces ≈ surface_area / (sqrt(3)/4 * edge^2)
    targetlen = math.sqrt(4 * surface_area / (target_faces * math.sqrt(3)))

    with tempfile.TemporaryDirectory(prefix="t3d_remesh_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        mesh.export(in_ply)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)

        # Run multiple iterations for quality
        # adaptive=True: shorter edges in high-curvature areas (eyes, ears, fingers),
        # longer in flat areas (body, clothing) — preserves fine detail.
        for _ in range(4):
            ms.meshing_isotropic_explicit_remeshing(
                iterations=3,
                targetlen=pymeshlab.PureValue(targetlen),
                adaptive=True,
            )

        ms.meshing_remove_duplicate_faces()
        ms.meshing_remove_duplicate_vertices()
        ms.meshing_remove_unreferenced_vertices()
        ms.save_current_mesh(out_ply)

        result = trimesh.load(out_ply, force="mesh")
        if isinstance(result, trimesh.Trimesh) and len(result.faces) > 0:
            return result
        return mesh


def _uv_unwrap(mesh: trimesh.Trimesh) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """UV unwrap com xatlas. Retorna (vmapping, indices, uvs)."""
    import xatlas

    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(mesh.faces, dtype=np.int32)
    vmapping, indices, uvs = xatlas.parametrize(vertices, faces)
    return vmapping, indices, uvs


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
    source_mesh: trimesh.Trimesh,
    query_points: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Closest-point query — scipy KDTree (fast CPU) with trimesh fallback."""
    try:
        return _closest_point_kdtree(
            source_verts=source_mesh.vertices,
            source_faces=source_mesh.faces,
            query_points=query_points,
        )
    except ImportError:
        pass

    log.info("scipy unavailable — falling back to trimesh CPU closest_point (%d queries)", len(query_points))
    from trimesh.proximity import closest_point

    closest_pts, _distances, face_ids = closest_point(source_mesh, query_points)
    return closest_pts, face_ids


def _transfer_texture_direct(
    source_mesh: trimesh.Trimesh,
    source_tex: np.ndarray,
    source_uvs: np.ndarray,
    remeshed_verts: np.ndarray,
    remeshed_faces: np.ndarray,
    new_uvs: np.ndarray,
    texture_size: int,
    padding: int = 4,
) -> np.ndarray:
    """Transferência directa pixel-a-pixel da textura fonte para o novo atlas UV.

    Para cada pixel no novo atlas: rasteriza o triângulo UV correspondente,
    calcula a posição 3D na mesh remeshed, encontra o ponto mais próximo na
    mesh fonte, e amostra a textura fonte via UV interpoladas com bilinear.

    Args:
        source_mesh: Mesh fonte original com geometria e UVs.
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

    closest_pts, src_face_ids = _closest_point_batch(source_mesh, positions_3d)

    log.info("Interpolando UVs fonte e amostrando textura...")

    # Phase 4: Coords baricêntricas na face fonte e interpolação de UVs
    src_verts = source_mesh.vertices
    src_faces_arr = source_mesh.faces
    src_tri = src_verts[src_faces_arr[src_face_ids]]  # (N, 3, 3)

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
    src_uv_tri = source_uvs[src_faces_arr[src_face_ids]]  # (N, 3, 2)
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


def remesh_geometry_only(
    mesh: trimesh.Trimesh,
    target_faces: int,
) -> trimesh.Trimesh:
    """Isotropic remesh **geometry only** (sem UV/textura) para ~target_faces.

    Pipeline:
    1. Isotropic explicit remeshing via pymeshlab (adaptive=True)
    2. Post-remesh repair (close cracks, remove debris)
    3. Cleanup final

    Args:
        mesh: Mesh de entrada (qualquer tipo de visual).
        target_faces: Número alvo de faces.

    Returns:
        Nova Trimesh com geometria regularizada e ~target_faces.

    Raises:
        ValueError: Geometria inválida (área zero, poucas faces).
    """
    n = len(mesh.faces)
    log.info("remesh_geometry_only: %d faces → ~%d", n, target_faces)

    # Strip visual — só precisamos de geometria
    geom = trimesh.Trimesh(vertices=mesh.vertices.copy(), faces=mesh.faces.copy(), process=False)

    remeshed = _isotropic_remesh(geom, target_faces)
    log.info("Isotropic remesh: %d → %d faces", n, len(remeshed.faces))

    remeshed = _post_remesh_repair(remeshed)
    log.info("Post-remesh repair: %d faces, %d vertices", len(remeshed.faces), len(remeshed.vertices))

    return remeshed


def remesh_geometry_only_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
) -> Path:
    """Carrega GLB, aplica isotropic remesh (só geometria) e guarda resultado.

    Args:
        path_in: Caminho do GLB de entrada.
        path_out: Caminho do GLB de saída.
        target_faces: Número alvo de faces.

    Returns:
        Path do ficheiro escrito.
    """
    path_in = Path(path_in)
    path_out = Path(path_out)

    mesh = _load_single_mesh(path_in)
    if len(mesh.faces) < 4:
        raise ValueError(f"Mesh com poucas faces ({len(mesh.faces)}); remesh não aplicável.")

    result = remesh_geometry_only(mesh, target_faces)

    path_out.parent.mkdir(parents=True, exist_ok=True)
    _export_glb_with_normals(result, path_out)
    return path_out


def remesh_with_texture_reprojection(
    mesh: trimesh.Trimesh,
    target_faces: int,
    texture_size: int = 2048,
) -> trimesh.Trimesh:
    """Remesh isotrópico com reprojeção de textura via transferência directa pixel-a-pixel.

    Pipeline:
    1. Extrair textura e UVs da mesh original
    2. Isotropic remesh para ~target_faces
    3. UV unwrap do remesh com xatlas
    4. Transferência directa: para cada pixel do novo atlas, encontrar o ponto
       correspondente na mesh fonte via closest_point e amostrar a textura
       original com interpolação bilinear
    5. Dilatação nas fronteiras das ilhas UV para evitar costuras pretas
    6. Construir Trimesh final com TextureVisuals

    Args:
        mesh: Mesh texturada de entrada (com TextureVisuals).
        target_faces: Número alvo de faces após remesh.
        texture_size: Resolução da textura de saída (default 2048).

    Returns:
        Nova Trimesh com geometria remeshed e textura reprojetada.

    Raises:
        ValueError: Mesh sem textura/UV ou com geometria inválida.
        RuntimeError: Dependências em falta (pymeshlab, xatlas).
    """
    if not _has_uv_texture_image(mesh):
        raise ValueError("Mesh de entrada não tem textura UV com imagem.")

    source_tex = _get_texture_image(mesh)
    source_uvs = np.array(mesh.visual.uv, dtype=np.float64)

    # Se texture_size não for explicitamente diferente do default, usar
    # a resolução da textura fonte como referência
    src_h, src_w = source_tex.shape[:2]
    effective_size = max(src_h, src_w) if texture_size == 2048 else texture_size

    n = len(mesh.faces)
    log.info("Mesh original: %d faces, %d vertices, textura %dx%d", n, len(mesh.vertices), src_w, src_h)

    # Step 1: Isotropic remesh
    log.info("Remeshing isotrópico para ~%d faces...", target_faces)
    remeshed = _isotropic_remesh(mesh, target_faces)
    log.info("Remesh: %d faces, %d vertices", len(remeshed.faces), len(remeshed.vertices))

    # Step 1b: Close cracks introduced by remeshing
    remeshed = _post_remesh_repair(remeshed)
    log.info("Post-remesh repair: %d faces, %d vertices", len(remeshed.faces), len(remeshed.vertices))

    # Step 2: UV unwrap
    log.info("UV unwrap com xatlas...")
    vmapping, indices, uvs = _uv_unwrap(remeshed)

    # Remap vertices/faces to match xatlas output
    remapped_verts = remeshed.vertices[vmapping]
    remapped_faces = indices

    # Step 3: Direct per-pixel texture transfer
    log.info("Transferência directa pixel-a-pixel (%dx%d)...", effective_size, effective_size)
    baked_tex = _transfer_texture_direct(
        source_mesh=mesh,
        source_tex=source_tex,
        source_uvs=source_uvs,
        remeshed_verts=remapped_verts,
        remeshed_faces=remapped_faces,
        new_uvs=uvs,
        texture_size=effective_size,
        padding=4,
    )

    # Step 4: Build final mesh with TextureVisuals
    from PIL import Image

    baked_img = Image.fromarray(baked_tex, mode="RGB")

    material = trimesh.visual.material.SimpleMaterial(image=baked_img)
    material.doubleSided = True
    visual = TextureVisuals(uv=uvs, material=material)

    final_mesh = trimesh.Trimesh(
        vertices=remapped_verts,
        faces=remapped_faces,
        visual=visual,
        process=False,
    )

    log.info("Resultado final: %d faces, %d vertices", len(final_mesh.faces), len(final_mesh.vertices))
    return final_mesh


def remesh_textured_glb(
    path_in: str | Path,
    path_out: str | Path,
    *,
    target_faces: int,
    texture_size: int = 2048,
) -> Path:
    """Carrega GLB texturado, aplica remesh com reprojeção e guarda resultado.

    Args:
        path_in: Caminho do GLB de entrada.
        path_out: Caminho do GLB de saída.
        target_faces: Número alvo de faces.
        texture_size: Resolução da textura de saída.

    Returns:
        Path do ficheiro escrito.
    """
    path_in = Path(path_in)
    path_out = Path(path_out)

    mesh = _load_single_mesh(path_in)
    if len(mesh.faces) < 4:
        raise ValueError(f"Mesh com poucas faces ({len(mesh.faces)}); remesh não aplicável.")

    result = remesh_with_texture_reprojection(mesh, target_faces, texture_size=texture_size)

    path_out.parent.mkdir(parents=True, exist_ok=True)
    _export_glb_with_normals(result, path_out)
    return path_out
