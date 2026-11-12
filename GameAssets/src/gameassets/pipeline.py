"""Pipeline orchestration: argv builders, post-processing, e master DAG.

Contém:

* Os builders de argv e helpers de subprocesso usados por ``batch``,
  ``resume`` e ``cli`` (ex.: ``_text3d_argv``, ``_paint3d_texture_argv``,
  ``_rigging3d_pipeline_argv``, ``_animator3d_game_pack_argv``).
* O orquestrador master pipeline (``run_master_pipeline`` /
  ``resume_master_pipeline``) com a sequência (Round 3):

      text3d topology-fix (``_clean``)
      -> simplify ``_to_paint`` (orçamento atlas) -> paint3d (``_painted``)
      -> rigging3d sobre o PAINTED (``_rigged``)        [assets com rig]
      -> animator3d game-pack x1 (``_rigged_animated``) [assets com animate]
      -> text3d lod sobre o animated/rigged -> lod0/1/2 (decimate preserva
         skins+clips; estático: bake-master identity + lod do painted)
      -> collision -> validate

  Sem ``_rigged_hi`` (rig sobre ``_clean`` HI sem textura, descartado) nem
  ``transfer-weights`` por LOD (KDTree x3): o rig corre uma vez sobre a
  topologia final e a ladder herda tudo por decimação.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import shutil
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from . import multiview
from .categories import (
    animator_preset_for_category,
    category_wants_bake_normals,
    get_lod_ref_m,
    get_target_faces,
)
from .helpers import (
    _append_gpu_kill_flag,
    _resolve_rocks3d_bin,
    _row_wants_animate,
    _row_wants_rig,
    _seed_for_row,
    _timing_append,
    effective_face_ratio,
)
from .manifest import ManifestRow
from .param_optimizer import (
    optimize_paint_for_target,
    optimize_text3d_for_target,
    should_optimize_text3d,
)
from .paths import (
    _animated_existing,
    _animated_path,
    _base_stem,
    _canonical_mesh_final,
    _clean_existing,
    _clean_path,
    _collision_path,
    _glb_has_geometry,
    _glb_is_promoted_animated,
    _glb_is_promoted_rigged,
    _intermediate_dir,
    _lod_path,
    _painted_existing,
    _painted_path,
    _path_for_log,
    _precompute_path,
    _refined_existing,
    _refined_path,
    _rigged_existing,
    _rigged_path,
    _shape_existing,
    _shape_path,
    _shell_path,
    _split_path,
    _stump_path,
    _to_paint_existing,
    _to_paint_path,
    _top_path,
    _unsplit_lod0_path,
    archive_legacy_rig_intermediates,
    finalize_mesh_deliverables,
    move_to_intermediate,
)
from .profile import Animator3DProfile, GameProfile, Paint3DProfile, Rigging3DProfile, Rocks3DProfile
from .runner import merge_subprocess_output, resolve_binary, run_cmd

try:
    from aigamekit_shared.subprocess_utils import run_cmd_streaming as _run_cmd_streaming
except ImportError:  # pragma: no cover
    _run_cmd_streaming = None  # type: ignore[assignment]

console = Console()
log = logging.getLogger(__name__)

# Último nível da ladder que ainda leva rig; acima disso o entregável é mesh
# estático (``text3d lod --rig-max-level``). Um esqueleto por nível custa no
# runtime mesmo quando o nível está escondido — o motor percorre e recompõe
# todos os nós da cena por frame, e quase todos esses nós estavam em níveis de
# LOD que ninguém vê animar.
RIG_MAX_LEVEL = 1


def _resolve_animator3d_bin() -> str | None:
    try:
        return resolve_binary("ANIMATOR3D_BIN", "animator3d")
    except FileNotFoundError:
        return None


def _count_faces_glb(path: Path) -> int:
    """Count total triangles in a GLB file by parsing the binary header (no bpy required)."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 20 or data[:4] != b"glTF":
            return -1
        json_len = struct.unpack_from("<I", data, 12)[0]
        chunk = json.loads(data[20 : 20 + json_len])
        accessors = chunk.get("accessors", [])
        faces = 0
        for m in chunk.get("meshes", []):
            for p in m.get("primitives", []):
                idx = p.get("indices")
                if idx is not None and idx < len(accessors):
                    faces += accessors[idx].get("count", 0) // 3
        return faces
    except Exception:
        return -1


def _release_vramd_before_external_stage(stage_name: str) -> None:
    """``vramd zero`` antes de stages que NÃO passam pelo supervisor.

    rigging3d/animator3d correm como CLIs externos e precisam da GPU limpa —
    mas os workers do vramd (ex.: o paint3d, que segura ~4 GiB) ficam vivos a
    segurar VRAM depois das fases anteriores e o stage externo OOMa numa GPU
    de 6 GB (medido: rigging3d com 297 MiB livres a falhar).
    """
    try:
        from aigamekit_shared.vramd_client import zero_vramd_vram

        if zero_vramd_vram() is not None:
            log.info("master: VRAM do vramd zerada antes do stage %s (CLI externo)", stage_name)
    except Exception:
        pass


def _rigging3d_pipeline_argv(
    rigging3d_bin: str,
    mesh_in: Path,
    mesh_out: Path,
    *,
    seed: int | None,
    rig_profile: Rigging3DProfile | None,
    gpu_ids: list[int] | None = None,
    hw_auto: bool = True,
    quality: str | None = None,
    gpu: str | None = None,
) -> list[str]:
    args = [rigging3d_bin]
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    if not hw_auto:
        args.append("--no-hw-auto")
    args.append("pipeline")
    if gpu in ("remote", "local"):
        # Override por asset; ``auto`` deixa a política global (VRAMD_GPU_MODE).
        args.extend(["--gpu", gpu])
    args.extend(["--input", str(mesh_in), "--output", str(mesh_out)])
    if seed is not None:
        args.extend(["--seed", str(seed)])
    if quality:
        args.extend(["--quality", quality])
    if rig_profile:
        if rig_profile.root:
            args.extend(["--root", rig_profile.root])
        if rig_profile.python:
            args.extend(["--python", rig_profile.python])
    return args


def _animator3d_game_pack_argv(
    animator3d_bin: str,
    rig_out: Path,
    anim_out: Path,
    *,
    preset: str,
    clips: str | None = None,
    procedural: bool = False,
    force_preset: bool = False,
    anim_pack: str = "quaternius",
    ik_limits: bool | None = None,
) -> list[str]:
    args = [
        animator3d_bin,
        "game-pack",
        _shell_path(rig_out),
        _shell_path(anim_out),
        "--preset",
        preset,
    ]
    if clips:
        args.extend(["--clips", clips])
    if procedural:
        args.append("--procedural")
    if force_preset:
        args.append("--force-preset")
    if anim_pack and anim_pack != "quaternius":
        args.extend(["--anim-pack", anim_pack])
    if ik_limits is False:
        args.append("--no-ik-limits")
    return args


def _lod_output_paths(mesh_path: Path, basename: str, num_levels: int = 3) -> list[Path]:
    """Espera-se: {mesh_dir}/{basename}_lod0.glb … _lod{N-1}.glb."""
    d = mesh_path.parent
    return [d / f"{basename}_lod{i}.glb" for i in range(num_levels)]


def _collision_output_path(mesh_path: Path) -> Path:
    """Espera-se: {meshes_dir}/{id}_collision.glb (nunca em ``_intermediate/``)."""
    return _collision_path(mesh_path)


def collision_source(
    mesh_final: Path,
    painted_p: Path,
    lod0_p: Path,
    *,
    rigged: bool,
    allow_refined: bool = True,
) -> Path:
    """Escolhe a malha de onde nasce a colisão.

    **Riggados** → ``lod0``. O ``_painted``/``_rigged`` estão em T-pose (braços
    abertos) e o ``lod0`` é o que a engine desenha: o herói mede 1.27 m de
    largura em T-pose contra 0.67 m posado, portanto a cápsula do
    ``precompute`` saía com quase o dobro do raio e o personagem prendia em
    portas e esquinas. Medido em 14 personagens entregues: desvio de 0.29 a
    0.80 m entre a colisão e o visual.

    **Estáticos** → ``_refined`` quando existe (é a geometria que o paint e o
    ``lod0`` usam, logo a colisão fica alinhada com o que se desenha). O refine
    Hi3D pode mudar o enquadramento (city_gate_arch +0.24 m; scarecrow) e a
    colisão vinda do ``_clean`` ficava até 0.34 m fora. Fallback: ``_clean``
    (fechado em 108/109 assets; o envelope voxel precisa de superfície fechada
    — ver ``text3d.utils.collision._apply_envelope``), ``_painted`` e ``lod0``.
    """
    if rigged:
        for cand in (lod0_p, painted_p):
            if cand and cand.is_file():
                return cand
        return mesh_final
    for cand in (
        _refined_existing(mesh_final) if allow_refined else None,
        _clean_existing(mesh_final),
        painted_p,
        lod0_p,
    ):
        if cand and cand.is_file():
            return cand
    return mesh_final


# Desvio máximo tolerado entre a AABB da colisão e a do visual.
COLLISION_ALIGN_TOL_M = 0.15


def collision_alignment_deviation(coll_p: Path, lod0_p: Path) -> float | None:
    """Maior desvio (m) entre as AABB da colisão e do ``lod0``, ou ``None``.

    Uma colisão desalinhada é invisível em revisão e só aparece em jogo
    (jogador atravessa parede, ou preso numa cápsula grande demais). Devolve
    ``None`` — sem veredicto — quando o ``lod0`` é skinned: os ``min``/``max``
    dos accessors de uma malha com skin estão em espaço de bind, portanto a
    comparação seria contra a T-pose e não contra o que se desenha.
    """
    from aigamekit_shared.glb_verify import extract_glb_meta, glb_world_bounds

    if not (coll_p.is_file() and lod0_p.is_file()):
        return None
    if extract_glb_meta(lod0_p).get("has_joints"):
        return None
    a = glb_world_bounds(coll_p)
    b = glb_world_bounds(lod0_p)
    if not a or not b:
        return None
    return max(max(abs(a[i][j] - b[i][j]) for j in range(3)) for i in range(2))


_TREE_LIKE_NAME_RE = re.compile(
    r"(?:^|[^a-z0-9])(tree|pine|oak|willow|cactus|fir|spruce|palm)(?:[^a-z0-9]|$)",
    re.IGNORECASE,
)


def _row_bbox_preset(row: ManifestRow) -> str | None:
    """``bbox_preset`` efectivo: override Omni da row ou default da categoria."""
    omni = row.omni
    if omni is not None:
        bp = omni.get("bbox_preset") if isinstance(omni, dict) else getattr(omni, "bbox_preset", None)
        if bp:
            return str(bp).strip().lower()
    cat = (row.category or "").strip().lower()
    if not cat:
        return None
    try:
        from .omni_ctrl import _category_omni_defaults

        return (_category_omni_defaults(cat).get("bbox_preset") or "").strip().lower() or None
    except Exception:
        if cat in {"tree", "vegetation"}:
            return "tree"
        return None


def is_tree_like_asset(row: ManifestRow) -> bool:
    """Árvore derrubável: category tree/vegetation + bbox tree ou nome típico."""
    cat = (row.category or "").strip().lower()
    if cat not in {"tree", "vegetation"}:
        return False
    if (_row_bbox_preset(row) or "") == "tree":
        return True
    blob = f"{row.id} {row.idea or ''}"
    return bool(_TREE_LIKE_NAME_RE.search(blob))


def _row_refine_disabled(row: ManifestRow | None) -> bool:
    """True quando o manifest desliga o Hi3D neste asset (``refine: false``).

    Vegetação de ramos finos (dead_willow) sai do refine com centenas de ramos
    soltos: o atlas do paint fragmenta-se e o LOD0 não desce ao orçamento da
    categoria. Desligado, o paint e o LOD partem do ``clean`` (mais contínuo).
    """
    return bool(row is not None and row.refine is not None and row.refine.enabled is False)


def wants_split_at_height(profile: GameProfile, row: ManifestRow) -> bool:
    """True quando o asset deve correr ``text3d split-at-height``.

    Override por asset (``text3d.split_at_height`` no manifest) ganha do profile.
    ``false`` desliga. ``true`` ou default (omitido) só activa assets tree-like
    (vegetation/tree + bbox_preset tree ou nome típico) — rocks/props nunca,
    mesmo com flag global true.
    """
    rt3 = row.text3d
    if rt3 is not None and rt3.split_at_height is not None:
        return bool(rt3.split_at_height)
    t3 = profile.text3d
    if t3 is not None and t3.split_at_height is False:
        return False
    return is_tree_like_asset(row)


def _split_seal_stamp_path(mesh_final: Path) -> Path:
    mesh_final = _canonical_mesh_final(mesh_final)
    base = mesh_final.stem
    # stem pode ser id.glb → base = id
    from .paths import _base_stem, _intermediate_dir

    return _intermediate_dir(mesh_final) / f"{_base_stem(base)}_split_seal.txt"


def invalidate_split_artifacts(mesh_final: Path) -> list[Path]:
    """Apaga derivados do split (mantém ``*_painted.glb`` unsplit).

    Remove stump/top painted, dirs LOD por peça, lods compostos, collisions
    stump/top/fallback e temp de split — para resume a partir do seal novo.
    """
    from .paths import _base_stem, _collision_path, _intermediate_dir, _lod_path

    mesh_final = _canonical_mesh_final(mesh_final)
    base = _base_stem(mesh_final.stem)
    inter = _intermediate_dir(mesh_final)
    removed: list[Path] = []

    def _rm(p: Path) -> None:
        if p.is_file():
            p.unlink(missing_ok=True)
            removed.append(p)
        elif p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
            removed.append(p)

    for name in (
        f"{base}_stump_painted.glb",
        f"{base}_top_painted.glb",
        f"{base}_split_tmp.glb",
        f"{base}_split_seal.txt",
    ):
        _rm(inter / name)
    for dname in (f"{base}_stump_lod", f"{base}_top_lod"):
        _rm(inter / dname)
    for lvl in (0, 1, 2):
        _rm(_lod_path(mesh_final, lvl))
    parent = mesh_final.parent
    for name in (
        f"{base}_stump_collision.glb",
        f"{base}_top_collision.glb",
        f"{base}_stump.glb",
        f"{base}_top.glb",
        f"{base}_split.glb",
    ):
        _rm(parent / name)
    _rm(_collision_path(mesh_final))
    # Unsplit backup do path legado (não apagar painted).
    return removed


def _split_seal_stale(mesh_final: Path) -> bool:
    """True se falta stamp ou o seal no disco ≠ ``SEAL_VERSION`` actual."""
    try:
        from aigamekit_shared.mesh_split import SEAL_VERSION
    except Exception:
        SEAL_VERSION = "bisect-fill-v1"
    stamp = _split_seal_stamp_path(mesh_final)
    if not stamp.is_file():
        # Artefactos split existem sem stamp → assumir stale (seal antigo).
        inter = _intermediate_dir(mesh_final)
        from .paths import _base_stem

        base = _base_stem(mesh_final.stem)
        return (inter / f"{base}_stump_painted.glb").is_file()
    return stamp.read_text(encoding="utf-8").strip() != str(SEAL_VERSION)


def _write_split_seal_stamp(mesh_final: Path) -> None:
    try:
        from aigamekit_shared.mesh_split import SEAL_VERSION
    except Exception:
        SEAL_VERSION = "bisect-fill-v1"
    stamp = _split_seal_stamp_path(mesh_final)
    stamp.parent.mkdir(parents=True, exist_ok=True)
    stamp.write_text(f"{SEAL_VERSION}\n", encoding="utf-8")


def _split_at_height_done(mesh_final: Path) -> bool:
    """Resume: unsplit arquivado + composição ``id_split.glb`` já existem."""
    return _unsplit_lod0_path(mesh_final).is_file() and _split_path(mesh_final).is_file()


def run_split_at_height_stage(
    *,
    text3d_bin: str,
    mesh_final: Path,
    profile: GameProfile,
    run_stage: Any,
) -> StageResult:
    """Backup LOD0 unsplit → ``text3d split-at-height`` → promove composição a LOD0.

    Também escreve ``id_split.glb`` e, se ``split_files``, ``id_stump`` / ``id_top``.
    """

    lod0 = _lod_path(mesh_final, 0)
    if not lod0.is_file():
        return StageResult("split-at-height", False, 0.0, "lod0 ausente")

    if _split_at_height_done(mesh_final) and lod0.is_file():
        return StageResult("split-at-height", True, 0.0, "skipped (split existente)", _split_path(mesh_final))

    unsplit = _unsplit_lod0_path(mesh_final)
    unsplit.parent.mkdir(parents=True, exist_ok=True)
    if not unsplit.is_file():
        shutil.copy2(lod0, unsplit)

    split_out = _split_path(mesh_final)
    t3 = profile.text3d
    cut_h = None if t3 is None else t3.split_cut_height
    want_files = True if t3 is None else bool(t3.split_files)

    argv = [
        text3d_bin,
        "split-at-height",
        str(unsplit),
        "-o",
        str(split_out),
        "--no-cap",
    ]
    if cut_h is not None:
        argv.extend(["--cut-height", str(float(cut_h))])
    if want_files:
        argv.append("--split-files")

    s = run_stage("split-at-height", argv, split_out)
    if not s.ok or not split_out.is_file():
        return s

    # Promove composição a LOD0 (árvore = Stump+Top).
    shutil.copy2(split_out, lod0)

    if want_files:
        # CLI escreve {stem}_stump / {stem}_top a partir do stem de -o (id_split).
        raw_stump = split_out.with_name(f"{split_out.stem}_stump{split_out.suffix}")
        raw_top = split_out.with_name(f"{split_out.stem}_top{split_out.suffix}")
        stump = _stump_path(mesh_final)
        top = _top_path(mesh_final)
        if raw_stump.is_file() and raw_stump.resolve() != stump.resolve():
            shutil.move(str(raw_stump), str(stump))
        if raw_top.is_file() and raw_top.resolve() != top.resolve():
            shutil.move(str(raw_top), str(top))

    cut_msg = f"cut_height={cut_h}" if cut_h is not None else "cut=min(0.8,h/4)"
    return StageResult(
        "split-at-height",
        True,
        s.elapsed_s,
        f"{cut_msg} → {split_out.name} (+lod0)",
        split_out,
    )


