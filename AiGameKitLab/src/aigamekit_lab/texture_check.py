"""Verificação de integridade de textura entre GLBs por projeção de superfície.

Amostra pontos área-pesados na referência, lê o basecolor no atlas próprio, e
**projeta cada ponto na malha do candidato** (BVHTree ``find_nearest``) para
avaliar a textura do candidato exatamente nesse ponto (triângulo ->
baricêntricas -> UV -> atlas). Como a avaliação é por projeção e não por raio
de emparelhamento entre amostras independentes, o caso «igual a si próprio» dá
erro ~0 mesmo em texturas de alta frequência — o ruído de emparelhamento por
KDTree+raio atravessava superfícies vizinhas (fruta vs balcão) e cegava o
teste. É o teste directo de «textura despedaçada»: UVs partidas mostram a cor
errada no sítio errado mesmo com o atlas intacto.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np

log = logging.getLogger(__name__)

DEFAULT_SAMPLES = 4000
DEFAULT_TOLERANCE = 0.12
DEFAULT_FAIL_ABOVE = 0.25
DEFAULT_MIN_MATCHED = 0.8
DEFAULT_MAX_NORMAL_DEG = 60.0


def _require_bpy():
    try:
        import bpy

        return bpy
    except ImportError:
        raise ImportError("bpy is required for texture-check. Install with: pip install bpy") from None


def _material_basecolor_image(mat) -> Any:
    """Imagem de base color de um material (node ligado ao Principled, fallback primeiro TEX_IMAGE)."""
    if not mat.use_nodes:
        return None
    nodes = mat.node_tree.nodes
    principled = next((n for n in nodes if n.type == "BSDF_PRINCIPLED"), None)
    if principled is not None:
        socket = principled.inputs.get("Base Color")
        if socket is not None:
            for link in socket.links:
                if link.from_node.type == "TEX_IMAGE" and link.from_node.image:
                    return link.from_node.image
    tex = next((n for n in nodes if n.type == "TEX_IMAGE" and n.image), None)
    return tex.image if tex else None


def _material_basecolor_constant(mat) -> tuple[float, float, float]:
    """Cor chapada de base color: default_value do Principled (diffuse_color é só viewport display)."""
    if mat is not None and mat.use_nodes:
        principled = next((n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
        if principled is not None and "Base Color" in principled.inputs:
            r, g, b, _a = principled.inputs["Base Color"].default_value
            return (float(r), float(g), float(b))
    if mat is not None:
        r, g, b, _a = mat.diffuse_color
        return (float(r), float(g), float(b))
    return (0.0, 0.0, 0.0)


def _object_tris_world(obj) -> dict[str, Any] | None:
    """Triângulos do objeto em espaço mundo + estruturas para amostrar UV/atlas.

    Returns:
        Dict com ``tris`` (M,3,3 world), ``tri_loops``, ``uv_flat``, ``coords_w``,
        ``tri_mat``, ``mat_imgs``/``mat_const``, e ``uv_layer`` válido; None se o
        objeto não for amostrável (sem UVs).
    """
    mesh = obj.data
    uv_layer = mesh.uv_layers.active
    if uv_layer is None:
        return None

    n_verts = len(mesh.vertices)
    coords = np.empty(n_verts * 3, dtype=np.float64)
    mesh.vertices.foreach_get("co", coords)
    coords = coords.reshape(n_verts, 3)

    # world space uma só vez por objeto — áreas e amostras saem já no
    # referencial do mundo (escalas não-uniformes contam na área)
    mw = np.array(obj.matrix_world.copy(), dtype=np.float64)
    hom = np.concatenate([coords, np.ones((n_verts, 1))], axis=1)
    coords_w = (hom @ mw.T)[:, :3]

    uv_flat = np.empty(len(mesh.loops) * 2, dtype=np.float32)
    uv_layer.data.foreach_get("uv", uv_flat)
    uv_flat = uv_flat.reshape(-1, 2)

    mat_imgs = [_material_basecolor_image(m) if m else None for m in mesh.materials]
    mat_const = [_material_basecolor_constant(m) if m else (0.0, 0.0, 0.0) for m in mesh.materials]

    # bpy 3.x+: calc_loop_triangles() preenche mesh.loop_triangles (retorna None)
    mesh.calc_loop_triangles()
    tris = mesh.loop_triangles
    tri_mat = np.fromiter((t.material_index for t in tris), dtype=np.int64, count=len(tris))
    tri_loops = np.fromiter((li for t in tris for li in t.loops), dtype=np.int64, count=len(tris) * 3)
    tri_verts = np.fromiter((v for t in tris for v in t.vertices), dtype=np.int64, count=len(tris) * 3)

    tl = tri_loops.reshape(-1, 3)
    tv = tri_verts.reshape(-1, 3)
    tri_w = np.stack([coords_w[tv[:, 0]], coords_w[tv[:, 1]], coords_w[tv[:, 2]]], axis=1)
    return {
        "obj": obj,
        "tris_w": tri_w,
        "tri_loops": tl,
        "uv_flat": uv_flat,
        "tri_mat": tri_mat,
        "mat_imgs": mat_imgs,
        "mat_const": mat_const,
        "mesh": mesh,
    }


def _image_buffer_cache(img, cache: dict[int, tuple[int, int, np.ndarray]]) -> tuple[int, int, np.ndarray]:
    key = id(img)
    if key not in cache:
        w, h = int(img.size[0]), int(img.size[1])
        buf = np.empty(w * h * 4, dtype=np.float32)
        img.pixels.foreach_get(buf)
        cache[key] = (w, h, buf.reshape(h, w, 4))
    return cache[key]


def _sample_uvs_at_tris(data: dict[str, Any], tri_idx: np.ndarray, weights: np.ndarray, cache: dict) -> np.ndarray:
    """Cor basecolor amostrada nos triângulos ``tri_idx`` com pesos baricêntricos ``weights`` (M,3)."""
    tl = data["tri_loops"][tri_idx]
    uv_flat = data["uv_flat"]
    ua, ub, uc = uv_flat[tl[:, 0]], uv_flat[tl[:, 1]], uv_flat[tl[:, 2]]
    uvs = ua * weights[:, :1] + ub * weights[:, 1:2] + uc * weights[:, 2:3]

    tri_mat = data["tri_mat"][tri_idx]
    out = np.zeros((len(tri_idx), 3), dtype=np.float32)
    for mat_idx in np.unique(tri_mat):
        sel = tri_mat == mat_idx
        img = data["mat_imgs"][mat_idx] if mat_idx < len(data["mat_imgs"]) else None
        if img is None:
            out[sel] = np.array(
                data["mat_const"][mat_idx] if mat_idx < len(data["mat_const"]) else (0.0, 0.0, 0.0),
                dtype=np.float32,
            )
            continue
        w, h, buf = _image_buffer_cache(img, cache)
        uu = np.clip(uvs[sel, 0], 0.0, 1.0)
        vv = np.clip(uvs[sel, 1], 0.0, 1.0)
        ix = np.round(uu * (w - 1)).astype(np.int64)
        iy = np.round(vv * (h - 1)).astype(np.int64)
        out[sel] = buf[iy, ix, :3]
    return out


def _barycentric_weights(tris: np.ndarray, points: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pesos baricêntricos de ``points`` nos triângulos ``tris`` (M,3,3 world).

    Os pontos vêm de ``find_nearest`` — estão no plano do triângulo; resolver
    por duas arestas dá pesos estáveis (soma 1) mesmo em triângulos finos.

    Returns:
        ``(weights (M,3), inside (M,) bool)`` — inside=False quando algum peso
        é negativo além de tolerância (projeção caiu fora do triângulo).
    """
    a, b, c = tris[:, 0], tris[:, 1], tris[:, 2]
    p = points
    v0, v1, v2 = b - a, c - a, p - a
    d00 = np.einsum("ij,ij->i", v0, v0)
    d01 = np.einsum("ij,ij->i", v0, v1)
    d11 = np.einsum("ij,ij->i", v1, v1)
    d20 = np.einsum("ij,ij->i", v2, v0)
    d21 = np.einsum("ij,ij->i", v2, v1)
    denom = d00 * d11 - d01 * d01
    denom = np.where(np.abs(denom) < 1e-18, 1e-18, denom)
    wb = (d11 * d20 - d01 * d21) / denom
    wc = (d00 * d21 - d01 * d20) / denom
    wa = 1.0 - wb - wc
    weights = np.stack([wa, wb, wc], axis=1)
    inside = (weights >= -1e-3).all(axis=1)
    return weights, inside


