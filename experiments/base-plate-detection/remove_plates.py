#!/usr/bin/env python3
"""
Remove backing plates detectadas pelo quality check e repara a mesh.

Detecta placas usando a mesma heurística do ``mesh_quality_check``
(cobertura de faces planas nos extremos do bbox), remove as faces
da placa + uma margem acima para garantir que a interface é cortada,
e usa pymeshlab para fechar os buracos e tornar a mesh watertight.

Uso:
    cd experiments/base-plate-detection
    source ../../Text3D/.venv/bin/activate
    python remove_plates.py meshes/chair_modern.glb -o output/chair_clean.glb
    python remove_plates.py meshes/  # processa todos os .glb na pasta
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np
import trimesh
import trimesh.repair as trimesh_repair


def detect_plates(
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
    **concentração de faces numa camada fina** (2% da altura) no extremo:
    artefatos empacotam >35% de todas as faces nessa camada, superfícies
    legítimas distribuem faces de forma proporcional (~20%).

    Retorna lista de dicts com axis (0/1/2), side ("min"/"max"),
    coverage, thin_concentration e os limites da banda.
    """
    if len(mesh.faces) == 0:
        return []

    normals = mesh.face_normals
    areas = mesh.area_faces
    centers = mesh.triangles_center
    bounds = mesh.bounds
    extents = mesh.extents
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
            flat_in_band = in_band & aligned
            flat_area = float(areas[flat_in_band].sum())
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
                print(
                    f"  Placa {ax_names[ax]}-{side_label} ignorada: "
                    f"coverage={coverage:.2f} mas concentração fina={thin_conc:.1%} "
                    f"(superfície legítima, não artefato)"
                )
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
    """Encontra a fronteira da placa avançando camada a camada desde o extremo.

    Avança em camadas finas (``layer_frac`` da altura) e para quando
    a porcentagem de faces horizontais cai abaixo de ``min_horizontal_pct``.
    Retorna a coordenada de corte no eixo, ou None se nenhuma camada qualifica.
    """
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

        n_horiz = int(np.count_nonzero(in_layer & (n_axis >= normal_align)))
        pct = n_horiz / n_in

        if pct >= min_horizontal_pct:
            cut_coord = layer_hi if side == "min" else layer_lo
        else:
            break

    return cut_coord


def _dilate_selection(mesh: trimesh.Trimesh, mask: np.ndarray, iterations: int = 2) -> np.ndarray:
    """Expande a seleção de faces por adjacência (flood fill limitado)."""
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


def remove_plate_faces(
    mesh: trimesh.Trimesh,
    plates: list[dict],
    *,
    margin_frac: float = 0.06,
    dilate_iters: int = 2,
    min_remaining_faces: int = 1000,
) -> trimesh.Trimesh:
    """Remove faces das placas + margem para cortar a interface.

    Estratégia em 3 passos por placa:
    1. Scan camada a camada para encontrar onde a placa acaba
    2. Corte espacial: tudo abaixo da fronteira + margem
    3. Dilatação por adjacência para pegar faces da interface

    Não usa limite percentual fixo — placas densas (como a imagem mostra)
    podem conter >70% das faces. A validação é feita pelo número mínimo
    de faces restantes e pela existência de componente significativa.

    Args:
        mesh: Mesh de entrada.
        plates: Lista de placas detectadas por ``detect_plates``.
        margin_frac: Fração extra da altura além da fronteira da placa.
        dilate_iters: Iterações de dilatação por adjacência na borda do corte.
        min_remaining_faces: Mínimo absoluto de faces que devem sobrar.
    """
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
            print(f"  Placa {plate['axis_name']}-{side}: fronteira não encontrada, usando banda original")
            cut = plate["band_hi"] if side == "min" else plate["band_lo"]

        if side == "min":
            cut_with_margin = cut + margin
            spatial_mask = centers[:, ax] <= cut_with_margin
            plate_depth = cut - float(bounds[0, ax])
        else:
            cut_with_margin = cut - margin
            spatial_mask = centers[:, ax] >= cut_with_margin
            plate_depth = float(bounds[1, ax]) - cut

        n_spatial = int(np.count_nonzero(spatial_mask))
        print(
            f"  Placa {plate['axis_name']}-{side}: fronteira={cut:.4f}, "
            f"profundidade={plate_depth:.4f} ({plate_depth / h * 100:.1f}% da altura), "
            f"{n_spatial:,} faces no corte espacial"
        )

        dilated = _dilate_selection(m, spatial_mask, iterations=dilate_iters)
        n_dilated = int(np.count_nonzero(dilated)) - n_spatial
        if n_dilated > 0:
            print(f"  +{n_dilated:,} faces pela dilatação de interface ({dilate_iters} iterações)")

        remove_mask |= dilated

    n_remove = int(np.count_nonzero(remove_mask))
    n_total = len(m.faces)
    n_remaining = n_total - n_remove

    if n_remove == 0:
        print("  Nenhuma face marcada para remoção.")
        return mesh

    if n_remaining < min_remaining_faces:
        print(f"  AVISO: sobrariam apenas {n_remaining:,} faces (mínimo={min_remaining_faces:,}) — abortando.")
        return mesh

    remove_ratio = n_remove / n_total
    print(f"  Removendo {n_remove:,} faces ({remove_ratio:.1%} do total), restam {n_remaining:,}")

    keep = ~remove_mask
    sub = m.submesh([np.where(keep)[0]], append=True, only_watertight=False)
    if isinstance(sub, trimesh.Trimesh) and len(sub.faces) > 0:
        sub.remove_unreferenced_vertices()

        parts = sub.split(only_watertight=False)
        if len(parts) > 1:
            main_size = max(len(p.faces) for p in parts)
            min_size = max(500, int(main_size * 0.03))
            significant = [p for p in parts if len(p.faces) >= min_size]
            if significant:
                sub = trimesh.util.concatenate(significant)
                sub.remove_unreferenced_vertices()
                print(f"  Componentes: {len(parts)} -> {len(significant)} (removidas ilhas pequenas)")

        return sub

    return mesh