def _post_text3d_mesh_extras(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    rigging3d_bin: str | None,
    with_rig: bool,
    with_animate: bool,
    animator3d_bin: str | None = None,
    has_rigging_profile: bool = False,
    gpu_ids: list[int] | None = None,
    with_lod: bool = False,
    with_collision: bool = False,
    with_validate: bool | None = None,
    bake_normals: bool | None = None,
    on_progress_line: Any = None,
) -> bool:
    """Define mesh_path e corre o master pipeline (Round 3: rig sobre painted -> animate x1 -> ladder).

    Devolve True se algum passo falhou. O master pipeline é agora o único
    caminho - o antigo fluxo linha-a-linha (text3d lod / rigging3d /
    animator3d) foi removido.
    """
    mesh_final = _canonical_mesh_final(mesh_final)
    rec["mesh_path"] = _path_for_log(mesh_final, manifest_dir)

    if with_validate is None:
        with_validate = bool(getattr(profile, "master_validate", True))
    if bake_normals is None:
        bake_normals = bool(getattr(profile, "master_bake_normals", False))

    # Filtragem por-row: respeita ``manifest.pipeline`` (ex.: ``wooden_crate``
    # com ``pipeline: [3d, paint, lod, collision]`` não deve correr rig).
    row_wants_rig = _row_wants_rig(row, has_rigging_profile)
    row_wants_animate = _row_wants_animate(row, with_rig, has_rigging_profile)
    effective_with_rig = with_rig and row_wants_rig and (rigging3d_bin is not None)
    effective_with_animate = with_animate and row_wants_animate and (animator3d_bin is not None)
    # ``manifest.pipeline`` também filtra lod/collision: o batch ligava
    # with_lod/with_collision para o manifest inteiro e um cliff com
    # ``[3d, paint, collision]`` recebia ladder na mesma.
    effective_with_lod = bool(with_lod and getattr(row, "generate_lod", True))
    effective_with_collision = bool(with_collision and getattr(row, "generate_collision", True))
    from .vramd_coord import MASTER_FORCE

    mres = run_master_pipeline(
        profile,
        row,
        mesh_final,
        manifest_dir=manifest_dir,
        child_env=child_env,
        with_lod=effective_with_lod,
        with_collision=effective_with_collision,
        with_rig=effective_with_rig,
        with_animate=effective_with_animate,
        with_validate=with_validate,
        bake_normals=bake_normals,
        on_progress_line=on_progress_line,
        gpu_ids=gpu_ids,
        force=bool(MASTER_FORCE.get()),
    )
    aggregate_master_results(mres.stages, rec)
    if mres.lod0_path and mres.lod0_path.is_file():
        rec["lod0_path"] = _path_for_log(mres.lod0_path, manifest_dir)
    if mres.intermediates_dir is not None:
        rec["intermediates_dir"] = _path_for_log(mres.intermediates_dir, manifest_dir)
    _emit_precompute(row, mres, rec, manifest_dir, child_env)
    if not mres.ok:
        # validate-lod1/2 são só avisos: listar primeiro o que fez o gate falhar.
        failed = [s for s in mres.stages if not s.ok and s.error]
        failed.sort(key=lambda s: s.name in ("validate-lod1", "validate-lod2"))
        errors = [f"{s.name}: {s.error}" for s in failed]
        rec["status"] = "error"
        rec["error"] = "; ".join(errors[:3]) or "master pipeline falhou"
        console.print(f"[red]master pipeline falhou[/red] {row.id}: {rec['error'][:200]}")
        return True
    return False


