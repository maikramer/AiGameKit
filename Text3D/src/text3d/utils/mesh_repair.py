"""
Pós-processamento de meshes Hunyuan3D: componentes desconexas, artefactos finos.

O modelo image-to-3D frequentemente gera várias ilhas (ex.: pés separados do corpo)
ou perde geometria fina; aqui aplicamos heurísticas conservadoras.

Sombras / contact shadows na imagem de referência tendem a virar um disco ou placa
fina na base; removemos por geometria (componentes planos no solo + faces horizontais
na faixa inferior), no mesmo espaço Y-up que o export GLB.
"""

from __future__ import annotations

import contextlib
from collections import deque
from typing import Literal

import numpy as np
import trimesh
import trimesh.repair as trimesh_repair
from trimesh.grouping import group_rows
from trimesh.transformations import rotation_matrix

from ..defaults import get_export_rotation_x_rad

try:
    import networkx as nx
except ImportError:
    nx = None  # type: ignore[assignment]


def _rotate_mesh_x(mesh: trimesh.Trimesh, angle: float) -> trimesh.Trimesh:
    """Rotação no eixo X (mesma convenção que ``export._apply_rotation_trimesh``)."""
    m = mesh.copy()
    m.apply_transform(rotation_matrix(angle, [1, 0, 0]))
    return m


def _to_export_y_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Alinha com ``save_mesh(..., rotate=True)`` (``get_export_rotation_x_rad()``)."""
    return _rotate_mesh_x(mesh, float(get_export_rotation_x_rad()))


def _from_export_y_up(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Inverso de ``_to_export_y_up`` para devolver a mesh no espaço Hunyuan."""
    return _rotate_mesh_x(mesh, -float(get_export_rotation_x_rad()))


def _is_thin_plaque(part: trimesh.Trimesh, *, thin_ratio: float) -> bool:
    """Placa fina (disco de sombra, pedestal) — ignora protrusões verticais.

    Uma placa de sombra é fina no eixo VERTICAL (Y) mas larga no plano horizontal (XZ).
    Uma protrusão vertical (ponta de espada) é fina no plano HORIZONTAL mas estendida
    no eixo vertical — NÃO é placa de sombra.
    """
    e = sorted(float(x) for x in part.extents)
    if len(e) != 3:
        return False

    if e[0] >= thin_ratio * e[2]:
        return False

    raw_extents = [float(x) for x in part.extents]
    y_extent = raw_extents[1]
    max_other = max(raw_extents[0], raw_extents[2])

    return not (y_extent > max_other * 1.5)


def _normal_concentration(part: trimesh.Trimesh) -> float:
    """O quanto as normais estão concentradas num eixo cardenal (1 = placa plana, <0.5 = orgânico).

    Placas de sombra (discos, pedestais) têm >80% da área com normais alinhadas a um eixo.
    Geometria orgânica (rochas, anatomia) tem normais espalhadas por muitos eixos.
    """
    if len(part.faces) < 4:
        return 0.0
    try:
        normals = part.face_normals
        areas = part.area_faces
        total_area = float(areas.sum())
        if total_area < 1e-12:
            return 0.0
        max_aligned = 0.0
        for ax in range(3):
            aligned = float(areas[np.abs(normals[:, ax]) >= 0.7].sum()) / total_area
            max_aligned = max(max_aligned, aligned)
        return max_aligned
    except Exception:
        return 0.0


def _remove_flat_bottom_islands(
    mesh_yup: trimesh.Trimesh,
    *,
    aggressive: bool = False,
) -> trimesh.Trimesh:
    """
    Descarta componentes desconexas que são placas finas coladas ao solo.

    Só actua quando existe pelo menos uma componente que *não* é uma placa fina
    (corpo principal), para não destruir props inteiramente chatos.

    ``aggressive``: placas/cascas mais espessas (ratio maior) e mais volume relativo
    ainda removidos se estiverem na base.
    """
    try:
        parts = mesh_yup.split(only_watertight=False)
    except Exception:
        return mesh_yup
    if len(parts) <= 1:
        return mesh_yup

    y_min_global = float(mesh_yup.bounds[0, 1])
    h_global = float(mesh_yup.extents[1])
    if h_global < 1e-8:
        return mesh_yup

    bottom_eps = (0.065 if aggressive else 0.042) * h_global
    thin_ratio = 0.28 if aggressive else 0.085
    vol_ratio_max = 0.52 if aggressive else 0.22

    def touches_bottom(p: trimesh.Trimesh) -> bool:
        return float(p.bounds[0, 1]) <= y_min_global + bottom_eps

    has_non_plaque = any(not _is_thin_plaque(p, thin_ratio=thin_ratio) or not touches_bottom(p) for p in parts)
    if not has_non_plaque:
        return mesh_yup

    def bbox_volume(p: trimesh.Trimesh) -> float:
        e = p.extents
        return float(e[0] * e[1] * e[2])

    max_vol = max(bbox_volume(p) for p in parts)
    if max_vol < 1e-18:
        return mesh_yup

    kept: list[trimesh.Trimesh] = []
    for p in parts:
        if touches_bottom(p) and _is_thin_plaque(p, thin_ratio=thin_ratio) and bbox_volume(p) < vol_ratio_max * max_vol:
            if _normal_concentration(p) < 0.45:
                kept.append(p)
                continue
            continue

        kept.append(p)

    if not kept:
        return mesh_yup
    if len(kept) == 1:
        return kept[0]
    try:
        return trimesh.util.concatenate(kept)
    except Exception:
        return mesh_yup


def _peel_bottom_upward_faces(
    mesh_yup: trimesh.Trimesh,
    *,
    band_frac: float = 0.042,
    min_normal_y: float = 0.82,
    max_remove_frac: float = 0.14,
    max_iterations: int = 3,
    scan_frac: float = 0.30,
    max_cross_section_frac: float = 0.45,
) -> trimesh.Trimesh:
    """Remove pedestal / shadow disc by cross-section and iterative peel.

    Detects which end of the Y axis has higher horizontal-face density,
    then slices the mesh on a plane and lets downstream ``make_watertight``
    seal the flat cut.
    """
    result = mesh_yup
    original_n = len(mesh_yup.faces)
    if original_n == 0:
        return result

    ymin = float(result.vertices[:, 1].min())
    ymax = float(result.vertices[:, 1].max())
    h = ymax - ymin
    if h < 1e-8:
        return result

    centers = result.triangles_center
    normals = result.face_normals
    if normals is None or len(normals) != len(result.faces):
        return result
    ny = np.abs(np.asarray(normals[:, 1], dtype=np.float64))
    horizontal = ny >= min_normal_y

    # Determine which end has denser horizontal faces (that is the pedestal end).
    bottom_horiz = float(np.count_nonzero(horizontal & (centers[:, 1] <= ymin + scan_frac * h)))
    top_horiz = float(np.count_nonzero(horizontal & (centers[:, 1] >= ymax - scan_frac * h)))
    pedestal_at_max = top_horiz >= bottom_horiz

    # Phase 1: iterative peel from the pedestal end (light cases only).
    # Skipped entirely when pedestal is large — Phase 2 cross-section handles those.
    for _ in range(max_iterations):
        if len(result.faces) == 0:
            break
        r_ymin = float(result.vertices[:, 1].min())
        r_ymax = float(result.vertices[:, 1].max())
        r_h = r_ymax - r_ymin
        if r_h < 1e-8:
            break
        band = max(band_frac * r_h, 1e-6)
        r_centers = result.triangles_center
        r_normals = result.face_normals
        if r_normals is None or len(r_normals) != len(result.faces):
            break
        r_ny = np.abs(np.asarray(r_normals[:, 1], dtype=np.float64))
        r_horizontal = r_ny >= min_normal_y
        if pedestal_at_max:
            remove = (r_centers[:, 1] >= r_ymax - band) & r_horizontal
        else:
            remove = (r_centers[:, 1] <= r_ymin + band) & r_horizontal
        n_remove = int(np.count_nonzero(remove))
        if n_remove == 0 or n_remove > max_remove_frac * original_n:
            break
        keep = ~remove
        try:
            sub = result.submesh([np.where(keep)[0]], append=True, only_watertight=False)
            if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
                result = sub
            else:
                break
        except Exception:
            break

    # Phase 2: cross-section cut at the pedestal boundary.
    # Scan from the pedestal end; find the transition from dense-horizontal
    # to organic, then slice the mesh on that plane.
    if len(result.faces) == 0:
        return result
    r_ymin = float(result.vertices[:, 1].min())
    r_ymax = float(result.vertices[:, 1].max())
    r_h = r_ymax - r_ymin
    if r_h < 1e-8:
        return result

    r_centers = result.triangles_center
    r_normals = result.face_normals
    if r_normals is None or len(r_normals) != len(result.faces):
        return result
    r_ny = np.abs(np.asarray(r_normals[:, 1], dtype=np.float64))
    r_horizontal = r_ny >= min_normal_y

    n_bins = max(int(scan_frac * 50), 10)
    bin_size = r_h / n_bins
    min_dense_bins = 1
    dense_run = 0
    cut_frac: float | None = None

    if pedestal_at_max:
        for i in range(n_bins):
            lo = r_ymax - (i + 1) * bin_size
            hi_y = r_ymax - i * bin_size
            in_bin = (r_centers[:, 1] >= lo) & (r_centers[:, 1] < hi_y)
            n_bin = int(np.count_nonzero(in_bin))
            if n_bin < 3:
                dense_run = 0
                continue
            pct = float(np.count_nonzero(r_horizontal & in_bin)) / n_bin
            if pct >= 0.40:
                dense_run += 1
            else:
                if dense_run >= min_dense_bins:
                    cut_frac = 1.0 - i / n_bins
                    break
                dense_run = 0
    else:
        for i in range(n_bins):
            lo = r_ymin + i * bin_size
            hi_y = r_ymin + (i + 1) * bin_size
            in_bin = (r_centers[:, 1] >= lo) & (r_centers[:, 1] < hi_y)
            n_bin = int(np.count_nonzero(in_bin))
            if n_bin < 3:
                dense_run = 0
                continue
            pct = float(np.count_nonzero(r_horizontal & in_bin)) / n_bin
            if pct >= 0.40:
                dense_run += 1
            else:
                if dense_run >= min_dense_bins:
                    cut_frac = i / n_bins
                    break
                dense_run = 0

    if cut_frac is None or cut_frac < 0.02:
        return result

    cut_y = r_ymin + cut_frac * r_h
    from trimesh.intersections import slice_faces_plane

    plane_normal = np.array([0.0, -1.0, 0.0]) if pedestal_at_max else np.array([0.0, 1.0, 0.0])

    new_verts, new_faces, _ = slice_faces_plane(
        result.vertices.copy(),
        result.faces.copy(),
        plane_normal=plane_normal,
        plane_origin=np.array([0.0, cut_y, 0.0]),
    )
    if len(new_faces) < 4:
        return result

    sub = trimesh.Trimesh(vertices=new_verts, faces=new_faces, process=False)
    already_removed = original_n - len(result.faces)
    if len(sub.faces) + already_removed < (1.0 - max_cross_section_frac) * original_n:
        return result

    return sub


def _remove_bottom_center_cylinder(
    mesh_yup: trimesh.Trimesh,
    *,
    height_frac: float = 0.17,
    radius_frac: float = 0.9,
    min_normal_y: float | None = 0.4,
    max_remove_frac: float = 0.52,
) -> trimesh.Trimesh:
    """
    Remove faces no cilindro vertical sob o centro (XZ) na parte baixa do bbox.

    Corta cascas 3D / “ovos” de sombra grandes que ocupam o solo sob o prop,
    desde que as faces sejam sobretudo horizontais (|ny| alto) ou ``min_normal_y``
    None para corte total na zona (último recurso).
    """
    if len(mesh_yup.faces) == 0:
        return mesh_yup
    bounds = mesh_yup.bounds
    ymin = float(bounds[0, 1])
    h = float(bounds[1, 1] - bounds[0, 1])
    if h < 1e-8:
        return mesh_yup
    cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
    cz = 0.5 * (bounds[0, 2] + bounds[1, 2])
    rx = 0.5 * (bounds[1, 0] - bounds[0, 0])
    rz = 0.5 * (bounds[1, 2] - bounds[0, 2])
    R = radius_frac * max(rx, rz, 1e-9)
    y_cut = ymin + height_frac * h
    centers = mesh_yup.triangles_center
    dx = centers[:, 0] - cx
    dz = centers[:, 2] - cz
    in_disk = (dx * dx + dz * dz) <= (R * R)
    in_bottom = centers[:, 1] <= y_cut
    remove = in_disk & in_bottom
    if min_normal_y is not None:
        _ = np.asarray(mesh_yup.face_normals)
        ny = np.abs(np.asarray(mesh_yup.face_normals)[:, 1], dtype=np.float64)
        remove = remove & (ny >= float(min_normal_y))
    n_remove = int(np.count_nonzero(remove))
    if n_remove == 0:
        return mesh_yup
    if n_remove > max_remove_frac * len(mesh_yup.faces):
        return mesh_yup
    keep = ~remove
    try:
        sub = mesh_yup.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
            return sub
    except Exception:
        pass
    return mesh_yup


