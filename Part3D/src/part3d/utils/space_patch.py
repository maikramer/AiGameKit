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

PERF_MARKER = "AIGAMEKIT_PERF_PATCH_V1"
NMS_MARKER = "AIGAMEKIT_NMS_VECTORIZED"
NMS_CONSENSUS_MARKER = "AIGAMEKIT_NMS_CONSENSUS"
MULTIHEAD_MARKER = "AIGAMEKIT_MULTIHEAD"
POOL_SORT_MARKER = "AIGAMEKIT_POOL_SORT"
VOTE_ASSIGN_MARKER = "AIGAMEKIT_VOTE_ASSIGN"
QUALITY_MARKER = "AIGAMEKIT_MASK_QUALITY_V3"
_OLD_QUALITY_PREFIXES = (
    "\n\n# AIGAMEKIT_MASK_QUALITY_V2 —",
    "\n\n# AIGAMEKIT_MASK_QUALITY_V3 —",
)

_QUALITY_TAIL = """

# AIGAMEKIT_MASK_QUALITY_V3 — multi-head pool + consensus NMS + vote assign.
AIGAMEKIT_MASK_NMS_IOU = {mask_nms_iou}
AIGAMEKIT_SECONDARY_MASK_IOU = {secondary_mask_iou}
AIGAMEKIT_MIN_CLUSTER_SUPPORT = {min_cluster_support}
AIGAMEKIT_MIN_PREDICTED_IOU = {min_predicted_iou}
AIGAMEKIT_PROMPT_BATCH_SIZE = {prompt_batch_size}
AIGAMEKIT_BBOX_MERGE_IOU = {bbox_merge_iou}
AIGAMEKIT_MULTI_HEAD = {multi_head}
AIGAMEKIT_HEAD_MIN_SCORE = {head_min_score}
AIGAMEKIT_HEAD_SCORE_RATIO = {head_score_ratio}
AIGAMEKIT_CONSENSUS = {consensus}
AIGAMEKIT_CONSENSUS_VOTE = {consensus_vote}


def _aigamekit_cluster_support(members, prompt_ids_sorted):
    try:
        from part3d.utils.mask_consensus import distinct_prompt_support
        return distinct_prompt_support(members, prompt_ids_sorted)
    except Exception:
        return len(members)


def configure_aigamekit_mask_quality(
    *,
    mask_nms_iou=0.9,
    secondary_mask_iou=0.25,
    min_cluster_support=3,
    min_predicted_iou=0.75,
    prompt_batch_size=4,
    bbox_merge_iou=0.7,
    multi_head=True,
    head_min_score=0.5,
    head_score_ratio=0.85,
    consensus=True,
    consensus_vote=0.5,
):
    global AIGAMEKIT_MASK_NMS_IOU
    global AIGAMEKIT_SECONDARY_MASK_IOU
    global AIGAMEKIT_MIN_CLUSTER_SUPPORT
    global AIGAMEKIT_MIN_PREDICTED_IOU
    global AIGAMEKIT_PROMPT_BATCH_SIZE
    global AIGAMEKIT_BBOX_MERGE_IOU
    global AIGAMEKIT_MULTI_HEAD
    global AIGAMEKIT_HEAD_MIN_SCORE
    global AIGAMEKIT_HEAD_SCORE_RATIO
    global AIGAMEKIT_CONSENSUS
    global AIGAMEKIT_CONSENSUS_VOTE
    AIGAMEKIT_MASK_NMS_IOU = float(mask_nms_iou)
    AIGAMEKIT_SECONDARY_MASK_IOU = float(secondary_mask_iou)
    AIGAMEKIT_MIN_CLUSTER_SUPPORT = max(1, int(min_cluster_support))
    AIGAMEKIT_MIN_PREDICTED_IOU = float(min_predicted_iou)
    AIGAMEKIT_PROMPT_BATCH_SIZE = max(1, int(prompt_batch_size))
    AIGAMEKIT_BBOX_MERGE_IOU = float(bbox_merge_iou)
    AIGAMEKIT_MULTI_HEAD = bool(multi_head)
    AIGAMEKIT_HEAD_MIN_SCORE = float(head_min_score)
    AIGAMEKIT_HEAD_SCORE_RATIO = float(head_score_ratio)
    AIGAMEKIT_CONSENSUS = bool(consensus)
    AIGAMEKIT_CONSENSUS_VOTE = float(consensus_vote)
"""

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
        # AIGAMEKIT_NMS_VECTORIZED — IoU par-a-par via matmul; mesma semântica greedy.
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

