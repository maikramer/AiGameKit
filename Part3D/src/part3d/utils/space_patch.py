"""Patches textuais ao código do Space HF ``tencent/Hunyuan3D-Part``.

O Space assume Docker + GPUs grandes e tem heurísticas que fundem partes
distintas (porta+moldura). Estas funções transformam o conteúdo dos ficheiros
fonte no cache HF:

* ``transform_p3sam_model`` — download root do sonata fora de ``/root``.
* ``transform_auto_mask`` — hardcodes de point/prompt, batch de inferência,
  cutoffs anti-fuse, bbox-IoU de merge de clusters, NMS vectorizado (matmul)
  e overrides rápidos de ``fix_label`` (numba) / ``get_connected_region``
  (scipy csgraph) — os originais são O(N²) em Python puro.
* ``transform_surface_extractors`` — marching cubes precisa float32.

Todas são puras (content → content) para testabilidade; a escrita em ficheiro
faz unlink de symlinks para não corromper blobs partilhados do cache HF.
"""

from __future__ import annotations

import os
import re

PERF_MARKER = "GAMEDEV_PERF_PATCH_V1"
NMS_MARKER = "GAMEDEV_NMS_VECTORIZED"

_NMS_ORIG = """\
    with Timer("NMS"):
        clusters = defaultdict(list)
        with ThreadPoolExecutor(max_workers=20) as executor:
            for i in tqdm(range(prompt_num), desc="NMS", disable=not show_info):
                _mask = mask_sorted[i]
                futures = []
                for j in clusters.keys():
                    futures.append(executor.submit(cal_iou, _mask, mask_sorted[j]))

                for j, future in zip(clusters.keys(), futures):
                    if future.result() > 0.9:
                        clusters[j].append(i)
                        break
                else:
                    clusters[i].append(i)
"""

_NMS_FAST = """\
    with Timer("NMS"):
        # GAMEDEV_NMS_VECTORIZED — IoU par-a-par via matmul; mesma semântica greedy.
        clusters = defaultdict(list)
        _gd_masks = np.stack(mask_sorted, axis=0).astype(np.float32)
        _gd_inter = _gd_masks @ _gd_masks.T
        _gd_areas = _gd_masks.sum(axis=1)
        _gd_union = _gd_areas[:, None] + _gd_areas[None, :] - _gd_inter
        _gd_iou = _gd_inter / np.maximum(_gd_union, 1e-9)
        for i in range(prompt_num):
            for j in clusters.keys():
                if _gd_iou[i, j] > 0.9:
                    clusters[j].append(i)
                    break
            else:
                clusters[i].append(i)
"""