def sample_surface_colors(
    glb_path: Path | str,
    n_samples: int = DEFAULT_SAMPLES,
    seed: int = 0,
) -> dict[str, Any]:
    """Amostra cores de basecolor na superfície do mesh, em coordenadas do mundo.

    Args:
        glb_path: GLB de entrada (KTX2/meshopt são decodificados pelo importer).
        n_samples: Número de pontos área-pesados a amostrar.
        seed: Seed determinística do RNG.

    Returns:
        Dict com ``positions`` (N,3), ``colors`` (N,3 linear), ``normals``
        (N,3), contagem de triângulos (com e sem textura), área de superfície
        e imagens usadas.

    Raises:
        ImportError: bpy não instalado.
        ValueError: GLB sem meshes ou sem UVs.
    """
    bpy = _require_bpy()
    from aigamekit_lab.glb_import import import_glb
    from aigamekit_shared.bpy_mesh import clear_scene

    glb_path = Path(glb_path)
    clear_scene()
    import_glb(glb_path)

    rng = np.random.default_rng(seed)
    all_pos: list[np.ndarray] = []
    all_col: list[np.ndarray] = []
    all_norm: list[np.ndarray] = []
    n_tris = 0
    untextured_tris = 0
    surface_area = 0.0
    images_used: dict[str, tuple[int, int]] = {}
    img_cache: dict[int, tuple[int, int, np.ndarray]] = {}

    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        data = _object_tris_world(obj)
        if data is None:
            log.warning("mesh %s sem UV layer — ignorado no texture-check", obj.name)
            continue
        tris_w = data["tris_w"]
        pa, pb, pc = tris_w[:, 0], tris_w[:, 1], tris_w[:, 2]
        cross = np.cross(pb - pa, pc - pa)
        areas = 0.5 * np.linalg.norm(cross, axis=1)
        n_tris += len(tris_w)
        tri_mat = data["tri_mat"]
        untextured_tris += int(sum(1 for i in tri_mat if i >= len(data["mat_imgs"]) or data["mat_imgs"][i] is None))
        surface_area += float(areas.sum())
        valid = areas > 1e-12
        if not valid.any():
            continue

        probs = areas * valid
        total = probs.sum()
        if total <= 0:
            continue
        probs = probs / total
        pick = rng.choice(len(tris_w), size=n_samples, p=probs)

        # barycentrics uniformes na superfície do triângulo
        r1 = np.sqrt(rng.random(n_samples)).astype(np.float64)
        r2 = rng.random(n_samples).astype(np.float64)
        wa, wb, wc = (1.0 - r1), (r1 * (1.0 - r2)), (r1 * r2)
        weights = np.stack([wa, wb, wc], axis=1)

        pos_world = np.einsum("ij,ijk->ik", weights, tris_w[pick]).astype(np.float32)
        normals = (cross[pick] / np.maximum(np.linalg.norm(cross[pick], axis=1, keepdims=True), 1e-18)).astype(
            np.float32
        )
        colors = _sample_uvs_at_tris(data, pick, weights.astype(np.float32), img_cache)

        for img in data["mat_imgs"]:
            if img is not None:
                images_used[img.name] = (int(img.size[0]), int(img.size[1]))

        all_pos.append(pos_world)
        all_col.append(colors)
        all_norm.append(normals)

    clear_scene()

    if not all_pos:
        raise ValueError(f"nenhum mesh amostrável (UV + faces) em {glb_path.name}")
    return {
        "positions": np.concatenate(all_pos),
        "colors": np.concatenate(all_col),
        "normals": np.concatenate(all_norm),
        "n_tris": n_tris,
        "untextured_tris": untextured_tris,
        "surface_area_m2": round(surface_area, 4),
        "images": images_used,
    }