def _remove_connected_ground_plinth(
    mesh_yup: trimesh.Trimesh,
    *,
    bottom_frac: float = 0.15,
    min_normal_y: float = 0.35,
    max_remove_frac: float = 0.35,
    min_expansion: float = 1.15,
) -> trimesh.Trimesh:
    """
    Remove pedestal/plataforma conectada ao mesh principal na base.

    Diferente de `_remove_flat_bottom_islands`, esta função lida com pedestais
    que estão conectados aos pés do modelo (não são componentes separadas).

    Heurística:
    1. Identifica faces na base do modelo (próximas ao Y mínimo)
    2. Detecta faces com normais predominantemente horizontais (|ny| alto)
    3. Analisa a silhueta em XZ: se houver uma expansão repentina na base,
       provavelmente é um pedestal (usa raio E médio + percentil 90)
    4. Usa flood-fill para marcar faces conectadas que parecem pedestal
    5. Remove faces marcadas se estiverem na base e forem "planas"

    ``bottom_frac``: altura da zona inferior a considerar (fracção do bbox).
    ``min_normal_y``: mínimo |normal_y| para considerar face "horizontal".
    ``max_remove_frac``: máximo de faces que pode remover (protecção).
    ``min_expansion``: factor de expansão mínimo (área/raio) para considerar pedestal.
    """
    if len(mesh_yup.faces) == 0:
        return mesh_yup

    bounds = mesh_yup.bounds
    ymin = float(bounds[0, 1])
    ymax = float(bounds[1, 1])
    h = ymax - ymin
    if h < 1e-8:
        return mesh_yup

    # Altura de corte para a zona inferior (pedestal)
    y_cut = ymin + bottom_frac * h

    # Centros das faces
    centers = mesh_yup.triangles_center
    normals = mesh_yup.face_normals

    # Faces na zona inferior
    in_bottom = centers[:, 1] <= y_cut

    # Faces com normais horizontais (|ny| alto = face plana horizontal)
    ny = np.abs(normals[:, 1])
    is_horizontal = ny >= min_normal_y

    # Candidate faces: na base E horizontais
    candidate_mask = in_bottom & is_horizontal

    if np.count_nonzero(candidate_mask) == 0:
        return mesh_yup

    # Análise de silhueta: comparar raio efetivo na base vs no topo da zona
    # Usamos múltiplas métricas para ser mais robusto
    cx = 0.5 * (bounds[0, 0] + bounds[1, 0])
    cz = 0.5 * (bounds[0, 2] + bounds[1, 2])

    # Raio efetivo na base (zona inferior) - usamos percentil 90 para ser robusto a outliers
    bottom_faces_idx = np.where(in_bottom)[0]
    if len(bottom_faces_idx) == 0:
        return mesh_yup

    bottom_centers = centers[bottom_faces_idx]
    dx_bottom = bottom_centers[:, 0] - cx
    dz_bottom = bottom_centers[:, 2] - cz
    r_bottom_max = np.sqrt(dx_bottom**2 + dz_bottom**2).max()
    r_bottom_p90 = np.percentile(np.sqrt(dx_bottom**2 + dz_bottom**2), 90)
    r_bottom_mean = np.sqrt(dx_bottom**2 + dz_bottom**2).mean()

    # Raio efetivo acima da zona de pedestal
    upper_y = ymin + 0.35 * h
    upper_mask = (centers[:, 1] > y_cut) & (centers[:, 1] <= upper_y)
    upper_faces_idx = np.where(upper_mask)[0]

    if len(upper_faces_idx) == 0:
        # Se não há faces no nível superior, assume que pode haver pedestal
        # e usa uma estimativa conservadora baseada no bbox
        r_upper_p90 = min(bounds[1, 0] - bounds[0, 0], bounds[1, 2] - bounds[0, 2]) * 0.25
        r_upper_mean = r_upper_p90 * 0.8
    else:
        upper_centers = centers[upper_faces_idx]
        dx_upper = upper_centers[:, 0] - cx
        dz_upper = upper_centers[:, 2] - cz
        r_upper_p90 = np.percentile(np.sqrt(dx_upper**2 + dz_upper**2), 90)
        r_upper_mean = np.sqrt(dx_upper**2 + dz_upper**2).mean()

    if r_upper_p90 < 1e-9:
        return mesh_yup

    # Múltiplas métricas de expansão
    expansion_max = r_bottom_max / r_upper_p90 if r_upper_p90 > 0 else 1.0
    expansion_p90 = r_bottom_p90 / r_upper_p90 if r_upper_p90 > 0 else 1.0
    expansion_mean = r_bottom_mean / r_upper_mean if r_upper_mean > 0 else 1.0

    # Usa a maior expansão entre p90 e mean (max pode ser muito sensível a outliers)
    expansion_ratio = max(expansion_p90, expansion_mean)

    # Se a base não é significativamente mais larga, provavelmente não há pedestal
    if expansion_ratio < min_expansion and expansion_max < min_expansion * 1.3:
        return mesh_yup

    # Flood-fill: partir das faces candidatas horizontais na base
    # e expandir para faces conectadas que também estão na zona inferior
    visited = np.zeros(len(mesh_yup.faces), dtype=bool)
    to_remove = np.zeros(len(mesh_yup.faces), dtype=bool)

    # Criar mapeamento de face -> faces adjacentes
    face_to_faces = [set() for _ in range(len(mesh_yup.faces))]

    # Usar face_adjacency para conectividade (mais robusto)
    try:
        adjacency = mesh_yup.face_adjacency
        for f1, f2 in adjacency:
            face_to_faces[f1].add(f2)
            face_to_faces[f2].add(f1)
    except Exception:
        # Fallback: usar edges
        try:
            edges = mesh_yup.edges_unique
            edge_faces = mesh_yup.edges_face
            edge_to_faces = {}
            for i, edge in enumerate(edges):
                f = edge_faces[i]
                if edge not in edge_to_faces:
                    edge_to_faces[edge] = []
                edge_to_faces[edge].append(f)
            for _edge, faces in edge_to_faces.items():
                if len(faces) == 2:
                    f1, f2 = faces
                    face_to_faces[f1].add(f2)
                    face_to_faces[f2].add(f1)
        except Exception:
            pass  # Sem conectividade, flood-fill não funcionará

    # BFS a partir de cada face candidata
    from collections import deque

    seed_faces = np.where(candidate_mask)[0]
    for seed in seed_faces:
        if visited[seed]:
            continue

        # BFS para encontrar componente conectada de faces na zona inferior
        component = []
        queue = deque([seed])
        visited[seed] = True

        while queue:
            face_idx = queue.popleft()
            component.append(face_idx)

            # Expandir para faces vizinhas
            for neighbor in face_to_faces[face_idx]:
                if not visited[neighbor] and in_bottom[neighbor]:
                    # Só expande se a face vizinha também estiver na base
                    visited[neighbor] = True
                    queue.append(neighbor)

        # Analisar componente: se for predominantemente horizontal, é pedestal
        if len(component) == 0:
            continue

        component_arr = np.array(component)
        component_horizontal = is_horizontal[component_arr]
        horizontal_ratio = np.count_nonzero(component_horizontal) / len(component)

        # Se mais de 55% das faces são horizontais, considera pedestal
        # (threshold mais baixo que antes para pegar mais casos)
        if horizontal_ratio > 0.55:
            to_remove[component_arr] = True

    n_remove = int(np.count_nonzero(to_remove))
    if n_remove == 0:
        return mesh_yup

    if n_remove > max_remove_frac * len(mesh_yup.faces):
        return mesh_yup

    keep = ~to_remove
    try:
        sub = mesh_yup.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
            return sub
    except Exception:
        pass

    return mesh_yup


def _remove_pedestal_by_layers(
    mesh_yup: trimesh.Trimesh,
    *,
    layer_depth_frac: float = 0.015,
    min_horizontal_pct: float = 0.80,
    normal_threshold: float = 0.95,
    max_layers: int = 5,
    max_remove_frac: float = 0.35,
    cleanup_small_components: int = 2000,
) -> trimesh.Trimesh:
    """
    Remove pedestal removendo camadas finas da base onde >80% das faces são
    puramente horizontais (normal alinhada com eixo Y).

    Esta abordagem preserva partes anatômicas (pés, cauda) que têm geometria
    curva/variada, removendo apenas o plano horizontal do pedestal.
    """
    if len(mesh_yup.faces) == 0:
        return mesh_yup

    # Usa eixo Y (Y-up space)
    axis = 1
    coords_all = mesh_yup.vertices[:, axis]
    height = coords_all.max() - coords_all.min()
    base_value = coords_all.min()

    if height < 1e-8:
        return mesh_yup

    layer_depth = layer_depth_frac * height
    centers = mesh_yup.triangles_center
    normals = mesh_yup.face_normals
    n_axis = np.abs(normals[:, axis])
    is_very_horizontal = n_axis >= normal_threshold

    to_remove = np.zeros(len(mesh_yup.faces), dtype=bool)

    for layer in range(max_layers):
        layer_start = base_value + (layer * layer_depth)
        layer_end = base_value + ((layer + 1) * layer_depth)
        in_layer = (centers[:, axis] >= layer_start) & (centers[:, axis] < layer_end)

        n_in_layer = np.count_nonzero(in_layer)
        if n_in_layer == 0:
            break

        n_horizontal = np.count_nonzero(in_layer & is_very_horizontal)
        horizontal_pct = n_horizontal / n_in_layer if n_in_layer > 0 else 0

        if horizontal_pct >= min_horizontal_pct:
            layer_remove_mask = in_layer & is_very_horizontal
            to_remove[layer_remove_mask] = True
        else:
            break

    n_remove = int(np.count_nonzero(to_remove))
    if n_remove == 0:
        return mesh_yup

    if n_remove > max_remove_frac * len(mesh_yup.faces):
        return mesh_yup

    keep = ~to_remove
    try:
        sub = mesh_yup.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
            sub.remove_unreferenced_vertices()

            # Limpeza: remover componentes pequenas/isoladas (artefatos)
            if cleanup_small_components > 0:
                try:
                    components = sub.split(only_watertight=False)
                    if len(components) > 1:
                        main_size = max(len(c.faces) for c in components)
                        min_size = max(cleanup_small_components, int(main_size * 0.05))
                        significant = [c for c in components if len(c.faces) >= min_size]
                        if len(significant) > 0 and len(significant) < len(components):
                            sub = trimesh.util.concatenate(significant)
                            sub.remove_unreferenced_vertices()
                except Exception:
                    pass

            return sub
    except Exception:
        pass

    return mesh_yup


def remove_small_islands(
    mesh: trimesh.Trimesh,
    *,
    min_face_ratio: float = 0.0002,
    min_faces_abs: int = 48,
) -> trimesh.Trimesh:
    """
    Remove componentes conexas muito pequenas (fragmentos flutuantes, lixo de triângulos).

    Mantém todas as ilhas com ``faces >= max(min_faces_abs, min_face_ratio * maior_ilha)``.
    """
    try:
        parts = mesh.split(only_watertight=False)
    except Exception:
        return mesh
    if len(parts) <= 1:
        return mesh
    main = max(parts, key=lambda m: len(m.faces))
    max_f = max(len(p.faces) for p in parts)
    thr = max(int(min_faces_abs), int(min_face_ratio * max_f))
    kept = [p for p in parts if len(p.faces) >= thr]
    if not kept:
        return main
    if len(kept) == 1:
        return kept[0]
    try:
        return trimesh.util.concatenate(kept)
    except Exception:
        return mesh