def _emit_precompute(
    row: ManifestRow,
    mres: MasterPipelineResult,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
) -> None:
    """Gera o sidecar ``{id}_precompute.json`` (colisor cápsula/cilindro).

    Roda depois do master pipeline OK — batch, dash e resume partilham este
    hook. Sem ``aigamekit-lab`` no PATH ou falha → warn + skip (soft): o
    handoff omite o bloco e a engine degrada para AABB-fit.

    Input preferencial: o ``*_collision.glb`` (uncompressed — meshopt é pulado
    nos collision); árvores split passam ``--stump <id>_stump_collision.glb``
    para a cápsula nascer do tronco exato.
    """
    bin_ = _bin_or_none("AIGAMEKITLAB_BIN", "aigamekit-lab")
    if not bin_:
        console.print("[yellow]precompute: aigamekit-lab não encontrado no PATH — a omitir sidecar[/yellow]")
        return
    if not mres.lod0_path or not Path(mres.lod0_path).is_file():
        return
    lod0 = Path(mres.lod0_path)
    out = _precompute_path(lod0)
    source = _collision_path(lod0)
    if not source.is_file():
        source = lod0

    argv = [bin_, "precompute", str(source), "-o", str(out), "--asset-id", row.id]
    if row.category:
        argv.extend(["--category", row.category])
    stump_coll = _stump_path(lod0).parent / f"{_base_stem(lod0.stem)}_stump_collision.glb"
    if stump_coll.is_file():
        argv.extend(["--stump", str(stump_coll)])

    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    if r.returncode != 0:
        console.print(f"[yellow]precompute falhou (rc={r.returncode}) — a omitir sidecar {row.id}[/yellow]")
        return
    try:
        payload = json.loads(out.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        console.print(f"[yellow]precompute: saída inválida — a omitir sidecar {row.id}[/yellow]")
        with contextlib.suppress(OSError):
            out.unlink()
        return
    if payload.get("error"):
        console.print(f"[yellow]precompute: {payload['error']} — a omitir sidecar[/yellow]")
        with contextlib.suppress(OSError):
            out.unlink()
        return
    rec["precompute_path"] = _path_for_log(out, manifest_dir)


def _try_paint3d_bin() -> str | None:
    try:
        return resolve_binary("PAINT3D_BIN", "paint3d")
    except FileNotFoundError:
        return None


def _paint3d_quick_argv(
    paint3d_bin: str,
    p3: Paint3DProfile,
    mesh_in: Path,
    mesh_out: Path,
    *,
    row_seed: int | None,
) -> list[str]:
    """Subcomando ``paint3d quick`` - cor sólida ou ruído Perlin/FBM (sem IA)."""
    style = (p3.style or "hunyuan").strip().lower()
    if style not in ("solid", "perlin"):
        raise RuntimeError(f"paint_style inválido para quick: {style!r}")
    eff = p3.perlin_seed
    if eff is None:
        eff = row_seed
    if eff is None:
        eff = 0

    args: list[str | Path] = [
        paint3d_bin,
        "quick",
        str(mesh_in),
        "-o",
        str(mesh_out),
        "--style",
        style,
    ]
    if style == "solid":
        args.extend(["--color", p3.solid_color])
    else:
        args.extend(
            [
                "--tint",
                p3.perlin_tint,
                "--frequency",
                str(p3.perlin_frequency),
                "--octaves",
                str(p3.perlin_octaves),
                "--seed",
                str(int(eff)),
                "--contrast",
                str(p3.perlin_contrast),
            ]
        )
    if p3.preserve_origin:
        args.append("--preserve-origin")
    else:
        args.append("--no-preserve-origin")
    return [str(x) for x in args]


def _paint3d_texture_argv(
    paint3d_bin: str,
    p3: Paint3DProfile | None,
    mesh_in: Path,
    image_path: Path,
    mesh_out: Path,
    gpu_ids: list[int] | None = None,
    hw_auto: bool = True,
    *,
    quality: str | None = None,
    category: str | None = None,
    texture_size: int | None = None,
) -> list[str]:
    """Subcomando ``paint3d texture`` (Hunyuan3D-Paint 2.1; saída GLB com material PBR)."""
    args = [
        paint3d_bin,
        "texture",
        str(mesh_in),
        "--image",
        str(image_path),
        "-o",
        str(mesh_out),
    ]
    if quality:
        args.extend(["--quality", quality])
    if category:
        args.extend(["--category", category])
    if p3 is None:
        if texture_size is not None and int(texture_size) > 0:
            args.extend(["--texture-size", str(int(texture_size))])
        return args
    if p3.max_views is not None:
        args.extend(["--max-views", str(p3.max_views)])
    if p3.view_resolution is not None:
        args.extend(["--view-resolution", str(p3.view_resolution)])
    if p3.render_size is not None:
        args.extend(["--render-size", str(p3.render_size)])
    tex = texture_size if texture_size is not None else p3.texture_size
    if tex is not None and int(tex) > 0:
        args.extend(["--texture-size", str(int(tex))])
    if p3.bake_exp is not None:
        args.extend(["--bake-exp", str(p3.bake_exp)])
    if p3.preserve_origin:
        args.append("--preserve-origin")
    else:
        args.append("--no-preserve-origin")
    if p3.smooth:
        args.append("--smooth")
    else:
        args.append("--no-smooth")
    if p3.smooth_passes is not None:
        args.extend(["--smooth-passes", str(p3.smooth_passes)])
    # Normal+AO derivados do albedo do atlas (bordos de ilha = arestas
    # falsas). O perfil do Vael desliga-o.
    args.append("--pbr-enrich" if p3.pbr_enrich else "--no-pbr-enrich")
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    if not hw_auto:
        args.append("--no-hw-auto")
    return args


def _texture_subprocess_argv(
    paint3d_bin: str,
    profile: GameProfile,
    mesh_in: Path,
    image_path: Path,
    mesh_out: Path,
    *,
    row_id: str | None = None,
    row: ManifestRow | None = None,
    gpu_ids: list[int] | None = None,
) -> list[str]:
    p3 = profile.paint3d or Paint3DProfile()
    # Overrides por linha (bloco ``paint3d:`` do manifest): views/render/tex.
    if row is not None and row.paint3d:
        import dataclasses as _dc

        _over = {
            k: v
            for k, v in row.paint3d.items()
            if k in ("max_views", "view_resolution", "render_size", "texture_size", "bake_exp")
        }
        if _over:
            p3 = _dc.replace(p3, **_over)
    # Personagens: mais vistas/resolução que o default do tier, sem pisar um
    # override explícito do manifest (``paint3d.max_views``/``view_resolution``).
    char_views, char_res = _character_paint_opts(row)
    if char_views is not None or char_res is not None:
        import dataclasses as _dc

        _char_over: dict[str, int] = {}
        if p3.max_views is None and char_views is not None:
            _char_over["max_views"] = char_views
        if p3.view_resolution is None and char_res is not None:
            _char_over["view_resolution"] = char_res
        if _char_over:
            p3 = _dc.replace(p3, **_char_over)
    effective_style = (p3.style or "hunyuan").strip().lower()
    if (
        profile.paint3d is not None
        and row
        and row.category
        and p3.max_views is None
        and p3.view_resolution is None
        and p3.texture_size is None
    ):
        target = _resolve_lod_target_faces(profile, row)
        paint_opts = optimize_paint_for_target(target)
        if paint_opts.style:
            effective_style = paint_opts.style
    if effective_style in ("solid", "perlin"):
        row_seed = _seed_for_row(profile, row_id) if row_id else None
        return _paint3d_quick_argv(paint3d_bin, p3, mesh_in, mesh_out, row_seed=row_seed)
    return _paint3d_texture_argv(
        paint3d_bin,
        p3,
        mesh_in,
        image_path,
        mesh_out,
        gpu_ids=gpu_ids,
        hw_auto=profile.hw_auto,
        quality=profile.generation,
        category=row.category if row else None,
        texture_size=_resolve_paint_texture_size(profile, row),
    )


def _remesh_shape_to_target(
    mesh_path: Path,
    row: ManifestRow,
    text3d_bin: str,
    *,
    run_cmd,
    child_env: dict[str, str],
    cwd: Path,
    manifest_dir: Path,
    rec: dict[str, Any],
    gpu_ids: list[int] | None = None,  # kept for call-site compat
) -> bool:
    """Isotropic remesh (geometry only) to target_faces via ``text3d remesh``.

    CPU-only (pymeshlab).  Returns True on error.
    """
    if not row.category:
        return False
    target = get_target_faces(row.category)  # sem profile/char — legado shape remesh
    if target <= 0:
        return False
    current_faces = _count_faces_glb(mesh_path)
    if current_faces < 0:
        return False
    if current_faces <= target:
        return False
    if current_faces < target * 1.2:
        return False

    console.print(f"[cyan]⏳ Remesh[/cyan] {row.id} ({current_faces:,} -> ~{target:,} faces)")

    remeshed = mesh_path.parent / f"{mesh_path.stem}_remeshed{mesh_path.suffix}"

    argv = [
        text3d_bin,
        "remesh",
        str(mesh_path),
        "-o",
        str(remeshed),
        "--target-faces",
        str(target),
    ]
    r = run_cmd(argv, extra_env=child_env, cwd=cwd)
    if r.returncode != 0 or not remeshed.is_file():
        err = merge_subprocess_output(r) or "remesh falhou"
        console.print(f"[yellow]remesh falhou[/yellow] {row.id}: {err[:200]}")
        return True

    remeshed.replace(mesh_path)
    rec["remesh_ratio"] = round(target / current_faces, 4)
    rec["remesh_faces_before"] = current_faces
    console.print(f"[green]✓ Remesh[/green] {row.id}")
    return False


def _remesh_textured_to_target(
    mesh_path: Path,
    row: ManifestRow,
    text3d_bin: str,
    *,
    profile: GameProfile | None = None,
    run_cmd,
    child_env: dict[str, str],
    cwd: Path,
    manifest_dir: Path,
    rec: dict[str, Any],
) -> bool:
    """Isotropic remesh with texture reprojection via ``text3d remesh-textured``.

    CPU-only (pymeshlab + xatlas).  Returns True on error.
    """
    if not row.category:
        return False
    target = _resolve_lod_target_faces(profile, row) if profile is not None else get_target_faces(row.category)
    if target <= 0:
        return False
    current_faces = _count_faces_glb(mesh_path)
    if current_faces < 0:
        return False
    if current_faces <= target:
        return False
    if current_faces < target * 1.2:
        return False

    console.print(f"[cyan]⏳ Simplify (textured)[/cyan] {row.id} ({current_faces:,} -> ~{target:,} faces)")

    simplified = mesh_path.parent / f"{mesh_path.stem}_simplified{mesh_path.suffix}"

    argv = [
        text3d_bin,
        "remesh-textured",
        str(mesh_path),
        "-o",
        str(simplified),
        "--target-faces",
        str(target),
    ]
    t3 = profile.text3d if profile else None
    if t3 and t3.simplify_texture_size is not None:
        argv.extend(["--texture-size", str(t3.simplify_texture_size)])
    r = run_cmd(argv, extra_env=child_env, cwd=cwd)
    if r.returncode != 0 or not simplified.is_file():
        err = merge_subprocess_output(r) or "remesh-textured falhou"
        console.print(f"[yellow]remesh-textured falhou[/yellow] {row.id}: {err[:200]}")
        return True

    simplified.replace(mesh_path)
    rec["remesh_textured_ratio"] = round(target / current_faces, 4)
    rec["remesh_textured_faces_before"] = current_faces
    console.print(f"[green]✓ Simplify (textured)[/green] {row.id}")
    return False


def _simplify_to_target(
    mesh_path: Path,
    row: ManifestRow,
    text3d_bin: str,
    *,
    profile: GameProfile | None = None,
    run_cmd,
    child_env: dict[str, str],
    cwd: Path,
    manifest_dir: Path,
    rec: dict[str, Any],
) -> bool:
    """Simplify mesh via text3d remesh (delegated to text3d via subprocess).

    Legacy-only: no master pipeline o LOD0 nasce do ``bake-master`` (decimação
    com bake de normais a partir do HI). Simplificar aqui destruiria o
    ``_shape``/``_painted`` high-poly **in-place** - fonte de bake/transfer.
    """
    if profile is not None and getattr(profile, "master_pipeline", True):
        return False
    if not row.category:
        return False
    target = _resolve_lod_target_faces(profile, row) if profile is not None else get_target_faces(row.category)
    if target <= 0:
        return False
    current_faces = _count_faces_glb(mesh_path)
    if current_faces < 0:
        return False  # can't read mesh
    if current_faces <= target:
        return False
    if current_faces < target * 1.2:
        return False

    return _remesh_textured_to_target(
        mesh_path,
        row,
        text3d_bin,
        profile=profile,
        run_cmd=run_cmd,
        child_env=child_env,
        cwd=cwd,
        manifest_dir=manifest_dir,
        rec=rec,
    )


def resolve_row_omni(
    profile: GameProfile,
    row: ManifestRow | None,
    *,
    manifest_dir: Path | None = None,
) -> Any:
    """Merge ``text3d.omni`` (profile) + ``row.omni``; soft-fill categoria; ``point_from``."""
    from .omni_ctrl import OmniControls, merge_omni, resolve_point_from, softfill_omni_from_category

    base = OmniControls()
    t3 = profile.text3d
    if t3 is not None and getattr(t3, "omni", None) is not None:
        base = t3.omni
    override = OmniControls()
    if row is not None and getattr(row, "omni", None) is not None:
        override = row.omni
    merged = merge_omni(base, override)
    cat = None
    if row is not None:
        cat = getattr(row, "category", None) or getattr(row, "kind", None)
    merged = softfill_omni_from_category(merged, cat)
    if merged.point_from and manifest_dir is not None:
        from .paths import _shape_existing, _shape_path

        sibling_mesh = manifest_dir / "meshes" / f"{merged.point_from}.glb"
        # Prefer shape intermediate of sibling
        sib_shape = _shape_existing(sibling_mesh) or _shape_path(sibling_mesh)
        merged = resolve_point_from(merged, sibling_shape=sib_shape)
    return merged


def _text3d_argv(
    text3d_bin: str,
    profile: GameProfile,
    image_path: Path,
    mesh_path: Path,
    row: ManifestRow | None = None,
    *,
    gpu_ids: list[int] | None = None,
    quality: str | None = None,
    category: str | None = None,
    manifest_dir: Path | None = None,
) -> list[str]:
    """Shape-only argv for ``text3d generate`` (image -> mesh, sem --texture).

    Paint is a separate step via ``paint3d texture``.
    """
    from .omni_ctrl import omni_to_cli_flags

    args = [
        text3d_bin,
        "generate",
        "--from-image",
        str(image_path),
        "-o",
        str(mesh_path),
        # Sidecar ``<stem>_input.png`` junto ao shape: é a imagem que o stage de
        # refine (Hi3D) usa como condição. Sem isto um ``text3d generate`` manual
        # (prompt → mesh) ficava sem referência para refinar.
        "--save-reference-image",
    ]
    if quality:
        args.extend(["--quality", quality])
    cat = category or (row.category if row is not None else None)
    if cat:
        args.extend(["--category", cat])
    t3 = profile.text3d
    # Overrides ``text3d:`` por asset (manifest) — ganham do profile/optimize.
    rt3 = row.text3d if row is not None else None
    if not t3:
        if rt3 is not None:
            if rt3.steps is not None:
                args.extend(["--steps", str(rt3.steps)])
            if rt3.octree_resolution is not None:
                args.extend(["--octree-resolution", str(rt3.octree_resolution)])
            if rt3.mc_level is not None:
                args.extend(["--mc-level", str(rt3.mc_level)])
        omni = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
        args.extend(omni_to_cli_flags(omni))
        return args

    if should_optimize_text3d(t3) and row is not None and row.category:
        target = _resolve_lod_target_faces(profile, row)
        opts = optimize_text3d_for_target(target)
        args.extend(["--steps", str(opts.steps)])
        args.extend(["--num-chunks", str(opts.num_chunks)])
        # Com size_m/height_m o Text3D ``bbox_tune`` sobe octree por metros.
        # Passar ``--octree-resolution`` aqui marca o eixo como override e
        # bloqueia o size-tune (buildings ficavam presos em 256).
        omni_pre = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
        if omni_pre.size_m is None and omni_pre.height_m is None:
            args.extend(["--octree-resolution", str(opts.octree_resolution)])
    else:
        explicit_hunyuan = t3.steps is not None or t3.octree_resolution is not None or t3.num_chunks is not None
        if t3.preset and not explicit_hunyuan:
            args.extend(["--preset", t3.preset])
        if t3.steps is not None:
            args.extend(["--steps", str(t3.steps)])
        if t3.octree_resolution is not None:
            args.extend(["--octree-resolution", str(t3.octree_resolution)])
        if t3.num_chunks is not None:
            args.extend(["--num-chunks", str(t3.num_chunks)])
    if t3.model_subfolder:
        args.extend(["--model-subfolder", t3.model_subfolder])
    if rt3 is not None:
        # Por asset (manifest) ganha do profile/optimize — flags repetidas:
        # click fica com a última ocorrência.
        if rt3.steps is not None:
            args.extend(["--steps", str(rt3.steps)])
        if rt3.octree_resolution is not None:
            args.extend(["--octree-resolution", str(rt3.octree_resolution)])
    _mc = rt3.mc_level if rt3 is not None and rt3.mc_level is not None else t3.mc_level
    if _mc is not None:
        args.extend(["--mc-level", str(_mc)])
    if getattr(t3, "bounds_mode", None):
        args.extend(["--bounds-mode", str(t3.bounds_mode)])
    if t3.guidance is not None:
        args.extend(["--guidance", str(t3.guidance)])
    if t3.allow_shared_gpu:
        args.append("--allow-shared-gpu")
    _append_gpu_kill_flag(args, t3.gpu_kill_others)
    if t3.full_gpu:
        args.append("--t2d-full-gpu")
    args.extend(["--export-origin", t3.export_origin])
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    if not profile.hw_auto:
        args.append("--no-hw-auto")
    omni = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
    args.extend(omni_to_cli_flags(omni))
    return args


def _rocks3d_argv(
    rocks3d_bin: str,
    rock_type: str,
    output_path: Path,
    *,
    seed: int | None = None,
    quality: str | None = None,
) -> list[str]:
    """Argv for ``rocks3d generate <type> --seed N --quality Q -o output.glb``."""
    args = [rocks3d_bin, "generate", rock_type]
    if seed is not None:
        args.extend(["--seed", str(seed)])
    if quality:
        args.extend(["--quality", quality])
    args.extend(["-o", str(output_path)])
    return args


def _row_is_rock(row: ManifestRow) -> bool:
    return (row.category or "").strip().lower() == "rock"


def _rocks3d_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    gpu_ids: list[int] | None = None,
) -> bool:
    """Run ``rocks3d generate`` for rock assets. Returns True on failure."""
    if not _row_is_rock(row) or not row.generate_3d:
        return False
    try:
        rocks3d_bin = _resolve_rocks3d_bin()
    except FileNotFoundError:
        return False

    rk = profile.rocks3d or Rocks3DProfile()
    seed = _seed_for_row(profile, row.id)
    quality = rk.quality or getattr(profile, "generation", None) or "medium"
    rock_type = row.kind or "boulder"
    console.print(f"[cyan]⏳ Rocks3D[/cyan] {row.id} (type={rock_type}) ...")
    t0 = time.perf_counter()
    argv = _rocks3d_argv(rocks3d_bin, rock_type, mesh_final, seed=seed, quality=quality)
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "rocks3d", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Rocks3D[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "rocks3d falhou"
        rec["status"] = "error"
        rec["error"] = err
        preview = merge_subprocess_output(r, max_chars=4000) or err
        console.print(f"[red]rocks3d falhou[/red] {row.id}: {preview}")
        return True
    if not mesh_final.is_file() and not _lod_path(mesh_final, 0).is_file():
        rec["status"] = "error"
        rec["error"] = "rocks3d não produziu GLB"
        console.print(f"[red]rocks3d sem GLB[/red] {row.id}")
        return True
    # Bare → lod0; meshes/ só deliverables _lod*.
    finalize_mesh_deliverables(mesh_final)
    lod0 = _lod_path(mesh_final, 0)
    rec["mesh_path"] = _path_for_log(lod0 if lod0.is_file() else mesh_final, manifest_dir)
    return False


@dataclass
class StageResult:
    name: str
    ok: bool
    elapsed_s: float
    error: str = ""
    output: Path | None = None


@dataclass
class MasterPipelineResult:
    asset_id: str
    ok: bool
    stages: list[StageResult] = field(default_factory=list)
    lod0_path: Path | None = None
    intermediates_dir: Path | None = None
    # Round 2 - observabilidade.
    total_elapsed_s: float = 0.0
    cumulative_vram_mb_peak: float = 0.0

    def recompute_totals(self) -> None:
        self.total_elapsed_s = round(sum(s.elapsed_s for s in self.stages), 2)


def _bin_or_none(name_env: str, name: str) -> str | None:
    try:
        return resolve_binary(name_env, name)
    except FileNotFoundError:
        return None


def _topology_fix_extra_argv(
    profile: GameProfile,
    row: ManifestRow | None = None,
    *,
    manifest_dir: Path | None = None,
) -> list[str]:
    """Flags de escala para ``text3d topology-fix`` (genérico por metros).

    ``morph_close`` (metros) row > profile → ``--morph-close``.
    ``morph_close_voxels`` / voxel_merge row > profile → ``--morph-close-voxels``.
    Omitido → auto Text3D (N por category: terrain/rock=3x default 0.18).
    ``--size-m`` também escala a mesh para metros reais no topology-fix.
    """
    args: list[str] = []
    t3 = profile.text3d
    rt3 = row.text3d if row is not None else None
    if t3 is not None and t3.export_origin:
        args.extend(["--export-origin", t3.export_origin])
    morph_m = None
    if rt3 is not None and rt3.morph_close is not None:
        morph_m = rt3.morph_close
    elif t3 is not None and t3.morph_close is not None:
        morph_m = t3.morph_close
    if morph_m is not None:
        args.extend(["--morph-close", str(morph_m)])
    morph_n = None
    if rt3 is not None and rt3.morph_close_voxels is not None:
        morph_n = rt3.morph_close_voxels
    elif t3 is not None and t3.morph_close_voxels is not None:
        morph_n = t3.morph_close_voxels
    if morph_n is not None:
        args.extend(["--morph-close-voxels", str(morph_n)])
    omni = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
    if omni.size_m is not None:
        args.extend(["--size-m", ",".join(str(x) for x in omni.size_m)])
    elif omni.height_m is not None:
        # expand height→size_m for topology scale hints
        from .omni_ctrl import expand_omni_world_size

        exp = expand_omni_world_size(omni, category=row.category if row else None)
        if exp.size_m is not None:
            args.extend(["--size-m", ",".join(str(x) for x in exp.size_m)])
    if omni.bbox_preset:
        args.extend(["--bbox-preset", omni.bbox_preset])
    cat = (row.category if row is not None else None) or None
    if cat:
        args.extend(["--category", str(cat)])
    if t3 is not None and t3.octree_resolution:
        args.extend(["--octree", str(int(t3.octree_resolution))])
    # Default explícito: engine arrays (CLI já defaulta arrays; força no batch).
    args.extend(["--engine", "arrays"])
    return args


def _reference_image_for(mesh_final: Path, profile: GameProfile) -> Path | None:
    """Localiza a imagem de referência (concept) usada no shape do asset.

    Ordem: sidecars do Text3D (``<base>_text2d.png``/``_input.png``) →
    ``images_subdir`` do layout split → ficheiro ao lado da mesh (layout flat).
    Sem imagem o refine não arranca (o modelo é image-conditioned).
    """
    base = _base_stem(mesh_final.stem)
    canon = _canonical_mesh_final(mesh_final)
    inter = _intermediate_dir(canon)
    meshes_dir = canon.parent
    exts = ("png", "jpg", "jpeg")

    candidates: list[Path] = []
    for stem_suffix in ("_text2d", "_input"):
        for ext in exts:
            candidates.append(inter / f"{base}{stem_suffix}.{ext}")
            candidates.append(meshes_dir / f"{base}{stem_suffix}.{ext}")
    imgs_sub = getattr(profile, "images_subdir", "images") or "images"
    # Raiz do layout split: tirar ``meshes_subdir`` (pode ter vários níveis,
    # ex. ``meshes/characters`` com ``images/characters``) do diretório da mesh.
    meshes_sub = Path(getattr(profile, "meshes_subdir", "meshes") or "meshes").parts
    root = meshes_dir.parent
    if meshes_sub and meshes_dir.parts[-len(meshes_sub) :] == meshes_sub:
        root = Path(*meshes_dir.parts[: -len(meshes_sub)])
    for ext in exts:
        candidates.append(root / imgs_sub / f"{base}.{ext}")
        # Pack do layout split (``meshes/<pack>`` → ``images/<pack>``). O
        # ``root`` acima só acerta quando ``profile.meshes_subdir`` traz o
        # pack; o profile tem o default ``meshes`` e o pack vive no manifest.
        candidates.append(meshes_dir.parent.parent / imgs_sub / meshes_dir.name / f"{base}.{ext}")
        candidates.append(canon.parent / f"{base}.{ext}")
    for cand in candidates:
        if cand.is_file() and cand.stat().st_size > 0:
            return cand
    # Último recurso: sidecar com prefixo (``<base>_v2_text2d.png`` ou
    # ``<base>_shape_input.png`` — o ``--save-reference-image`` do generate
    # escreve ``<stem-do-output>_input``, que inclui o sufixo do shape).
    if inter.is_dir():
        for pattern in (f"{base}*_text2d.*", f"{base}*_input.*"):
            for cand in sorted(inter.glob(pattern)):
                if cand.is_file() and cand.stat().st_size > 0:
                    return cand
    return None


# Entrada do Hi3D: um clean acima disto estoura a VRAM do spconv a 6 GB
# (campfire_pit a 384: clean 3,15M faces → CUDA OOM, refine saltado). O refine
# re-mesha de qualquer forma e o alvo dele é ≤320k — simplificar a entrada
# preserva o detalhe útil e evita o OOM.
_REFINE_INPUT_MAX_FACES = 1_200_000


def _run_refine_stage(
    clean_p: Path,
    mesh_final: Path,
    *,
    text3d_bin: str,
    profile: GameProfile,
    child_env: dict[str, str],
    manifest_dir: Path,
    force: bool,
    row: ManifestRow | None,
) -> Path | None:
    """Corre o Hi3D (``text3d refine``) e devolve ``_refined.glb`` ou ``None``.

    Skip gracioso (devolve ``None``, nunca levanta) quando: refine desligado no
    perfil, ``hi3d`` ausente, imagem de referência ausente, GPU/stack
    indisponíveis (exit 3) ou o binário falhou. O asset segue para o paint com
    o ``_clean`` — o refinamento é qualidade extra, não pré-condição.
    """
    from .profile import RefineProfile

    rp = profile.refine or RefineProfile()
    if not rp.enabled:
        return None

    # Override do manifest (``refine: false``): não corre o Hi3D e **não** reusa
    # um ``_refined`` órfão de uma passagem anterior — senão o LOD volta a
    # partir da geometria errada (o ``collision_source`` também o ignora).
    if _row_refine_disabled(row):
        log.info("refine: skip %s (desligado no manifest)", row.id if row is not None else mesh_final.stem)
        return None

    # Riggados e personagens/criaturas: o Hi3D re-sintetiza a forma a partir da
    # imagem e **muda a pose** (medido no hero: braços sobem/torcem, olhos
    # saltam). O rigging3d/animator3d esperam a A-pose do omni. Gate por
    # categoria além de ``generate_rig`` — cobre também quem ainda não vai ser
    # rigado nesta passagem. O detalhe vem do octree do shape.
    if row is not None:
        _rigged = bool(getattr(row, "generate_rig", False))
        _cat = str(getattr(row, "category", None) or getattr(row, "kind", None) or "").strip().lower()
        if _rigged or _cat in {"humanoid", "creature"}:
            log.info(
                "refine: skip %s (%s — Hi3D muda a pose)",
                row.id,
                "rigged" if _rigged else _cat,
            )
            return None

    refined_p = _refined_path(mesh_final)

    image_p = _reference_image_for(mesh_final, profile)
    if image_p is None:
        log.warning("refine: imagem de referência ausente para %s — a saltar", row.id if row else mesh_final.stem)
        return None

    # Cadeia multiview: uma imagem por vista, um passe Hi3D por imagem. Vistas
    # extra em falta são saltadas (a fase de imagens do resume/batch devia
    # tê-las gerado); uma falha nunca invalida o asset. O sidecar do refine
    # registra as vistas que correram, por isso o próximo resume volta a tentar
    # as que faltarem.
    chain: list[tuple[str, Path]] = []
    for view in multiview.configured_views(profile, row):
        view_p = image_p if view == "front" else multiview.resolve_view_image(mesh_final, profile, view, front=image_p)
        if view_p is None:
            log.warning(
                "refine: vista %s em falta para %s — a saltar essa vista",
                view,
                row.id if row is not None else mesh_final.stem,
            )
            continue
        chain.append((view, view_p))
    if not chain:
        return None
    multi = len(chain) > 1

    target = _resolve_lod_target_faces(profile, row) if row is not None else 0
    max_faces = int(target * rp.max_faces_scale) if target > 0 and rp.max_faces_scale > 0 else 0
    # Piso do teto: nunca decimar abaixo da coarse. Sem isto um asset com
    # orçamento LOD baixo (boss_ogre: target 6k → teto 24k) saía do refine
    # **com menos faces do que o clean** (103k) — "refinado low-poly".
    if max_faces > 0:
        clean_faces = _count_faces_glb(clean_p)
        if clean_faces > max_faces:
            # Tecto do piso: o paint já simplifica para ~320k (atlas 2k); um
            # clean de edifício com 1-2M faces não deve arrastar o refine para
            # um intermédio gigante (unwrap/raster e disco).
            floor = min(clean_faces, 320_000)
            if floor > max_faces:
                log.info(
                    "refine: teto %s → %s faces (piso = clean, máx 320k)",
                    f"{max_faces:,}",
                    f"{floor:,}",
                )
                max_faces = floor
    # seed: perfil > override da row (re-roll cirúrgico, como no shape) >
    # determinístico por id.
    seed = rp.seed
    if seed is None and row is not None:
        seed = getattr(row, "seed", None)
    if seed is None:
        seed = _seed_for_row(profile, row.id if row is not None else "")
    cat = (row.category or row.kind) if row is not None else None
    label = row.id if row is not None else mesh_final.stem

    refined_p.parent.mkdir(parents=True, exist_ok=True)
    # Clean grande → simplifica a entrada do Hi3D (evita OOM do spconv).
    refine_in = clean_p
    clean_in_faces = _count_faces_glb(clean_p)
    if clean_in_faces > _REFINE_INPUT_MAX_FACES:
        refine_in = clean_p.with_name(clean_p.stem + "_refinein" + clean_p.suffix)
        reuse_in = (
            not force
            and refine_in.is_file()
            and refine_in.stat().st_mtime >= clean_p.stat().st_mtime
            and 0 < _count_faces_glb(refine_in) <= int(_REFINE_INPUT_MAX_FACES * 1.1)
        )
        if not reuse_in:
            console.print(
                f"[cyan]⏳ refine input[/cyan] {label} ({clean_in_faces:,} → {_REFINE_INPUT_MAX_FACES:,} faces)"
            )
            sargv = [
                text3d_bin,
                "simplify",
                str(clean_p),
                "-o",
                str(refine_in),
                "--target-faces",
                str(_REFINE_INPUT_MAX_FACES),
                "--no-repair",
            ]
            rs = run_cmd(sargv, extra_env=child_env, cwd=manifest_dir)
            if rs.returncode != 0 or not refine_in.is_file() or not _glb_has_geometry(refine_in):
                err = merge_subprocess_output(rs, max_chars=300) or "simplify do input do refine falhou"
                log.warning("refine: simplify do input falhou (%s) — a tentar com o clean", err)
                refine_in = clean_p

    params = {
        "resolution": rp.resolution,
        "steps": rp.steps,
        "seed": seed,
        "max_faces": max_faces,
        "model": rp.model,
        "remove_background": rp.remove_background,
    }

    # Reuso. Mono-vista mantém o contrato legacy (mtime); multiview exige o
    # sidecar com vistas/params/clean exatos — mudar uma vista re-refina.
    if not force and refined_p.is_file() and _glb_has_geometry(refined_p):
        if multi:
            if multiview.refine_cache_ok(
                mesh_final,
                views=chain,
                clean_path=clean_p,
                input_path=refine_in,
                params=params,
            ):
                log.info("refine: reuso %s (multiview cache)", refined_p.name)
                return refined_p
        else:
            with contextlib.suppress(OSError):
                if refined_p.stat().st_mtime >= clean_p.stat().st_mtime:
                    log.info("refine: reuso %s", refined_p.name)
                    return refined_p

    hi3d_bin = _bin_or_none("HI3D_BIN", "hi3d")
    if not hi3d_bin:
        console.print(
            "[yellow]⤵ refine[/yellow] hi3d ausente — instala com "
            "[cyan]./install.sh hi3d[/cyan] + [cyan]hi3d download-model[/cyan]"
        )
        return None

    n = len(chain)

    def _pass_input(src: Path, idx: int) -> Path:
        """Entrada do passe ``idx``.

        512/1024 devolvem ~1M faces (< cap); 1536 pode devolver ~2M — simplifica
        para o próximo passe não rebentar o spconv.
        """
        if idx == 1 or _count_faces_glb(src) <= _REFINE_INPUT_MAX_FACES:
            return src
        dst = src.with_name(src.stem + "_refinein" + src.suffix)
        sargv = [
            text3d_bin,
            "simplify",
            str(src),
            "-o",
            str(dst),
            "--target-faces",
            str(_REFINE_INPUT_MAX_FACES),
            "--no-repair",
        ]
        rs = run_cmd(sargv, extra_env=child_env, cwd=manifest_dir)
        if rs.returncode != 0 or not dst.is_file() or not _glb_has_geometry(dst):
            log.warning("refine: simplify do input intermédio falhou — a usar %s", src.name)
            return src
        return dst

    # Temporários de uma execução anterior.
    for stale in refined_p.parent.glob(f"{refined_p.stem}_pass*.glb"):
        with contextlib.suppress(OSError):
            stale.unlink()

    t_all = time.perf_counter()
    prev = refine_in
    used: list[tuple[str, Path]] = []
    for i, (view, view_p) in enumerate(chain, start=1):
        out = refined_p.parent / f"{refined_p.stem}_pass{i}.glb"
        pass_in = _pass_input(prev, i)
        # Intermédios sem decimação (preserva detalhe); o último aplica o teto.
        pass_max = 0 if i < n else max_faces
        argv = [
            text3d_bin,
            "refine",
            str(pass_in),
            "-i",
            str(view_p),
            "-o",
            str(out),
            "--resolution",
            rp.resolution,
            "--steps",
            str(rp.steps),
            "--max-faces",
            str(pass_max),
            "--json",
        ]
        if seed is not None:
            argv.extend(["--seed", str(seed)])
        if cat:
            argv.extend(["--category", str(cat)])
        if rp.model:
            argv.extend(["--model", rp.model])
        if rp.remove_background:
            argv.append("--remove-background")
        # O Hi3D fica **local**: é o único stack cu124 (torch 2.5) sem build
        # cu13. O shape/paint seguem o alvo do asset/batch; o refine não.
        argv.extend(["--gpu", "local"])

        tag = f"[{i}/{n}] {view} " if multi else ""
        console.print(f"[cyan]⏳ refine[/cyan] {label} {tag}(Hi3D @{rp.resolution})")
        r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)

        if r.returncode == 3:
            reason = merge_subprocess_output(r, max_chars=300) or "indisponível nesta GPU/instalação"
            if i == 1:
                console.print(f"[yellow]⤵ refine[/yellow] {label}: {reason}")
                return None
            console.print(
                f"[yellow]⤵ refine[/yellow] {label}: vista {view} indisponível ({reason}); a usar {len(used)} vista(s)"
            )
            break
        if r.returncode != 0 or not out.is_file() or not _glb_has_geometry(out):
            err = merge_subprocess_output(r, max_chars=400) or "text3d refine falhou"
            if i == 1:
                log.warning("refine falhou (%s): %s — a continuar com _clean", label, err)
                console.print(f"[yellow]⤵ refine[/yellow] {label}: falhou (segue _clean)")
                return None
            log.warning("refine falhou na vista %s (%s): %s — última saída boa", view, label, err)
            console.print(f"[yellow]⤵ refine[/yellow] {label}: vista {view} falhou; a usar {len(used)} vista(s)")
            break

        prev = out
        used.append((view, view_p))

    if not used:
        return None
    prev.replace(refined_p)
    if multi:
        # Um _refined novo invalida tudo o que nasceu do painted/LOD antigo.
        # Sem isto o master via os LOD/collision existentes e não os
        # reconstruía — o entregável ficava com a geometria velha.
        invalidate_split_artifacts(mesh_final)
        for stale in (
            _painted_path(mesh_final),
            _lod_path(mesh_final, 0),
            _lod_path(mesh_final, 1),
            _lod_path(mesh_final, 2),
            _collision_path(mesh_final),
            _precompute_path(mesh_final),
        ):
            with contextlib.suppress(OSError):
                stale.unlink()
        multiview.write_refine_sidecar(
            mesh_final,
            views=used,
            clean_path=clean_p,
            input_path=refine_in,
            params=params,
        )
    for stale in refined_p.parent.glob(f"{refined_p.stem}_pass*.glb"):
        with contextlib.suppress(OSError):
            stale.unlink()

    total = time.perf_counter() - t_all
    faces = _count_faces_glb(refined_p)
    inner = (f"{faces:,} faces, " if faces > 0 else "") + f"{total:.0f}s"
    if multi:
        inner += f", {len(used)}/{n} vistas"
    console.print(f"[green]✓ refine[/green] {label} → {refined_p.name} ({inner})")
    return refined_p


