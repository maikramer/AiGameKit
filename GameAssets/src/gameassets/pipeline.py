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
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console

from .categories import (
    animator_preset_for_category,
    category_wants_bake_normals,
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
    _canonical_mesh_final,
    _clean_existing,
    _clean_path,
    _collision_path,
    _glb_is_promoted_animated,
    _glb_is_promoted_rigged,
    _intermediate_dir,
    _lod_path,
    _painted_existing,
    _painted_path,
    _path_for_log,
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
    from gamedev_shared.subprocess_utils import run_cmd_streaming as _run_cmd_streaming
except ImportError:  # pragma: no cover
    _run_cmd_streaming = None  # type: ignore[assignment]

console = Console()
log = logging.getLogger(__name__)


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
) -> list[str]:
    args = [rigging3d_bin]
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    if not hw_auto:
        args.append("--no-hw-auto")
    args.append("pipeline")
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
    return args


def _lod_output_paths(mesh_path: Path, basename: str, num_levels: int = 3) -> list[Path]:
    """Espera-se: {mesh_dir}/{basename}_lod0.glb … _lod{N-1}.glb."""
    d = mesh_path.parent
    return [d / f"{basename}_lod{i}.glb" for i in range(num_levels)]


def _collision_output_path(mesh_path: Path) -> Path:
    """Espera-se: {meshes_dir}/{id}_collision.glb (nunca em ``_intermediate/``)."""
    return _collision_path(mesh_path)