def _plate_score(part: trimesh.Trimesh) -> float:
    """Pontua o quão "placa" uma componente parece (0 = não-placa, 1 = placa pura).

    Critérios combinados:
    - Flatness: razão min_extent/max_extent (placa é muito achatada)
    - Alignment: fração da área com normais alinhadas a um só eixo
    - Volume efficiency: convex_hull_vol / bbox_vol (placa tem alta eficiência)
    """
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


def remove_plate_components(mesh: trimesh.Trimesh) -> tuple[trimesh.Trimesh, int]:
    """Remove componentes desconexas que parecem placas.

    Depois de cortar placas e reparar buracos, podem sobrar componentes
    separadas que ainda são placas (ex.: placas em "+", onde o corte
    espacial só remove uma direção). Este passo detecta e remove essas.

    Nunca remove todas as componentes — mantém pelo menos a melhor
    (menor plate_score).

    Returns:
        Tupla (mesh_limpa, n_removidas).
    """
    parts = mesh.split(only_watertight=False)
    if len(parts) <= 1:
        return mesh, 0

    scored = [(p, _plate_score(p)) for p in parts]
    scored.sort(key=lambda x: x[1])

    kept = []
    removed_info = []
    for p, score in scored:
        e = sorted(float(x) for x in p.extents)
        flat_ratio = e[0] / e[2] if e[2] > 1e-9 else 0

        is_plate = (flat_ratio < 0.05 and score > 0.4) or (score > 0.7) or (flat_ratio < 0.02)

        if is_plate and len(kept) > 0:
            removed_info.append((len(p.faces), round(score, 3), round(flat_ratio, 4)))
        else:
            kept.append(p)

    if not kept:
        kept = [scored[0][0]]

    if removed_info:
        for faces, score, flat in removed_info:
            print(
                f"  Removendo componente-placa desconectada: {faces:,} faces (plate_score={score}, flat_ratio={flat})"
            )

        if len(kept) == 1:
            result = kept[0]
        else:
            result = trimesh.util.concatenate(kept)
        result.remove_unreferenced_vertices()
        return result, len(removed_info)

    return mesh, 0