def remove_plate_components(
    mesh: trimesh.Trimesh,
    *,
    flatness_threshold: float = 0.15,
    aligned_area_threshold: float = 0.55,
    min_keep: int = 1,
) -> trimesh.Trimesh:
    """Remove componentes que são placas/discos separados (não colados ao modelo).

    Uma componente é considerada placa se:
    - É muito achatada (min_extent/max_extent < ``flatness_threshold``), OU
    - >55% da sua área de superfície tem normais alinhadas com um só eixo

    Nunca remove todas as componentes — mantém pelo menos ``min_keep`` (a melhor
    pelo score de ``_component_score``).
    """
    try:
        parts = mesh.split(only_watertight=False)
    except Exception:
        return mesh
    if len(parts) <= 1:
        return mesh

    def _is_plate(p: trimesh.Trimesh) -> bool:
        e = sorted(float(x) for x in p.extents)
        if e[2] < 1e-9:
            return True
        if e[0] / e[2] < flatness_threshold:
            return True
        try:
            normals = p.face_normals
            areas = p.area_faces
            total = areas.sum()
            if total > 0:
                for ax in range(3):
                    if areas[np.abs(normals[:, ax]) >= 0.7].sum() / total > aligned_area_threshold:
                        return True
        except Exception:
            pass
        return False

    kept = [p for p in parts if not _is_plate(p)]

    if len(kept) < min_keep:
        ranked = sorted(parts, key=_component_score, reverse=True)
        kept = ranked[:min_keep]

    if not kept:
        return mesh
    if len(kept) == 1:
        return kept[0]
    try:
        return trimesh.util.concatenate(kept)
    except Exception:
        return mesh


def _order_cycle_vertices_planar(mesh: trimesh.Trimesh, cycle: list[int]) -> list[int]:
    """Ordena vértices de um ciclo de buraco (ângulo no plano PCA) para fan triangulation."""
    vidx = np.asarray(cycle, dtype=np.int64)
    if len(vidx) < 3:
        return list(map(int, vidx))
    pts = mesh.vertices[vidx]
    c = pts.mean(axis=0)
    centered = pts - c
    if np.linalg.norm(centered) < 1e-12:
        return list(map(int, vidx))
    _, _, vh = np.linalg.svd(centered, full_matrices=False)
    u, v = vh[0], vh[1]
    ang = np.arctan2(np.dot(centered, v), np.dot(centered, u))
    order = np.argsort(ang)
    return list(map(int, vidx[order]))


def fill_small_boundary_holes(
    mesh: trimesh.Trimesh,
    *,
    max_boundary_edges: int = 16,
) -> trimesh.Trimesh:
    """
    Fecha buracos com contorno curto: primeiro o ``fill_holes`` do trimesh (tri/quad),
    depois triangulação em leque em ciclos de fronteira com ≤ ``max_boundary_edges`` arestas.

    Buracos grandes ou não planares podem ficar por fechar (evita distorcer o modelo).
    """
    m = mesh.copy()
    _fill_small_boundary_holes_inplace(m, max_boundary_edges)
    return m


def _fill_small_boundary_holes_inplace(mesh: trimesh.Trimesh, max_boundary_edges: int) -> None:
    """Igual a ``fill_small_boundary_holes`` mas altera ``mesh`` in-place (menos cópias)."""
    if max_boundary_edges < 3:
        return
    if mesh.is_watertight:
        return
    if nx is None:
        return
    with contextlib.suppress(Exception):
        trimesh_repair.fill_holes(mesh)
    if mesh.is_watertight:
        return
    boundary_groups = group_rows(mesh.edges_sorted, require_count=1)
    if len(boundary_groups) < 3:
        return
    be = mesh.edges[boundary_groups]
    # cycle_basis fica pesado com fronteiras enormes (malhas densas)
    if len(be) > 48_000:
        return
    g = nx.from_edgelist(be)
    new_faces: list[list[int]] = []
    for cycle in nx.cycle_basis(g):
        n = len(cycle)
        if n < 3 or n > max_boundary_edges:
            continue
        ordered = _order_cycle_vertices_planar(mesh, list(map(int, cycle)))
        if len(ordered) < 3:
            continue
        for i in range(1, len(ordered) - 1):
            new_faces.append([ordered[0], ordered[i], ordered[i + 1]])
    if not new_faces:
        return
    nf = np.asarray(new_faces, dtype=np.int64)
    try:
        from trimesh import triangles as tri_mod

        tri_pts = mesh.vertices[nf]
        _, valid = tri_mod.normals(tri_pts)
        nf = nf[valid]
    except Exception:
        pass
    if len(nf) == 0:
        return
    try:
        mesh.faces = np.vstack((mesh.faces, nf))
        mesh.remove_unreferenced_vertices()
        trimesh_repair.fix_normals(mesh, multibody=True)
    except Exception:
        pass


def remove_ground_shadow_artifacts(
    mesh: trimesh.Trimesh,
    *,
    mesh_space: Literal["hunyuan", "y_up"] = "hunyuan",
    y_up_flip_x_rad: float = 0.0,
    aggressive: bool = False,
    very_aggressive: bool = False,
) -> trimesh.Trimesh:
    """
    Remove disco/placa de sombra na base.

    ``hunyuan`` (defeito): mesh no espaço bruto Hunyuan3D; alinha com a rotação do
    ``save_mesh(rotate=True)`` antes de processar.

    ``y_up``: mesh já em Y-up no motor; não aplica a rotação Hunyuan→export.

    ``y_up_flip_x_rad``: só com ``y_up`` — roda π rad em X **antes** do peel (ficheiro
    gravado de cabeça para baixo); a saída fica em pé + limpa.

    ``aggressive``: opt-in — cilindro estreito sob o centro + peel mais forte (só para
    cascas enormes na base; pode comer geometria lateral se estiver no cone).
    O defeito usa peel na faixa inferior (~2,8% da altura do bbox) para cortar restos
    de pedestal/sombra um pouco mais acima que antes.

    ``very_aggressive``: modo extremo para pedestais muito grudados ao modelo.
    Usa flood-fill para detectar e remover geometria conectada na base que parece
    pedestal/plataforma. Pode remover mais geometria — use com cuidado.
    """
    m = mesh.copy()
    if mesh_space == "y_up" and y_up_flip_x_rad != 0.0:
        m = _rotate_mesh_x(m, float(y_up_flip_x_rad))
    yup = _to_export_y_up(m) if mesh_space == "hunyuan" else m
    yup = _remove_flat_bottom_islands(yup, aggressive=aggressive or very_aggressive)

    if very_aggressive:
        # NOVO: Remoção por camadas - remove pedestal plano preservando anatomia
        # Este algoritmo remove camadas finas da base onde >80% das faces são
        # puramente horizontais, parando quando encontra geometria curva (pés, cauda)
        yup = _remove_pedestal_by_layers(
            yup,
            layer_depth_frac=0.018,
            min_horizontal_pct=0.80,
            normal_threshold=0.95,
            max_layers=7,
            max_remove_frac=0.35,
            cleanup_small_components=2000,
        )
        # Fallback: modo antigo para casos onde o novo não removeu nada
        yup = _remove_connected_ground_plinth(
            yup,
            bottom_frac=0.20,
            min_normal_y=0.25,
            max_remove_frac=0.40,
            min_expansion=1.08,
        )
        # Cilindro mais agressivo (corte mais alto na base)
        yup = _remove_bottom_center_cylinder(
            yup,
            height_frac=0.22,
            radius_frac=0.75,
            min_normal_y=0.45,
            max_remove_frac=0.40,
        )
        # Peel mais forte
        yup = _peel_bottom_upward_faces(
            yup,
            band_frac=0.078,
            min_normal_y=0.55,
            max_remove_frac=0.30,
        )
    elif aggressive:
        # Cilindro mais estreito e normais mais horizontais — menos risco nas laterais.
        yup = _remove_bottom_center_cylinder(
            yup,
            height_frac=0.16,
            radius_frac=0.58,
            min_normal_y=0.68,
            max_remove_frac=0.3,
        )
        yup = _peel_bottom_upward_faces(
            yup,
            band_frac=0.055,
            min_normal_y=0.62,
            max_remove_frac=0.26,
        )
    else:
        yup = _peel_bottom_upward_faces(yup)
    with contextlib.suppress(Exception):
        yup.remove_unreferenced_vertices()
    if len(yup.faces) == 0:
        return mesh
    if mesh_space == "hunyuan":
        return _from_export_y_up(yup)
    return yup


def _bbox_volume(part: trimesh.Trimesh) -> float:
    """Volume do bounding box (produto dos extents)."""
    e = part.extents
    return float(e[0] * e[1] * e[2])


def _component_score(part: trimesh.Trimesh) -> float:
    """Score composto para seleccionar o componente "real" vs casca/placa/caixa.

    Combina múltiplas métricas para evitar seleccionar:
    - Cascas/caixas (normais quase todas cardinais, geométricamente simples)
    - Placas finas (bbox achatado)

    Score = face_count * anti_flatness * anti_plate * anti_box
    """
    n_faces = len(part.faces)
    if n_faces == 0:
        return 0.0

    e = sorted(float(x) for x in part.extents)
    if e[2] < 1e-9:
        return 0.0

    # Anti-flatness: penaliza componentes achatados (placas)
    flatness = e[0] / e[2]
    anti_flat = min(flatness * 2.0, 1.0)

    plate_penalty = 1.0
    box_penalty = 1.0
    try:
        normals = part.face_normals
        areas = part.area_faces
        total_area = areas.sum()
        if total_area > 0:
            # Anti-plate: >50% área alinhada com UM eixo → provável placa
            for ax in range(3):
                aligned_area = areas[np.abs(normals[:, ax]) >= 0.7].sum()
                if aligned_area / total_area > 0.5:
                    plate_penalty = 0.1
                    break

            # Anti-box: normais quase todas cardinais → provável caixa/casca
            cardinal = np.any(np.abs(normals) >= 0.85, axis=1)
            cardinal_ratio = float(areas[cardinal].sum() / total_area)
            if cardinal_ratio > 0.92:
                box_penalty = 0.01
            elif cardinal_ratio > 0.85:
                box_penalty = 0.1
            else:
                # Boost para formas orgânicas (normais diversas)
                box_penalty = 1.0 + (1.0 - cardinal_ratio)
    except Exception:
        pass

    return float(n_faces) * anti_flat * plate_penalty * box_penalty