_NMS_CONSENSUS = """\
    with Timer("NMS"):
        # AIGAMEKIT_NMS_CONSENSUS — best-fit NMS + IoU-weighted cluster fuse.
        from part3d.utils.mask_consensus import (
            cluster_masks_bestfit,
            fuse_cluster_mask,
        )
        if AIGAMEKIT_CONSENSUS:
            clusters = cluster_masks_bestfit(mask_sorted, nms_iou=AIGAMEKIT_MASK_NMS_IOU)
            for _gd_rep, _gd_members in list(clusters.items()):
                mask_sorted[_gd_rep] = fuse_cluster_mask(
                    mask_sorted,
                    iou_sorted,
                    _gd_members,
                    vote=AIGAMEKIT_CONSENSUS_VOTE,
                )
        else:
            clusters = defaultdict(list)
            _gd_masks = np.stack(mask_sorted, axis=0).astype(np.float32)
            _gd_inter = _gd_masks @ _gd_masks.T
            _gd_areas = _gd_masks.sum(axis=1)
            _gd_union = _gd_areas[:, None] + _gd_areas[None, :] - _gd_inter
            _gd_iou = _gd_inter / np.maximum(_gd_union, 1e-9)
            for i in range(len(mask_sorted)):
                for j in clusters.keys():
                    if _gd_iou[i, j] > AIGAMEKIT_MASK_NMS_IOU:
                        clusters[j].append(i)
                        break
                else:
                    clusters[i].append(i)
"""

_WINNER_TAKE_ALL = """\
            pred_mask = np.stack(
                [pred_mask_1, pred_mask_2, pred_mask_3], axis=-1
            )  # [N, K, 3]
            max_idx = np.argmax(pred_iou, axis=-1)  # [K]
            for j in range(max_idx.shape[0]):
                mask_res.append(pred_mask[:, j, max_idx[j]])
                iou_res.append(pred_iou[j, max_idx[j]])
"""

_MULTIHEAD_COLLECT = """\
            pred_mask = np.stack(
                [pred_mask_1, pred_mask_2, pred_mask_3], axis=-1
            )  # [N, K, 3]
            # AIGAMEKIT_MULTIHEAD — keep near-best heads instead of argmax-only.
            from part3d.utils.mask_consensus import collect_batch_heads
            _gd_m, _gd_i, _gd_p = collect_batch_heads(
                pred_mask,
                pred_iou,
                bs * i,
                multi_head=AIGAMEKIT_MULTI_HEAD,
                min_score=AIGAMEKIT_HEAD_MIN_SCORE,
                score_ratio=AIGAMEKIT_HEAD_SCORE_RATIO,
            )
            mask_res.extend(_gd_m)
            iou_res.extend(_gd_i)
            aigamekit_prompt_ids.extend(_gd_p)
"""

_POOL_SORT_ORIG = """\
    with Timer("根据IOU排序"):
        iou_res = np.array(iou_res).tolist()
        mask_iou = [[mask_res[:, i], iou_res[i]] for i in range(prompt_num)]
        mask_iou_sorted = sorted(mask_iou, key=lambda x: x[1], reverse=True)
        mask_sorted = [mask_iou_sorted[i][0] for i in range(prompt_num)]
        iou_sorted = [mask_iou_sorted[i][1] for i in range(prompt_num)]
"""

_POOL_SORT_NEW = """\
    with Timer("根据IOU排序"):
        # AIGAMEKIT_POOL_SORT — pool size may exceed prompt_num (multi-head).
        iou_res = np.array(iou_res).tolist()
        _gd_n = int(mask_res.shape[1]) if hasattr(mask_res, "shape") else len(mask_res)
        if not aigamekit_prompt_ids:
            aigamekit_prompt_ids.extend(list(range(_gd_n)))
        mask_iou = [
            [mask_res[:, i], iou_res[i], aigamekit_prompt_ids[i]] for i in range(_gd_n)
        ]
        mask_iou_sorted = sorted(mask_iou, key=lambda x: x[1], reverse=True)
        mask_sorted = [mask_iou_sorted[i][0] for i in range(_gd_n)]
        iou_sorted = [mask_iou_sorted[i][1] for i in range(_gd_n)]
        aigamekit_prompt_ids_sorted = [mask_iou_sorted[i][2] for i in range(_gd_n)]
"""

_VOTE_ASSIGN_ORIG = """\
        result_mask = -np.ones(point_num, dtype=np.int64)
        for i in final_mask_sorted:
            part_mask = mask_sorted[i]
            result_mask[part_mask] = i
"""