def ensure_clean_for_paint(
    mesh_final: Path,
    *,
    text3d_bin: str,
    profile: GameProfile,
    child_env: dict[str, str],
    manifest_dir: Path,
    force: bool = False,
    row: ManifestRow | None = None,
) -> Path:
    """Garante ``_clean.glb`` (topology-fix) e devolve path para input do paint.

    Master DAG canónico: shape -> topology-fix -> **refine (Hi3D)** -> paint.
    Batch/resume antigos pintavam ``_shape`` cru (paredes duplas / cascas
    internas). Com isto o paint corre sobre mesh já limpa (incl.
    ``remove_internal_shell_faces``) e, quando o Hi3D está disponível, sobre a
    geometria refinada (mais fiel à silhueta da imagem).

    O refine é **default ON** mas opcional em runtime: qualquer indisponibilidade
    degrada para o ``_clean`` sem falhar o asset.
    """
    clean_existing = _clean_existing(mesh_final)
    if clean_existing is not None and clean_existing.is_file() and not force:
        return (
            _run_refine_stage(
                clean_existing,
                mesh_final,
                text3d_bin=text3d_bin,
                profile=profile,
                child_env=child_env,
                manifest_dir=manifest_dir,
                force=force,
                row=row,
            )
            or clean_existing
        )

    shape_p = _shape_existing(mesh_final) or _shape_path(mesh_final)
    if not shape_p.is_file():
        raise FileNotFoundError(f"shape ausente para topology-fix: {shape_p}")

    clean_p = _clean_path(mesh_final)
    if clean_p.is_file() and not _glb_has_geometry(clean_p):
        with contextlib.suppress(OSError):
            clean_p.unlink()
    clean_p.parent.mkdir(parents=True, exist_ok=True)
    topo_argv = [text3d_bin, "topology-fix", str(shape_p), "-o", str(clean_p)]
    topo_argv.extend(_topology_fix_extra_argv(profile, row, manifest_dir=manifest_dir))
    r = run_cmd(topo_argv, extra_env=child_env, cwd=manifest_dir)
    if r.returncode != 0 or not clean_p.is_file():
        err = merge_subprocess_output(r, max_chars=400) or "topology-fix falhou"
        raise RuntimeError(err)
    if not _glb_has_geometry(clean_p):
        with contextlib.suppress(OSError):
            clean_p.unlink()
        raise RuntimeError(f"topology-fix produziu clean vazio: {clean_p}")
    return (
        _run_refine_stage(
            clean_p,
            mesh_final,
            text3d_bin=text3d_bin,
            profile=profile,
            child_env=child_env,
            manifest_dir=manifest_dir,
            force=force,
            row=row,
        )
        or clean_p
    )


def _quality_paint_texture_cap(profile: GameProfile) -> int:
    """Tecto de atlas do tier de geração (QualityEngine / generation_profiles)."""
    from .generation_profiles import get_profile

    gen = (profile.generation or "medium").strip().lower() or "medium"
    try:
        return int(get_profile(gen).paint_texture_size)
    except Exception:
        return 2048


def _char_m_for_budget(profile: GameProfile, row: ManifestRow | None) -> float | None:
    """Silhueta equivalente para orçamento paint/LOD (``size_m`` / prior).

    Orçamentos de faces e de atlas escalam com a **área projectada em ecrã**, não
    com o volume: ``sqrt(d1·d2)`` dos dois eixos maiores. O volume-equivalente
    ``(L·H·W)^(1/3)`` do ``characteristic_meters`` (usado pelo octree) trata
    qualquer coisa alongada como minúscula — um herói de 1.55 m dava 0.70 m e
    caía no ``LOD_FACE_SCALE_FLOOR``, ~4.6k faces no lod0.
    """
    if row is None:
        return None
    from aigamekit_shared.lod_budget import silhouette_equivalent_meters

    from .omni_ctrl import expand_omni_world_size

    cat = getattr(row, "category", None) or getattr(row, "kind", None) or None
    omni = expand_omni_world_size(resolve_row_omni(profile, row), category=cat)
    size_m = list(omni.size_m) if omni.size_m is not None else None
    sil = silhouette_equivalent_meters(size_m)
    if sil is not None and sil > 0:
        return float(sil)
    # Sem size_m utilizável: prior de eixo típico por categoria/preset.
    try:
        from text3d.bbox_tune import characteristic_meters

        char, _src = characteristic_meters(None, category=cat, bbox_preset=omni.bbox_preset)
        return float(char) if char is not None and char > 0 else None
    except ImportError:
        return None


# Alias legado — paint e LOD partilham o mesmo char_m.
_char_m_for_paint = _char_m_for_budget


def _lod_ref_m_for_row(row: ManifestRow | None) -> float:
    """Silhueta de referência da categoria da row (default global)."""
    from aigamekit_shared.lod_budget import LOD_FACE_REF_M

    if row is None:
        return float(LOD_FACE_REF_M)
    cat = getattr(row, "category", None) or ""
    return get_lod_ref_m(str(cat))


def _resolve_lod_target_faces(profile: GameProfile, row: ManifestRow) -> int:
    """``get_target_faces`` com ``face_ratio`` + escala por silhueta."""
    fr = effective_face_ratio(profile, row)
    char = _char_m_for_budget(profile, row)
    if row.category:
        return get_target_faces(row.category, face_ratio=fr, char_m=char)
    return get_target_faces("", default=8000, face_ratio=fr, char_m=char)


def _resolve_lod0_texture_size(profile: GameProfile, row: ManifestRow | None = None) -> int:
    """Atlas lod0 por silhueta ∩ quality cap (snap64). Nunca acima do paint budget."""
    from aigamekit_shared.lod_budget import lod_texture_size_for_char, snap_tex_64

    quality_cap = _quality_paint_texture_cap(profile)
    # Paint override no profile também limita o LOD (não upscale além do bake).
    paint_cap = _resolve_paint_texture_size(profile, row)
    cap = min(quality_cap, paint_cap)
    char = _char_m_for_budget(profile, row)
    if char is not None:
        return lod_texture_size_for_char(char, quality_cap=cap, ref_m=_lod_ref_m_for_row(row))
    return snap_tex_64(cap, cap=cap)


def _resolve_paint_texture_size(profile: GameProfile, row: ManifestRow | None = None) -> int:
    """Atlas size efectivo: override game.yaml, senão size_m ∩ quality cap.

    ``paint3d.texture_size`` explícito no perfil ganha. Sem override: ladder por
    ``char_m`` (balde 512 … casa 4096) nunca acima do cap do tier de geração.
    """
    p3 = profile.paint3d
    if p3 is not None and p3.texture_size is not None and int(p3.texture_size) > 0:
        return int(p3.texture_size)
    quality_cap = _quality_paint_texture_cap(profile)
    char = _char_m_for_budget(profile, row)
    if char is not None:
        from aigamekit_shared.paint_budget import paint_texture_for_char

        return paint_texture_for_char(char, quality_cap=quality_cap, ref_m=_lod_ref_m_for_row(row))
    return quality_cap


_CHARACTER_PAINT_CATEGORIES = frozenset({"humanoid", "creature", "character", "npc", "monster", "beast", "animal"})
# Personagens/bosses: 8 vistas (combo do tier ``high``) em vez das 6 do
# ``medium``. Subir a resolução para 640 estoura a VRAM em 6 GiB (pico do
# worker com raster+UNet) — 8@512 é o ponto seguro e já cobre melhor o atlas.
_CHARACTER_PAINT_MAX_VIEWS = 8
_CHARACTER_PAINT_VIEW_RESOLUTION = 512
# O xatlas é superlinear nos charts: 260k faces num atlas 2048 rebentava o
# timeout de 300 s em vários personagens. 100k ainda dá ~2.5x o LOD0 e ~23
# texels/face; o reflexo no atlas é muito maior que o detalhe perdido.
_CHARACTER_TO_PAINT_FACES_CAP = 100_000
_CHARACTER_TO_PAINT_FACES_CAP_4K = 160_000
# Caminho ``clean`` (sem refine): o simplify directo deixa milhares de
# rebordos e o xatlas é superlinear — 80k: ~90 s; 100k: >150 s; 269k: ~500 s.
# 80k mantém ~26 texels/face no atlas 2048 e o unwrap dentro do watchdog.
# O ``_refined`` com refix+morph-close é coerente e pode ir ao orçamento cheio.
_PAINT_CLEAN_MAX_FACES = 80_000


def _is_character_row(row: ManifestRow | None) -> bool:
    """Row de personagem/criatura (o kind do manifest ou a categoria)."""
    if row is None:
        return False
    kind = str(getattr(row, "kind", None) or "").strip().lower()
    category = str(getattr(row, "category", None) or "").strip().lower()
    return kind == "character" or category in _CHARACTER_PAINT_CATEGORIES


def _character_paint_opts(row: ManifestRow | None) -> tuple[int | None, int | None]:
    """``(max_views, view_resolution)`` por defeito para personagens."""
    if not _is_character_row(row):
        return None, None
    return _CHARACTER_PAINT_MAX_VIEWS, _CHARACTER_PAINT_VIEW_RESOLUTION


def _paint_item_overrides(profile: GameProfile, row: ManifestRow | None) -> dict[str, object]:
    """Overrides por-item do paint (manifest row + defaults de personagem).

    O ``texture-batch`` do vramd lê estas chaves de cada item do manifesto; o
    caminho subprocesso usa o argv global (``_texture_subprocess_argv``).
    ``pbr_enrich`` vai sempre explícito para o worker não cair no default ON.
    """
    opts: dict[str, object] = {}
    if row is not None and row.paint3d:
        for key in ("max_views", "view_resolution", "render_size", "texture_size", "bake_exp"):
            if row.paint3d.get(key) is not None:
                opts[key] = row.paint3d[key]
    char_views, char_res = _character_paint_opts(row)
    if char_views is not None and "max_views" not in opts:
        opts["max_views"] = char_views
    if char_res is not None and "view_resolution" not in opts:
        opts["view_resolution"] = char_res
    p3 = profile.paint3d
    opts["pbr_enrich"] = bool(p3.pbr_enrich) if p3 is not None else False
    return opts