def check_connected_plate_residual(
    mesh: trimesh.Trimesh,
    original_plates: list[dict],
    *,
    plate_coverage_threshold: float = 0.7,
    band_frac: float = 0.10,
    normal_align: float = 0.7,
) -> list[dict]:
    """Verifica se a mesh ainda contém placas conectadas (grudadas ao objeto).

    Roda a mesma detecção de placas no resultado final. Ignora placas no
    mesmo eixo/lado que as originais — essas são a superfície exposta após
    o corte (ex.: topo de uma mesa, base de um personagem).

    Só retorna placas em **eixos ou lados diferentes**, que indicam uma
    placa genuína grudada no objeto (ex.: placa em Y-max num vaso onde
    o corte original foi em Z-min). Neste caso a mesh é irrecuperável
    e deve ser descartada no sistema de retries.
    """
    all_residual = detect_plates(
        mesh,
        plate_coverage_threshold=plate_coverage_threshold,
        band_frac=band_frac,
        normal_align=normal_align,
    )

    original_keys = {(p["axis"], p["side"]) for p in original_plates}

    new_plates = []
    for rp in all_residual:
        key = (rp["axis"], rp["side"])
        if key in original_keys:
            print(
                f"  Placa residual {rp['axis_name']}-{rp['side']} ignorada "
                f"(mesmo eixo/lado do corte original, superfície exposta)"
            )
        else:
            new_plates.append(rp)

    return new_plates


def _boundary_edge_count(mesh: trimesh.Trimesh) -> int:
    """Conta arestas de fronteira (buracos abertos)."""
    try:
        from trimesh.grouping import group_rows

        return len(group_rows(mesh.edges_sorted, require_count=1))
    except Exception:
        return -1


def repair_with_pymeshlab(
    mesh: trimesh.Trimesh,
    *,
    max_hole_size: int = 500,
    taubin_steps: int = 3,
    fillet_dilations: int = 4,
    fillet_smooth_steps: int = 12,
) -> trimesh.Trimesh:
    """Repara a mesh com pymeshlab: fecha buracos, corrige topologia, watertight.

    Pipeline:
    1. Reparação non-manifold (edges + vertices)
    2. Remoção de faces duplicadas/nulas
    3. Fechamento de buracos (Delaunay)
    4. Fillet: seleciona faces do patch + vizinhança, aplica Taubin
       smoothing localizado para suavizar a borda afiada do corte
    5. Fallback pymeshfix se ainda tiver buracos
    """
    import pymeshlab

    m = mesh
    boundary_before = _boundary_edge_count(m)
    print(f"  Arestas de fronteira antes do reparo: {boundary_before}")

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

        n_faces_before_close = ms.current_mesh().face_number()
        ms.meshing_close_holes(maxholesize=max_hole_size)
        n_faces_after_close = ms.current_mesh().face_number()
        n_patch = n_faces_after_close - n_faces_before_close

        if n_patch > 0 and fillet_smooth_steps > 0:
            ms.compute_selection_by_condition_per_face(condselect=f"(fi >= {n_faces_before_close})")
            ms.compute_selection_transfer_face_to_vertex()
            for _ in range(fillet_dilations):
                ms.apply_selection_dilatation()
            ms.compute_selection_transfer_vertex_to_face()
            n_fillet = ms.current_mesh().selected_face_number()

            diag = ms.current_mesh().bounding_box().diagonal()
            target_edge = diag / 180
            print(
                f"  Fillet: {n_patch} faces do patch + {n_fillet - n_patch} vizinhas "
                f"({fillet_dilations} anéis), remesh target={target_edge:.5f}"
            )

            ms.meshing_isotropic_explicit_remeshing(
                iterations=3,
                targetlen=pymeshlab.PureValue(target_edge),
                adaptive=True,
                selectedonly=True,
                checksurfdist=True,
                maxsurfdist=pymeshlab.PureValue(target_edge * 0.5),
            )

            n_after_remesh = ms.current_mesh().face_number()
            print(f"  Remesh: {n_faces_after_close:,} -> {n_after_remesh:,} faces")

            ms.compute_selection_by_condition_per_face(condselect=f"(fi >= {n_faces_before_close})")
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
    print(f"  Arestas de fronteira após pymeshlab: {boundary_after}")

    if boundary_after > 0:
        try:
            from pymeshfix import PyTMesh

            print("  Aplicando pymeshfix (MeshFix de Attene)...")
            verts = np.asarray(m.vertices, dtype=np.float64)
            faces = np.asarray(m.faces, dtype=np.int64)
            mfix = PyTMesh()
            mfix.load_array(verts, faces)
            mfix.fill_small_boundaries(nbe=0, refine=True)
            v, f = mfix.return_arrays()
            m_fix = trimesh.Trimesh(
                vertices=np.asarray(v, dtype=np.float64),
                faces=np.asarray(f, dtype=np.int64),
                process=True,
            )
            b_fix = _boundary_edge_count(m_fix)
            if b_fix < boundary_after:
                m = m_fix
                boundary_after = b_fix
                print(f"  Arestas de fronteira após pymeshfix: {boundary_after}")
        except ImportError:
            print("  pymeshfix não disponível, pulando fallback.")
        except Exception as e:
            print(f"  pymeshfix falhou: {e}")

    if boundary_after > 0 and not m.is_watertight:
        print("  Tentando pymeshlab close_holes com limite maior...")
        with tempfile.TemporaryDirectory(prefix="plate_repair2_") as tmpdir:
            in_ply = str(Path(tmpdir) / "in.ply")
            out_ply = str(Path(tmpdir) / "out.ply")
            m.export(in_ply)
            ms2 = pymeshlab.MeshSet()
            ms2.load_new_mesh(in_ply)
            ms2.meshing_repair_non_manifold_edges()
            ms2.meshing_repair_non_manifold_vertices()
            ms2.meshing_close_holes(maxholesize=1000)
            ms2.save_current_mesh(out_ply)
            m_new2 = trimesh.load(out_ply, force="mesh")
            if len(m_new2.faces) > 0:
                b2 = _boundary_edge_count(m_new2)
                if b2 < boundary_after:
                    m = m_new2
                    boundary_after = b2

    trimesh_repair.fix_normals(m, multibody=True)

    print(f"  Arestas de fronteira final: {_boundary_edge_count(m)}")
    print(f"  Watertight: {m.is_watertight}")

    return m