def keep_largest_component(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Mantém a componente "principal" descartando placas/cascas/ilhas.

    Usa score composto (faces x compacidade x anti-flatness x anti-plate)
    em vez de apenas volume de bbox, para evitar seleccionar:
    - Cascas ocas que envolvem o modelo (bbox grande, pouca compacidade)
    - Placas separadas (muitas faces mas achatadas)
    """
    mesh = mesh.copy()
    try:
        parts = mesh.split(only_watertight=False)
    except Exception:
        return mesh
    if len(parts) <= 1:
        return mesh
    return max(parts, key=_component_score)


def _mean_edge_length(mesh: trimesh.Trimesh) -> float:
    """Comprimento médio das arestas únicas da mesh (0.0 se vazia)."""
    try:
        edges = mesh.edges_unique
        if len(edges) == 0:
            return 0.0
        vecs = mesh.vertices[edges[:, 1]] - mesh.vertices[edges[:, 0]]
        return float(np.mean(np.linalg.norm(vecs, axis=1)))
    except Exception:
        return 0.0


def _boundary_edge_count(mesh: trimesh.Trimesh) -> int:
    """Conta arestas de fronteira (buracos abertos)."""
    try:
        return len(group_rows(mesh.edges_sorted, require_count=1))
    except Exception:
        return -1


def _boundary_holes_info(
    mesh: trimesh.Trimesh,
) -> list[dict]:
    """Informação sobre cada boundary loop: area, centroid, n_edges.

    Retorna lista de dicts com chaves ``area``, ``centroid`` (ndarray 3D),
    ``n_edges``, ``vertex_ids`` (ndarray de índices).
    """
    if nx is None:
        return []
    try:
        boundary_groups = group_rows(mesh.edges_sorted, require_count=1)
        if len(boundary_groups) < 3:
            return []
        be = mesh.edges[boundary_groups]
        if len(be) > 60_000:
            return []
        g = nx.from_edgelist(be)
        holes: list[dict] = []
        for cycle in nx.cycle_basis(g):
            if len(cycle) < 3:
                continue
            ids = np.array(cycle)
            pts = mesh.vertices[ids]
            centroid = pts.mean(axis=0)
            area = 0.0
            n = len(pts)
            for i in range(n):
                v1 = pts[i] - centroid
                v2 = pts[(i + 1) % n] - centroid
                area += float(np.linalg.norm(np.cross(v1, v2))) * 0.5
            holes.append(
                {
                    "area": area,
                    "centroid": centroid,
                    "n_edges": n,
                    "vertex_ids": ids,
                }
            )
        return holes
    except Exception:
        return []


def _detect_structural_openings(
    mesh: trimesh.Trimesh,
    *,
    area_frac_threshold: float = 0.15,
    min_hole_edges: int = 50,
) -> list[dict]:
    """Detecta aberturas estruturais grandes (buracos de fronteira) em qualquer posição.

    Uma abertura é "estrutural" se: area > ``area_frac_threshold`` x area total da mesh
    **e** o boundary loop tem pelo menos ``min_hole_edges`` arestas (filtra rachas
    entre tábuas de crate, que são pequenos mas podem ter area relativa elevada).

    Retorna lista de dicts com: area, centroid, n_edges, axis, side, band coords.
    """
    try:
        total_area = float(mesh.area)
        if total_area < 1e-12:
            return []
        holes = _boundary_holes_info(mesh)
        if not holes:
            return []
        bounds = mesh.bounds
        openings: list[dict] = []
        for hole in holes:
            frac = hole["area"] / total_area
            if frac <= area_frac_threshold:
                continue
            # Filtrar buracos com boundary loop demasiado curto (rachas entre tábuas)
            if hole["n_edges"] < min_hole_edges:
                continue
            centroid = hole["centroid"]
            # Determinar eixo e lado mais próximo ao centróide
            best_axis = 0
            best_side = "min"
            best_dist = float("inf")
            for ax in range(3):
                lo = float(bounds[0, ax])
                hi = float(bounds[1, ax])
                h_ax = hi - lo
                if h_ax < 1e-9:
                    continue
                dist_min = float(abs(centroid[ax] - lo))
                dist_max = float(abs(centroid[ax] - hi))
                dist = min(dist_min, dist_max)
                if dist < best_dist:
                    best_dist = dist
                    best_axis = ax
                    best_side = "min" if dist_min < dist_max else "max"
            # Verificar consistência axial: vértices do buraco devem estar
            # próximos do lado detectado (min/max) dentro de 20% da extensão.
            # Isso impede que gaps entre planks (vértices distribuídos por
            # toda a extensão Y) sejam classificados como aberturas.
            ax_lo = float(bounds[0, best_axis])
            ax_hi = float(bounds[1, best_axis])
            ax_extent = ax_hi - ax_lo
            if ax_extent > 1e-9 and len(hole["vertex_ids"]) > 0:
                vert_coords = mesh.vertices[hole["vertex_ids"], best_axis]
                if best_side == "min":
                    # Maioria dos vértices deve estar perto de ax_lo
                    ref = ax_lo + 0.20 * ax_extent
                    near_side = float(np.count_nonzero(vert_coords <= ref)) / len(vert_coords)
                else:
                    # Maioria dos vértices deve estar perto de ax_hi
                    ref = ax_hi - 0.20 * ax_extent
                    near_side = float(np.count_nonzero(vert_coords >= ref)) / len(vert_coords)
                if near_side < 0.5:
                    continue  # Buraco não está concentrado no lado — ignorar
            openings.append(
                {
                    "area": hole["area"],
                    "centroid": centroid,
                    "n_edges": hole["n_edges"],
                    "vertex_ids": hole["vertex_ids"],
                    "axis": best_axis,
                    "side": best_side,
                }
            )
        return openings
    except Exception:
        return []


def _has_large_base_hole_fn(mesh: trimesh.Trimesh, area_frac_threshold: float = 0.05) -> bool:
    """Verifica se existe um buraco grande **na base** da mesh.

    .. deprecated::
        Mantida para compatibilidade. Usa :func:`_detect_structural_openings` internamente.
    """
    return len(_detect_structural_openings(mesh, area_frac_threshold=area_frac_threshold)) > 0


def _detect_base_axis_mesh(mesh: trimesh.Trimesh) -> tuple[int, int]:
    """Qual eixo parece ser a base (solo) e se é no mínimo (-1) ou máximo (+1) do eixo."""
    best_axis = 1
    best_direction = -1
    best_score = 0
    for axis in range(3):
        coords = mesh.vertices[:, axis]
        min_c, max_c = float(coords.min()), float(coords.max())
        range_c = max_c - min_c
        if range_c < 1e-6:
            continue
        lower_zone = coords <= (min_c + 0.05 * range_c)
        upper_zone = coords >= (max_c - 0.05 * range_c)
        n_lower = int(np.count_nonzero(lower_zone))
        n_upper = int(np.count_nonzero(upper_zone))
        score = abs(n_lower - n_upper)
        if score > best_score:
            best_score = score
            best_axis = axis
            best_direction = -1 if n_lower > n_upper else 1
    return best_axis, best_direction


def _bottom_zone_face_mask(mesh: trimesh.Trimesh, axis: int, direction: int, band_frac: float) -> np.ndarray:
    """Faces cujo centroide está na faixa inferior (ou superior) ao longo do eixo de suporte."""
    coords = mesh.vertices[:, axis]
    h = float(coords.max() - coords.min())
    if h < 1e-8:
        return np.zeros(len(mesh.faces), dtype=bool)
    base_min, base_max = float(coords.min()), float(coords.max())
    centers = mesh.triangles_center
    if direction == -1:
        return centers[:, axis] <= base_min + band_frac * h
    return centers[:, axis] >= base_max - band_frac * h


def _repair_holes_after_cut(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Repara non-manifold e fecha buracos após remoção topológica de faces."""
    m = mesh
    if _boundary_edge_count(m) <= 0:
        return m
    try:
        import tempfile as _tf
        from pathlib import Path as _Path

        import pymeshlab as _pml

        with _tf.TemporaryDirectory(prefix="cut_repair_") as tmpdir:
            in_ply = str(_Path(tmpdir) / "in.ply")
            out_ply = str(_Path(tmpdir) / "out.ply")
            m.export(in_ply)
            ms = _pml.MeshSet()
            ms.load_new_mesh(in_ply)
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_close_holes(maxholesize=120)
            ms.save_current_mesh(out_ply)
            m_new = trimesh.load(out_ply, force="mesh")
            if len(m_new.faces) > 0:
                m = m_new
    except Exception:
        pass
    if _boundary_edge_count(m) > 0:
        try:
            from pymeshfix import PyTMesh

            verts = np.asarray(m.vertices, dtype=np.float64)
            faces = np.asarray(m.faces, dtype=np.int64)
            mfix = PyTMesh()
            mfix.load_array(verts, faces)
            mfix.fill_small_boundaries(nbe=0, refine=True)
            v, f = mfix.return_arrays()
            m = trimesh.Trimesh(vertices=np.asarray(v), faces=np.asarray(f), process=True)
        except Exception:
            pass
    return m


def _remove_planar_webs_at_base(
    mesh: trimesh.Trimesh,
    axis: int,
    direction: int,
    *,
    bottom_frac: float = 0.10,
    normal_dot: float = 0.88,
    thin_ratio_max: float = 0.182,
    edge_aspect_min: float = 4.0,
    max_remove_frac: float = 0.12,
) -> trimesh.Trimesh:
    """
    Remove "teias" planas entre garras/pés: triângulos quase horizontais, muito chatos
    no eixo de suporte ou excessivamente alongados no plano perpendicular.
    """
    try:
        m = mesh.copy()
        h = float(m.vertices[:, axis].max() - m.vertices[:, axis].min())
        if h < 1e-8:
            return mesh

        bottom = _bottom_zone_face_mask(m, axis, direction, bottom_frac)
        normals = m.face_normals
        n_axis = np.abs(normals[:, axis])
        horizontal = n_axis >= normal_dot

        ax1 = (axis + 1) % 3
        ax2 = (axis + 2) % 3
        remove = np.zeros(len(m.faces), dtype=bool)

        for i in range(len(m.faces)):
            if not bottom[i] or not horizontal[i]:
                continue
            tri = m.vertices[m.faces[i]]
            ext_axis = float(tri[:, axis].max() - tri[:, axis].min())
            dx = float(tri[:, ax1].max() - tri[:, ax1].min())
            dy = float(tri[:, ax2].max() - tri[:, ax2].min())
            span_perp = float(np.hypot(dx, dy))
            thin_ratio = ext_axis / max(span_perp, 1e-9)

            e0 = tri[1] - tri[0]
            e1 = tri[2] - tri[0]
            e2 = tri[2] - tri[1]
            l0 = float(np.linalg.norm(e0))
            l1 = float(np.linalg.norm(e1))
            l2 = float(np.linalg.norm(e2))
            le = max(l0, l1, l2)
            se = min(l0, l1, l2)
            aspect = le / max(se, 1e-9)

            v0, v1, v2 = tri[0], tri[1], tri[2]
            ev0_a, ev0_b = v1 - v0, v2 - v0
            ev1_a, ev1_b = v0 - v1, v2 - v1
            ev2_a, ev2_b = v0 - v2, v1 - v2
            n0 = float(np.linalg.norm(ev0_a) * np.linalg.norm(ev0_b))
            n1 = float(np.linalg.norm(ev1_a) * np.linalg.norm(ev1_b))
            n2 = float(np.linalg.norm(ev2_a) * np.linalg.norm(ev2_b))
            a0 = float(np.degrees(np.arccos(np.clip(float(np.dot(ev0_a, ev0_b)) / max(n0, 1e-9), -1.0, 1.0))))
            a1 = float(np.degrees(np.arccos(np.clip(float(np.dot(ev1_a, ev1_b)) / max(n1, 1e-9), -1.0, 1.0))))
            a2 = float(np.degrees(np.arccos(np.clip(float(np.dot(ev2_a, ev2_b)) / max(n2, 1e-9), -1.0, 1.0))))
            amin, amax = min(a0, a1, a2), max(a0, a1, a2)

            # Chapa finíssima no eixo OU agulha longa no plano OU triângulo obtuso muito alongado
            is_pancake = thin_ratio <= thin_ratio_max and span_perp >= 0.004 * h
            is_needle = aspect >= edge_aspect_min
            is_obtuse_slab = amin >= 52.0 and amax >= 108.0 and aspect >= 2.8

            if is_pancake or is_needle or is_obtuse_slab:
                remove[i] = True

        n_rm = int(np.count_nonzero(remove))
        if n_rm == 0 or n_rm > max_remove_frac * len(m.faces):
            return mesh

        keep = ~remove
        sub = m.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
            sub.remove_unreferenced_vertices()
            return sub
    except Exception:
        pass
    return mesh


def _remove_thin_plaque_clusters_at_base(
    mesh: trimesh.Trimesh,
    axis: int,
    direction: int,
    *,
    band_frac: float = 0.155,
    normal_dot: float = 0.86,
    max_thickness_ratio: float = 0.108,
    min_faces: int = 16,
    max_cluster_frac: float = 0.22,
    max_remove_frac: float = 0.25,
    min_dot_ref_normal: float = 0.72,
) -> trimesh.Trimesh:
    """Remove placas horizontais finas conexas na base (pedestal grudado)."""
    try:
        m = mesh.copy()
        h = float(m.vertices[:, axis].max() - m.vertices[:, axis].min())
        if h < 1e-8 or len(m.faces) == 0:
            return mesh

        bottom = _bottom_zone_face_mask(m, axis, direction, band_frac)
        normals = m.face_normals
        n_axis = np.abs(normals[:, axis])
        horizontal = bottom & (n_axis >= normal_dot)
        if not np.any(horizontal):
            return mesh

        adj = m.face_adjacency
        nbr: list[list[int]] = [[] for _ in range(len(m.faces))]
        for a, b in adj:
            a, b = int(a), int(b)
            if horizontal[a] and horizontal[b]:
                nbr[a].append(b)
                nbr[b].append(a)

        visited = np.zeros(len(m.faces), dtype=bool)
        to_remove = np.zeros(len(m.faces), dtype=bool)
        ax1 = (axis + 1) % 3
        ax2 = (axis + 2) % 3
        n_face = len(m.faces)

        for seed in range(n_face):
            if not horizontal[seed] or visited[seed]:
                continue
            comp: list[int] = []
            q = deque([seed])
            visited[seed] = True
            while q:
                f = q.popleft()
                comp.append(f)
                for j in nbr[f]:
                    if not visited[j]:
                        visited[j] = True
                        q.append(j)

            n_c = len(comp)
            if n_c < min_faces or n_c > max_cluster_frac * n_face:
                continue

            fi = np.array(comp, dtype=np.int64)
            verts_idx = m.faces[fi].ravel()
            pts = m.vertices[verts_idx]
            ext_axis = float(pts[:, axis].max() - pts[:, axis].min())
            span = float(
                np.hypot(
                    pts[:, ax1].max() - pts[:, ax1].min(),
                    pts[:, ax2].max() - pts[:, ax2].min(),
                )
            )
            ratio = ext_axis / max(span, 1e-9)
            if ratio > max_thickness_ratio:
                continue

            nc = normals[fi]
            ref = nc[0] / max(float(np.linalg.norm(nc[0])), 1e-9)
            dots = np.abs(nc @ ref)
            if float(np.min(dots)) < min_dot_ref_normal:
                continue

            to_remove[fi] = True

        n_rm = int(np.count_nonzero(to_remove))
        if n_rm == 0 or n_rm > max_remove_frac * n_face:
            return mesh

        keep = ~to_remove
        sub = m.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
            sub.remove_unreferenced_vertices()
            return sub
    except Exception:
        pass
    return mesh


def _pymeshlab_roundtrip(mesh: trimesh.Trimesh, apply_fn) -> trimesh.Trimesh:
    """Exporta → aplica filtros pymeshlab via callback → reimporta."""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="pml_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        mesh.export(in_ply)
        import pymeshlab

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)
        apply_fn(ms)
        ms.save_current_mesh(out_ply)
        m_new = trimesh.load(out_ply, force="mesh")
        if isinstance(m_new, trimesh.Trimesh) and len(m_new.faces) > 0:
            return m_new
    return mesh