# Overrides rápidos: definidos APÓS os originais, capturam-nos por nome antes
# de os sombrear a nível de módulo (mesh_sam resolve globals em call-time).
_PERF_TAIL = """

# GAMEDEV_PERF_PATCH_V1 — overrides rápidos (numba fix_label, scipy connected regions).
_gamedev_fix_label_orig = fix_label
_gamedev_get_connected_region_orig = get_connected_region


@njit
def _gamedev_fix_label_kernel(face_ids, adjacent_faces):
    faces_max = adjacent_faces.shape[0]
    max_deg = adjacent_faces.shape[1]
    n = face_ids.shape[0]
    pending = np.empty(n, dtype=np.int64)
    cnt = 0
    for i in range(n):
        if face_ids[i] < 0 and 0 <= i < faces_max:
            pending[cnt] = i
            cnt += 1
    labels_buf = np.empty(max_deg, dtype=np.int64)
    for _loop in range(50):
        changed = False
        new_cnt = 0
        for pi in range(cnt):
            i = pending[pi]
            k = 0
            for d in range(max_deg):
                j = adjacent_faces[i, d]
                if j == -1:
                    break
                if face_ids[j] >= 0:
                    labels_buf[k] = face_ids[j]
                    k += 1
            if k == 0:
                pending[new_cnt] = i
                new_cnt += 1
                continue
            best_label = np.int64(-1)
            best_count = 0
            for a in range(k):
                la = labels_buf[a]
                c = 0
                for b in range(k):
                    if labels_buf[b] == la:
                        c += 1
                if c > best_count or (c == best_count and la < best_label):
                    best_count = c
                    best_label = la
            face_ids[i] = best_label
            changed = True
        cnt = new_cnt
        if not changed or cnt == 0:
            break
    return face_ids


def fix_label(face_ids, adjacent_faces, use_aabb=False, mesh=None, show_info=False):
    if use_aabb:
        return _gamedev_fix_label_orig(
            face_ids, adjacent_faces, use_aabb=use_aabb, mesh=mesh, show_info=show_info
        )
    try:
        ids = np.ascontiguousarray(np.asarray(face_ids, dtype=np.int64))
        adj = np.ascontiguousarray(np.asarray(adjacent_faces, dtype=np.int64))
        out = _gamedev_fix_label_kernel(ids, adj)
        if isinstance(face_ids, np.ndarray) and out is not face_ids:
            face_ids[...] = out
            return face_ids
        return out
    except Exception:
        return _gamedev_fix_label_orig(
            face_ids, adjacent_faces, use_aabb=use_aabb, mesh=mesh, show_info=show_info
        )


def get_connected_region(face_ids, adjacent_faces, return_face_part_ids=False):
    try:
        from scipy.sparse import coo_matrix
        from scipy.sparse.csgraph import connected_components

        fids = np.asarray(face_ids)
        n = fids.shape[0]
        fm = min(n, adjacent_faces.shape[0])
        adj = np.asarray(adjacent_faces[:fm])
        rows = np.repeat(np.arange(fm, dtype=np.int64), adj.shape[1])
        cols = adj.reshape(-1).astype(np.int64)
        keep = (cols >= 0) & (cols < n)
        rows, cols = rows[keep], cols[keep]
        same = fids[rows] == fids[cols]
        rows, cols = rows[same], cols[same]
        graph = coo_matrix(
            (np.ones(rows.shape[0], dtype=np.int8), (rows, cols)), shape=(n, n)
        )
        n_comp, comp = connected_components(graph, directed=False)
        # Reordenar componentes por primeira ocorrência (= ordem BFS original).
        first_seen = np.full(n_comp, n, dtype=np.int64)
        np.minimum.at(first_seen, comp, np.arange(n, dtype=np.int64))
        order_of_comp = np.empty(n_comp, dtype=np.int64)
        order_of_comp[np.argsort(first_seen, kind="stable")] = np.arange(n_comp)
        comp = order_of_comp[comp]
        sort_idx = np.argsort(comp, kind="stable")
        sorted_comp = comp[sort_idx]
        starts = np.searchsorted(sorted_comp, np.arange(n_comp), side="left")
        ends = np.searchsorted(sorted_comp, np.arange(n_comp), side="right")
        parts = [sort_idx[starts[c] : ends[c]].tolist() for c in range(n_comp)]
        if return_face_part_ids:
            return parts, comp.astype(np.int64)
        return parts
    except Exception:
        return _gamedev_get_connected_region_orig(
            face_ids, adjacent_faces, return_face_part_ids
        )
"""


def transform_p3sam_model(content: str, sonata_root: str) -> str:
    """Corrige ``download_root='/root/sonata'`` para um caminho gravável."""
    return content.replace(
        "download_root='/root/sonata'",
        f"download_root='{sonata_root}'",
    )


def transform_surface_extractors(content: str) -> str:
    """Marching cubes precisa float32 (não BF16) antes de ``.numpy()``."""
    if "grid_logit.float().cpu().numpy()" in content:
        return content
    return content.replace(
        "grid_logit.cpu().numpy()",
        "grid_logit.float().cpu().numpy()",
    )


