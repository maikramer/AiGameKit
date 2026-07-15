"""Tests for the pure Space-code transforms (part3d.utils.space_patch)."""

from __future__ import annotations

import os
from collections import defaultdict

import numpy as np
import pytest
from part3d.utils.space_patch import (
    _NMS_ORIG,
    _PERF_TAIL,
    NMS_MARKER,
    PERF_MARKER,
    transform_auto_mask,
    transform_p3sam_model,
    transform_surface_extractors,
    write_if_changed,
)

_FIXTURE = (
    "import numpy as np\n"
    "from numba import njit\n"
    "\n"
    "def get_mask():\n"
    "        bs = 64\n"
    "        return bs\n"
    "\n"
    "def fix_label(face_ids, adjacent_faces, use_aabb=False, mesh=None, show_info=False):\n"
    "    return face_ids\n"
    "\n"
    "def get_connected_region(face_ids, adjacent_faces, return_face_part_ids=False):\n"
    "    return []\n"
    "\n"
    "def do_post_process():\n"
    "    if integral_part_areas[i] > threshold and part_areas[i] < 0.01:\n"
    "        pass\n"
    "\n"
    "def mesh_sam():\n"
    "    point_num = 100000\n"
    "    prompt_num = 400\n" + _NMS_ORIG + "    if (\n"
    "        cal_bbox_iou(\n"
    "            _points, mask_sorted[tar_cluster], mask_sorted[cur_cluster]\n"
    "        )\n"
    "        > 0.5\n"
    "    ):\n"
    "        pass\n"
    "    if area / (cp_area + 1e-7) > 0.001:\n"
    "        pass\n"
    "    if _area / mesh_total_area > 0.001:\n"
    "        pass\n"
)

_KW = {"part_area_merge": 0.0025, "area_ratio_keep": 0.00025, "bbox_merge_iou": 0.7}


class TestTransformAutoMask:
    def test_all_patches_applied(self):
        out = transform_auto_mask(_FIXTURE, **_KW)
        assert "point_num = 100000" not in out
        assert "prompt_num = 400" not in out
        assert "bs = 4" in out and "bs = 64" not in out
        assert "part_areas[i] < 0.0025" in out
        assert "area / (cp_area + 1e-7) > 0.00025" in out
        assert "_area / mesh_total_area > 0.00025" in out
        assert "> 0.7" in out and "> 0.5" not in out
        assert NMS_MARKER in out and _NMS_ORIG not in out
        assert PERF_MARKER in out

    def test_idempotent(self):
        once = transform_auto_mask(_FIXTURE, **_KW)
        twice = transform_auto_mask(once, **_KW)
        assert once == twice

    def test_bbox_iou_value_updatable_on_repatch(self):
        once = transform_auto_mask(_FIXTURE, **_KW)
        redo = transform_auto_mask(once, part_area_merge=0.0025, area_ratio_keep=0.00025, bbox_merge_iou=0.65)
        assert "> 0.65" in redo and "> 0.7" not in redo

    def test_perf_tail_is_valid_python(self):
        compile(_PERF_TAIL, "<perf_tail>", "exec")

    def test_result_still_compiles(self):
        compile(transform_auto_mask(_FIXTURE, **_KW), "<patched>", "exec")


def _reference_get_connected_region(face_ids, adjacent_faces, return_face_part_ids=False):
    """Port of the upstream pure-Python BFS (semantics oracle)."""
    vis = [False] * len(face_ids)
    parts = []
    face_part_ids = np.ones_like(face_ids) * -1
    for i in range(len(face_ids)):
        if vis[i]:
            continue
        _part = []
        _queue = [i]
        while _queue:
            cur = _queue.pop(0)
            if vis[cur]:
                continue
            vis[cur] = True
            _part.append(cur)
            face_part_ids[cur] = len(parts)
            if not (0 <= cur < adjacent_faces.shape[0]):
                continue
            cur_id = face_ids[cur]
            for j in adjacent_faces[cur]:
                if j == -1:
                    break
                if not vis[j] and face_ids[j] == cur_id:
                    _queue.append(j)
        parts.append(_part)
    if return_face_part_ids:
        return parts, face_part_ids
    return parts