def _resolve_to_paint_faces(profile: GameProfile, row: ManifestRow | None = None) -> int:
    """Faces alvo do ``_to_paint``: row > profile > fórmula ~ texture_size."""
    if row is not None and row.paint3d:
        row_tpf = row.paint3d.get("to_paint_faces")
        if row_tpf is not None and int(row_tpf) >= 4:
            return int(row_tpf)
    p3 = profile.paint3d
    if p3 is not None and p3.to_paint_faces is not None and int(p3.to_paint_faces) >= 4:
        return int(p3.to_paint_faces)
    from aigamekit_shared.paint_budget import paint_target_faces

    tex = _resolve_paint_texture_size(profile, row)
    target = paint_target_faces(tex)
    if _is_character_row(row):
        cap = _CHARACTER_TO_PAINT_FACES_CAP if tex <= 2048 else _CHARACTER_TO_PAINT_FACES_CAP_4K
        target = min(target, cap)
    return target


def _explicit_to_paint_faces(
    profile: GameProfile,
    row: ManifestRow | None,
    target_faces: int | None,
) -> bool:
    """Há um orçamento de ``_to_paint`` escolhido à mão?

    O cap de segurança do caminho ``_clean`` (``_PAINT_CLEAN_MAX_FACES``) é para
    a fórmula (~texture_size), não para um override explícito da row/profile:
    esse pedido tem de ganhar, como documenta ``_resolve_to_paint_faces``.
    """
    if target_faces is not None:
        return True
    if row is not None and row.paint3d and row.paint3d.get("to_paint_faces") is not None:
        return True
    p3 = profile.paint3d
    return bool(p3 is not None and p3.to_paint_faces is not None)


def ensure_to_paint_for_paint(
    mesh_final: Path,
    *,
    text3d_bin: str,
    profile: GameProfile,
    child_env: dict[str, str],
    manifest_dir: Path,
    force: bool = False,
    row: ManifestRow | None = None,
    target_faces: int | None = None,
) -> Path:
    """Garante ``_clean`` + ``_to_paint`` (remesh orçado) e devolve input do paint.

    DAG: shape -> topology-fix (``_clean``, HI) -> simplify (``_to_paint``) -> paint.
    O HI ``_clean`` continua a alimentar bake-master ``--high-poly``; o paint
    só vê a malha orçada ao atlas (evita unwrap/raster em 1-2M faces).

    Se ``_clean`` já está ≤ ~110% do alvo, devolve ``_clean`` (sem ficheiro extra).

    Args:
        target_faces: Override do alvo de faces (degradação de recovery do
            paint — ex. 0.6x do budget quando o item estoura a VRAM do worker).
    """
    clean_p = ensure_clean_for_paint(
        mesh_final,
        text3d_bin=text3d_bin,
        profile=profile,
        child_env=child_env,
        manifest_dir=manifest_dir,
        force=force,
        row=row,
    )
    target = (
        int(target_faces)
        if target_faces is not None and int(target_faces) >= 4
        else _resolve_to_paint_faces(profile, row)
    )
    tex_size = _resolve_paint_texture_size(profile, row)
    current = _count_faces_glb(clean_p)
    if current < 0:
        log.warning("to_paint: não consegui contar faces de %s - paint no clean", clean_p)
        return clean_p

    to_paint_p = _to_paint_path(mesh_final)
    existing = _to_paint_existing(mesh_final)
    # O refine Hi3D devolve topologia própria (não passou topology-fix): o
    # xatlas parte-a em >15k charts (campfire_pit: >60 s) e o bake fica sem
    # cobertura (trust 0,9% vs 62-70% de uma malha limpa). A malha do paint
    # tem de ser refixada mesmo quando cabe no orçamento de faces — e um
    # ``_to_paint`` antigo (gerado antes do refix+morph) não pode ser reusado.
    refined_input = "_refined" in clean_p.stem
    if not refined_input and not _explicit_to_paint_faces(profile, row, target_faces):
        target = min(target, _PAINT_CLEAN_MAX_FACES)
    reuse_ok = (
        not force
        and not refined_input
        and existing is not None
        and existing.is_file()
        and existing.stat().st_mtime >= clean_p.stat().st_mtime
    )

    if current <= int(target * 1.1) and not refined_input:
        log.info(
            "to_paint: skip remesh %s faces=%s ≤ alvo %s (tex=%s)",
            clean_p.name,
            f"{current:,}",
            f"{target:,}",
            tex_size,
        )
        return clean_p

    if reuse_ok:
        tp_faces = _count_faces_glb(existing)
        if 0 < tp_faces <= int(target * 1.25):
            log.info(
                "to_paint: reuso %s faces=%s (alvo %s)",
                existing.name,
                f"{tp_faces:,}",
                f"{target:,}",
            )
            return existing

    row_id = row.id if row is not None else clean_p.stem
    to_paint_p.parent.mkdir(parents=True, exist_ok=True)

    if refined_input:
        # ORDEM: refix ANTES do simplify. O Hi3D devolve arestas non-manifold;
        # o Decimate sobre essa malha estilhaça-a (5.186 componentes/86k bordos
        # no longhouse) e o xatlas passa de >600 s a ~160 s depois do morph-close
        # + keep das cascas. O budget do atlas aplica-se ao simplify seguinte.
        refix_src = to_paint_p.with_name(to_paint_p.stem + "_refixsrc" + to_paint_p.suffix)
        _refix_paint_input(
            clean_p,
            refix_src,
            text3d_bin=text3d_bin,
            child_env=child_env,
            manifest_dir=manifest_dir,
            row=row,
            morph_close=_refined_refix_morph(profile, row),
            keep_internal_shells=True,
        )
        refix_faces = _count_faces_glb(refix_src)
        paint_src = refix_src
        if refix_faces > int(target * 1.1):
            simplified_p = to_paint_p.with_name(to_paint_p.stem + "_simplified" + to_paint_p.suffix)
            console.print(
                f"[cyan]⏳ to_paint[/cyan] {row_id} "
                f"({refix_src.name} {refix_faces:,} -> ~{target:,} faces, atlas={tex_size})"
            )
            sargv = [
                text3d_bin,
                "simplify",
                str(refix_src),
                "-o",
                str(simplified_p),
                "--target-faces",
                str(target),
                "--no-repair",
            ]
            rs = run_cmd(sargv, extra_env=child_env, cwd=manifest_dir)
            if rs.returncode != 0 or not simplified_p.is_file():
                err = merge_subprocess_output(rs, max_chars=400) or "simplify do refined falhou"
                raise RuntimeError(err)
            paint_src = simplified_p
        if paint_src != to_paint_p:
            paint_src.replace(to_paint_p)
        if refix_src.is_file() and refix_src != to_paint_p:
            with contextlib.suppress(OSError):
                refix_src.unlink()
        out_faces = _count_faces_glb(to_paint_p)
        console.print(
            f"[green]✓ to_paint[/green] {row_id} -> {to_paint_p.name}"
            + (f" ({out_faces:,} faces)" if out_faces > 0 else "")
        )
        return to_paint_p

    console.print(f"[cyan]⏳ to_paint[/cyan] {row_id} ({current:,} -> ~{target:,} faces, atlas={tex_size})")
    # ``_clean`` já passou topology-fix: --no-repair evita pre_decimate_uv /
    # post_decimate a apagar slivers/paredes finas (edifícios casca-plástico).
    argv = [
        text3d_bin,
        "simplify",
        str(clean_p),
        "-o",
        str(to_paint_p),
        "--target-faces",
        str(target),
        "--no-repair",
    ]
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    if r.returncode != 0 or not to_paint_p.is_file():
        err = merge_subprocess_output(r, max_chars=400) or "simplify to_paint falhou"
        raise RuntimeError(err)
    out_faces = _count_faces_glb(to_paint_p)
    console.print(
        f"[green]✓ to_paint[/green] {row_id} -> {to_paint_p.name}"
        + (f" ({out_faces:,} faces)" if out_faces > 0 else "")
    )
    # SEM re-fix no caminho clean: o simplify do ``_clean`` já é uma malha sã
    # (longhouse 80k: 349 componentes/3.860 bordos). O topology-fix com
    # ``--morph-close 0`` parte-a em 1.568/31.414 (market_stall) e o xatlas
    # enche o atlas de ilhas minúsculas (mosaico). O re-fix só é necessário no
    # caminho ``_refined``, e aí corre ANTES do simplify com morph-close.
    return to_paint_p


def _refined_refix_morph(profile: GameProfile, row: ManifestRow | None) -> float:
    """Morph-close (m) do refix do ``_refined`` antes do simplify.

    O Hi3D devolve arestas não-manifold (4 faces por aresta). Decimar essa
    malha directamente estilhaça-a (longhouse: 5.186 componentes/86k bordos a
    150k) e o xatlas explode (>600 s; bake trust 28,8% → mosaico). Um fecho
    sub-cm sela as rachas sem apagar detalhe visível e deixa o xatlas em ~160 s
    a 265k (medido a 2026-11-11). Escala com a silhueta: 0,5 mm por metro,
    clamp 0,3-10 mm.
    """
    char = _char_m_for_budget(profile, row)
    if char is None or char <= 0:
        return 0.003
    return max(0.0003, min(0.01, float(char) * 0.0005))


def _refix_paint_input(
    src: Path,
    dst: Path,
    *,
    text3d_bin: str,
    child_env: dict[str, str],
    manifest_dir: Path,
    row: ManifestRow | None,
    required: bool = True,
    morph_close: float = 0.0,
    keep_internal_shells: bool = True,
) -> Path:
    """``topology-fix`` leve (weld/fill) do input do paint.

    O refine Hi3D devolve topologia que o xatlas não abre (vértices split,
    arestas não-manifold, 15,8k charts e >60 s) e o bake fica sem cobertura.
    Refixar antes do paint dá ao xatlas uma malha saneada.

    ``keep_internal_shells=True``: o refix é weld/fill, não limpeza de cascas.
    Strip por face abre a malha (longhouse: 15k faces removidas, 21 componentes)
    e custa 264 s vs 4 s do keep; a limpeza de cascas já foi feita no ``_clean``.
    ``morph_close`` > 0 sela rachas/non-manifold do refine (voxel close).
    ``required=False`` mantém o caminho antigo (simplify já saneia) e só avisa.
    """
    refix_p = dst.with_name(dst.stem + "_refixed" + dst.suffix)
    refix_argv = [
        text3d_bin,
        "topology-fix",
        str(src),
        "-o",
        str(refix_p),
        "--engine",
        "arrays",
        "--morph-close",
        f"{float(morph_close):g}",
    ]
    if keep_internal_shells:
        refix_argv.append("--keep-internal-shells")
    if row is not None and row.category:
        refix_argv.extend(["--category", str(row.category)])
    r = run_cmd(refix_argv, extra_env=child_env, cwd=manifest_dir)
    # Um rc=0 com GLB vazio/corrupto (worker morto a meio do save) não pode
    # substituir a malha boa do paint — o simplify/paint seguinte falharia longe
    # daqui. ``_glb_has_geometry`` é o mesmo gate do refine/master.
    if r.returncode == 0 and refix_p.is_file() and _glb_has_geometry(refix_p):
        refix_p.replace(dst)
        return dst
    err = merge_subprocess_output(r, max_chars=400) or "topology-fix (refix) falhou"
    if required:
        raise RuntimeError(err)
    log.warning("to_paint re-fix falhou para %s (continua com simplify)", dst.name)
    return src


def _rules_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "rules"


# Chaves de regra que o parser JSON (`glb_meta`) não consegue avaliar — exigem
# o import bpy do `aigamekit-lab check` (ver `_run_check_glb`).
_RULES_REQUIRING_BPY = ("armatures", "actions_min", "bone_count")


def _rules_need_bpy_inspect(rules: Path) -> bool:
    """``True`` se o ficheiro de regras depender de introspecção bpy."""
    try:
        text = Path(rules).read_text(encoding="utf-8")
    except OSError:
        return True  # não conseguimos decidir — validar a fundo é o lado seguro
    return any(key in text for key in _RULES_REQUIRING_BPY)


def _check_glb_failures(stdout: str) -> list[str]:
    """Lista ``failures`` do JSON do ``aigamekit-lab check glb`` (vazia se não parseável)."""
    start = stdout.find("{")
    if start < 0:
        return []
    try:
        data, _ = json.JSONDecoder().raw_decode(stdout[start:])
    except ValueError:
        return []
    failures = data.get("failures") if isinstance(data, dict) else None
    return [str(f) for f in failures] if isinstance(failures, list) else []


def _run_check_glb(
    glb: Path,
    rules: Path,
    *,
    category: str | None,
    env: dict[str, str],
    cwd: Path,
) -> StageResult:
    import time as _time

    bin_ = _bin_or_none("AIGAMEKITLAB_BIN", "aigamekit-lab")
    if not bin_:
        return StageResult("validate", False, 0.0, "aigamekit-lab não encontrado no PATH")
    argv = [bin_, "check", "glb", str(glb), str(rules)]
    if category:
        argv.extend(["--category", category])
    # `--no-bpy-inspect` lê só o JSON do GLB (glb_meta) — rápido, mas cego a
    # armatures/actions: `armatures[]`/`actions_min` reportavam sempre 0 e
    # rigged.yaml/animated.yaml falhavam em TODOS os personagens (medido:
    # goblin_lod0 com 35 joints + 7 clips dava "modelo tem só 0 armature(s)").
    # Regras que precisam de introspecção pedem o import bpy.
    if not _rules_need_bpy_inspect(rules):
        argv.extend(["--no-bpy-inspect"])
    t0 = _time.perf_counter()
    r = run_cmd(argv, extra_env=env, cwd=cwd)
    dt = _time.perf_counter() - t0
    if r.returncode != 0:
        # O JSON do check é longo: o tail truncado escondia qual regra falhou.
        failures = _check_glb_failures(r.stdout or "")
        if failures:
            err = f"{glb.name} ({Path(rules).name}): " + "; ".join(failures)
        else:
            err = merge_subprocess_output(r, max_chars=400) or f"check glb falhou (rc={r.returncode})"
        return StageResult("validate", False, dt, err, glb)
    return StageResult("validate", True, dt, output=glb)