def _pymeshlab_close_holes(mesh: trimesh.Trimesh, *, max_hole_edges: int = 2000) -> trimesh.Trimesh:
    """Close boundary holes using pymeshlab only (no remeshing/clean)."""
    try:

        def _apply(ms):
            ms.meshing_close_holes(maxholesize=max_hole_edges)

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        return mesh


def pymeshlab_repair_non_manifold(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Repara arestas e vértices não‑manifold (útil após merge_close_vertices)."""
    try:

        def _apply(ms):
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        return mesh


def _pymeshfix_fill_gentle(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Close boundary holes via pymeshfix without aggressive decimation.

    ``fill_small_boundaries(nbe=0, refine=True)`` creates new triangles to seal
    holes while preserving existing geometry.  ``clean(max_iters=3, inner_loops=1)``
    removes only degenerate faces introduced by the fill.
    """
    try:
        from pymeshfix import PyTMesh

        verts = np.asarray(mesh.vertices, dtype=np.float64)
        faces = np.asarray(mesh.faces, dtype=np.int64)
        mfix = PyTMesh()
        mfix.load_array(verts, faces)
        mfix.fill_small_boundaries(nbe=0, refine=True)
        mfix.clean(max_iters=3, inner_loops=1)
        v, f = mfix.return_arrays()
        result = trimesh.Trimesh(
            vertices=np.asarray(v, dtype=np.float64),
            faces=np.asarray(f, dtype=np.int64),
            process=True,
        )
        return result
    except Exception:
        return mesh


def manifold_repair(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Repara topologia non-manifold, duplicatas e vértices órfãos via pymeshlab."""
    try:

        def _apply(ms):
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_unreferenced_vertices()

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        m = mesh.copy()
        with contextlib.suppress(Exception):
            m.merge_vertices()
            m.remove_unreferenced_vertices()
            trimesh_repair.fix_normals(m, multibody=True)
        return m


def prepare_mesh_topology(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Prepara topologia antes de export, LOD ou :func:`repair_mesh`.

    Funde vértices duplicados e quase coincidentes (export GLB), remove duplicatas e aplica
    :func:`manifold_repair` (sem remoção de bases, sem pymeshfix ``clean()``). Reduz
    tamanho em disco e evita faces soltas após decimação ou pipelines seguintes.

    Usada por defeito no início de :func:`repair_mesh` e antes de gerar LODs.
    """
    m = mesh.copy()
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True)
    with contextlib.suppress(Exception):
        m.remove_duplicate_faces()
    with contextlib.suppress(Exception):
        m.remove_unreferenced_vertices()
    # Aproximar vértices quase coincidentes (tolerância por casas decimais)
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True, digits_vertex=5)
    m = manifold_repair(m)
    with contextlib.suppress(Exception):
        m.merge_vertices(merge_tex=True)
    with contextlib.suppress(Exception):
        m.remove_duplicate_faces()
    with contextlib.suppress(Exception):
        m.remove_unreferenced_vertices()
    with contextlib.suppress(Exception):
        trimesh_repair.fix_normals(m, multibody=True)
    return m


prepare_mesh_for_lod_decimation = prepare_mesh_topology


def make_watertight(
    mesh: trimesh.Trimesh,
    *,
    max_hole_edges: int = 500,
) -> trimesh.Trimesh:
    """Cascata multi-etapa para fechar todos os buracos e tornar a mesh watertight.

    Ordem (para em cada etapa se já estiver watertight):
    1. pymeshlab: manifold repair + meshing_close_holes (Delaunay)
    2. pymeshfix: MeshFix de Attene — algoritmo específico para watertight
    3. trimesh: fill_holes fallback

    ``max_hole_edges``: buracos maiores que isto não são fechados pelo pymeshlab
    (evita distorção em buracos enormes). pymeshfix tenta fechar tudo.
    """
    m = mesh.copy()
    boundary = _boundary_edge_count(m)
    if boundary <= 0:
        return m

    # Etapa 1: pymeshlab — repair manifold + close holes
    try:

        def _apply(ms):
            ms.meshing_repair_non_manifold_edges()
            ms.meshing_repair_non_manifold_vertices()
            ms.meshing_remove_duplicate_faces()
            ms.meshing_remove_duplicate_vertices()
            ms.meshing_remove_unreferenced_vertices()
            ms.meshing_close_holes(maxholesize=max_hole_edges)

        m_new = _pymeshlab_roundtrip(m, _apply)
        b_new = _boundary_edge_count(m_new)
        if b_new < boundary:
            m = m_new
            boundary = b_new
    except Exception:
        pass

    if boundary <= 0:
        return m

    # Etapa 2: pymeshfix — algoritmo MeshFix (preenche tudo, refina triângulos)
    try:
        from pymeshfix import PyTMesh

        verts = np.asarray(m.vertices, dtype=np.float64)
        faces = np.asarray(m.faces, dtype=np.int64)
        mfix = PyTMesh()
        mfix.load_array(verts, faces)
        mfix.fill_small_boundaries(nbe=0, refine=True)
        v, f = mfix.return_arrays()
        m_new = trimesh.Trimesh(
            vertices=np.asarray(v, dtype=np.float64),
            faces=np.asarray(f, dtype=np.int64),
            process=True,
        )
        b_new = _boundary_edge_count(m_new)
        if b_new < boundary:
            m = m_new
            boundary = b_new
    except Exception:
        pass

    if boundary <= 0:
        return m

    # Etapa 3: pymeshfix clean() — remoção de degenerescências + watertight forçado
    try:
        from pymeshfix import PyTMesh

        verts = np.asarray(m.vertices, dtype=np.float64)
        faces = np.asarray(m.faces, dtype=np.int64)
        mfix = PyTMesh()
        mfix.load_array(verts, faces)
        mfix.clean(max_iters=10, inner_loops=3)
        v, f = mfix.return_arrays()
        m_new = trimesh.Trimesh(
            vertices=np.asarray(v, dtype=np.float64),
            faces=np.asarray(f, dtype=np.int64),
            process=True,
        )
        b_new = _boundary_edge_count(m_new)
        if b_new < boundary:
            m = m_new
            boundary = b_new
    except Exception:
        pass

    if boundary <= 0:
        return m

    # Etapa 4: trimesh fallback
    with contextlib.suppress(Exception):
        trimesh_repair.fill_holes(m)

    with contextlib.suppress(Exception):
        trimesh_repair.fix_normals(m, multibody=True)

    return m