def wants_split_at_height(profile: GameProfile, row: ManifestRow) -> bool:
    """True quando o asset deve correr ``text3d split-at-height``.

    Default on para ``category=tree``; ``text3d.split_at_height: false`` desliga.
    """
    t3 = profile.text3d
    if t3 is not None and t3.split_at_height is False:
        return False
    if t3 is not None and t3.split_at_height is True:
        return True
    cat = (row.category or "").strip().lower()
    return cat == "tree"


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
    import shutil

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
    mres = run_master_pipeline(
        profile,
        row,
        mesh_final,
        manifest_dir=manifest_dir,
        child_env=child_env,
        with_lod=with_lod,
        with_collision=with_collision,
        with_rig=effective_with_rig,
        with_animate=effective_with_animate,
        with_validate=with_validate,
        bake_normals=bake_normals,
        on_progress_line=on_progress_line,
        gpu_ids=gpu_ids,
    )
    aggregate_master_results(mres.stages, rec)
    if mres.lod0_path and mres.lod0_path.is_file():
        rec["lod0_path"] = _path_for_log(mres.lod0_path, manifest_dir)
    if mres.intermediates_dir is not None:
        rec["intermediates_dir"] = _path_for_log(mres.intermediates_dir, manifest_dir)
    if not mres.ok:
        errors = [s.error for s in mres.stages if not s.ok and s.error]
        rec["status"] = "error"
        rec["error"] = "; ".join(errors[:3]) or "master pipeline falhou"
        console.print(f"[red]master pipeline falhou[/red] {row.id}: {rec['error'][:200]}")
        return True
    return False


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
        return args
    if p3.max_views is not None:
        args.extend(["--max-views", str(p3.max_views)])
    if p3.view_resolution is not None:
        args.extend(["--view-resolution", str(p3.view_resolution)])
    if p3.render_size is not None:
        args.extend(["--render-size", str(p3.render_size)])
    if p3.texture_size is not None:
        args.extend(["--texture-size", str(p3.texture_size)])
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
    effective_style = (p3.style or "hunyuan").strip().lower()
    if (
        profile.paint3d is not None
        and row
        and row.category
        and p3.max_views is None
        and p3.view_resolution is None
        and p3.texture_size is None
    ):
        fr = effective_face_ratio(profile, row)
        target = get_target_faces(row.category, face_ratio=fr)
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
    target = get_target_faces(row.category)
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
    fr = effective_face_ratio(profile, row) if profile else 1.0
    target = get_target_faces(row.category, face_ratio=fr)
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
    fr = effective_face_ratio(profile, row) if profile else 1.0
    target = get_target_faces(row.category, face_ratio=fr)
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
        fr = effective_face_ratio(profile, row)
        target = get_target_faces(row.category, face_ratio=fr)
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
    Omitido → auto Text3D (N por category: terrain/rock=3× default 0.125).
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

    Master DAG canónico: shape -> topology-fix -> paint. Batch/resume antigos
    pintavam ``_shape`` cru (paredes duplas / cascas internas). Com isto o
    paint corre sobre mesh já limpa (incl. ``remove_internal_shell_faces``).
    """
    clean_existing = _clean_existing(mesh_final)
    if clean_existing is not None and clean_existing.is_file() and not force:
        return clean_existing

    shape_p = _shape_existing(mesh_final) or _shape_path(mesh_final)
    if not shape_p.is_file():
        raise FileNotFoundError(f"shape ausente para topology-fix: {shape_p}")

    clean_p = _clean_path(mesh_final)
    clean_p.parent.mkdir(parents=True, exist_ok=True)
    topo_argv = [text3d_bin, "topology-fix", str(shape_p), "-o", str(clean_p)]
    topo_argv.extend(_topology_fix_extra_argv(profile, row, manifest_dir=manifest_dir))
    r = run_cmd(topo_argv, extra_env=child_env, cwd=manifest_dir)
    if r.returncode != 0 or not clean_p.is_file():
        err = merge_subprocess_output(r, max_chars=400) or "topology-fix falhou"
        raise RuntimeError(err)
    return clean_p


def _resolve_paint_texture_size(profile: GameProfile) -> int:
    """Atlas size efectivo para orçamento ``_to_paint`` (default medium=2048)."""
    p3 = profile.paint3d
    if p3 is not None and p3.texture_size is not None and int(p3.texture_size) > 0:
        return int(p3.texture_size)
    return 2048


def _resolve_to_paint_faces(profile: GameProfile) -> int:
    """Faces alvo do ``_to_paint``: override profile ou fórmula ~ texture_size."""
    p3 = profile.paint3d
    if p3 is not None and p3.to_paint_faces is not None and int(p3.to_paint_faces) >= 4:
        return int(p3.to_paint_faces)
    from gamedev_shared.paint_budget import paint_target_faces

    return paint_target_faces(_resolve_paint_texture_size(profile))


def ensure_to_paint_for_paint(
    mesh_final: Path,
    *,
    text3d_bin: str,
    profile: GameProfile,
    child_env: dict[str, str],
    manifest_dir: Path,
    force: bool = False,
    row: ManifestRow | None = None,
) -> Path:
    """Garante ``_clean`` + ``_to_paint`` (remesh orçado) e devolve input do paint.

    DAG: shape -> topology-fix (``_clean``, HI) -> simplify (``_to_paint``) -> paint.
    O HI ``_clean`` continua a alimentar bake-master ``--high-poly``; o paint
    só vê a malha orçada ao atlas (evita unwrap/raster em 1-2M faces).

    Se ``_clean`` já está ≤ ~110% do alvo, devolve ``_clean`` (sem ficheiro extra).
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
    target = _resolve_to_paint_faces(profile)
    tex_size = _resolve_paint_texture_size(profile)
    current = _count_faces_glb(clean_p)
    if current < 0:
        log.warning("to_paint: não consegui contar faces de %s - paint no clean", clean_p)
        return clean_p
    if current <= int(target * 1.1):
        log.info(
            "to_paint: skip remesh %s faces=%s ≤ alvo %s (tex=%s)",
            clean_p.name,
            f"{current:,}",
            f"{target:,}",
            tex_size,
        )
        return clean_p

    to_paint_p = _to_paint_path(mesh_final)
    existing = _to_paint_existing(mesh_final)
    if (
        not force
        and existing is not None
        and existing.is_file()
        and existing.stat().st_mtime >= clean_p.stat().st_mtime
    ):
        tp_faces = _count_faces_glb(existing)
        if 0 < tp_faces <= int(target * 1.25):
            log.info(
                "to_paint: reuso %s faces=%s (alvo %s)",
                existing.name,
                f"{tp_faces:,}",
                f"{target:,}",
            )
            return existing

    to_paint_p.parent.mkdir(parents=True, exist_ok=True)
    row_id = row.id if row is not None else clean_p.stem
    console.print(f"[cyan]⏳ to_paint[/cyan] {row_id} ({current:,} -> ~{target:,} faces, atlas={tex_size})")
    argv = [
        text3d_bin,
        "simplify",
        str(clean_p),
        "-o",
        str(to_paint_p),
        "--target-faces",
        str(target),
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

    # Re-topology-fix no to_paint: o simplify (decimate) parte o mesh em
    # ilhas (1→33+ comps). Um segundo topology-fix (morph default auto, SEM
    # o override do profile para não amplificar) fecha as ilhas novamente
    # (33→1 comp, 0 boundary) antes do paint/split. Crítico para que o
    # split-at-height produza um corte limpo sem fendas Hunyuan.
    refix_p = to_paint_p.with_name(to_paint_p.stem + "_refixed" + to_paint_p.suffix)
    refix_argv = [text3d_bin, "topology-fix", str(to_paint_p), "-o", str(refix_p)]
    # Morph default (auto) — SEM _topology_fix_extra_argv (não amplificar o override do user).
    refix_argv.extend(["--engine", "arrays"])
    if row is not None and row.category:
        refix_argv.extend(["--category", str(row.category)])
    r2 = run_cmd(refix_argv, extra_env=child_env, cwd=manifest_dir)
    if r2.returncode == 0 and refix_p.is_file():
        refix_p.replace(to_paint_p)
        refix_faces = _count_faces_glb(to_paint_p)
        console.print(
            f"[green]✓ to_paint re-fix[/green] {row_id}"
            + (f" ({refix_faces:,} faces, ilhas fechadas)" if refix_faces > 0 else "")
        )
    else:
        log.warning("to_paint re-fix falhou para %s (continua com simplify)", row_id)

    return to_paint_p


def _rules_dir() -> Path:
    return Path(__file__).resolve().parent / "data" / "rules"


def _run_check_glb(
    glb: Path,
    rules: Path,
    *,
    category: str | None,
    env: dict[str, str],
    cwd: Path,
) -> StageResult:
    import time as _time

    bin_ = _bin_or_none("GAMEDEVLAB_BIN", "gamedev-lab")
    if not bin_:
        return StageResult("validate", False, 0.0, "gamedev-lab não encontrado no PATH")
    argv = [bin_, "check", "glb", str(glb), str(rules)]
    if category:
        argv.extend(["--category", category])
    argv.extend(["--no-bpy-inspect"])  # rules estão preparadas para glb_meta
    t0 = _time.perf_counter()
    r = run_cmd(argv, extra_env=env, cwd=cwd)
    dt = _time.perf_counter() - t0
    if r.returncode != 0:
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
    ``profile_enabled`` (controlado por ``GAMEDEV_PROFILE`` no child_env).
    Emite ``emit_progress`` no início e fim para visibilidade no dashboard.

    ``on_progress_line``: callback alimentado linha-a-linha com stdout do
    subprocesso. Permite encaminhar ``emit_progress`` events emitidos pelas
    ferramentas (text3d, rigging3d, animator3d) para o dashboard do
    gameassets - sem isso, o dashboard só vê os events do orquestrador
    (start/end por stage) e parece "congelar" após paint3d.
    """
    import time as _time

    from gamedev_shared.profiler.session import ProfilerSession
    from gamedev_shared.progress import emit_progress

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
        return StageResult(name, False, dt, f"ProfilerSession: {exc}", output)

    dt = _time.perf_counter() - t0
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=400) or f"{name} falhou (rc={r.returncode})"
        _emit("run", 100, status="error")
        return StageResult(name, False, dt, err, output)
    if output is not None and not output.is_file():
        _emit("run", 100, status="error")
        return StageResult(name, False, dt, f"{name}: output não foi criado", output)
    # Emite como ``progress`` (não ``ok``) - ``ok`` no dashboard sinaliza
    # conclusão do asset INTEIRO; usá-lo aqui faria a célula piscar OK entre
    # cada stage e contar duplicado no progresso global.
    _emit("run", 100, status="progress", seconds=round(dt, 2))
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

        gltf_transform_finish(p, p)
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
) -> None:
    """Caminho estático (sem rig): ladder LOD0/1/2 via ``text3d lod``.

    LOD0 = 1.2x ``target_faces`` (decimado do painted com preservação de UV),
    LOD1 = target/2, LOD2 = target/4. ``--finish-lod0`` aplica tangents +
    KTX2 + meshopt ao LOD0 (alinha com regras lod0.yaml). Se um lod0 promovido
    (rig/animated de outra run) existir, nada e clobberado. O finish corre
    dentro do proprio ``text3d lod`` — depois desta fase nenhum stage le os
    entregaveis via bpy (collision usa o painted).
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
    lod0_matches_painted = (
        lod0_p.is_file()
        and not lod0_is_promoted
        and _glb_has_materials(lod0_p)
        and painted_faces > 0
        and lod0_faces >= int(painted_faces * 0.99)
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

    # Stage 5 - Ladder completa (LOD0/1/2) a partir do PAINTED.
    # text3d lod com --target-faces decima LOD0=1.2x target, LOD1=target/2,
    # LOD2=target/4. --finish-lod0 aplica tangents+KTX2+meshopt ao LOD0.
    # Skip só se lod0 promoted OU ladder já válida.
    if with_lod:
        lod1_faces = _count_faces_glb(lod1_p) if lod1_p.is_file() else -1
        lod2_faces = _count_faces_glb(lod2_p) if lod2_p.is_file() else -1
        lod_ladder_ok = (
            lod1_p.is_file()
            and lod2_p.is_file()
            and _glb_has_materials(lod1_p)
            and _glb_has_materials(lod2_p)
            and painted_faces > 0
            and lod1_faces >= int(painted_faces * 0.25)
            and lod2_faces >= int(painted_faces * 0.08)
            and lod2_faces < lod1_faces
        )
        if lod0_is_promoted:
            res.stages.append(StageResult("lod", True, 0.0, "skipped (lod0 already promoted)", lod1_p))
        elif lod_ladder_ok and lod0_matches_painted:
            res.stages.append(StageResult("lod", True, 0.0, "skipped (lod1/lod2 ok)", lod1_p))
        else:
            # LOD0 budget = 1.2× category target_faces (alinha com lod0.yaml
            # max_per_category). Piso 8 para evitar degeneração em categorias
            # pequenas (ex.: effects target=2000).
            lod0_target = max(8, int(target_faces * 1.2))
            lod_min1 = max(target_faces // 2, 500)
            lod_min2 = max(target_faces // 4, 150)
            lod_argv = [
                text3d_bin,
                "lod",
                str(painted_p),
                "-o",
                str(mesh_final.parent),
                "--basename",
                base,
                "--painted-mesh",
                str(painted_p),
                "--target-faces",
                str(lod0_target),
                "--finish-lod0",
                "--lod1-ratio",
                "0.40",
                "--lod2-ratio",
                "0.15",
                "--min-faces-lod1",
                str(lod_min1),
                "--min-faces-lod2",
                str(lod_min2),
            ]
            s = run_stage("lod", lod_argv, None)
            res.stages.append(s)


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
    res = MasterPipelineResult(asset_id=row.id, ok=True)
    res.intermediates_dir = _intermediate_dir(mesh_final)

    # Round 2 - smart defaults para bake-normals.
    # Precedência: argumento explícito -> profile.master_bake_normals -> categoria.
    if bake_normals is None:
        bake_normals = bool(getattr(profile, "master_bake_normals", False)) or category_wants_bake_normals(
            row.category,
            overrides=getattr(profile, "master_bake_normals_categories", None),
        )

    profile_enabled = str(child_env.get("GAMEDEV_PROFILE", "")).strip() == "1"

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
    if clean_existing is not None and clean_existing.is_file():
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

    fr = effective_face_ratio(profile, row)
    # target_faces = orçamento base; LOD0 = 1.2×target, LOD1 = target/2, LOD2 = target/4.
    target_faces = get_target_faces(row.category or "", face_ratio=fr) if row.category else 0
    if target_faces <= 0:
        target_faces = 8000
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
        if lod0_promoted_anim or (not with_animate and lod0_promoted_rig):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (lod0 já promovido)", lod0_p))
        elif with_animate and _animated_ok(animated_p):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (animated existente)", animated_p))
        elif _rigged_ok(rigged_p):
            res.stages.append(StageResult("rigging3d", True, 0.0, "skipped (rigged existente)", rigged_p))
        else:
            rig_argv = _rigging3d_pipeline_argv(
                rigging3d_bin,
                painted_p,
                rigged_p,
                seed=_seed_for_row(profile, row.id),
                rig_profile=profile.rigging3d,
                gpu_ids=gpu_ids,
                hw_auto=profile.hw_auto,
                quality=profile.generation,
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
                an_argv = _animator3d_game_pack_argv(
                    animator3d_bin,
                    rigged_p,
                    animated_p,
                    preset=eff_preset,
                    clips=eff_clips,
                    procedural=eff_procedural,
                    force_preset=eff_force_preset,
                )
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
    if rig_source is not None:
        expect = _glb_is_promoted_animated if promotion_kind == "animated" else _glb_is_promoted_rigged
        ladder_ok = expect(lod0_p) and (
            not with_lod or (lod1_p.is_file() and lod2_p.is_file() and expect(lod1_p) and expect(lod2_p))
        )
        if ladder_ok:
            res.stages.append(StageResult("lod", True, 0.0, f"skipped (ladder {promotion_kind} ok)", lod0_p))
        elif with_lod:
            lod_min1 = max(target_faces // 2, 500)
            lod_min2 = max(target_faces // 4, 150)
            lod_argv = [
                text3d_bin,
                "lod",
                str(rig_source),
                "-o",
                str(mesh_final.parent),
                "--basename",
                _bake_base,
                "--lod1-ratio",
                "0.40",
                "--lod2-ratio",
                "0.15",
                "--min-faces-lod1",
                str(lod_min1),
                "--min-faces-lod2",
                str(lod_min2),
                "--no-meshopt",
            ]
            s = _run("lod", lod_argv)
            res.stages.append(s)
            # Finish (KTX2+meshopt+tangents) por ficheiro, DEPOIS da ladder
            # completa — nenhum stage posterior lê estes GLBs via bpy
            # (collision usa o painted). Rollback automático se o roundtrip
            # destruir skins/clips — entregável sem compressão > lod mudo.
            for lvl, p in ((0, lod0_p), (1, lod1_p), (2, lod2_p)):
                if p.is_file():
                    _finish_lod_with_rollback(p, lvl, expect, res)
        else:
            import shutil as _sh

            _sh.copy2(rig_source, lod0_p)
            _finish_lod_with_rollback(lod0_p, 0, expect, res)
            res.stages.append(StageResult("lod", True, 0.0, f"lod0={promotion_kind} copy (sem ladder)", lod0_p))
        res.lod0_path = lod0_p
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
        )

    # Stage 6 - collision a partir do PAINTED (geometria estática idêntica ao
    # lod0, sem armature nem meshopt — o builder não precisa de os desenredar).
    if with_collision:
        coll_p = _collision_path(mesh_final)
        if coll_p.is_file():
            res.stages.append(StageResult("collision", True, 0.0, "skipped (collision existente)", coll_p))
        else:
            coll_src = painted_p if painted_p.is_file() else lod0_p
            coll_argv = [
                text3d_bin,
                "collision",
                str(coll_src),
                "-o",
                str(coll_p),
            ]
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

    # Stage 6b - split-at-height (árvores): composição Stump+Top no LOD0.
    # Corre depois da collision para o hull nascer do painted/lod0 unsplit.
    if wants_split_at_height(profile, row):
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
        if promotion_kind == "animated":
            rule_for = {0: "animated.yaml", 1: "animated.yaml", 2: "animated.yaml"}
        elif promotion_kind == "rigged":
            rule_for = {0: "rigged.yaml", 1: "rigged.yaml", 2: "rigged.yaml"}
        else:
            rule_for = {0: "lod0.yaml", 1: "lod1.yaml", 2: "lod2.yaml"}

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