def _stage(
    name: str,
    argv: list[str],
    env: dict[str, str],
    cwd: Path,
    output: Path | None = None,
    *,
    item_id: str | None = None,
    profile_enabled: bool = False,
    on_progress_line: Callable[[str], None] | None = None,
) -> StageResult:
    """Executa um stage do master pipeline.

    Round 2: envolto em ``ProfilerSession`` para spans no perf.db quando
    ``profile_enabled`` (controlado por ``AIGAMEKIT_PROFILE`` no child_env).
    Emite ``emit_progress`` no início e fim para visibilidade no dashboard.

    ``on_progress_line``: callback alimentado linha-a-linha com stdout do
    subprocesso. Permite encaminhar ``emit_progress`` events emitidos pelas
    ferramentas (text3d, rigging3d, animator3d) para o dashboard do
    gameassets - sem isso, o dashboard só vê os events do orquestrador
    (start/end por stage) e parece "congelar" após paint3d.
    """
    import time as _time

    from aigamekit_shared.profiler.session import ProfilerSession
    from aigamekit_shared.progress import emit_progress

    profiler_tool = name.replace("-", "_")

    def _emit(phase: str, percent: float, status: str = "progress", **meta: object) -> None:
        """Emite progresso E alimenta o dashboard directamente.

        ``emit_progress`` escreve no stdout do processo gameassets, que NÃO
        passa pelo callback ``on_progress_line`` do dashboard (esse só vê
        stdout dos sub-processos via ``run_cmd_streaming``). Sem este
        encaminhamento manual o dashboard congela em "Paint3D 100%" durante
        os stages do master pipeline (que duram dezenas de segundos cada).
        """
        if not item_id:
            return
        emit_progress(item_id, profiler_tool, phase=phase, percent=percent, **meta)
        if on_progress_line is not None:
            import json as _json

            data: dict = {
                "id": item_id,
                "tool": profiler_tool,
                "status": status,
                "phase": phase,
                "percent": round(percent, 1),
            }
            data.update(meta)
            with contextlib.suppress(Exception):
                on_progress_line(_json.dumps(data))

    _emit("run", 0)

    with contextlib.suppress(Exception):
        from aigamekit_shared.pipeline_trace import trace_stage

        trace_stage(
            asset=str(item_id or "?"),
            stage=name,
            status="start",
            argv=list(argv),
            output=str(output) if output else None,
            cwd=str(cwd) if cwd else None,
        )

    t0 = _time.perf_counter()
    try:
        with ProfilerSession(
            profiler_tool,
            cli_profile=profile_enabled,
            params={"item_id": item_id} if item_id else None,
        ):
            if on_progress_line is not None and _run_cmd_streaming is not None:
                # Stream stdout para callback (dashboard) E acumula resultado.
                # Sub-tools (text3d/rigging3d/animator3d) emitem events com
                # ``id`` derivado do filename (ex.: "goblin_lod0"); o
                # dashboard chaveia por ``row.id`` ("goblin"), portanto
                # reescrevemos o ``id`` em cada linha JSON antes de
                # encaminhar para que a célula do asset reflicta a fase
                # corrente. ``phase`` ganha o nome do stage para distinguir
                # entre rigging-merge-lod0/lod1/animate-lod0/etc.
                import json as _json

                stdout_buf: list[str] = []
                stderr_buf: list[str] = []

                def _on_out(line: str) -> None:
                    stdout_buf.append(line)
                    try:
                        forwarded = line
                        s = line.strip()
                        if item_id and s.startswith("{") and s.endswith("}"):
                            try:
                                data = _json.loads(s)
                            except (ValueError, _json.JSONDecodeError):
                                data = None
                            if isinstance(data, dict) and "id" in data:
                                # Preserva o id original em sub_id e mostra
                                # o ``name`` (stage do master) como tool.
                                data["sub_id"] = data.get("id")
                                data["sub_tool"] = data.get("tool", "")
                                data["id"] = item_id
                                data["tool"] = profiler_tool
                                if "phase" not in data and data.get("sub_tool"):
                                    data["phase"] = data["sub_tool"]
                                forwarded = _json.dumps(data)
                        on_progress_line(forwarded)
                    except Exception:
                        pass

                def _on_err(line: str) -> None:
                    stderr_buf.append(line)

                rs = _run_cmd_streaming(
                    argv,
                    on_stdout_line=_on_out,
                    on_stderr_line=_on_err,
                    cwd=cwd,
                    extra_env=env,
                )
                r = rs
            else:
                r = run_cmd(argv, extra_env=env, cwd=cwd)
    except Exception as exc:
        dt = _time.perf_counter() - t0
        _emit("run", 100, status="error")
        with contextlib.suppress(Exception):
            from aigamekit_shared.pipeline_trace import trace_exception, trace_stage

            trace_exception("pipeline_stage_error", exc, asset=str(item_id or "?"), stage=name)
            trace_stage(asset=str(item_id or "?"), stage=name, status="error", seconds=round(dt, 3), error=str(exc))
        return StageResult(name, False, dt, f"ProfilerSession: {exc}", output)

    dt = _time.perf_counter() - t0
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=400) or f"{name} falhou (rc={r.returncode})"
        _emit("run", 100, status="error")
        with contextlib.suppress(Exception):
            from aigamekit_shared.pipeline_trace import trace_stage

            trace_stage(
                asset=str(item_id or "?"),
                stage=name,
                status="fail",
                seconds=round(dt, 3),
                returncode=r.returncode,
                error=err,
                stdout_tail=(getattr(r, "stdout", None) or "")[-2000:],
                stderr_tail=(getattr(r, "stderr", None) or "")[-2000:],
            )
        return StageResult(name, False, dt, err, output)
    if output is not None and not output.is_file():
        _emit("run", 100, status="error")
        with contextlib.suppress(Exception):
            from aigamekit_shared.pipeline_trace import trace_stage

            trace_stage(
                asset=str(item_id or "?"),
                stage=name,
                status="fail",
                seconds=round(dt, 3),
                error="output missing",
                output=str(output),
            )
        return StageResult(name, False, dt, f"{name}: output não foi criado", output)
    # Emite como ``progress`` (não ``ok``) - ``ok`` no dashboard sinaliza
    # conclusão do asset INTEIRO; usá-lo aqui faria a célula piscar OK entre
    # cada stage e contar duplicado no progresso global.
    _emit("run", 100, status="progress", seconds=round(dt, 2))
    verify_payload: dict = {}
    if output is not None and output.is_file():
        with contextlib.suppress(Exception):
            from aigamekit_shared.glb_verify import post_save_verify

            vr = post_save_verify(output)
            if vr is not None:
                verify_payload = {
                    "glb_ok": vr.ok,
                    "glb_stage": vr.stage,
                    "glb_issues": [i.code for i in vr.issues],
                    "v_per_tri": vr.meta.get("v_per_tri"),
                    "has_normals": vr.meta.get("has_normals"),
                    "has_tangents": vr.meta.get("has_tangents"),
                    "has_uv": vr.meta.get("has_uv"),
                    "verts": vr.meta.get("vertex_count_total"),
                    "tris": vr.meta.get("triangle_count_total"),
                    "bytes": vr.meta.get("byte_size"),
                }
    with contextlib.suppress(Exception):
        from aigamekit_shared.pipeline_trace import trace_stage

        trace_stage(
            asset=str(item_id or "?"),
            stage=name,
            status="ok",
            seconds=round(dt, 3),
            output=str(output) if output else None,
            **verify_payload,
        )
    return StageResult(name, True, dt, output=output)


def _glb_has_skin(path: Path) -> bool:
    """Verifica se um GLB tem skin real (``skins[]`` + node com ``skin``).

    JOINTS_0/WEIGHTS_0 sozinhos NÃO contam - transfer-weights partido exportava
    attrs sem ``skins[]``, e o fallback animava ``rigged_hi`` sem paint.
    """
    try:
        import json
        import struct

        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return False
            f.read(8)  # version + total length
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)  # chunk type
            j = json.loads(f.read(json_len))
        skins = j.get("skins") or []
        if not skins:
            return False
        return any("skin" in n for n in (j.get("nodes") or []))
    except Exception:
        return False


def _glb_has_materials(path: Path) -> bool:
    """True se o GLB tem material com baseColorTexture (paint presente)."""
    try:
        import json
        import struct

        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return False
            f.read(8)
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            j = json.loads(f.read(json_len))
        for m in j.get("materials") or []:
            pbr = m.get("pbrMetallicRoughness") or {}
            if "baseColorTexture" in pbr:
                return True
        return False
    except Exception:
        return False


def _glb_has_animations(path: Path) -> bool:
    """True se o GLB declara pelo menos uma animation."""
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return False
            f.read(8)
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            j = json.loads(f.read(json_len))
        return bool(j.get("animations"))
    except Exception:
        return False


def _glb_has_duplicate_clips(path: Path) -> bool:
    """True se o GLB tem clips duplicadas (``attack`` + ``attack.001`` ...).

    Legado do DAG antigo: o game-pack corria sobre meshes que já traziam
    clips copiadas pelo transfer-weights e o Blender renomeava as cópias com
    sufixo ``.00N`` (bandit/boss_ogre com 14-18 clips em vez de 7-9). Um GLB
    assim NÃO é fonte aceitável para a ladder — o animate corre de novo.
    """
    try:
        with open(path, "rb") as f:
            if f.read(4) != b"glTF":
                return False
            f.read(8)
            json_len = struct.unpack("<I", f.read(4))[0]
            f.read(4)
            j = json.loads(f.read(json_len))
        names = [a.get("name", "") for a in (j.get("animations") or [])]
        bases = [n.split(".")[0] for n in names]
        return len(names) != len(set(names)) or len(bases) != len(set(bases))
    except Exception:
        return False


def _finish_lod_with_rollback(
    p: Path,
    level: int,
    expect_ok: Callable[[Path], bool],
    res: MasterPipelineResult,
) -> None:
    """``gltf_transform_finish`` com backup: rollback se perder skin/clips/paint.

    Corre DEPOIS da ladder estar completa — a partir daqui nenhum stage lê
    estes GLBs via bpy (collision usa o ``_painted``), logo meshopt é seguro.
    Entregável animado sem compressão > lod mudo: se o roundtrip npx destruir
    ``skins[]``/clips/materiais, restaura o ficheiro pré-finish.
    """
    import shutil as _sh_finish

    pre = p.with_suffix(p.suffix + ".pre_finish")
    try:
        _sh_finish.copy2(p, pre)
        from text3d.utils.gltf_finish import gltf_transform_finish

        fr = gltf_transform_finish(p, p, apply_uastc=True, apply_meshopt=True)
        if not expect_ok(p):
            log.error("master: finish lod%d destruiu skins/clips/paint — a restaurar pré-finish", level)
            _sh_finish.copy2(pre, p)
            res.stages.append(
                StageResult(
                    f"finish-lod{level}-rollback",
                    False,
                    0.0,
                    "finish destruiu animations/skins; restaurado pré-finish",
                    p,
                )
            )
        else:
            log.info(
                "master: finish lod%d ktx2=%s meshopt=%s(%s)",
                level,
                fr.ktx2_applied,
                fr.meshopt_applied,
                fr.meshopt_backend or "-",
            )
            if not fr.ktx2_applied or not fr.meshopt_applied:
                log.warning(
                    "master: finish lod%d incompleto (ktx2=%s meshopt=%s) — "
                    "correr `text3d doctor` (npx @gltf-transform/cli + libmeshoptimizer)",
                    level,
                    fr.ktx2_applied,
                    fr.meshopt_applied,
                )
    except Exception as exc:
        log.warning("master: finish promoted lod%d falhou: %s", level, exc)
    finally:
        with contextlib.suppress(OSError):
            if pre.is_file():
                pre.unlink()