def pymeshfix_mesh_repair_only(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Só PyTMesh ``fill_small_boundaries`` (sem ``clean``).

    ``clean()`` remove triângulos com auto-intersecções; em meshes decimadas (LOD) isso
    costuma eliminar troncos ou quase toda a geometria. Não usa pymeshlab nem
    ``repair_mesh``.

    Se ``pymeshfix`` não estiver instalado ou não houver fronteira aberta, devolve a mesh
    inalterada.
    """
    m = mesh.copy()
    try:
        from pymeshfix import PyTMesh
    except ImportError:
        return m

    if _boundary_edge_count(m) <= 0:
        return m

    try:
        verts = np.asarray(m.vertices, dtype=np.float64)
        faces = np.asarray(m.faces, dtype=np.int64)
        mfix = PyTMesh()
        mfix.load_array(verts, faces)
        mfix.fill_small_boundaries(nbe=0, refine=True)
        v, f = mfix.return_arrays()
        return trimesh.Trimesh(
            vertices=np.asarray(v, dtype=np.float64),
            faces=np.asarray(f, dtype=np.int64),
            process=True,
        )
    except Exception:
        return m


def taubin_smooth(
    mesh: trimesh.Trimesh,
    *,
    iterations: int = 5,
    lambda_: float = 0.5,
    mu: float = -0.53,
) -> trimesh.Trimesh:
    """Suavização Taubin volume-preserving via pymeshlab.

    Ao contrário da Laplaciana, o Taubin aplica um passo de expansão (mu < 0)
    após cada passo de contração (lambda > 0), preservando o volume global.
    Ideal antes de rigging/animação pois não encolhe a mesh.
    """
    if iterations <= 0:
        return mesh
    try:

        def _apply(ms):
            ms.apply_coord_taubin_smoothing(
                stepsmoothnum=iterations,
                lambda_=lambda_,
                mu=mu,
            )

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        return mesh


def hc_laplacian_smooth(
    mesh: trimesh.Trimesh,
    *,
    iterations: int = 3,
) -> trimesh.Trimesh:
    """HC Laplacian smoothing via pymeshlab — preserva melhor o volume que Laplacian simples."""
    if iterations <= 0:
        return mesh
    try:

        def _apply(ms):
            for _ in range(iterations):
                ms.apply_coord_hc_laplacian_smoothing()

        return _pymeshlab_roundtrip(mesh, _apply)
    except Exception:
        return mesh


def laplacian_smooth(mesh: trimesh.Trimesh, iterations: int = 1, lamb: float = 0.5) -> trimesh.Trimesh:
    """Suavização Laplaciana (pode encolher a mesh — preferir taubin_smooth para rigging)."""
    if iterations <= 0:
        return mesh
    m = mesh.copy()
    trimesh.smoothing.filter_laplacian(m, iterations=iterations, lamb=lamb)
    return m


def isotropic_remesh(
    mesh: trimesh.Trimesh,
    *,
    resolution: int = 150,
    iterations: int = 5,
    adaptive: bool = True,
    max_surf_dist_factor: float = 0.42,
    close_holes: bool = True,
    close_holes_max_edges: int = 300,
    taubin_steps: int = 3,
    taubin_lambda: float = 0.5,
    taubin_mu: float = -0.53,
) -> trimesh.Trimesh:
    """Isotropic remeshing via pymeshlab + Taubin smoothing.

    Pipeline: manifold repair → close holes → remesh → Taubin smooth.
    Reconstrói a topologia com triângulos uniformes, eliminando faces
    degeneradas (spikes) sem perder as features da superfície.

    ``resolution`` controla o nível de detalhe (~nº de subdivisões na diagonal).
    ``max_surf_dist_factor`` multiplica o target edge para ``maxsurfdist`` (menor =
    mais fiel à superfície original; típico 0.35-0.5).
    ``close_holes`` fecha buracos (marching cubes deixa buracos nas bordas do volume).
    ``taubin_steps`` controla suavização pós-remesh (0 = desliga).
    """
    try:
        import pymeshlab
    except ImportError:
        import warnings

        warnings.warn(
            "pymeshlab não instalado — isotropic_remesh indisponível. pip install pymeshlab",
            stacklevel=2,
        )
        return mesh

    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="remesh_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        mesh.export(in_ply)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)

        ms.meshing_repair_non_manifold_edges()
        ms.meshing_repair_non_manifold_vertices()
        ms.meshing_remove_duplicate_faces()
        ms.meshing_remove_duplicate_vertices()
        ms.meshing_remove_unreferenced_vertices()

        if close_holes:
            ms.meshing_close_holes(maxholesize=close_holes_max_edges)

        diag = ms.current_mesh().bounding_box().diagonal()
        target_edge = diag / max(resolution, 10)

        avg_edge = _mean_edge_length(mesh)
        if avg_edge > 0:
            target_edge = max(target_edge, avg_edge * 0.80)

        ms.meshing_isotropic_explicit_remeshing(
            iterations=iterations,
            targetlen=pymeshlab.PureValue(target_edge),
            adaptive=adaptive,
            selectedonly=False,
            checksurfdist=True,
            maxsurfdist=pymeshlab.PureValue(target_edge * max_surf_dist_factor),
        )

        if taubin_steps > 0:
            ms.apply_coord_taubin_smoothing(
                stepsmoothnum=taubin_steps,
                lambda_=taubin_lambda,
                mu=taubin_mu,
            )

        ms.save_current_mesh(out_ply)
        return trimesh.load(out_ply, force="mesh")


def _remove_spikes_and_repair(
    mesh: trimesh.Trimesh,
    *,
    max_spike_angle: float = 10.0,
) -> trimesh.Trimesh:
    """Remove pontas finas (spikes) na base e refecha buracos criados."""
    try:
        m = mesh.copy()
        axis, direction = _detect_base_axis_mesh(m)
        bottom_zone = _bottom_zone_face_mask(m, axis, direction, 0.08)

        spike_faces = []
        for i, face in enumerate(m.faces):
            if not bottom_zone[i]:
                continue
            tri = m.vertices[face]
            edges = [tri[1] - tri[0], tri[2] - tri[0], tri[2] - tri[1]]
            lengths = [float(np.linalg.norm(e)) for e in edges]
            if min(lengths) < 1e-10:
                continue
            cos_a0 = np.clip(float(np.dot(edges[0], edges[1])) / (lengths[0] * lengths[1]), -1.0, 1.0)
            if np.degrees(np.arccos(cos_a0)) < max_spike_angle:
                spike_faces.append(i)
                continue
            e0_inv = -edges[0]
            cos_a1 = np.clip(float(np.dot(e0_inv, edges[2])) / (lengths[0] * lengths[2]), -1.0, 1.0)
            if np.degrees(np.arccos(cos_a1)) < max_spike_angle:
                spike_faces.append(i)
                continue
            e1_inv, e2_inv = -edges[1], -edges[2]
            cos_a2 = np.clip(float(np.dot(e1_inv, e2_inv)) / (lengths[1] * lengths[2]), -1.0, 1.0)
            if np.degrees(np.arccos(cos_a2)) < max_spike_angle:
                spike_faces.append(i)

        if 0 < len(spike_faces) < len(m.faces) * 0.2:
            keep_mask = np.ones(len(m.faces), dtype=bool)
            keep_mask[spike_faces] = False
            sub = m.submesh([np.where(keep_mask)[0]], append=True, only_watertight=False)
            if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
                sub.remove_unreferenced_vertices()
                return make_watertight(sub)
        return m
    except Exception:
        return mesh


def mesh_quality_check(
    mesh: trimesh.Trimesh,
    *,
    plate_coverage_threshold: float = 0.7,
    flatness_threshold: float = 0.12,
    volume_efficiency_threshold: float = 0.15,
    thickness_ratio_threshold: float = 0.10,
    band_frac: float = 0.10,
    normal_align: float = 0.7,
) -> dict:
    """Analisa qualidade da mesh e detecta artefactos comuns do image-to-3D.

    Detecta:
    - **Backing plate**: superfície plana grande numa extremidade de qualquer eixo
      (coverage > ``plate_coverage_threshold`` na secção transversal do bbox).
    - **Flat cutout (bbox)**: modelo demasiado fino num eixo do bbox
      (min/max extent < ``flatness_threshold``).
    - **Flat cutout (volume)**: volume do convex hull muito pequeno vs bbox
      (vol_hull/vol_bbox < ``volume_efficiency_threshold``). Detecta meshes que
      parecem ter 3D no bbox mas são quase 2D na realidade (ex.: folha fina).
    - **Flat-backed**: espessura mediana ao longo do eixo mais fino é muito baixa
      (< ``thickness_ratio_threshold``). Detecta meshes com backing plate integrada
      onde a geometria real está concentrada numa face.
    - **Watertight**: mesh fechada sem buracos.

    Retorna dict com:
      ``passed``: bool — True se a mesh é aceitável
      ``issues``: list[str] — descrição dos problemas
      ``plate_axes``: list[dict] — eixos com backing plate
      ``flatness_ratio``: float — min(extents)/max(extents)
      ``volume_efficiency``: float — convex_hull_vol/bbox_vol
      ``thickness_ratio``: float — espessura mediana no eixo mais fino
      ``watertight``: bool
    """
    result: dict = {
        "passed": True,
        "issues": [],
        "plate_axes": [],
        "flatness_ratio": 0.0,
        "volume_efficiency": 0.0,
        "thickness_ratio": 0.0,
        "watertight": False,
    }

    if len(mesh.faces) == 0:
        result["passed"] = False
        result["issues"].append("empty mesh")
        return result

    extents = mesh.extents
    e_sorted = sorted(float(x) for x in extents)
    flatness = e_sorted[0] / e_sorted[2] if e_sorted[2] > 1e-9 else 0
    result["flatness_ratio"] = round(flatness, 4)
    result["watertight"] = bool(mesh.is_watertight)

    # --- Flat cutout (bbox) ---
    if flatness < flatness_threshold:
        result["passed"] = False
        result["issues"].append(f"flat cutout bbox (ratio={flatness:.3f})")

    # --- Volume efficiency (convex hull / bbox) ---
    bbox_vol = float(e_sorted[0] * e_sorted[1] * e_sorted[2])
    if bbox_vol > 1e-12:
        try:
            ch_vol = float(mesh.convex_hull.volume)
        except Exception:
            ch_vol = 0.0
        vol_eff = ch_vol / bbox_vol
    else:
        vol_eff = 0.0
    result["volume_efficiency"] = round(vol_eff, 4)

    if vol_eff < volume_efficiency_threshold:
        result["passed"] = False
        result["issues"].append(f"flat cutout volume (efficiency={vol_eff:.3f})")

    # --- Thickness ratio (median depth on thinnest axis) ---
    min_ax = int(np.argmin(extents))
    coords = mesh.vertices[:, min_ax]
    half_range = (float(coords.max()) - float(coords.min())) / 2
    if half_range > 1e-9:
        median_c = float(np.median(coords))
        dists = np.abs(coords - median_c)
        thickness = float(np.median(dists) / half_range)
    else:
        thickness = 0.0
    result["thickness_ratio"] = round(thickness, 4)

    if thickness < thickness_ratio_threshold:
        result["passed"] = False
        result["issues"].append(f"flat-backed (thickness={thickness:.3f})")

    # --- Backing plate (per-axis coverage) ---
    normals = mesh.face_normals
    areas = mesh.area_faces
    centers = mesh.triangles_center
    bounds = mesh.bounds

    ax_names = ["X", "Y", "Z"]
    for ax in range(3):
        lo = float(bounds[0, ax])
        hi = float(bounds[1, ax])
        h = hi - lo
        if h < 1e-8:
            continue

        other = [i for i in range(3) if i != ax]
        cross_area = float(extents[other[0]] * extents[other[1]])
        if cross_area < 1e-12:
            continue

        for side_label, band_lo, band_hi in [
            ("min", lo, lo + band_frac * h),
            ("max", hi - band_frac * h, hi),
        ]:
            in_band = (centers[:, ax] >= band_lo) & (centers[:, ax] <= band_hi)
            aligned = np.abs(normals[:, ax]) >= normal_align
            flat_in_band = in_band & aligned
            flat_area = float(areas[flat_in_band].sum())
            coverage = flat_area / cross_area

            if coverage > plate_coverage_threshold:
                result["passed"] = False
                plate_info = {
                    "axis": ax_names[ax],
                    "side": side_label,
                    "coverage": round(coverage, 3),
                }
                result["plate_axes"].append(plate_info)
                result["issues"].append(f"backing plate {ax_names[ax]}-{side_label} (coverage={coverage:.2f})")

    return result


def _detect_artifact_plates(
    mesh: trimesh.Trimesh,
    *,
    plate_coverage_threshold: float = 0.7,
    band_frac: float = 0.10,
    normal_align: float = 0.7,
    min_thin_concentration: float = 0.35,
) -> list[dict]:
    """Detecta backing plates de artefato nos extremos de cada eixo do bbox.

    Só retorna placas que são artefatos de geração (image-to-3D), não
    superfícies legítimas (ex.: tampo de mesa). A distinção é feita pela
    concentração de faces numa camada fina (2% da altura) no extremo:
    artefatos empacotam >35% de todas as faces nessa camada, superfícies
    legítimas distribuem faces de forma proporcional (~20%).
    """
    if len(mesh.faces) == 0:
        return []

    extents = [float(x) for x in mesh.extents]
    y_extent = extents[1]
    max_other = max(extents[0], extents[2])
    if y_extent > max_other * 1.5:
        return []

    normals = mesh.face_normals
    areas = mesh.area_faces
    centers = mesh.triangles_center
    bounds = mesh.bounds
    n_total = len(mesh.faces)

    plates: list[dict] = []
    ax_names = ["X", "Y", "Z"]

    for ax in range(3):
        lo = float(bounds[0, ax])
        hi = float(bounds[1, ax])
        h = hi - lo
        if h < 1e-8:
            continue

        other = [i for i in range(3) if i != ax]
        cross_area = float(extents[other[0]] * extents[other[1]])
        if cross_area < 1e-12:
            continue

        for side_label, band_lo, band_hi in [
            ("min", lo, lo + band_frac * h),
            ("max", hi - band_frac * h, hi),
        ]:
            in_band = (centers[:, ax] >= band_lo) & (centers[:, ax] <= band_hi)
            aligned = np.abs(normals[:, ax]) >= normal_align
            flat_area = float(areas[in_band & aligned].sum())
            coverage = flat_area / cross_area

            if coverage <= plate_coverage_threshold:
                continue

            if side_label == "min":
                thin_lo, thin_hi = lo, lo + 0.02 * h
            else:
                thin_lo, thin_hi = hi - 0.02 * h, hi
            in_thin = (centers[:, ax] >= thin_lo) & (centers[:, ax] <= thin_hi)
            thin_conc = int(np.count_nonzero(in_thin)) / n_total

            if thin_conc < min_thin_concentration:
                continue

            plates.append(
                {
                    "axis": ax,
                    "axis_name": ax_names[ax],
                    "side": side_label,
                    "coverage": round(coverage, 3),
                    "thin_concentration": round(thin_conc, 3),
                    "band_lo": band_lo,
                    "band_hi": band_hi,
                }
            )

    return plates


def _find_plate_boundary(
    mesh: trimesh.Trimesh,
    ax: int,
    side: str,
    *,
    layer_frac: float = 0.01,
    normal_align: float = 0.85,
    min_horizontal_pct: float = 0.70,
    max_layers: int = 15,
) -> float | None:
    """Avança camada a camada desde o extremo até a fronteira da placa."""
    bounds = mesh.bounds
    lo = float(bounds[0, ax])
    hi = float(bounds[1, ax])
    h = hi - lo
    if h < 1e-8:
        return None

    layer_depth = layer_frac * h
    centers = mesh.triangles_center
    normals = mesh.face_normals
    n_axis = np.abs(normals[:, ax])

    cut_coord = None
    for layer_i in range(max_layers):
        if side == "min":
            layer_lo = lo + layer_i * layer_depth
            layer_hi = lo + (layer_i + 1) * layer_depth
        else:
            layer_hi = hi - layer_i * layer_depth
            layer_lo = hi - (layer_i + 1) * layer_depth

        in_layer = (centers[:, ax] >= layer_lo) & (centers[:, ax] <= layer_hi)
        n_in = int(np.count_nonzero(in_layer))
        if n_in == 0:
            continue

        pct = int(np.count_nonzero(in_layer & (n_axis >= normal_align))) / n_in
        if pct >= min_horizontal_pct:
            cut_coord = layer_hi if side == "min" else layer_lo
        else:
            break

    return cut_coord


def _dilate_face_selection(mesh: trimesh.Trimesh, mask: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Expande seleção de faces por adjacência."""
    result = mask.copy()
    adj = mesh.face_adjacency
    for _ in range(iterations):
        new_mask = result.copy()
        for a, b in adj:
            a, b = int(a), int(b)
            if result[a] and not result[b]:
                new_mask[b] = True
            elif result[b] and not result[a]:
                new_mask[a] = True
        if np.array_equal(new_mask, result):
            break
        result = new_mask
    return result


def _cut_plate_faces(
    mesh: trimesh.Trimesh,
    plates: list[dict],
    *,
    margin_frac: float = 0.06,
    dilate_iters: int = 2,
    min_remaining_faces: int = 1000,
) -> trimesh.Trimesh:
    """Remove faces das placas + margem para cortar a interface."""
    if not plates or len(mesh.faces) == 0:
        return mesh

    m = mesh.copy()
    centers = m.triangles_center
    bounds = m.bounds
    remove_mask = np.zeros(len(m.faces), dtype=bool)

    for plate in plates:
        ax = plate["axis"]
        side = plate["side"]
        h = float(bounds[1, ax] - bounds[0, ax])
        margin = margin_frac * h

        cut = _find_plate_boundary(m, ax, side)
        if cut is None:
            cut = plate["band_hi"] if side == "min" else plate["band_lo"]

        spatial_mask = centers[:, ax] <= cut + margin if side == "min" else centers[:, ax] >= cut - margin

        remove_mask |= _dilate_face_selection(m, spatial_mask, iterations=dilate_iters)

    n_remove = int(np.count_nonzero(remove_mask))
    n_remaining = len(m.faces) - n_remove
    if n_remove == 0 or n_remaining < min_remaining_faces:
        return mesh

    keep = ~remove_mask
    sub = m.submesh([np.where(keep)[0]], append=True, only_watertight=False)
    if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
        sub.remove_unreferenced_vertices()
        parts = sub.split(only_watertight=False)
        if len(parts) > 1:
            main_size = max(len(p.faces) for p in parts)
            significant = [p for p in parts if len(p.faces) >= max(500, int(main_size * 0.03))]
            if significant:
                sub = trimesh.util.concatenate(significant)
                sub.remove_unreferenced_vertices()
        return sub

    return mesh


def _repair_plate_holes(
    mesh: trimesh.Trimesh,
    *,
    max_hole_size: int = 500,
    taubin_steps: int = 3,
    fillet_dilations: int = 4,
    fillet_smooth_steps: int = 12,
) -> trimesh.Trimesh:
    """Fecha buracos do corte de placa com fillet suavizado."""
    import tempfile
    from pathlib import Path

    import pymeshlab

    m = mesh
    boundary_before = _boundary_edge_count(m)
    if boundary_before <= 0:
        return m

    with tempfile.TemporaryDirectory(prefix="plate_repair_") as tmpdir:
        in_ply = str(Path(tmpdir) / "in.ply")
        out_ply = str(Path(tmpdir) / "out.ply")
        m.export(in_ply)

        ms = pymeshlab.MeshSet()
        ms.load_new_mesh(in_ply)

        ms.meshing_repair_non_manifold_edges()
        ms.meshing_repair_non_manifold_vertices()
        ms.meshing_remove_duplicate_faces()
        ms.meshing_remove_duplicate_vertices()
        ms.meshing_remove_null_faces()
        ms.meshing_remove_unreferenced_vertices()

        n_before_close = ms.current_mesh().face_number()
        ms.meshing_close_holes(maxholesize=max_hole_size)
        n_after_close = ms.current_mesh().face_number()
        n_patch = n_after_close - n_before_close

        if n_patch > 0 and fillet_smooth_steps > 0:
            ms.compute_selection_by_condition_per_face(condselect=f"(fi >= {n_before_close})")
            ms.compute_selection_transfer_face_to_vertex()
            for _ in range(fillet_dilations):
                ms.apply_selection_dilatation()
            ms.compute_selection_transfer_vertex_to_face()

            diag = ms.current_mesh().bounding_box().diagonal()
            target_edge = diag / 180
            ms.meshing_isotropic_explicit_remeshing(
                iterations=3,
                targetlen=pymeshlab.PureValue(target_edge),
                adaptive=True,
                selectedonly=True,
                checksurfdist=True,
                maxsurfdist=pymeshlab.PureValue(target_edge * 0.5),
            )

            ms.compute_selection_by_condition_per_face(condselect=f"(fi >= {n_before_close})")
            ms.compute_selection_transfer_face_to_vertex()
            for _ in range(fillet_dilations):
                ms.apply_selection_dilatation()

            ms.apply_coord_taubin_smoothing(
                stepsmoothnum=fillet_smooth_steps,
                lambda_=0.5,
                mu=-0.53,
                selected=True,
            )

        if taubin_steps > 0:
            ms.apply_coord_taubin_smoothing(
                stepsmoothnum=taubin_steps,
                lambda_=0.5,
                mu=-0.53,
            )

        ms.save_current_mesh(out_ply)
        m_new = trimesh.load(out_ply, force="mesh")
        if len(m_new.faces) > 0:
            m = m_new

    boundary_after = _boundary_edge_count(m)
    if boundary_after > 0:
        with contextlib.suppress(Exception):
            m = make_watertight(m)

    with contextlib.suppress(Exception):
        trimesh_repair.fix_normals(m, multibody=True)

    return m


def _plate_component_score(part: trimesh.Trimesh) -> float:
    """Pontua o quão "placa" uma componente parece (0 = não-placa, 1 = placa pura)."""
    e = sorted(float(x) for x in part.extents)
    if e[2] < 1e-9:
        return 1.0
    flat_ratio = e[0] / e[2]
    flatness_score = max(0.0, 1.0 - flat_ratio / 0.15)

    normals = part.face_normals
    areas = part.area_faces
    total_area = float(areas.sum())
    align_max = 0.0
    if total_area > 0:
        for ax in range(3):
            aligned = float(areas[np.abs(normals[:, ax]) >= 0.7].sum()) / total_area
            align_max = max(align_max, aligned)
    alignment_score = max(0.0, (align_max - 0.55) / 0.45)

    bbox_vol = float(e[0] * e[1] * e[2])
    if bbox_vol > 1e-12:
        try:
            ch_vol = float(part.convex_hull.volume)
        except Exception:
            ch_vol = 0.0
        vol_eff = ch_vol / bbox_vol
    else:
        vol_eff = 1.0
    vol_score = max(0.0, (vol_eff - 0.5) / 0.5)

    return (flatness_score * 0.5) + (alignment_score * 0.35) + (vol_score * 0.15)


def _remove_disconnected_plate_components(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Remove componentes desconexas que parecem placas (ex.: placas em "+")."""
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh, 0

    scored = [(p, _plate_component_score(p)) for p in parts]
    scored.sort(key=lambda x: x[1])

    kept: list[trimesh.Trimesh] = []
    n_removed = 0
    for p, score in scored:
        e = sorted(float(x) for x in p.extents)
        flat_ratio = e[0] / e[2] if e[2] > 1e-9 else 0
        is_plate = (flat_ratio < 0.05 and score > 0.4) or score > 0.7 or flat_ratio < 0.02
        if is_plate and len(kept) > 0:
            n_removed += 1
        else:
            kept.append(p)

    if not kept:
        kept = [scored[0][0]]

    if n_removed > 0:
        result = kept[0] if len(kept) == 1 else trimesh.util.concatenate(kept)
        result.remove_unreferenced_vertices()
        return result, n_removed
    return mesh, 0


def remove_backing_plates(
    mesh: trimesh.Trimesh,
    *,
    plate_coverage_threshold: float = 0.7,
    min_thin_concentration: float = 0.35,
    margin_frac: float = 0.06,
    fillet_smooth_steps: int = 12,
    fillet_dilations: int = 4,
) -> tuple[trimesh.Trimesh, dict]:
    """Remove backing plates de artefato e repara a mesh.

    Pipeline completa:
    1. Detecta placas de artefato (concentração de faces no extremo)
    2. Corta faces da placa + margem para remover interface
    3. Repara buracos com pymeshlab (fillet suavizado)
    4. Remove componentes desconexas que ainda parecem placas
    5. Verifica se restam placas conectadas (irrecuperável)

    Returns:
        Tupla ``(mesh, info)`` onde ``info`` contém:
        - ``plates_detected``: int
        - ``plates_removed``: int
        - ``components_removed``: int
        - ``needs_discard``: bool (placa conectada em outro eixo)
    """
    info: dict = {
        "plates_detected": 0,
        "plates_removed": 0,
        "components_removed": 0,
        "needs_discard": False,
    }

    plates = _detect_artifact_plates(
        mesh,
        plate_coverage_threshold=plate_coverage_threshold,
        min_thin_concentration=min_thin_concentration,
    )
    info["plates_detected"] = len(plates)

    if not plates:
        return mesh, info

    cleaned = _cut_plate_faces(mesh, plates, margin_frac=margin_frac)
    info["plates_removed"] = len(plates)

    repaired = _repair_plate_holes(
        cleaned,
        fillet_smooth_steps=fillet_smooth_steps,
        fillet_dilations=fillet_dilations,
    )

    repaired, n_comp = _remove_disconnected_plate_components(repaired)
    info["components_removed"] = n_comp

    if n_comp > 0 and not repaired.is_watertight:
        repaired = _repair_plate_holes(repaired, fillet_smooth_steps=0)

    original_keys = {(p["axis"], p["side"]) for p in plates}
    residual = _detect_artifact_plates(repaired, plate_coverage_threshold=plate_coverage_threshold)
    new_plates = [rp for rp in residual if (rp["axis"], rp["side"]) not in original_keys]
    info["needs_discard"] = len(new_plates) > 0

    return repaired, info


def _is_box_cage(part: trimesh.Trimesh, *, cardinal_ratio_threshold: float = 0.85) -> bool:
    """Detecta se uma componente é uma caixa/jaula (normais quase todas cardinais).

    Uma jaula tem >85% da área de superfície com normais alinhadas a eixos cardinais
    (±X, ±Y, ±Z). Modelos reais têm normais variadas (curvas, formas orgânicas).
    """
    if len(part.faces) < 12:
        return False
    try:
        normals = part.face_normals
        areas = part.area_faces
        total_area = float(areas.sum())
        if total_area < 1e-12:
            return False
        cardinal = np.any(np.abs(normals) >= 0.85, axis=1)
        cardinal_ratio = float(areas[cardinal].sum() / total_area)
        if cardinal_ratio < cardinal_ratio_threshold:
            return False
        # Elongated shapes (pillars, poles, swords) are never cages — a cage must be
        # roughly cubic to surround another model.
        extents_sorted = sorted(float(x) for x in part.extents)
        return extents_sorted[2] / max(extents_sorted[0], 1e-9) <= 2.5
    except Exception:
        return False


def _try_remove_single_component_cage(
    mesh: trimesh.Trimesh,
    *,
    band_frac: float = 0.06,
    cardinal_threshold: float = 0.70,
    min_face_ratio: float = 0.30,
) -> trimesh.Trimesh:
    """Tenta remover faces de jaula/caixa quando o mesh é um único componente.

    Remove faces dentro de ``band_frac`` de qualquer face do bbox cujas normais
    estejam alinhadas a esse eixo (|n_ax| >= cardinal_threshold). Depois faz split
    e fica com componentes não-cage. Aborta se remover < min_face_ratio das faces.

    Não faz pré-gate com ``_is_box_cage``: o mesh misto (modelo + cage) pode não
    passar no teste de cardinalidade global, mas as paredes da jaula ainda têm
    normais fortemente cardinais.

    No entanto, rejeita meshes que não são aproximadamente cúbicos (cube_ratio > 2.5):
    uma cage deve ser cúbica para envolver outro modelo; meshes alongados (como crates)
    não podem conter uma cage interna significativa.
    """
    try:
        # Quick check: a cage must be roughly cubic to surround another model.
        extents_sorted = sorted(float(x) for x in mesh.extents)
        if extents_sorted[2] / max(extents_sorted[0], 1e-9) > 2.5:
            return mesh

        bounds = mesh.bounds
        centers = mesh.triangles_center
        normals = mesh.face_normals
        n_faces = len(mesh.faces)
        remove = np.zeros(n_faces, dtype=bool)

        for ax in range(3):
            lo = float(bounds[0, ax])
            hi = float(bounds[1, ax])
            h = hi - lo
            if h < 1e-9:
                continue
            ax_aligned = np.abs(normals[:, ax]) >= cardinal_threshold
            near_lo = centers[:, ax] <= lo + band_frac * h
            near_hi = centers[:, ax] >= hi - band_frac * h
            remove |= ax_aligned & (near_lo | near_hi)

        n_remove = int(np.count_nonzero(remove))
        if n_remove == 0:
            return mesh

        keep = ~remove
        try:
            sub = mesh.submesh([np.where(keep)[0]], append=True, only_watertight=False)
        except Exception:
            return mesh
        if not isinstance(sub, trimesh.Trimesh) or len(sub.faces) < 12:
            return mesh

        parts = sub.split(only_watertight=False)
        if len(parts) <= 1:
            return sub if not _is_box_cage(sub) else mesh

        parts_is_cage = [_is_box_cage(p) for p in parts]

        kept = [p for p, cage in zip(parts, parts_is_cage, strict=True) if not cage]
        if not kept:
            return mesh
        if len(kept) == 1:
            return kept[0]
        with contextlib.suppress(Exception):
            return trimesh.util.concatenate(kept)
        return sub
    except Exception:
        return mesh


def _remove_box_cage_components(mesh: trimesh.Trimesh) -> trimesh.Trimesh:
    """Remove componentes caixa/jaula que envolvem o modelo real.

    Só remove componentes cage quando há pelo menos uma componente não-cage
    restante (o modelo actual).
    """
    try:
        parts = mesh.split(only_watertight=False)
    except Exception:
        return mesh
    if len(parts) <= 1:
        return _try_remove_single_component_cage(mesh)

    is_cage = [_is_box_cage(p) for p in parts]

    has_model = any(not c for c in is_cage)
    if not has_model:
        return mesh

    kept = [p for p, cage in zip(parts, is_cage, strict=True) if not cage]
    if not kept:
        return mesh
    if len(kept) == 1:
        return kept[0]
    try:
        return trimesh.util.concatenate(kept)
    except Exception:
        return mesh


def repair_mesh(
    mesh: trimesh.Trimesh,
    *,
    repair_mode: Literal["light", "full"] = "light",
    topology_prep: bool = True,
    keep_largest: bool = True,
    merge_vertices: bool = True,
    remove_ground_shadow: bool = True,
    ground_artifact_mesh_space: Literal["hunyuan", "y_up"] = "hunyuan",
    ground_artifact_y_up_flip_x_rad: float = 0.0,
    ground_shadow_aggressive: bool = False,
    ground_shadow_very_aggressive: bool = False,
    remove_small_island_fragments: bool = True,
    small_island_min_face_ratio: float = 0.0002,
    small_island_min_faces_abs: int = 48,
    fill_small_holes_max_edges: int = 16,
    smooth_iterations: int = 0,
    smooth_lamb: float = 0.45,
    remesh: bool = False,
    remesh_resolution: int = 180,
    remesh_iterations: int = 6,
    remesh_max_surf_dist_factor: float = 0.38,
    remesh_taubin_steps: int = 3,
    watertight: bool = True,
    taubin_smooth_steps: int = 0,
) -> trimesh.Trimesh:
    """Pipeline de reparo completa: pedestais → topologia → watertight → remesh → smooth.

    ``repair_mode``:
    - ``"light"`` (defeito): apenas ``prepare_mesh_topology`` (merge vertices +
      fix normals + remove unreferenced). Rápido, preserva geometria.
    - ``"full"``: pipeline completa de 10 passos (sombras, ilhas, watertight,
      remesh, smooth).

    Args:
        repair_mode: ``"light"`` para topologia leve, ``"full"`` para pipeline completa.
        topology_prep: Se ``True`` (defeito), aplica :func:`prepare_mesh_topology` após
            pedestais/ilhas e evita duplicar merge+manifold soltos.
    """
    m = mesh.copy()

    if repair_mode == "light":
        with contextlib.suppress(Exception):
            m = prepare_mesh_topology(m)
        with contextlib.suppress(Exception):
            m.remove_unreferenced_vertices()
        return m

    # 1. Remoção de sombras/pedestais
    if remove_ground_shadow:
        with contextlib.suppress(Exception):
            m = remove_ground_shadow_artifacts(
                m,
                mesh_space=ground_artifact_mesh_space,
                y_up_flip_x_rad=ground_artifact_y_up_flip_x_rad,
                aggressive=ground_shadow_aggressive and not ground_shadow_very_aggressive,
                very_aggressive=ground_shadow_very_aggressive,
            )

    # 2. Remoção de ilhas minúsculas
    if remove_small_island_fragments:
        try:
            ratio = float(small_island_min_face_ratio)
            abs_m = int(small_island_min_faces_abs)
            if ground_shadow_aggressive or ground_shadow_very_aggressive:
                ratio = max(ratio, 0.0018)
                abs_m = max(abs_m, 256)
            m = remove_small_islands(m, min_face_ratio=ratio, min_faces_abs=abs_m)
        except Exception:
            pass

    # 2.5. Remover caixas/jaulas que envolvem o modelo
    with contextlib.suppress(Exception):
        m = _remove_box_cage_components(m)

    # 3. Topologia: weld/manifold depois de cortar pedestais — solda paredes
    #    duplas do marching cubes e corrige arestas non-manifold.
    if topology_prep:
        with contextlib.suppress(Exception):
            m = prepare_mesh_topology(m)
    else:
        if merge_vertices:
            with contextlib.suppress(Exception):
                m.merge_vertices()
        with contextlib.suppress(Exception):
            m = manifold_repair(m)

    # 4. Fechar rachas minúsculas (marching cubes): solidifica paredes grossas
    #    sem tocar em aberturas estruturais. Após este passo as paredes espessas
    #    (ex. crate) ficam reconhecidas como sólidas.
    with contextlib.suppress(Exception):
        _fill_small_boundary_holes_inplace(m, max(fill_small_holes_max_edges, 16))

    # 5. Watertight — inteligente: detecta aberturas estruturais em QUALQUER
    #    posição (base, topo, laterais) antes de make_watertight; depois remove
    #    faces que o watertight criou para selar essas aberturas, preservando-as.
    _has_structural_opening = False
    if watertight:
        structural_openings = _detect_structural_openings(m)
        _has_structural_opening = len(structural_openings) > 0
        if _has_structural_opening:
            n_faces_before = len(m.faces)

            # Guardar região espacial de cada abertura (centróide + raio aproximado)
            opening_regions: list[dict] = []
            for op in structural_openings:
                ax = op["axis"]
                side = op["side"]
                centroid = op["centroid"]
                verts = m.vertices[op["vertex_ids"]]
                if len(verts) < 3:
                    continue
                # Raio aproximado do buraco (desvio-padrão dos vértices no plano perpendicular)
                ax1, ax2 = (ax + 1) % 3, (ax + 2) % 3
                spread = max(float(verts[:, ax1].std()), float(verts[:, ax2].std()), 1e-9)
                opening_regions.append(
                    {
                        "axis": ax,
                        "side": side,
                        "centroid": centroid,
                        "radius": spread * 1.5,
                    }
                )

            with contextlib.suppress(Exception):
                m = make_watertight(m, max_hole_edges=max(fill_small_holes_max_edges, 500))

            # Remover faces que o watertight adicionou perto de cada abertura
            if len(m.faces) > n_faces_before and opening_regions:
                centers = m.triangles_center
                to_remove = np.zeros(len(m.faces), dtype=bool)
                for region in opening_regions:
                    ax = region["axis"]
                    centroid = region["centroid"]
                    r = region["radius"]
                    ax1, ax2 = (ax + 1) % 3, (ax + 2) % 3
                    dx = centers[:, ax1] - centroid[ax1]
                    dz = centers[:, ax2] - centroid[ax2]
                    dist_perp = np.sqrt(dx * dx + dz * dz)
                    near_opening = dist_perp <= r

                    # Faces na banda espacial perto do centróide da abertura
                    n_ax = np.abs(m.face_normals[:, ax])
                    in_region = near_opening & (n_ax > 0.75)
                    to_remove |= in_region

                n_new_faces = int(np.count_nonzero(to_remove))
                if 0 < n_new_faces < len(m.faces) * 0.35:
                    keep = ~to_remove
                    with contextlib.suppress(Exception):
                        sub = m.submesh([np.where(keep)[0]], append=True, only_watertight=False)
                        if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
                            m = sub
                            m.remove_unreferenced_vertices()
        else:
            n_boundary = _boundary_edge_count(m)
            if n_boundary > 200:
                # Cross-sectioned mesh: full make_watertight over-decimates.
                # Try pymeshlab close_holes only (no pymeshfix clean/remesh).
                m_backup = m.copy()
                with contextlib.suppress(Exception):
                    m = make_watertight(m, max_hole_edges=max(fill_small_holes_max_edges, 500))
                if len(m.faces) < 0.70 * len(m_backup.faces):
                    # Over-decimated — revert and try gentle fill via pymeshfix.
                    m = m_backup
                    with contextlib.suppress(Exception):
                        m = _pymeshfix_fill_gentle(m)
            else:
                with contextlib.suppress(Exception):
                    m = make_watertight(m, max_hole_edges=max(fill_small_holes_max_edges, 500))
    elif fill_small_holes_max_edges > 0:
        with contextlib.suppress(Exception):
            _fill_small_boundary_holes_inplace(m, fill_small_holes_max_edges)

    # 5. Spikes na base (very_aggressive pós-watertight)
    if ground_shadow_very_aggressive:
        with contextlib.suppress(Exception):
            m = _remove_spikes_and_repair(m)

    # 6. Remover componentes-placa separadas
    with contextlib.suppress(Exception):
        m = remove_plate_components(m)

    # 7. Keep best component
    if keep_largest:
        m = keep_largest_component(m)

    # 8. Isotropic remesh (após topologia estável e maior componente)
    if remesh:
        with contextlib.suppress(Exception):
            m = isotropic_remesh(
                m,
                resolution=remesh_resolution,
                iterations=remesh_iterations,
                max_surf_dist_factor=remesh_max_surf_dist_factor,
                close_holes=not _has_structural_opening,
                taubin_steps=remesh_taubin_steps,
            )

    # 9. Taubin smoothing volume-preserving
    if taubin_smooth_steps > 0:
        with contextlib.suppress(Exception):
            m = taubin_smooth(m, iterations=taubin_smooth_steps)

    # 10. Laplacian legacy
    if smooth_iterations > 0:
        m = laplacian_smooth(m, iterations=smooth_iterations, lamb=smooth_lamb)

    with contextlib.suppress(Exception):
        m.remove_unreferenced_vertices()

    return m