def _reference_fix_label(face_ids, adjacent_faces):
    """Port of the upstream fix_label (use_aabb=False path)."""
    face_ids = face_ids.copy()
    loop_cnt = 1
    changed = True
    no_mask_ids = np.where(face_ids < 0)[0].tolist()
    faces_max = adjacent_faces.shape[0]
    while changed and loop_cnt <= 50:
        changed = False
        new_no_mask_ids = []
        for i in no_mask_ids:
            if not (0 <= i < faces_max):
                continue
            adj_ids = [face_ids[j] for j in adjacent_faces[i] if j != -1 and face_ids[j] >= 0]
            if len(adj_ids) == 0:
                new_no_mask_ids.append(i)
                continue
            face_ids[i] = np.argmax(np.bincount(adj_ids))
            changed = True
        no_mask_ids = new_no_mask_ids
        loop_cnt += 1
    return face_ids


def _random_padded_adjacency(rng, n_faces: int, max_deg: int = 3) -> np.ndarray:
    adj = -np.ones((n_faces, max_deg), dtype=np.int64)
    counts = np.zeros(n_faces, dtype=np.int64)
    for _ in range(n_faces * 2):
        a, b = rng.integers(0, n_faces, size=2)
        if a == b or counts[a] >= max_deg or counts[b] >= max_deg:
            continue
        if b in adj[a]:
            continue
        adj[a, counts[a]] = b
        counts[a] += 1
        adj[b, counts[b]] = a
        counts[b] += 1
    return adj


class TestPerfTailSemantics:
    @pytest.fixture()
    def patched_ns(self):
        pytest.importorskip("numba")
        pytest.importorskip("scipy")
        ns = {
            "np": np,
            "njit": pytest.importorskip("numba").njit,
            "defaultdict": defaultdict,
            "fix_label": lambda face_ids, adjacent_faces, **kw: _reference_fix_label(face_ids, adjacent_faces),
            "get_connected_region": _reference_get_connected_region,
        }
        exec(_PERF_TAIL, ns)
        return ns

    def test_get_connected_region_matches_reference(self, patched_ns):
        rng = np.random.default_rng(0)
        for _trial in range(5):
            n = 40
            adj = _random_padded_adjacency(rng, n)
            labels = rng.integers(-1, 4, size=n).astype(np.int64)
            ref_parts, ref_ids = _reference_get_connected_region(labels, adj, return_face_part_ids=True)
            fast_parts, fast_ids = patched_ns["get_connected_region"](labels, adj, return_face_part_ids=True)
            assert len(fast_parts) == len(ref_parts)
            np.testing.assert_array_equal(np.asarray(fast_ids), np.asarray(ref_ids))
            for rp, fp in zip(ref_parts, fast_parts, strict=True):
                assert sorted(rp) == sorted(fp)

    def test_fix_label_matches_reference(self, patched_ns):
        rng = np.random.default_rng(1)
        for _trial in range(5):
            n = 40
            adj = _random_padded_adjacency(rng, n)
            labels = rng.integers(0, 4, size=n).astype(np.int64)
            holes = rng.random(n) < 0.3
            labels[holes] = -1
            ref = _reference_fix_label(labels, adj)
            fast = patched_ns["fix_label"](labels.copy(), adj)
            np.testing.assert_array_equal(np.asarray(fast), ref)


class TestOtherTransforms:
    def test_p3sam_sonata_root(self):
        content = "x = sonata.load(download_root='/root/sonata')\n"
        out = transform_p3sam_model(content, "/home/user/.cache/sonata")
        assert "/root/sonata" not in out
        assert "/home/user/.cache/sonata" in out

    def test_surface_extractors_float32(self):
        content = "grid = grid_logit.cpu().numpy()\n"
        out = transform_surface_extractors(content)
        assert "grid_logit.float().cpu().numpy()" in out
        assert transform_surface_extractors(out) == out


class TestWriteIfChanged:
    def test_skips_when_equal(self, tmp_path):
        p = tmp_path / "a.py"
        p.write_text("abc")
        assert write_if_changed(str(p), "abc") is False
        assert write_if_changed(str(p), "xyz") is True
        assert p.read_text() == "xyz"

    def test_symlink_replaced_blob_untouched(self, tmp_path):
        blob = tmp_path / "blob"
        blob.write_text("original")
        link = tmp_path / "link.py"
        os.symlink(blob, link)
        assert write_if_changed(str(link), "patched") is True
        assert not os.path.islink(link)
        assert link.read_text() == "patched"
        assert blob.read_text() == "original"