def transform_auto_mask(
    content: str,
    *,
    part_area_merge: float,
    area_ratio_keep: float,
    bbox_merge_iou: float,
) -> str:
    """Aplica todos os patches ao ``auto_mask_api.py`` do Space."""
    # mesh_sam() ignora os argumentos e hardcoda point/prompt — remover.
    for bad_line in (
        "    point_num = 100000\n",
        "    prompt_num = 400\n",
    ):
        content = content.replace(bad_line, "")

    # get_mask() com bs alto explode a VRAM em ~6 GB.
    for old_bs in ("        bs = 64\n", "        bs = 8\n"):
        content = content.replace(old_bs, "        bs = 4\n")

    # Anti-fuse: painel de porta (~0.3-1% área) era fundido na moldura.
    content = re.sub(
        r"part_areas\[i\]\s*<\s*[0-9.]+",
        f"part_areas[i] < {part_area_merge}",
        content,
        count=1,
    )

    # Manter ilhas finas (moldura/painel) antes do fix_label.
    content = re.sub(
        r"area\s*/\s*\(cp_area\s*\+\s*1e-7\)\s*>\s*[0-9.]+",
        f"area / (cp_area + 1e-7) > {area_ratio_keep}",
        content,
        count=1,
    )
    content = re.sub(
        r"_area\s*/\s*mesh_total_area\s*>\s*[0-9.]+",
        f"_area / mesh_total_area > {area_ratio_keep}",
        content,
        count=1,
    )

    # Merge de clusters por bbox-IoU: 0.5 upstream agrega porta + vizinhança.
    content = re.sub(
        r"(cal_bbox_iou\(\s*_points,\s*mask_sorted\[tar_cluster\],"
        r"\s*mask_sorted\[cur_cluster\]\s*\)\s*>\s*)[0-9.]+",
        rf"\g<1>{bbox_merge_iou}",
        content,
        count=1,
    )

    # NMS O(K²·N) com ThreadPoolExecutor → matmul vectorizado.
    if NMS_MARKER not in content and _NMS_ORIG in content:
        content = content.replace(_NMS_ORIG, _NMS_FAST, 1)

    # Overrides rápidos de fix_label / get_connected_region.
    if PERF_MARKER not in content:
        content = content.rstrip("\n") + "\n" + _PERF_TAIL

    return content


def write_if_changed(path: str, content: str) -> bool:
    """Escreve ``content`` se diferente; unlink de symlink antes (blobs HF)."""
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == content:
                return False
    except OSError:
        pass
    if os.path.islink(path):
        os.unlink(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def apply_space_patches(
    space_dir: str,
    *,
    part_area_merge: float,
    area_ratio_keep: float,
    bbox_merge_iou: float,
    sonata_root: str | None = None,
) -> list[str]:
    """Aplica os patches aos ficheiros do Space. Devolve os caminhos alterados."""
    if sonata_root is None:
        sonata_root = os.path.join(os.path.expanduser("~"), ".cache", "sonata")

    changed: list[str] = []

    p3sam_model = os.path.join(space_dir, "P3-SAM", "model.py")
    if os.path.isfile(p3sam_model):
        with open(p3sam_model, encoding="utf-8") as f:
            content = f.read()
        if write_if_changed(p3sam_model, transform_p3sam_model(content, sonata_root)):
            changed.append(p3sam_model)

    api_file = os.path.join(space_dir, "XPart", "partgen", "bbox_estimator", "auto_mask_api.py")
    if os.path.isfile(api_file):
        with open(api_file, encoding="utf-8") as f:
            content = f.read()
        new_content = transform_auto_mask(
            content,
            part_area_merge=part_area_merge,
            area_ratio_keep=area_ratio_keep,
            bbox_merge_iou=bbox_merge_iou,
        )
        if write_if_changed(api_file, new_content):
            changed.append(api_file)

    surf_file = os.path.join(space_dir, "XPart", "partgen", "models", "autoencoders", "surface_extractors.py")
    if os.path.isfile(surf_file):
        with open(surf_file, encoding="utf-8") as f:
            content = f.read()
        if write_if_changed(surf_file, transform_surface_extractors(content)):
            changed.append(surf_file)

    return changed