_VOTE_ASSIGN_NEW = """\
        # AIGAMEKIT_VOTE_ASSIGN — soft vote across final masks; smaller parts win ties.
        from part3d.utils.mask_consensus import assign_points_by_vote
        if AIGAMEKIT_CONSENSUS and final_mask_sorted:
            _gd_vote_masks = [mask_sorted[i] for i in final_mask_sorted]
            _gd_vote_ious = [iou_sorted[i] for i in final_mask_sorted]
            _gd_local = assign_points_by_vote(_gd_vote_masks, _gd_vote_ious, prefer_small=True)
            result_mask = -np.ones(point_num, dtype=np.int64)
            _gd_hit = _gd_local >= 0
            result_mask[_gd_hit] = np.asarray(final_mask_sorted, dtype=np.int64)[_gd_local[_gd_hit]]
        else:
            result_mask = -np.ones(point_num, dtype=np.int64)
            for i in final_mask_sorted:
                part_mask = mask_sorted[i]
                result_mask[part_mask] = i
"""

# Overrides rápidos: definidos APÓS os originais, capturam-nos por nome antes
# de os sombrear a nível de módulo (mesh_sam resolve globals em call-time).
_PERF_TAIL = """

# AIGAMEKIT_PERF_PATCH_V1 — overrides rápidos (numba fix_label, scipy connected regions).
_aigamekit_fix_label_orig = fix_label
_aigamekit_get_connected_region_orig = get_connected_region


@njit
def _aigamekit_fix_label_kernel(face_ids, adjacent_faces):
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
        return _aigamekit_fix_label_orig(
            face_ids, adjacent_faces, use_aabb=use_aabb, mesh=mesh, show_info=show_info
        )
    try:
        ids = np.ascontiguousarray(np.asarray(face_ids, dtype=np.int64))
        adj = np.ascontiguousarray(np.asarray(adjacent_faces, dtype=np.int64))
        out = _aigamekit_fix_label_kernel(ids, adj)
        if isinstance(face_ids, np.ndarray) and out is not face_ids:
            face_ids[...] = out
            return face_ids
        return out
    except Exception:
        return _aigamekit_fix_label_orig(
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
        return _aigamekit_get_connected_region_orig(
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


def _strip_old_quality_tail(content: str) -> str:
    for prefix in _OLD_QUALITY_PREFIXES:
        idx = content.find(prefix)
        if idx >= 0:
            return content[:idx].rstrip("\n") + "\n"
    return content


def transform_auto_mask(
    content: str,
    *,
    part_area_merge: float,
    area_ratio_keep: float,
    bbox_merge_iou: float,
    mask_nms_iou: float = 0.9,
    secondary_mask_iou: float = 0.25,
    min_cluster_support: int = 3,
    min_predicted_iou: float = 0.75,
    prompt_batch_size: int = 4,
    multi_head: bool = True,
    head_min_score: float = 0.5,
    head_score_ratio: float = 0.85,
    consensus: bool = True,
    consensus_vote: float = 0.5,
) -> str:
    """Aplica todos os patches ao ``auto_mask_api.py`` do Space."""
    # mesh_sam() ignora os argumentos e hardcoda point/prompt — remover.
    for bad_line in (
        "    point_num = 100000\n",
        "    prompt_num = 400\n",
    ):
        content = content.replace(bad_line, "")

    # get_mask() com bs alto explode a VRAM em ~6 GB. Prompt count only
    # changes runtime/CPU mask storage, not this peak.
    for old_bs in ("        bs = 64\n", "        bs = 8\n"):
        content = content.replace(old_bs, "        bs = AIGAMEKIT_PROMPT_BATCH_SIZE\n")
    content = re.sub(
        r"(?m)^        bs = (?:4|[0-9]+)$",
        "        bs = AIGAMEKIT_PROMPT_BATCH_SIZE",
        content,
        count=1,
    )
    content = content.replace(
        "        step_num = prompt_num // bs + 1\n",
        "        step_num = (prompt_num + bs - 1) // bs\n",
        1,
    )

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

    # Multi-head collect replaces winner-take-all.
    if MULTIHEAD_MARKER not in content and _WINNER_TAKE_ALL in content:
        content = content.replace(_WINNER_TAKE_ALL, _MULTIHEAD_COLLECT, 1)
    if "aigamekit_prompt_ids = []" not in content:
        content = content.replace(
            "        mask_res = []\n        iou_res = []\n",
            "        mask_res = []\n        iou_res = []\n        aigamekit_prompt_ids = []\n",
            1,
        )

    # Multi-head may make the candidate pool larger than prompt_num.
    if POOL_SORT_MARKER not in content and _POOL_SORT_ORIG in content:
        content = content.replace(_POOL_SORT_ORIG, _POOL_SORT_NEW, 1)

    # Best-fit NMS plus IoU-weighted consensus.
    if NMS_CONSENSUS_MARKER not in content:
        if _NMS_ORIG in content:
            content = content.replace(_NMS_ORIG, _NMS_CONSENSUS, 1)
        elif NMS_MARKER in content:
            content = re.sub(
                r'    with Timer\("NMS"\):\n'
                r"        # AIGAMEKIT_NMS_VECTORIZED.*?"
                r"                clusters\[i\]\.append\(i\)\n",
                _NMS_CONSENSUS,
                content,
                count=1,
                flags=re.DOTALL,
            )

    # Upstream discards every cluster supported by <=2 prompts. Small semantic
    # parts are exactly the masks least likely to receive three FPS prompts.
    # Keep stable clusters, plus high-confidence singleton/duo predictions.
    support_filter = (
        "if (_aigamekit_cluster_support(clusters[i], aigamekit_prompt_ids_sorted) "
        ">= AIGAMEKIT_MIN_CLUSTER_SUPPORT or iou_sorted[i] >= AIGAMEKIT_MIN_PREDICTED_IOU):"
    )
    content = content.replace("if len(clusters[i]) > 2:", support_filter, 1)
    content = content.replace(
        "if (len(clusters[i]) >= AIGAMEKIT_MIN_CLUSTER_SUPPORT or iou_sorted[i] >= AIGAMEKIT_MIN_PREDICTED_IOU):",
        support_filter,
        1,
    )

    # AABB overlap alone suppresses nested but distinct parts (door/frame).
    # Require actual point-mask overlap as evidence that both proposals denote
    # the same part; NMS remains the primary duplicate filter.
    bbox_only = re.compile(
        r"if \(\s*cal_bbox_iou\(\s*_points,\s*mask_sorted\[tar_cluster\],"
        r"\s*mask_sorted\[cur_cluster\]\s*\)\s*>\s*[0-9.]+\s*\):",
        re.MULTILINE,
    )
    content = bbox_only.sub(
        (
            "if (\n"
            "                    cal_iou(mask_sorted[tar_cluster], mask_sorted[cur_cluster])\n"
            "                    > AIGAMEKIT_SECONDARY_MASK_IOU\n"
            "                    and cal_bbox_iou(\n"
            "                        _points, mask_sorted[tar_cluster], mask_sorted[cur_cluster]\n"
            "                    ) > AIGAMEKIT_BBOX_MERGE_IOU\n"
            "                ):"
        ),
        content,
        count=1,
    )

    if VOTE_ASSIGN_MARKER not in content and _VOTE_ASSIGN_ORIG in content:
        content = content.replace(_VOTE_ASSIGN_ORIG, _VOTE_ASSIGN_NEW, 1)

    # Overrides rápidos de fix_label / get_connected_region.
    if PERF_MARKER not in content:
        content = content.rstrip("\n") + "\n" + _PERF_TAIL
    content = _strip_old_quality_tail(content)
    if QUALITY_MARKER not in content:
        content = content.rstrip("\n") + _QUALITY_TAIL.format(
            mask_nms_iou=mask_nms_iou,
            secondary_mask_iou=secondary_mask_iou,
            min_cluster_support=max(1, min_cluster_support),
            min_predicted_iou=min_predicted_iou,
            prompt_batch_size=max(1, prompt_batch_size),
            bbox_merge_iou=bbox_merge_iou,
            multi_head=bool(multi_head),
            head_min_score=float(head_min_score),
            head_score_ratio=float(head_score_ratio),
            consensus=bool(consensus),
            consensus_vote=float(consensus_vote),
        )

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
    mask_nms_iou: float = 0.9,
    secondary_mask_iou: float = 0.25,
    min_cluster_support: int = 3,
    min_predicted_iou: float = 0.75,
    prompt_batch_size: int = 4,
    multi_head: bool = True,
    head_min_score: float = 0.5,
    head_score_ratio: float = 0.85,
    consensus: bool = True,
    consensus_vote: float = 0.5,
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
            mask_nms_iou=mask_nms_iou,
            secondary_mask_iou=secondary_mask_iou,
            min_cluster_support=min_cluster_support,
            min_predicted_iou=min_predicted_iou,
            prompt_batch_size=prompt_batch_size,
            multi_head=multi_head,
            head_min_score=head_min_score,
            head_score_ratio=head_score_ratio,
            consensus=consensus,
            consensus_vote=consensus_vote,
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
