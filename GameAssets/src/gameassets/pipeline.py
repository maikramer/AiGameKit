"""Pipeline orchestration: argv builders, post-processing, e master DAG.

Contém:

* Os builders de argv e helpers de subprocesso usados por ``batch``,
  ``resume`` e ``cli`` (ex.: ``_text3d_argv``, ``_paint3d_texture_argv``,
  ``_rigging3d_pipeline_argv``, ``_animator3d_game_pack_argv``).
* O orquestrador master pipeline (``run_master_pipeline`` /
  ``resume_master_pipeline``) com a sequência:

      text3d topology-fix → bake-master → lod → collision
      rigging3d pipeline → merge (LODs)
      animator3d game-pack
      gamedev-lab check glb (validate)
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
    _clean_existing,
    _clean_path,
    _intermediate_dir,
    _lod_animated_path,
    _lod_path,
    _lod_rigged_path,
    _painted_existing,
    _painted_path,
    _path_for_log,
    _rigged_hi_existing,
    _rigged_hi_path,
    _shape_existing,
    _shape_path,
    _shell_path,
    archive_leftover_lod_rigged,
    move_to_intermediate,
    publish_rigged_animated_alias,
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
    """Espera-se: {mesh_dir}/{stem}_collision.glb."""
    stem = mesh_path.stem
    for sfx in ("_painted", "_shape", "_rigged_animated", "_rigged", "_segmented"):
        if stem.endswith(sfx):
            stem = stem[: -len(sfx)]
            break
    return mesh_path.parent / f"{stem}_collision{mesh_path.suffix}"


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
    """Define mesh_path e corre o master pipeline (LOD0 master, transfer-weights, validate).

    Devolve True se algum passo falhou. O master pipeline é agora o único
    caminho — o antigo fluxo linha-a-linha (text3d lod / rigging3d /
    animator3d) foi removido.
    """
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
    """Subcomando ``paint3d quick`` — cor sólida ou ruído Perlin/FBM (sem IA)."""
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

    console.print(f"[cyan]⏳ Remesh[/cyan] {row.id} ({current_faces:,} → ~{target:,} faces)")

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

    console.print(f"[cyan]⏳ Simplify (textured)[/cyan] {row.id} ({current_faces:,} → ~{target:,} faces)")

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
    """Simplify mesh via text3d remesh (delegated to text3d via subprocess)."""
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
) -> list[str]:
    """Shape-only argv for ``text3d generate`` (image → mesh, sem --texture).

    Paint is a separate step via ``paint3d texture``.
    """
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
    if category:
        args.extend(["--category", category])
    t3 = profile.text3d
    if not t3:
        return args

    if should_optimize_text3d(t3) and row is not None and row.category:
        fr = effective_face_ratio(profile, row)
        target = get_target_faces(row.category, face_ratio=fr)
        opts = optimize_text3d_for_target(target)
        args.extend(["--steps", str(opts.steps)])
        args.extend(["--octree-resolution", str(opts.octree_resolution)])
        args.extend(["--num-chunks", str(opts.num_chunks)])
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
    if t3.mc_level is not None:
        args.extend(["--mc-level", str(t3.mc_level)])
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
    if not mesh_final.is_file():
        rec["status"] = "error"
        rec["error"] = "rocks3d não produziu GLB"
        console.print(f"[red]rocks3d sem GLB[/red] {row.id}")
        return True
    rec["mesh_path"] = _path_for_log(mesh_final, manifest_dir)
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
    # Round 2 — observabilidade.
    total_elapsed_s: float = 0.0
    cumulative_vram_mb_peak: float = 0.0

    def recompute_totals(self) -> None:
        self.total_elapsed_s = round(sum(s.elapsed_s for s in self.stages), 2)


def _bin_or_none(name_env: str, name: str) -> str | None:
    try:
        return resolve_binary(name_env, name)
    except FileNotFoundError:
        return None


def ensure_clean_for_paint(
    mesh_final: Path,
    *,
    text3d_bin: str,
    profile: GameProfile,
    child_env: dict[str, str],
    manifest_dir: Path,
    force: bool = False,
) -> Path:
    """Garante ``_clean.glb`` (topology-fix) e devolve path para input do paint.

    Master DAG canónico: shape → topology-fix → paint. Batch/resume antigos
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
    t3 = profile.text3d
    if t3 is not None and t3.export_origin:
        topo_argv.extend(["--export-origin", t3.export_origin])
    r = run_cmd(topo_argv, extra_env=child_env, cwd=manifest_dir)
    if r.returncode != 0 or not clean_p.is_file():
        err = merge_subprocess_output(r, max_chars=400) or "topology-fix falhou"
        raise RuntimeError(err)
    return clean_p


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
    gameassets — sem isso, o dashboard só vê os events do orquestrador
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
    # Emite como ``progress`` (não ``ok``) — ``ok`` no dashboard sinaliza
    # conclusão do asset INTEIRO; usá-lo aqui faria a célula piscar OK entre
    # cada stage e contar duplicado no progresso global.
    _emit("run", 100, status="progress", seconds=round(dt, 2))
    return StageResult(name, True, dt, output=output)