def _candidate_evaluator(glb_path: Path):
    """Importa o candidato e devolve ``eval(points, normals) -> dict``.

    A cena fica com o candidato carregado (o chamador limpa depois). Para cada
    ponto: ``find_nearest`` no BVHTree de cada objeto, triângulo mais próximo,
    pesos baricêntricos, UV interpolada e cor do atlas/material do candidato.
    """
    bpy = _require_bpy()
    from mathutils.bvhtree import BVHTree

    from aigamekit_lab.glb_import import import_glb
    from aigamekit_shared.bpy_mesh import clear_scene

    clear_scene()
    import_glb(glb_path)

    entries = []
    img_cache: dict[int, tuple[int, int, np.ndarray]] = {}
    for obj in bpy.context.scene.objects:
        if obj.type != "MESH":
            continue
        data = _object_tris_world(obj)
        if data is None:
            continue
        tris_w = data["tris_w"]
        # FromPolygons com triângulos: o índice devolvido por find_nearest é
        # o índice do triângulo nas nossas estruturas
        verts = [tuple(v) for v in tris_w.reshape(-1, 3)]
        polys = [(i, i + 1, i + 2) for i in range(0, len(verts), 3)]
        tree = BVHTree.FromPolygons(verts, polys, all_triangles=True, epsilon=1e-8)
        entries.append((data, tree))

    if not entries:
        clear_scene()
        raise ValueError(f"nenhum mesh com UVs em {glb_path.name}")

    from mathutils import Vector

    def eval(points: np.ndarray, normals: np.ndarray) -> dict[str, np.ndarray]:
        n = len(points)
        best_dist = np.full(n, np.inf, dtype=np.float64)
        best_colors = np.zeros((n, 3), dtype=np.float32)
        best_normal = np.zeros((n, 3), dtype=np.float32)
        best_inside = np.zeros(n, dtype=bool)

        for data, tree in entries:
            dists = np.empty(n, dtype=np.float64)
            tri_idx = np.full(n, -1, dtype=np.int64)
            locs = np.zeros((n, 3), dtype=np.float64)
            norms = np.zeros((n, 3), dtype=np.float64)
            for i, p in enumerate(points):
                loc, norm, idx, dist = tree.find_nearest(Vector(p.tolist()))
                if idx is None:
                    dists[i], tri_idx[i] = np.inf, -1
                    continue
                dists[i], tri_idx[i] = dist, idx
                locs[i] = loc[:]
                norms[i] = norm[:]

            has = tri_idx >= 0
            colors = np.zeros((n, 3), dtype=np.float32)
            inside = np.zeros(n, dtype=bool)
            if has.any():
                sel_idx = tri_idx[has]
                weights, inside_h = _barycentric_weights(data["tris_w"][sel_idx], locs[has])
                colors_h = _sample_uvs_at_tris(data, sel_idx, weights.astype(np.float32), img_cache)
                colors[has] = colors_h
                inside[has] = inside_h

            better = (dists < best_dist) & has
            best_dist[better] = dists[better]
            best_colors[better] = colors[better]
            best_normal[better] = norms[better]
            best_inside[better] = inside[better]

        return {"dist": best_dist, "colors": best_colors, "normals": best_normal, "inside": best_inside}

    return eval