def _run_static_lod_stages(
    *,
    run_stage: Callable[[str, list[str], Path | None], StageResult],
    res: MasterPipelineResult,
    mesh_final: Path,
    painted_p: Path,
    target_faces: int,
    base: str,
    with_lod: bool,
    bake_normals: bool,  # reservado para futuras variantes HI/LO bake
    text3d_bin: str,
    with_rig: bool,
    texture_size_lod0: int | None = None,
    lod_profile: Any = None,
    category: str | None = None,
) -> None:
    """Caminho estático (sem rig): ladder LOD0/1/2 via ``text3d lod``.

    LOD0 = 1.2x ``target_faces`` (decimado do painted com preservação de UV),
    LOD1 = target/2, LOD2 = target/4. ``--finish-lod0`` aplica tangents +
    KTX2 + meshopt ao LOD0. ``--texture-size`` = atlas lod0 (volume); lod1/2
    = /2 /4. Se um lod0 promovido existir, nada e clobberado.
    """
    lod0_p = _lod_path(mesh_final, 0)
    lod1_p = _lod_path(mesh_final, 1)
    lod2_p = _lod_path(mesh_final, 2)
    painted_faces = _count_faces_glb(painted_p)
    lod0_faces = _count_faces_glb(lod0_p) if lod0_p.is_file() else -1
    # Entregável já promovido (animate/rig) NÃO pode ser clobberado por painted→lod0.
    lod0_is_promoted = lod0_p.is_file() and (
        _glb_is_promoted_animated(lod0_p) or (with_rig and _glb_is_promoted_rigged(lod0_p))
    )
    # Frescura por mtime: o skip antigo comparava as faces de LOD1/2 com o
    # painted (25%/8%) e nunca disparava (LOD1 ~ 0.4x target << 0.25x painted),
    # pelo que a ladder era regenerada em cada passagem do master.
    _painted_mtime = painted_p.stat().st_mtime if painted_p.is_file() else 0.0

    def _fresh_lod(p: Path) -> bool:
        return p.is_file() and p.stat().st_size > 64 and p.stat().st_mtime >= _painted_mtime and _glb_has_materials(p)

    lod0_matches_painted = (
        lod0_p.is_file() and not lod0_is_promoted and _glb_has_materials(lod0_p) and _fresh_lod(lod0_p)
    )

    if lod0_is_promoted:
        res.stages.append(
            StageResult("bake-master", True, 0.0, f"skipped (lod0 already promoted faces={lod0_faces})", lod0_p)
        )
    elif lod0_matches_painted:
        res.stages.append(StageResult("bake-master", True, 0.0, f"skipped (lod0=painted faces={lod0_faces})", lod0_p))
    else:
        # LOD0 será gerado pelo text3d lod abaixo (com --target-faces e
        # --finish-lod0). Não fazemos copy painted→lod0 porque a ladder agora
        # decima LOD0 para 1.2x target_faces + aplica tangents/KTX2/meshopt.
        res.stages.append(
            StageResult("bake-master", True, 0.0, f"deferred to lod stage (painted faces={painted_faces})", lod0_p)
        )
    res.lod0_path = lod0_p

    # Stage 5 - Ladder a partir do PAINTED.
    # text3d lod com --target-faces decima LOD0=1.2x target, LOD1=target/2,
    # LOD2=target/4. --finish-lod0 aplica tangents+KTX2+meshopt ao LOD0.
    # Skip só se lod0 promoted OU ladder já válida.
    # ``with_lod=False`` (pipeline sem 'lod'): mesmo assim emitir lod0 — é o
    # entregável Round 3. Sem isto bake-master "deferred to lod" + skip lod
    # deixava weapons/terrain forms sem *_lod0.glb para sempre.
    lod1_faces = _count_faces_glb(lod1_p) if lod1_p.is_file() else -1
    lod2_faces = _count_faces_glb(lod2_p) if lod2_p.is_file() else -1
    lod_ladder_ok = (
        _fresh_lod(lod1_p) and _fresh_lod(lod2_p) and lod1_faces > 0 and lod2_faces > 0 and lod2_faces < lod1_faces
    )
    if lod0_is_promoted:
        res.stages.append(
            StageResult("lod", True, 0.0, "skipped (lod0 already promoted)", lod1_p if with_lod else lod0_p)
        )
    elif with_lod and lod_ladder_ok and lod0_matches_painted:
        res.stages.append(StageResult("lod", True, 0.0, "skipped (lod1/lod2 ok)", lod1_p))
    elif not with_lod and lod0_p.is_file() and _glb_has_materials(lod0_p) and lod0_faces > 0:
        res.stages.append(StageResult("lod", True, 0.0, "skipped (lod0 ok, no ladder)", lod0_p))
    else:
        # LOD0 budget = 1.2x category target_faces (alinha com lod0.yaml
        # max_per_category). Piso 8 para evitar degeneração em categorias
        # pequenas (ex.: effects target=2000).
        lod0_target = max(8, int(target_faces * 1.2))
        lod_min1 = max(target_faces // 2, 500)
        # Piso LOD2 = target/3 (alinha com text3d lod //3; edifícios grandes).
        lod_min2 = max(target_faces // 3, 150)
        # Rácios do perfil (defaults 0.40/0.22, o mesmo da ladder do repo) e
        # alvos explícitos: o ``--painted-mesh`` usava /2 e /3 fixos, ignorando
        # o perfil e deixando LOD1/2 fora do contrato.
        _r1 = float(getattr(lod_profile, "lod1_ratio", 0.40) or 0.40)
        _r2 = float(getattr(lod_profile, "lod2_ratio", 0.22) or 0.22)
        lod1_faces = max(lod_min1, int(lod0_target * _r1))
        lod2_faces = max(lod_min2, int(lod0_target * _r2))
        out_dir = mesh_final.parent.resolve()
        lod_argv = [
            text3d_bin,
            "lod",
            str(painted_p.resolve()),
            "-o",
            str(out_dir),
            "--basename",
            base,
            "--painted-mesh",
            str(painted_p.resolve()),
            "--target-faces",
            str(lod0_target),
            "--finish-lod0",
            "--lod1-ratio",
            str(_r1),
            "--lod2-ratio",
            str(_r2),
            "--lod1-faces",
            str(lod1_faces),
            "--lod2-faces",
            str(lod2_faces),
            "--min-faces-lod1",
            str(lod_min1),
            "--min-faces-lod2",
            str(lod_min2),
            "--category",
            str(category or ""),
        ]
        if texture_size_lod0 is not None and int(texture_size_lod0) > 0:
            lod_argv.extend(["--texture-size", str(int(texture_size_lod0))])
        # Gate: com ladder exige lod0+1+2; sem 'lod' no pipeline só lod0.
        s = run_stage("lod", lod_argv, lod0_p)
        if s.ok:
            if with_lod and not (lod0_p.is_file() and lod1_p.is_file() and lod2_p.is_file()):
                missing = [p.name for p in (lod0_p, lod1_p, lod2_p) if not p.is_file() or p.stat().st_size < 64]
                s = StageResult(
                    "lod",
                    False,
                    s.elapsed_s,
                    f"ladder incompleta após text3d lod: {', '.join(missing)}",
                    lod0_p,
                )
            elif not with_lod and not (lod0_p.is_file() and lod0_p.stat().st_size >= 64):
                s = StageResult(
                    "lod",
                    False,
                    s.elapsed_s,
                    f"lod0 ausente após text3d lod: {lod0_p.name}",
                    lod0_p,
                )
        if not s.ok:
            res.ok = False
        res.stages.append(s)


def _run_patch_pbr_stage(
    *,
    run_stage: Callable[[str, list[str], Path | None], StageResult],
    res: MasterPipelineResult,
    text3d_bin: str,
    lod_paths: list[Path],
    patch_pbr: bool,
) -> None:
    """Pós-lod: ``text3d patch-pbr`` — normal+AO idempotente por ficheiro.

    O paint3d já embute os mapas no painted (pbr_enrich); este passo cobre o
    que o rebake de LOD deixa cair (occlusion) e GLBs legados. Não-bloqueante
    por design: falha de materialize/GPU regista o motivo e o asset segue
    albedo+MR (filosofia do pbr_enrich do paint3d).
    """
    first = next((p for p in lod_paths if p.is_file()), None)
    if not patch_pbr:
        res.stages.append(StageResult("patch-pbr", True, 0.0, "skipped (lod.patch_pbr=false)", first))
        return
    appended = 0
    for p in lod_paths:
        if not p.is_file() or p.stat().st_size < 64:
            continue
        s = run_stage("patch-pbr", [text3d_bin, "patch-pbr", str(p)], p)
        if not s.ok:
            detail = (s.error or "falha").strip()
            res.stages.append(StageResult("patch-pbr", True, s.elapsed_s, f"skip gracioso: {detail[:200]}", p))
        else:
            res.stages.append(s)
        appended += 1
    if appended == 0:
        res.stages.append(StageResult("patch-pbr", True, 0.0, "skip (nenhum LOD para patchar)", first))


def _run_split_lod_stages(
    *,
    run_stage: Callable[[str, list[str], Path | None], StageResult],
    res: MasterPipelineResult,
    mesh_final: Path,
    painted_p: Path,
    target_faces: int,
    base: str,
    with_lod: bool,
    text3d_bin: str,
    profile: GameProfile,
    row: ManifestRow | None = None,
) -> None:
    """Caminho split-antes-de-LOD para árvores (Stump+Top em cada LOD).

    DAG: painted → split-at-height → stump_painted + top_painted
         → LOD stump (lod0/1/2) + LOD top (lod0/1/2)
         → composição lodN = stump_lodN + top_lodN (nomes Stump/Top)
         → collision stump + collision top (separados)

    Cada LOD preserva os nomes Stump/Top para o DestructiblePlugin (fall style).
    Collision é gerado por peça (stump_collision + top_collision).
    """
    import bpy

    from aigamekit_shared.bpy_mesh import clear_scene, save_glb

    inter_dir = mesh_final.parent / "_intermediate"
    inter_dir.mkdir(parents=True, exist_ok=True)

    t3 = profile.text3d
    cut_h = None if t3 is None else t3.split_cut_height

    # --- 1. Split no painted (uma vez) ---
    stump_painted = inter_dir / f"{base}_stump_painted.glb"
    top_painted = inter_dir / f"{base}_top_painted.glb"

    force_split = os.environ.get("AIGAMEKIT_REDO_SPLIT", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    stale_seal = _split_seal_stale(mesh_final)
    if force_split or stale_seal:
        removed = invalidate_split_artifacts(mesh_final)
        if removed:
            log.info(
                "split-lod: invalidate %d artefactos (force=%s stale_seal=%s)",
                len(removed),
                force_split,
                stale_seal,
            )

    need_split = not (stump_painted.is_file() and top_painted.is_file())
    if need_split:
        split_out = inter_dir / f"{base}_split_tmp.glb"
        argv = [
            text3d_bin,
            "split-at-height",
            str(painted_p),
            "-o",
            str(split_out),
            "--split-files",
            "--no-cap",
        ]
        if cut_h is not None:
            argv.extend(["--cut-height", str(float(cut_h))])
        s = run_stage("split-at-height", argv, split_out)
        res.stages.append(s)
        if not s.ok:
            res.ok = False
            return
        raw_stump = split_out.with_name(f"{split_out.stem}_stump{split_out.suffix}")
        raw_top = split_out.with_name(f"{split_out.stem}_top{split_out.suffix}")
        if raw_stump.is_file():
            shutil.move(str(raw_stump), str(stump_painted))
        if raw_top.is_file():
            shutil.move(str(raw_top), str(top_painted))
        # Limpar temporário.
        split_out.unlink(missing_ok=True)
        _write_split_seal_stamp(mesh_final)

    if not stump_painted.is_file() or not top_painted.is_file():
        res.stages.append(StageResult("split-at-height", False, 0.0, "stump/top painted ausentes"))
        res.ok = False
        return

    if not _split_seal_stamp_path(mesh_final).is_file():
        _write_split_seal_stamp(mesh_final)

    # --- 2. LOD por peça ---
    lod0_p = _lod_path(mesh_final, 0)
    lod1_p = _lod_path(mesh_final, 1)
    lod2_p = _lod_path(mesh_final, 2)

    if with_lod:
        stump_target = max(8, int(target_faces * 0.45))
        top_target = max(8, int(target_faces * 0.75))
        stump_dir = inter_dir / f"{base}_stump_lod"
        top_dir = inter_dir / f"{base}_top_lod"
        stump_dir.mkdir(exist_ok=True)
        top_dir.mkdir(exist_ok=True)

        # LOD stump/top SEM finish (KTX2): bpy 5.2 não importa KHR_texture_basisu
        # no compose. Finish (UASTC+meshopt) corre no lod composto abaixo.
        split_tex = _resolve_lod0_texture_size(profile, row)
        stump_lod_argv = [
            text3d_bin,
            "lod",
            str(stump_painted),
            "-o",
            str(stump_dir),
            "--basename",
            "stump",
            "--painted-mesh",
            str(stump_painted),
            "--target-faces",
            str(stump_target),
            "--texture-size",
            str(int(split_tex)),
            "--no-finish",
            "--lod1-ratio",
            "0.40",
            "--lod2-ratio",
            "0.15",
            "--min-faces-lod1",
            str(max(stump_target // 2, 300)),
            "--min-faces-lod2",
            str(max(stump_target // 4, 100)),
            "--category",
            str(row.category or row.kind or ""),
        ]
        s_stump = run_stage("lod-stump", stump_lod_argv, None)
        res.stages.append(s_stump)

        # LOD top.
        top_lod_argv = [
            text3d_bin,
            "lod",
            str(top_painted),
            "-o",
            str(top_dir),
            "--basename",
            "top",
            "--painted-mesh",
            str(top_painted),
            "--target-faces",
            str(top_target),
            "--texture-size",
            str(int(split_tex)),
            "--no-finish",
            "--lod1-ratio",
            "0.40",
            "--lod2-ratio",
            "0.15",
            "--min-faces-lod1",
            str(max(top_target // 2, 400)),
            "--min-faces-lod2",
            str(max(top_target // 4, 150)),
            "--category",
            str(row.category or row.kind or ""),
        ]
        s_top = run_stage("lod-top", top_lod_argv, None)
        res.stages.append(s_top)

        # --- 3. Composição lodN = stump_lodN + top_lodN ---
        for lvl, out_p in ((0, lod0_p), (1, lod1_p), (2, lod2_p)):
            stump_lod = stump_dir / f"stump_lod{lvl}.glb"
            top_lod = top_dir / f"top_lod{lvl}.glb"
            if not stump_lod.is_file() or not top_lod.is_file():
                log.warning("split-lod: falta lod%d (stump=%s top=%s)", lvl, stump_lod, top_lod)
                continue
            clear_scene()
            from aigamekit_shared.bpy_mesh import import_gltf

            for glb in (stump_lod, top_lod):
                # import_gltf pré-decodifica KTX2/meshopt (bpy sem BasisU).
                import_gltf(glb)
            # Renomear para Stump/Top (findTreeSplitParts procura estes nomes).
            for obj in bpy.context.scene.objects:
                if obj.type != "MESH":
                    continue
                if "stump" in obj.name.lower():
                    obj.name = "Stump"
                    obj.data.name = "Stump"
                elif "top" in obj.name.lower():
                    obj.name = "Top"
                    obj.data.name = "Top"
            objs = [o for o in bpy.context.scene.objects if o.type == "MESH"]
            save_glb(objs, out_p, export_apply=True)
            clear_scene()
            res.stages.append(StageResult(f"compose-lod{lvl}", True, 0.0, f"stump+top → {out_p.name}", out_p))
            # Finish no composto (não nas peças) — evita KTX2 no import bpy.
            _finish_lod_with_rollback(out_p, lvl, _glb_has_materials, res)

    res.lod0_path = lod0_p

    # --- 4. Collision por peça ---
    stump_coll = _stump_path(mesh_final).parent / f"{base}_stump_collision.glb"
    top_coll = _stump_path(mesh_final).parent / f"{base}_top_collision.glb"
    for src, dst, name in ((stump_painted, stump_coll, "collision-stump"), (top_painted, top_coll, "collision-top")):
        if dst.is_file():
            res.stages.append(StageResult(name, True, 0.0, "skipped (existente)", dst))
            continue
        coll_argv = [text3d_bin, "collision", str(src), "-o", str(dst), "--max-faces", "200"]
        s = run_stage(name, coll_argv, dst)
        res.stages.append(s)

    # Collision principal (fallback para compatibilidade — usa stump).
    coll_p = _collision_path(mesh_final)
    if not coll_p.is_file() and stump_coll.is_file():
        shutil.copy2(stump_coll, coll_p)


def run_master_pipeline(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    *,
    manifest_dir: Path,
    child_env: dict[str, str],
    with_lod: bool = True,
    with_collision: bool = True,
    with_rig: bool = False,
    with_animate: bool = False,
    with_validate: bool = True,
    bake_normals: bool | None = None,
    on_progress_line: Callable[[str], None] | None = None,
    gpu_ids: list[int] | None = None,
    force: bool = False,
) -> MasterPipelineResult:
    """Executa o DAG novo a partir de ``id_shape.glb`` e ``id_painted.glb``.

    Pré-condições:
    - ``_shape_path(mesh_final)`` existe (saída de Stage 1 - text3d generate
      com ``--no-topology-fix`` ou legacy generate).
    - ``_painted_path(mesh_final)`` existe (Stage 3 - paint3d texture sobre
      o GLB intermediário; tipicamente sobre o ``_clean.glb`` produzido aqui).

    Pós-condições em sucesso:
    - ``_lod_path(mesh_final, 0|1|2).glb`` em ``meshes/``.
    - ``_rigged_path(...)`` e ``_animated_path(...)`` quando ``with_rig`` e
      ``with_animate``.
    - Intermediários em ``_intermediate/``.
    """
    # Aceita painted/shape/lod paths — ancora sempre em meshes/{id}.glb.
    mesh_final = _canonical_mesh_final(mesh_final)
    if force:
        # ``--force`` tem de invalidar o que o master reutiliza por existência:
        # rigged/animated, ladder, collision e peças de split. O painted é
        # tratado pelo stage de paint (que também recebe force).
        for _p in (_rigged_path(mesh_final), _animated_path(mesh_final)):
            with contextlib.suppress(OSError):
                _p.unlink()
        invalidate_split_artifacts(mesh_final)
    res = MasterPipelineResult(asset_id=row.id, ok=True)
    res.intermediates_dir = _intermediate_dir(mesh_final)

    # Round 2 - smart defaults para bake-normals.
    # Precedência: argumento explícito -> profile.master_bake_normals -> categoria.
    if bake_normals is None:
        bake_normals = bool(getattr(profile, "master_bake_normals", False)) or category_wants_bake_normals(
            row.category,
            overrides=getattr(profile, "master_bake_normals_categories", None),
        )

    profile_enabled = str(child_env.get("AIGAMEKIT_PROFILE", "")).strip() == "1"

    def _run(name: str, argv: list[str], output: Path | None = None) -> StageResult:
        return _stage(
            name,
            argv,
            child_env,
            manifest_dir,
            output,
            item_id=row.id,
            profile_enabled=profile_enabled,
            on_progress_line=on_progress_line,
        )

    text3d_bin = _bin_or_none("TEXT3D_BIN", "text3d")
    rigging3d_bin = _bin_or_none("RIGGING3D_BIN", "rigging3d")
    animator3d_bin = _bin_or_none("ANIMATOR3D_BIN", "animator3d")
    if not text3d_bin:
        res.ok = False
        res.stages.append(StageResult("setup", False, 0.0, "text3d não encontrado"))
        return res

    # Stage 0 - rocks3d generate (para assets da categoria "rock").
    # Produz mesh_final diretamente via rocks3d CLI e salta as stages
    # do Text3D (shape/topology-fix/paint/bake-master). Retorna cedo.
    if _row_is_rock(row):
        rocks3d_bin = _bin_or_none("ROCKS3D_BIN", "rocks3d")
        if rocks3d_bin:
            rk = profile.rocks3d or Rocks3DProfile()
            rk_seed = _seed_for_row(profile, row.id)
            rk_quality = rk.quality or getattr(profile, "generation", None) or "medium"
            rock_type = row.kind or "boulder"
            rk_argv = _rocks3d_argv(rocks3d_bin, rock_type, mesh_final, seed=rk_seed, quality=rk_quality)
            s = _run("rocks3d", rk_argv, mesh_final)
            res.stages.append(s)
            if not s.ok:
                res.ok = False
                return res
            res.lod0_path = mesh_final
            res.recompute_totals()
            return res
        # rocks3d não disponível - cai para o pipeline Text3D normal

    # Round 2: shape/painted podem estar em meshes/ OU em meshes/_intermediate/
    # (após uma run anterior). Resolve dinamicamente para permitir resume.
    shape_p = _shape_existing(mesh_final) or _shape_path(mesh_final)
    painted_p = _painted_existing(mesh_final) or _painted_path(mesh_final)
    clean_existing = _clean_existing(mesh_final)
    clean_p = clean_existing if clean_existing is not None else _clean_path(mesh_final)

    # Shape obrigatório só se precisamos de topology-fix (sem clean).
    # Resume: clean+painted sem shape (apagado por stale) -> segue bake/LOD.
    if not shape_p.is_file() and not (clean_existing is not None and clean_existing.is_file()):
        res.ok = False
        res.stages.append(StageResult("preflight", False, 0.0, f"shape ausente: {shape_p}"))
        return res

    # Stage 2 - topology-fix (shape -> clean). Skip se já temos um clean
    # válido (em meshes/ ou _intermediate/) - resume-friendly.
    clean_p.parent.mkdir(parents=True, exist_ok=True)
    if clean_existing is not None and clean_existing.is_file() and not force:
        res.stages.append(StageResult("topology-fix", True, 0.0, "skipped (clean existente)", clean_p))
    else:
        topo_argv = [text3d_bin, "topology-fix", str(shape_p), "-o", str(clean_p)]
        topo_argv.extend(_topology_fix_extra_argv(profile, row, manifest_dir=manifest_dir))
        s = _run(
            "topology-fix",
            topo_argv,
            clean_p,
        )
        res.stages.append(s)
        if not s.ok:
            res.ok = False
            return res

    # ------------------------------------------------------------------
    # Round 3 DAG — painted é pré-condição; daqui dois caminhos:
    #   rig:    rigging3d sobre o PAINTED -> animator3d game-pack x1 ->
    #           text3d lod sobre o animated/rigged (decimate preserva
    #           armature/weights/clips nativamente) -> lod0/1/2 + finish.
    #   static: text3d lod com --target-faces (LOD0=1.2x target) +
    #           --finish-lod0 (tangents+KTX2+meshopt). LOD1/2 = target/2, /4.
    # Sem rigged_hi (GPU sobre _clean HI sem textura, descartado) nem
    # transfer-weights por LOD (KDTree x3 + game-pack x3 + clips duplicadas).
    # ------------------------------------------------------------------
    if not painted_p.is_file():
        res.ok = False
        res.stages.append(StageResult("bake-master", False, 0.0, f"painted ausente: {painted_p}"))
        return res

    # target_faces = orçamento base (categoria x volume); LOD0 = 1.2xtarget.
    target_faces = _resolve_lod_target_faces(profile, row)
    if target_faces <= 0:
        target_faces = 8000
    lod0_tex = _resolve_lod0_texture_size(profile, row)
    lod0_p = _lod_path(mesh_final, 0)
    lod1_p = _lod_path(mesh_final, 1)
    lod2_p = _lod_path(mesh_final, 2)

    from .paths import _base_stem as _bs_bake

    _bake_base = _bs_bake(mesh_final.stem)

    # Arquiva intermediários do DAG antigo (rigged_hi, per-lod rigged/
    # animated/pre_promote) antes de qualquer skip — idempotente.
    archived_legacy = archive_legacy_rig_intermediates(mesh_final)
    if archived_legacy:
        log.info("master: %d intermediários legados -> _intermediate/", len(archived_legacy))

    lod0_promoted_anim = _glb_is_promoted_animated(lod0_p) if lod0_p.is_file() else False
    lod0_promoted_rig = _glb_is_promoted_rigged(lod0_p) if lod0_p.is_file() else False

    rigged_p = _rigged_existing(mesh_final) or _rigged_path(mesh_final)
    animated_p = _animated_existing(mesh_final) or _animated_path(mesh_final)

    def _rigged_ok(p: Path | None) -> bool:
        """Rigged utilizável: skin real + o paint herdado do painted."""
        return p is not None and p.is_file() and _glb_has_skin(p) and _glb_has_materials(p)

    def _animated_ok(p: Path | None) -> bool:
        """Animated utilizável: skin + clips + paint, sem clips duplicadas .00N."""
        return (
            p is not None
            and p.is_file()
            and _glb_has_skin(p)
            and _glb_has_animations(p)
            and _glb_has_materials(p)
            and not _glb_has_duplicate_clips(p)
        )

    promotion_kind = "none"
    rig_source: Path | None = None

    if with_rig and rigging3d_bin:
        # Stage 7 - rigging3d pipeline sobre o PAINTED (topologia final do
        # LOD0, com textura — ~6x mais leve que o antigo rig sobre _clean HI
        # e o output serve directamente de fonte da ladder).
        if not force and (lod0_promoted_anim or (not with_animate and lod0_promoted_rig)):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (lod0 já promovido)", lod0_p))
        elif not force and with_animate and _animated_ok(animated_p):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (animated existente)", animated_p))
        elif not force and _rigged_ok(rigged_p):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (rigged existente)", rigged_p))
        else:
            from .vramd_coord import gpu_is_remote

            rig_gpu = str(getattr(row, "gpu", "auto") or "auto")
            rig_remote = gpu_is_remote(rig_gpu)
            if not rig_remote:
                # Só o vramd local precisa de ser zerado: no remoto o job passa
                # pelo supervisor e um `zero` mataria o worker do próprio rig.
                _release_vramd_before_external_stage("rigging3d")
            # Explicitar o destino: com ``gpu: auto`` o filho herdava
            # VRAMD_GPU_MODE, mas um subprocesso lançado sem a env (ou num
            # runner que a limpa) caía no rig in-process e enchia a GPU local.
            # O Rigging3D tem worker/adapter no vramd remoto (SkinTokens).
            rig_gpu_arg = "remote" if rig_remote else ("local" if rig_gpu == "local" else None)
            rig_argv = _rigging3d_pipeline_argv(
                rigging3d_bin,
                painted_p,
                rigged_p,
                seed=_seed_for_row(profile, row.id),
                rig_profile=profile.rigging3d,
                gpu_ids=gpu_ids,
                hw_auto=profile.hw_auto,
                quality=profile.generation,
                gpu=rig_gpu_arg,
            )
            s = _run("rigging3d", rig_argv, rigged_p)
            res.stages.append(s)
            if s.ok and not _rigged_ok(rigged_p):
                # Gate: skin real E paint herdado — senão a ladder nasce branca.
                res.stages.append(
                    StageResult("rigging3d-gate", False, 0.0, "rigged sem skins[] ou sem baseColorTexture", rigged_p)
                )
                s = StageResult("rigging3d", False, s.elapsed_s, "rigged output inválido (gate)", rigged_p)
            if not s.ok:
                with_rig = False  # fallback estático — o asset não fica sem LODs

        # Stage 8 - animator3d game-pack x1 sobre o rigged (antes: um por LOD).
        if with_rig and with_animate and animator3d_bin:
            if lod0_promoted_anim:
                res.stages.append(StageResult("animate", True, 0.0, "skipped (lod0 já animado)", lod0_p))
            elif _animated_ok(animated_p):
                res.stages.append(StageResult("animate", True, 0.0, "skipped (animated existente)", animated_p))
            elif _rigged_ok(rigged_p):
                # Animation config: per-row ``animate:`` sub-dict overrides global ``animator3d:`` profile.
                anim_prof = profile.animator3d or Animator3DProfile()
                eff_preset = (row.animate_preset or anim_prof.preset or "humanoid").strip().lower()
                # Fallback por categoria só quando nem a row nem o profile definem preset.
                # ``creature`` -> humanoid (mocap); não-humanoides reais exigem animate.preset explícito.
                if not row.animate_preset and not (profile.animator3d and profile.animator3d.preset):
                    eff_preset = animator_preset_for_category(row.category)
                eff_clips = row.animate_clips or anim_prof.clips
                eff_procedural = row.animate_procedural if row.animate_procedural is not None else anim_prof.procedural
                eff_force_preset = (
                    row.animate_force_preset if row.animate_force_preset is not None else anim_prof.force_preset
                )
                from aigamekit_shared.anim_packs import AnimPackError, expand_anim_packs

                eff_anim_pack = (row.animate_anim_pack or anim_prof.anim_pack or "quaternius").strip().lower()
                try:
                    expand_anim_packs(eff_anim_pack)
                except AnimPackError:
                    log.warning("animate.anim_pack inválido (%s) — a usar quaternius", eff_anim_pack)
                    eff_anim_pack = "quaternius"
                an_argv = _animator3d_game_pack_argv(
                    animator3d_bin,
                    rigged_p,
                    animated_p,
                    preset=eff_preset,
                    clips=eff_clips,
                    procedural=eff_procedural,
                    force_preset=eff_force_preset,
                    anim_pack=eff_anim_pack,
                    ik_limits=anim_prof.ik_limits,
                )
                _release_vramd_before_external_stage("animate")
                s = _run("animate", an_argv, animated_p)
                res.stages.append(s)
                if s.ok and not _animated_ok(animated_p):
                    log.error("master: animate perdeu skin/clips/paint (%s) — a promover rigged", animated_p.name)
                    res.stages.append(
                        StageResult("animate-gate", False, 0.0, "animated sem skins/clips/baseColorTexture", animated_p)
                    )
            else:
                res.stages.append(StageResult("animate", False, 0.0, "rigged indisponível para animate", rigged_p))

        # Fonte da ladder: animated > rigged (animate falhado => promove rigged).
        if with_animate and _animated_ok(animated_p):
            rig_source = animated_p
            promotion_kind = "animated"
        elif _rigged_ok(rigged_p):
            rig_source = rigged_p
            promotion_kind = "rigged"

    # Fallback de resume: entregável já promovido mas os intermediários foram
    # apagados — a ladder (re)gera-se a partir do próprio lod0 (o import do
    # text3d lod é meshopt-aware via bpy 5.2 / gltf-transform).
    if with_rig and rig_source is None and (lod0_promoted_anim or lod0_promoted_rig):
        rig_source = lod0_p
        promotion_kind = "animated" if lod0_promoted_anim else "rigged"

    # Stage 9 - lod0 + ladder a partir da fonte rigada.
    # rig_max_level do perfil (default 1: só lod0/lod1 rigados; criaturas
    # que animam em *_lod2 precisam de 2 — meshes sem weights não animam).
    rig_max_level = profile.lod.rig_max_level if profile.lod else RIG_MAX_LEVEL
    if rig_source is not None:
        expect_rigged = _glb_is_promoted_animated if promotion_kind == "animated" else _glb_is_promoted_rigged

        def expect(path: Path, level: int = 0) -> bool:
            # Níveis acima de RIG_MAX_LEVEL saem estáticos de propósito
            # (``text3d lod --rig-max-level``): exigir skins[] neles punha a
            # ladder a regenerar-se para sempre e o finish em rollback.
            if level > rig_max_level:
                return path.is_file() and path.stat().st_size >= 64
            return expect_rigged(path)

        ladder_ok = expect(lod0_p, 0) and (
            not with_lod or (lod1_p.is_file() and lod2_p.is_file() and expect(lod1_p, 1) and expect(lod2_p, 2))
        )
        if not force and ladder_ok:
            res.stages.append(StageResult("lod", True, 0.0, f"skipped (ladder {promotion_kind} ok)", lod0_p))
        elif with_lod:
            lod_min1 = max(target_faces // 2, 500)
            lod_min2 = max(target_faces // 3, 150)
            _r1 = float(getattr(profile.lod, "lod1_ratio", 0.40) or 0.40) if profile.lod else 0.40
            _r2 = float(getattr(profile.lod, "lod2_ratio", 0.22) or 0.22) if profile.lod else 0.22
            lod_argv = [
                text3d_bin,
                "lod",
                str(rig_source),
                "-o",
                str(mesh_final.parent),
                "--basename",
                _bake_base,
                "--lod1-ratio",
                str(_r1),
                "--lod2-ratio",
                str(_r2),
                "--min-faces-lod1",
                str(lod_min1),
                "--min-faces-lod2",
                str(lod_min2),
                "--no-meshopt",
                "--texture-size",
                str(int(lod0_tex)),
                "--rig-max-level",
                str(rig_max_level),
            ]
            if target_faces > 0:
                lod_argv.extend(["--target-faces", str(max(8, int(target_faces * 1.2)))])
            s = _run("lod", lod_argv)
            res.stages.append(s)
            # Finish (KTX2+meshopt+tangents) por ficheiro, DEPOIS da ladder
            # completa — nenhum stage posterior lê estes GLBs via bpy
            # (collision usa o painted). Rollback automático se o roundtrip
            # destruir skins/clips — entregável sem compressão > lod mudo.
            for lvl, p in ((0, lod0_p), (1, lod1_p), (2, lod2_p)):
                if p.is_file():
                    _finish_lod_with_rollback(p, lvl, lambda q, _lvl=lvl: expect(q, _lvl), res)
        else:
            import shutil as _sh

            _sh.copy2(rig_source, lod0_p)
            _finish_lod_with_rollback(lod0_p, 0, lambda q: expect(q, 0), res)
            res.stages.append(StageResult("lod", True, 0.0, f"lod0={promotion_kind} copy (sem ladder)", lod0_p))
        res.lod0_path = lod0_p
    elif wants_split_at_height(profile, row):
        # Árvores com split-at-height: split ANTES do LOD, cada peça LODed
        # separadamente, composição Stump+Top em cada LOD, collision por peça.
        _run_split_lod_stages(
            run_stage=_run,
            res=res,
            mesh_final=mesh_final,
            painted_p=painted_p,
            target_faces=target_faces,
            base=_bake_base,
            with_lod=with_lod,
            text3d_bin=text3d_bin,
            profile=profile,
            row=row,
        )
    else:
        _run_static_lod_stages(
            run_stage=_run,
            res=res,
            mesh_final=mesh_final,
            painted_p=painted_p,
            target_faces=target_faces,
            base=_bake_base,
            with_lod=with_lod,
            bake_normals=bool(bake_normals),
            text3d_bin=text3d_bin,
            with_rig=with_rig,
            texture_size_lod0=lod0_tex,
            lod_profile=profile.lod,
            category=row.category or row.kind,
        )

    # Stage 5.5 - patch-pbr pós-lod: garante normal+AO em todos os níveis
    # (idempotente; cobre o rebake de LOD e GLBs legados).
    _lod_profile = profile.lod
    _patch_pbr = _lod_profile.patch_pbr if _lod_profile is not None else True
    _patch_paths: list[Path] = []
    for _lvl in (0, 1, 2):
        _patch_paths.append(mesh_final.parent / f"{_bake_base}_lod{_lvl}.glb")
        _patch_paths.append(mesh_final.parent / f"{_bake_base}_stump_lod{_lvl}.glb")
        _patch_paths.append(mesh_final.parent / f"{_bake_base}_top_lod{_lvl}.glb")
    _run_patch_pbr_stage(
        run_stage=_run,
        res=res,
        text3d_bin=text3d_bin,
        lod_paths=_patch_paths,
        patch_pbr=_patch_pbr,
    )

    # Stage 6 - collision: ``_clean`` nos estáticos (fechado, o envelope precisa)
    # e ``lod0`` nos riggados (o painted está em T-pose) — ver collision_source.
    # Saltado quando o split-lod já gerou collision por peça (stump+top).
    _split_lod_did_collision = wants_split_at_height(profile, row) and not bool(rig_source)
    if with_collision and not _split_lod_did_collision:
        coll_p = _collision_path(mesh_final)
        if coll_p.is_file() and not force:
            res.stages.append(StageResult("collision", True, 0.0, "skipped (collision existente)", coll_p))
        else:
            coll_src = collision_source(
                mesh_final,
                painted_p,
                lod0_p,
                rigged=bool(rig_source),
                allow_refined=not _row_refine_disabled(row),
            )
            from .manifest import effective_collision_args

            coll_opts = effective_collision_args(profile, row)
            coll_argv = [
                text3d_bin,
                "collision",
                str(coll_src),
                "-o",
                str(coll_p),
                "--max-faces",
                str(coll_opts["max_faces"]),
                "--mode",
                str(coll_opts["mode"]),
            ]
            if coll_opts.get("voxel_size") is not None:
                coll_argv.extend(["--voxel-size", str(coll_opts["voxel_size"])])
            if coll_opts.get("inflate") is not None:
                coll_argv.extend(["--inflate", str(coll_opts["inflate"])])
            s = _run("collision", coll_argv, coll_p)
            res.stages.append(s)
            # Round 2 - finalizar collision: dedup+prune (sem KTX2/meshopt/tangents).
            if s.ok and coll_p.is_file():
                try:
                    from text3d.utils.gltf_finish import gltf_transform_finish

                    gltf_transform_finish(
                        coll_p,
                        coll_p,
                        apply_tangents=False,
                        apply_uastc=False,
                        apply_meshopt=False,
                        apply_dedup=True,
                        apply_prune=True,
                    )
                except Exception as exc:
                    log.warning("master: finish collision falhou: %s", exc)
                dev = collision_alignment_deviation(coll_p, lod0_p)
                if dev is not None and dev > COLLISION_ALIGN_TOL_M:
                    console.print(
                        f"[yellow]collision desalinhada em {row.id}: {dev:.2f} m entre a AABB da "
                        f"colisão e a do lod0 (fonte {coll_src.name})[/yellow]"
                    )

    # Stage 6b - split-at-height LEGADO: só para árvores que NÃO passaram pelo
    # _run_split_lod_stages (ex.: category=tree sem rig, path estático antigo).
    # O _run_split_lod_stages já faz split+LOD+collision por peça internamente.
    if wants_split_at_height(profile, row) and not _split_lod_did_collision:
        s = run_split_at_height_stage(
            text3d_bin=text3d_bin,
            mesh_final=mesh_final,
            profile=profile,
            run_stage=_run,
        )
        res.stages.append(s)
        if not s.ok:
            res.ok = False

    # Stage 10 - validação. LOD0 é gate; LOD1/2 são warnings.
    # As regras efectivas dependem de quem foi promovido: animated.yaml >
    # rigged.yaml > lod{N}.yaml. Quando promotion_kind != "none" usamos a
    # mesma regra para LOD0/1/2 (mesmo nível semântico).
    if with_validate:
        rules_dir = _rules_dir()
        static_rules = {0: "lod0.yaml", 1: "lod1.yaml", 2: "lod2.yaml"}
        promoted_rules = {"animated": "animated.yaml", "rigged": "rigged.yaml"}.get(promotion_kind)
        # Acima de RIG_MAX_LEVEL a ladder sai estática **de propósito**
        # (``text3d lod --rig-max-level``, ver a helper ``expect`` acima), por
        # isso validar esses níveis com rigged/animated.yaml exigia armature +
        # actions + JOINTS_0 que nunca lá estão: validate-lod2 falhava sempre
        # em qualquer personagem.
        rule_for = {
            lvl: (promoted_rules if promoted_rules and lvl <= rig_max_level else static_rules[lvl]) for lvl in (0, 1, 2)
        }

        for lvl, lod_p in ((0, lod0_p), (1, lod1_p), (2, lod2_p)):
            if lod_p.is_file():
                rules = rules_dir / rule_for[lvl]
                if rules.is_file():
                    s = _run_check_glb(
                        lod_p,
                        rules,
                        category=row.category,
                        env=child_env,
                        cwd=manifest_dir,
                    )
                    s.name = f"validate-lod{lvl}"
                    res.stages.append(s)
                    if not s.ok and lvl == 0:
                        # LOD0 inválido é gate.
                        res.ok = False
        # Validação extra contra lod{N}.yaml (face count caps). Mesmo após
        # promoção, lod0 deve respeitar limites de faces da categoria.
        if promotion_kind != "none":
            base_rules = rules_dir / "lod0.yaml"
            if base_rules.is_file() and lod0_p.is_file():
                s = _run_check_glb(lod0_p, base_rules, category=row.category, env=child_env, cwd=manifest_dir)
                s.name = "validate-lod0-base"
                res.stages.append(s)
                if not s.ok:
                    res.ok = False

    # Move intermediários (shape, painted) para _intermediate/.
    move_to_intermediate(shape_p, mesh_final)
    move_to_intermediate(painted_p, mesh_final)
    # rigged_hi e clean já nascem em _intermediate/.

    # Deliverables: só id_lodN + id_collision. Arquiva bare / aliases.
    finalized = finalize_mesh_deliverables(mesh_final)
    if finalized:
        log.info(
            "master: deliverables limpos (%d): %s",
            len(finalized),
            ", ".join(p.name for p in finalized[:8]),
        )
        res.stages.append(
            StageResult(
                "finalize-deliverables",
                True,
                0.0,
                f"archived/promoted {len(finalized)} non-lod files",
                _lod_path(mesh_final, 0),
            )
        )

    res.recompute_totals()
    return res


def resume_master_pipeline(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    *,
    manifest_dir: Path,
    child_env: dict[str, str],
    with_lod: bool = True,
    with_collision: bool = True,
    with_rig: bool = False,
    with_animate: bool = False,
    with_validate: bool = True,
    bake_normals: bool | None = None,
    on_progress_line: Callable[[str], None] | None = None,
    gpu_ids: list[int] | None = None,
) -> MasterPipelineResult:
    """Retoma o master pipeline a partir do checkpoint detectado.

    Diferente de ``run_master_pipeline``: não falha se um stage de pré-condição
    já está pronto; usa ``_classify_row_state_master`` para decidir o entry
    point. Re-executa apenas o que falta.
    """
    from .paths import (
        _ROW_DONE,
        _classify_row_state_master,
    )

    img_final = mesh_final.with_suffix(".png")  # heurística - caller tipicamente fornece via row
    state = _classify_row_state_master(
        img_final=img_final,
        mesh_final=mesh_final,
        want_texture=True,
        wants_rig=with_rig,
        wants_animate=with_animate,
        wants_lod=with_lod,
        wants_collision=with_collision,
    )

    if state == _ROW_DONE:
        res = MasterPipelineResult(asset_id=row.id, ok=True)
        res.lod0_path = _lod_path(mesh_final, 0)
        res.intermediates_dir = _intermediate_dir(mesh_final)
        return res

    # Para qualquer estado parcial, simplesmente re-corre o pipeline completo.
    # ``run_master_pipeline`` tem skips implícitos (chamada a binary é o caro;
    # quando o output já existe, podemos delegar verificação a cada stage no
    # futuro). Isto cobre 90% dos casos de retomada sem complicar o DAG.
    log.info("resume-master: state=%s - retomando pipeline para %s", state, row.id)
    return run_master_pipeline(
        profile,
        row,
        mesh_final,
        manifest_dir=manifest_dir,
        child_env=child_env,
        with_lod=with_lod,
        with_collision=with_collision,
        with_rig=with_rig,
        with_animate=with_animate,
        with_validate=with_validate,
        bake_normals=bake_normals,
        on_progress_line=on_progress_line,
        gpu_ids=gpu_ids,
    )


def aggregate_master_results(
    results: list[StageResult],
    rec: dict[str, Any],
) -> None:
    """Despeja stages num record de manifest (run.jsonl)."""
    timing: dict[str, float] = rec.get("timing") or {}
    for st in results:
        timing[st.name] = round(st.elapsed_s, 2)
    rec["timing"] = timing
    rec["total_elapsed_s"] = round(sum(s.elapsed_s for s in results), 2)
    rec["stages"] = [
        {"name": s.name, "ok": s.ok, "elapsed_s": round(s.elapsed_s, 2), "error": s.error} for s in results
    ]