def _glb_has_skin(path: Path) -> bool:
    """Verifica se um GLB tem skin real (``skins[]`` + node com ``skin``).

    JOINTS_0/WEIGHTS_0 sozinhos NÃO contam — transfer-weights partido exportava
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
    - ``_shape_path(mesh_final)`` existe (saída de Stage 1 — text3d generate
      com ``--no-topology-fix`` ou legacy generate).
    - ``_painted_path(mesh_final)`` existe (Stage 3 — paint3d texture sobre
      o GLB intermediário; tipicamente sobre o ``_clean.glb`` produzido aqui).

    Pós-condições em sucesso:
    - ``_lod_path(mesh_final, 0|1|2).glb`` em ``meshes/``.
    - ``_rigged_path(...)`` e ``_animated_path(...)`` quando ``with_rig`` e
      ``with_animate``.
    - Intermediários em ``_intermediate/``.
    """
    res = MasterPipelineResult(asset_id=row.id, ok=True)
    res.intermediates_dir = _intermediate_dir(mesh_final)

    # Round 2 — smart defaults para bake-normals.
    # Precedência: argumento explícito → profile.master_bake_normals → categoria.
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

    # Stage 0 — rocks3d generate (para assets da categoria "rock").
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
        # rocks3d não disponível — cai para o pipeline Text3D normal

    # Round 2: shape/painted podem estar em meshes/ OU em meshes/_intermediate/
    # (após uma run anterior). Resolve dinamicamente para permitir resume.
    shape_p = _shape_existing(mesh_final) or _shape_path(mesh_final)
    painted_p = _painted_existing(mesh_final) or _painted_path(mesh_final)
    clean_existing = _clean_existing(mesh_final)
    clean_p = clean_existing if clean_existing is not None else _clean_path(mesh_final)

    if not shape_p.is_file():
        res.ok = False
        res.stages.append(StageResult("preflight", False, 0.0, f"shape ausente: {shape_p}"))
        return res

    # Stage 2 — topology-fix (shape → clean). Skip se já temos um clean
    # válido (em meshes/ ou _intermediate/) — resume-friendly.
    clean_p.parent.mkdir(parents=True, exist_ok=True)
    if clean_existing is not None and clean_existing.is_file():
        res.stages.append(StageResult("topology-fix", True, 0.0, "skipped (clean existente)", clean_p))
    else:
        t3_prof = profile.text3d
        topo_argv = [text3d_bin, "topology-fix", str(shape_p), "-o", str(clean_p)]
        if t3_prof is not None and t3_prof.export_origin:
            topo_argv.extend(["--export-origin", t3_prof.export_origin])
        s = _run(
            "topology-fix",
            topo_argv,
            clean_p,
        )
        res.stages.append(s)
        if not s.ok:
            res.ok = False
            return res

    # Stage 4 — bake-master (painted → lod0 com tangents/KTX2/meshopt)
    if not painted_p.is_file():
        res.ok = False
        res.stages.append(StageResult("bake-master", False, 0.0, f"painted ausente: {painted_p}"))
        return res

    fr = effective_face_ratio(profile, row)
    target_faces = get_target_faces(row.category or "", face_ratio=fr) if row.category else 0
    if target_faces <= 0:
        target_faces = 8000
    lod0_p = _lod_path(mesh_final, 0)
    # Quando há rigging/anim downstream, o LOD0 vai ser re-importado por bpy
    # (rigging3d, animator3d). bpy não suporta EXT_meshopt_compression,
    # portanto saltamos a compressão em bake-master e re-aplicamos
    # gltf_transform_finish na promoção (Stage 9.5) sobre o output final.
    needs_bpy_downstream = (with_rig and rigging3d_bin is not None) or (with_animate and animator3d_bin is not None)

    # Resume: lod0 com paint (ou cópia do bake arquivado) → skip bake caro.
    # lod0 animado branco (sem baseColorTexture) NÃO conta — precisa restore/bake.
    from .paths import _base_stem as _bs_bake
    from .paths import _intermediate_dir as _inter_bake_dir

    _bake_base = _bs_bake(mesh_final.stem)
    _painted_lod0_arch = _inter_bake_dir(mesh_final) / f"{_bake_base}_lod0_painted{mesh_final.suffix}"
    if lod0_p.is_file() and _glb_has_materials(lod0_p):
        res.stages.append(StageResult("bake-master", True, 0.0, "skipped (lod0 com material)", lod0_p))
    elif _painted_lod0_arch.is_file() and _glb_has_materials(_painted_lod0_arch):
        import shutil as _sh_bake

        _sh_bake.copy2(_painted_lod0_arch, lod0_p)
        res.stages.append(StageResult("bake-master", True, 0.0, f"restored from {_painted_lod0_arch.name}", lod0_p))
    else:
        bake_argv = [
            text3d_bin,
            "bake-master",
            str(painted_p),
            "-o",
            str(lod0_p),
            "--target-faces",
            str(target_faces),
            "--high-poly",
            str(clean_p),
        ]
        if bake_normals:
            bake_argv.append("--bake-normals")
        if needs_bpy_downstream:
            bake_argv.extend(["--no-meshopt", "--no-ktx2"])
        s = _run("bake-master", bake_argv, lod0_p)
        res.stages.append(s)
        if not s.ok:
            res.ok = False
            return res
    res.lod0_path = lod0_p

    # Stage 5 — LOD1/LOD2 a partir do LOD0
    lod1_p = _lod_path(mesh_final, 1)
    lod2_p = _lod_path(mesh_final, 2)
    if with_lod:
        if lod1_p.is_file() and lod2_p.is_file() and _glb_has_materials(lod1_p) and _glb_has_materials(lod2_p):
            res.stages.append(StageResult("lod", True, 0.0, "skipped (lod1/lod2 com material)", lod1_p))
        else:
            # text3d lod com painted-mesh=lod0 dá-nos rácio half/quarter
            lod_target_lod1 = max(target_faces // 2, 100)
            lod_target_lod2 = max(target_faces // 4, 50)
            lod_argv = [
                text3d_bin,
                "lod",
                str(lod0_p),
                "-o",
                str(mesh_final.parent),
                "--basename",
                mesh_final.stem.replace("_lod0", "").replace("_painted", "").replace("_shape", ""),
                "--painted-mesh",
                str(lod0_p),
                "--target-faces",
                str(target_faces),
                "--min-faces-lod1",
                str(lod_target_lod1),
                "--min-faces-lod2",
                str(lod_target_lod2),
            ]
            # Igual a bake-master: salta finish quando bpy precisa importar
            # depois (rigging/animação por LOD). Re-comprimimos na promoção.
            if needs_bpy_downstream:
                lod_argv.append("--no-finish")
            s = _run("lod", lod_argv)
            res.stages.append(s)

    # Stage 6 — collision a partir do LOD0
    if with_collision:
        # Usa o stem-base (strip _shape/_painted/_lod0) para evitar nomes
        # como ``goblin_painted_collision.glb`` quando mesh_final aponta
        # para ``goblin_painted.glb``.
        from .paths import _base_stem as _bs

        coll_p = mesh_final.with_name(f"{_bs(mesh_final.stem)}_collision{mesh_final.suffix}")
        if coll_p.is_file():
            res.stages.append(StageResult("collision", True, 0.0, "skipped (collision existente)", coll_p))
        else:
            coll_argv = [
                text3d_bin,
                "collision",
                str(lod0_p),
                "-o",
                str(coll_p),
            ]
            s = _run("collision", coll_argv, coll_p)
            res.stages.append(s)
            # Round 2 — finalizar collision: dedup+prune (sem KTX2/meshopt/tangents).
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

    # Stage 7 — rigging3d pipeline sobre _clean.glb. Skip se já temos um
    # rigged_hi válido (em meshes/ ou _intermediate/) — resume-friendly.
    rigged_hi_existing = _rigged_hi_existing(mesh_final)
    rigged_hi_p = rigged_hi_existing if rigged_hi_existing is not None else _rigged_hi_path(mesh_final)
    if with_rig and rigging3d_bin:
        if rigged_hi_existing is not None and rigged_hi_existing.is_file():
            res.stages.append(StageResult("rigging3d-hi", True, 0.0, "skipped (rigged_hi existente)", rigged_hi_p))
        else:
            rig_argv = _rigging3d_pipeline_argv(
                rigging3d_bin,
                clean_p,
                rigged_hi_p,
                seed=_seed_for_row(profile, row.id),
                rig_profile=profile.rigging3d,
                gpu_ids=gpu_ids,
                hw_auto=profile.hw_auto,
                quality=profile.generation,
            )
            s = _run("rigging3d-hi", rig_argv, rigged_hi_p)
            res.stages.append(s)
            if not s.ok:
                with_rig = False  # bloqueia stages dependentes mas não aborta o asset

    # Stage 8 — Rigging por LOD via ``rigging3d transfer-weights``.
    #
    # Re-usa o skeleton+skin do ``_rigged_hi.glb`` e transfere os weights
    # para cada LOD low-poly via KDTree (ver ``transfer_weights.py``),
    # produzindo GLB com Armature real (re-importável por bpy → animator3d).
    # Não corre o modelo de inferência de novo (sem GPU).
    #
    # Se lod0 foi promovido a animated SEM paint (bug antigo), restaura o
    # bake-master arquivado em ``_intermediate/*_lodN_painted.glb`` antes
    # do transfer — senão o target é mesh branca e o paint nunca volta.
    from .paths import _base_stem as _bs_restore

    _base_restore = _bs_restore(mesh_final.stem)
    _inter_restore = _intermediate_dir(mesh_final)
    for _lvl, _lod in enumerate((lod0_p, lod1_p, lod2_p)):
        if not _lod.is_file():
            continue
        if _glb_has_materials(_lod):
            continue
        _painted_lod = _inter_restore / f"{_base_restore}_lod{_lvl}_painted{mesh_final.suffix}"
        if _painted_lod.is_file() and _glb_has_materials(_painted_lod):
            import shutil as _sh_restore

            _sh_restore.copy2(_painted_lod, _lod)
            log.warning(
                "master: lod%d sem paint — restaurado de %s antes do transfer",
                _lvl,
                _painted_lod.name,
            )

    rigged_lods: list[Path] = []
    if with_rig and rigging3d_bin and rigged_hi_p.is_file():
        targets: list[Path] = [lod0_p]
        if lod1_p.is_file():
            targets.append(lod1_p)
        if lod2_p.is_file():
            targets.append(lod2_p)
        for i, tgt in enumerate(targets):
            out = _lod_rigged_path(mesh_final, i)
            merge_argv = [
                rigging3d_bin,
                "transfer-weights",
                "--source",
                str(rigged_hi_p),
                "--target",
                str(tgt),
                "--output",
                str(out),
            ]
            s = _run(f"rigging3d-merge-lod{i}", merge_argv, out)
            res.stages.append(s)
            if s.ok and out.is_file():
                # Valida que o output tem skin (o exportador GLTF do Blender
                # às vezes não emite skin para outputs de transfer-weights).
                has_skin = _glb_has_skin(out)
                if has_skin:
                    rigged_lods.append(out)
                else:
                    log.warning("master: rigged-lod%d sem skin (transfer-weights); a usar rigged_hi", i)

    # Fallback: se nenhum LOD rigged válido, NÃO usar rigged_hi cego —
    # rigged_hi vem do _clean (sem paint) e produziria LOD0 branco.
    # Só fallback se o painted também não tiver material (asset sem paint).
    if with_rig and with_animate and not rigged_lods and rigged_hi_p.is_file():
        painted_has_mat = _glb_has_materials(painted_p) if painted_p.is_file() else False
        if painted_has_mat:
            log.error(
                "master: transfer-weights sem skin válido mas painted TEM material — "
                "a recusar fallback rigged_hi (evita LOD0 branco sem paint). "
                "Corrigir transfer-weights e re-correr resume."
            )
            res.stages.append(
                StageResult(
                    "rigging3d-fallback",
                    False,
                    0.0,
                    "recusado: painted tem material; transfer-weights tem de emitir skins[]",
                    rigged_hi_p,
                )
            )
            with_animate = False
        else:
            rigged_lods = [rigged_hi_p]
            res.stages.append(
                StageResult(
                    "rigging3d-fallback",
                    True,
                    0.0,
                    "usando rigged_hi (sem painted/material)",
                    rigged_hi_p,
                )
            )

    # Stage 9 — animate cada LOD rigged.
    # Animation config: per-row ``animate:`` sub-dict overrides global ``animator3d:`` profile.
    animated_lods: list[Path] = []
    anim_prof = profile.animator3d or Animator3DProfile()
    # Resolve effective animation settings (row overrides → profile defaults → category fallback).
    eff_preset = (row.animate_preset or anim_prof.preset or "humanoid").strip().lower()
    # Fallback por categoria só quando nem a row nem o profile definem preset.
    # ``creature`` → humanoid (mocap); não-humanoides reais exigem animate.preset explícito.
    if not row.animate_preset and not (profile.animator3d and profile.animator3d.preset):
        eff_preset = animator_preset_for_category(row.category)
    eff_clips = row.animate_clips or anim_prof.clips
    eff_procedural = row.animate_procedural if row.animate_procedural is not None else anim_prof.procedural
    eff_force_preset = row.animate_force_preset if row.animate_force_preset is not None else anim_prof.force_preset
    if with_rig and with_animate and animator3d_bin and rigged_lods:
        for rg in rigged_lods:
            try:
                level_str = rg.stem.split("_lod")[1].split("_")[0]
                lvl = int(level_str)
            except (IndexError, ValueError):
                lvl = 0
            anim_p = _lod_animated_path(mesh_final, lvl)
            an_argv = _animator3d_game_pack_argv(
                animator3d_bin,
                rg,
                anim_p,
                preset=eff_preset,
                clips=eff_clips,
                procedural=eff_procedural,
                force_preset=eff_force_preset,
            )
            s = _run(f"animate-lod{lvl}", an_argv, anim_p)
            res.stages.append(s)
            if s.ok and anim_p.is_file():
                # Gate: animate NÃO pode perder o paint do input rigged.
                if _glb_has_materials(rg) and not _glb_has_materials(anim_p):
                    log.error(
                        "master: animate-lod%d perdeu materiais/texturas (%s → %s)",
                        lvl,
                        rg.name,
                        anim_p.name,
                    )
                    res.stages.append(
                        StageResult(
                            f"animate-lod{lvl}-paint-check",
                            False,
                            0.0,
                            "output animado sem baseColorTexture",
                            anim_p,
                        )
                    )
                    continue
                # Round 2: finish em _animated.glb (mantém skin/animation tracks).
                # Sem UASTC/KTX2 (requer CLI ktx externo) — usa só dedup+prune+meshopt.
                try:
                    from text3d.utils.gltf_finish import gltf_transform_finish

                    finish_res = gltf_transform_finish(anim_p, anim_p, apply_uastc=False, apply_meshopt=True)
                    if not anim_p.is_file():
                        log.error("master: finish animated apagou %s — a re-gerar sem finish", anim_p)
                except Exception as exc:
                    log.warning("master: finish animated falhou em %s: %s", anim_p, exc)
                if anim_p.is_file():
                    animated_lods.append(anim_p)

    # Stage 9.5 — Promoção: o output do estágio mais alto vira lodN.glb.
    # Semântica: lod0.glb é SEMPRE o asset pronto-pra-jogo. Se houve animate,
    # lod0=animated; se houve só rig, lod0=rigged; senão fica o bake-master.
    # Versões "intermediárias" (bake-master sem rig, ou rigged se animado
    # promovido) movem-se para _intermediate/ para debug, evitando ficheiros
    # redundantes em meshes/.
    promotion_kind = "none"
    if animated_lods:
        promotion_kind = "animated"
        winners = animated_lods
    elif rigged_lods:
        promotion_kind = "rigged"
        winners = rigged_lods
    else:
        winners = []

    import shutil as _shutil

    if winners:
        log.info("master: promovendo %s outputs como lod0/1/2 (%s)", len(winners), promotion_kind)
        promoted_levels: set[int] = set()
        for w in winners:
            try:
                level_str = w.stem.rsplit("_lod", 1)[1].split("_")[0]
                level = int(level_str)
            except (IndexError, ValueError):
                continue
            target = _lod_path(mesh_final, level)
            # Move bake-master output para _intermediate/_lodN_painted.glb (debug).
            if target.is_file() and target.resolve() != w.resolve():
                base = mesh_final.stem
                from .paths import _base_stem as _bs

                base = _bs(base)
                debug = _intermediate_dir(mesh_final) / f"{base}_lod{level}_painted{mesh_final.suffix}"
                debug.parent.mkdir(parents=True, exist_ok=True)
                try:
                    _shutil.move(str(target), str(debug))
                except OSError as exc:
                    log.warning("master: move bake-master→intermediate falhou: %s", exc)
            try:
                _shutil.move(str(w), str(target))
                promoted_levels.add(level)
            except OSError as exc:
                log.warning("master: promoção %s→%s falhou: %s", w, target, exc)
                continue
            # Garante que o output final está totalmente optimizado
            # (KTX2+meshopt+tangents+dedup+prune). Bake-master e
            # rigging/animação correm com finish minimal quando há bpy
            # downstream — aplicamos a finalização pesada aqui no fim.
            try:
                from text3d.utils.gltf_finish import gltf_transform_finish

                gltf_transform_finish(target, target)
            except Exception as exc:
                log.warning("master: finish promoted lod%d falhou: %s", level, exc)

        # Se animated promovido, arquivar _lod*_rigged e publicar alias de runtime.
        if promotion_kind == "animated":
            for r in rigged_lods:
                if r.is_file():
                    try:
                        move_to_intermediate(r, mesh_final)
                    except OSError as exc:
                        log.warning("master: move rigged→intermediate falhou: %s", exc)
            # Catch leftovers from resume/re-rig parcial (podem existir sem skin).
            archived = archive_leftover_lod_rigged(mesh_final)
            if archived:
                log.info("master: arquivados %d lod*_rigged órfãos → _intermediate/", len(archived))
            # Alias estável para o jogo: id_rigged_animated.glb ← id_lod0.glb
            lod0_promoted = _lod_path(mesh_final, 0)
            alias = publish_rigged_animated_alias(mesh_final, lod0_promoted)
            if alias is not None:
                log.info("master: alias runtime %s ← %s", alias.name, lod0_promoted.name)
                res.stages.append(StageResult("publish-rigged-animated-alias", True, 0.0, alias.name, alias))

    # Stage 10 — validação. LOD0 é gate; LOD1/2 são warnings.
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

    img_final = mesh_final.with_suffix(".png")  # heurística — caller tipicamente fornece via row
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
    log.info("resume-master: state=%s — retomando pipeline para %s", state, row.id)
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