def compare_surface_colors(
    reference: Path | str,
    candidate: Path | str,
    *,
    n_samples: int = DEFAULT_SAMPLES,
    seed: int = 0,
    tolerance: float = DEFAULT_TOLERANCE,
    fail_above: float = DEFAULT_FAIL_ABOVE,
    min_matched: float = DEFAULT_MIN_MATCHED,
    match_dist: float | None = None,
    max_normal_deg: float = DEFAULT_MAX_NORMAL_DEG,
) -> dict[str, Any]:
    """Compara a cor de superfície de dois GLBs nos mesmos pontos do mundo.

    Cada amostra da referência é projetada na malha do candidato
    (``find_nearest``); a cor do candidato vem do atlas próprio no ponto
    projetado. Amostras com projeção mais distante que ``match_dist`` ou normal
    incompatível (> ``max_normal_deg``) ficam por emparelhar — silhueta
    decimada e paredes finas (projeção na face de trás) não geram falsos erros.

    Args:
        reference: GLB de referência (ex.: ``*_painted.glb``).
        candidate: GLB a validar (ex.: ``*_lod1.glb``).
        n_samples: Pontos de amostragem na referência.
        seed: Seed determinística.
        tolerance: Distância RGB (linear, 0..~1.73) aceitável por amostra.
        fail_above: Fração de amostras acima da toleração que falha (0..1).
        min_matched: Fração mínima de amostras emparelhadas.
        match_dist: Tolerância de projeção em metros; None = 0.5% da diagonal
            da bbox da referência (drift de silhueta após decimação).
        max_normal_deg: Ângulo máximo entre normal da amostra e da projeção.

    Returns:
        Relatório com estatísticas de erro e veredicto ``pass_``.
    """
    _require_bpy()
    from aigamekit_shared.bpy_mesh import clear_scene

    ref = sample_surface_colors(reference, n_samples=n_samples, seed=seed)
    evaluator = _candidate_evaluator(Path(candidate))
    try:
        result = evaluator(ref["positions"], ref["normals"])
    finally:
        clear_scene()

    rp, rc, rn = ref["positions"], ref["colors"], ref["normals"]
    diag = float(np.linalg.norm(rp.max(axis=0) - rp.min(axis=0)))
    dist_tol = float(match_dist) if match_dist is not None else max(diag * 0.005, 1e-4)
    cos_limit = float(np.cos(np.radians(max_normal_deg)))

    dots = np.einsum("ij,ij->i", rn, result["normals"])
    matched = (result["dist"] <= dist_tol) & (dots >= cos_limit) & result["inside"]
    n_matched = int(matched.sum())
    err = np.linalg.norm(rc[matched] - result["colors"][matched], axis=1)
    frac_bad = float((err > tolerance).mean()) if n_matched else 1.0
    matched_ratio = n_matched / len(rp)

    passed = matched_ratio >= min_matched and frac_bad <= fail_above
    report: dict[str, Any] = {
        "reference": str(reference),
        "candidate": str(candidate),
        "samples": len(rp),
        "matched": n_matched,
        "matched_ratio": round(matched_ratio, 4),
        "match_dist_m": round(dist_tol, 6),
        "max_normal_deg": max_normal_deg,
        "tolerance": tolerance,
        "fail_above": fail_above,
        "min_matched": min_matched,
        "err_mean": round(float(err.mean()), 4) if n_matched else None,
        "err_median": round(float(np.median(err)), 4) if n_matched else None,
        "err_p95": round(float(np.percentile(err, 95)), 4) if n_matched else None,
        "frac_above_tolerance": round(frac_bad, 4),
        "ref_untextured_tris": ref["untextured_tris"],
        "ref_surface_area_m2": ref["surface_area_m2"],
        "pass_": bool(passed),
    }
    return report


def format_texture_check_summary(report: dict[str, Any]) -> str:
    """Linha-resumo humanamente legível do relatório (para CLI/logs)."""
    verdict = "PASS" if report.get("pass_") else "FAIL"
    return (
        f"{verdict}: matched={report.get('matched')}/{report.get('samples')} "
        f"err Médio={report.get('err_mean')} p95={report.get('err_p95')} "
        f"acima_tol={100 * report.get('frac_above_tolerance', 0):.1f}% (tol={report.get('tolerance')})"
    )