def process_mesh(
    input_path: Path,
    output_path: Path,
    *,
    plate_coverage_threshold: float = 0.7,
    band_frac: float = 0.10,
    margin_frac: float = 0.06,
    taubin_steps: int = 3,
) -> dict:
    """Processa um mesh: detecta placas, remove, repara.

    Retorna dict com métricas antes/depois.
    """
    print(f"\n{'=' * 70}")
    print(f"Processando: {input_path.name}")
    print(f"{'=' * 70}")

    mesh = trimesh.load(str(input_path), force="mesh")
    stats_before = {
        "vertices": len(mesh.vertices),
        "faces": len(mesh.faces),
        "watertight": bool(mesh.is_watertight),
        "boundary_edges": _boundary_edge_count(mesh),
    }
    print(
        f"  Antes: {stats_before['vertices']:,} verts, {stats_before['faces']:,} faces, "
        f"watertight={stats_before['watertight']}"
    )

    print("\n[1/3] Detectando placas...")
    plates = detect_plates(
        mesh,
        plate_coverage_threshold=plate_coverage_threshold,
        band_frac=band_frac,
    )

    if not plates:
        print("  Nenhuma placa detectada — mesh OK.")
        return {
            "file": input_path.name,
            "plates_detected": 0,
            "before": stats_before,
            "after": stats_before,
            "skipped": True,
        }

    print(f"  {len(plates)} placa(s) detectada(s):")
    for p in plates:
        print(f"    - {p['axis_name']}-{p['side']}: coverage={p['coverage']:.2f}")

    print("\n[2/4] Removendo placas + margem...")
    cleaned = remove_plate_faces(mesh, plates, margin_frac=margin_frac)

    print("\n[3/4] Reparando mesh com pymeshlab...")
    repaired = repair_with_pymeshlab(cleaned, taubin_steps=taubin_steps)

    print("\n[4/5] Removendo componentes-placa desconectadas...")
    repaired, n_components_removed = remove_plate_components(repaired)

    if n_components_removed > 0 and not repaired.is_watertight:
        print("  Re-reparando após remoção de componentes...")
        repaired = repair_with_pymeshlab(repaired, taubin_steps=0)

    print("\n[5/5] Verificando placas residuais conectadas...")
    residual_plates = check_connected_plate_residual(repaired, plates)
    needs_discard = len(residual_plates) > 0

    if needs_discard:
        for rp in residual_plates:
            print(
                f"  PLACA CONECTADA: {rp['axis_name']}-{rp['side']} "
                f"coverage={rp['coverage']:.2f} — mesh irrecuperável, DESCARTAR"
            )
    else:
        print("  Nenhuma placa residual — mesh limpa.")

    stats_after = {
        "vertices": len(repaired.vertices),
        "faces": len(repaired.faces),
        "watertight": bool(repaired.is_watertight),
        "boundary_edges": _boundary_edge_count(repaired),
    }
    print(
        f"\n  Depois: {stats_after['vertices']:,} verts, {stats_after['faces']:,} faces, "
        f"watertight={stats_after['watertight']}" + (" ** DESCARTAR **" if needs_discard else "")
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    repaired.export(str(output_path), file_type="glb")
    print(f"  Salvo: {output_path}")

    return {
        "file": input_path.name,
        "plates_detected": len(plates),
        "plates": plates,
        "before": stats_before,
        "after": stats_after,
        "skipped": False,
        "components_removed": n_components_removed,
        "needs_discard": needs_discard,
        "residual_plates": residual_plates,
    }


def print_summary(results: list[dict]) -> None:
    """Imprime tabela resumo dos resultados."""
    print(f"\n{'=' * 70}")
    print("RESUMO")
    print(f"{'=' * 70}")
    print(f"\n{'Arquivo':<30} {'Placas':<8} {'Faces antes':<14} {'Faces depois':<14} {'WT':<6} {'Status':<12}")
    print("-" * 84)

    for r in results:
        before = r["before"]
        after = r["after"]
        wt = "Sim" if after["watertight"] else "Nao"
        if r.get("skipped"):
            status = "sem placa"
        elif r.get("needs_discard"):
            status = "DESCARTAR"
        else:
            status = "OK"
        print(
            f"{r['file']:<30} {r['plates_detected']:<8} "
            f"{before['faces']:>10,}    {after['faces']:>10,}    "
            f"{wt:<6} {status:<12}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Remove backing plates detectadas pelo quality check e repara a mesh",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Exemplos:
  %(prog)s meshes/chair_modern.glb -o output/chair_clean.glb
  %(prog)s meshes/                     # processa todos os .glb
  %(prog)s meshes/ --margin 0.08       # margem extra mais agressiva
  %(prog)s meshes/ --coverage 0.5      # threshold de detecção mais sensível
        """,
    )
    parser.add_argument(
        "input",
        type=Path,
        help="Arquivo .glb ou diretório com .glb",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Arquivo ou diretório de saída (default: <input_dir>/cleaned/)",
    )
    parser.add_argument(
        "--coverage",
        type=float,
        default=0.7,
        help="Threshold de cobertura para detectar placa (default: 0.7)",
    )
    parser.add_argument(
        "--band",
        type=float,
        default=0.10,
        help="Fração da altura do bbox para a banda de detecção (default: 0.10)",
    )
    parser.add_argument(
        "--margin",
        type=float,
        default=0.06,
        help="Fração extra acima da placa a remover (default: 0.06)",
    )
    parser.add_argument(
        "--taubin",
        type=int,
        default=3,
        help="Passos de Taubin smoothing pós-reparo (default: 3, 0=desativar)",
    )

    args = parser.parse_args()
    input_path = Path(args.input).resolve()

    if input_path.is_dir():
        glb_files = sorted(input_path.glob("*.glb"))
        if not glb_files:
            print(f"Nenhum .glb encontrado em {input_path}", file=sys.stderr)
            return 1
        print(f"Encontrados {len(glb_files)} arquivos .glb em {input_path}")
    elif input_path.is_file():
        glb_files = [input_path]
    else:
        print(f"Caminho não existe: {input_path}", file=sys.stderr)
        return 1

    results: list[dict] = []

    for glb in glb_files:
        if args.output and len(glb_files) == 1 and not args.output.is_dir():
            out = args.output.resolve()
        elif args.output:
            out = args.output.resolve() / f"{glb.stem}_clean.glb"
        else:
            out = glb.parent / "cleaned" / f"{glb.stem}_clean.glb"

        result = process_mesh(
            glb,
            out,
            plate_coverage_threshold=args.coverage,
            band_frac=args.band,
            margin_frac=args.margin,
            taubin_steps=args.taubin,
        )
        results.append(result)

    if len(results) > 1:
        print_summary(results)

    n_processed = sum(1 for r in results if not r.get("skipped"))
    n_watertight = sum(1 for r in results if r["after"]["watertight"])
    n_discard = sum(1 for r in results if r.get("needs_discard"))
    n_ok = n_processed - n_discard
    print(
        f"\nProcessados: {n_processed}/{len(results)}, "
        f"OK: {n_ok}, Descartar: {n_discard}, "
        f"Watertight: {n_watertight}/{len(results)}"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
