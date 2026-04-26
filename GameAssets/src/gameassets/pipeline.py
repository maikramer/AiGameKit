"""Pipeline argv builders, post-processing functions, paint3d/text3d argv builders."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from rich.console import Console

from .categories import get_target_faces
from .helpers import (
    _row_wants_animate,
    _row_wants_parts,
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
from .paths import _animator3d_output_path, _path_for_log, _rigging3d_output_path, _shell_path
from .profile import Animator3DProfile, GameProfile, Paint3DProfile, Part3DProfile, Rigging3DProfile
from .runner import merge_subprocess_output, resolve_binary, run_cmd

console = Console()


def _resolve_animator3d_bin() -> str | None:
    try:
        return resolve_binary("ANIMATOR3D_BIN", "animator3d")
    except FileNotFoundError:
        return None


def _resolve_bpy_python() -> str | None:
    abin = _resolve_animator3d_bin()
    if not abin:
        return None
    bindir = Path(abin).parent
    for candidate in ("python", "python3"):
        p = bindir / candidate
        if p.is_file():
            return str(p)
    return None


def _bpy_simplify_script_path() -> Path | None:
    from . import bpy_simplify as _bs

    p = Path(_bs.__file__)
    return p if p.is_file() else None


def _rigging3d_pipeline_argv(
    rigging3d_bin: str,
    mesh_in: Path,
    mesh_out: Path,
    *,
    seed: int | None,
    rig_profile: Rigging3DProfile | None,
    gpu_ids: list[int] | None = None,
) -> list[str]:
    args = [rigging3d_bin]
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    args.append("pipeline")
    args.extend(["--input", str(mesh_in), "--output", str(mesh_out)])
    if seed is not None:
        args.extend(["--seed", str(seed)])
    if rig_profile:
        if rig_profile.root:
            args.extend(["--root", rig_profile.root])
        if rig_profile.python:
            args.extend(["--python", rig_profile.python])
    return args


def _rigging3d_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    rigging3d_bin: str | None,
    with_rig: bool,
    has_rigging_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> bool:
    """Corre ``rigging3d pipeline`` após Text3D (GLB base ou ``*_parts.glb`` se parts+rig). Devolve True se falhou."""
    if not with_rig or not _row_wants_rig(row, has_rigging_profile) or not rigging3d_bin:
        return False
    if not row.generate_3d:
        return False
    if not mesh_final.is_file():
        return False
    rg = profile.rigging3d
    sfx = rg.output_suffix if rg else "_rigged"
    rig_out = _rigging3d_output_path(mesh_final, sfx)
    seed = _seed_for_row(profile, row.id)
    argv = _rigging3d_pipeline_argv(
        rigging3d_bin,
        mesh_final,
        rig_out,
        seed=seed,
        rig_profile=rg,
        gpu_ids=gpu_ids,
    )
    if profile.text3d and profile.text3d.low_vram:
        argv.append("--low-vram")
    console.print(f"[cyan]⏳ Rigging[/cyan] {row.id} ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "rigging3d", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Rigging[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "rigging3d falhou"
        rec["status"] = "error"
        rec["error"] = err
        preview = merge_subprocess_output(r, max_chars=4000) or err
        console.print(f"[red]rigging3d falhou[/red] {row.id}: {preview}")
        return True
    if not rig_out.is_file():
        rec["status"] = "error"
        rec["error"] = "rigging3d não produziu GLB rigado"
        console.print(f"[red]rigging3d sem GLB[/red] {row.id}")
        return True
    rec["rig_mesh_path"] = _path_for_log(rig_out, manifest_dir)
    return False


def _animator3d_game_pack_argv(
    animator3d_bin: str,
    rig_out: Path,
    anim_out: Path,
    *,
    preset: str,
    gpu_ids: list[int] | None = None,
) -> list[str]:
    return [
        animator3d_bin,
        "game-pack",
        _shell_path(rig_out),
        _shell_path(anim_out),
        "--preset",
        preset,
    ]


def _animator3d_game_pack_failed(
    profile: GameProfile,
    row: ManifestRow,
    rigged_glb: Path,
    animated_glb: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    with_animate: bool,
    with_rig: bool,
    has_rigging_profile: bool = False,
    preset: str = "humanoid",
    gpu_ids: list[int] | None = None,
) -> bool:
    """Corre ``animator3d game-pack`` no GLB rigado. Devolve True se falhou."""
    if not with_animate or not _row_wants_animate(row, with_rig, has_rigging_profile):
        return False
    if not row.generate_3d:
        return False
    if not rigged_glb.is_file():
        return False
    abin = _resolve_animator3d_bin()
    if not abin:
        rec["status"] = "error"
        rec["error"] = "animator3d não encontrado (ANIMATOR3D_BIN ou PATH)"
        console.print(f"[red]animator3d em falta[/red] {row.id}")
        return True
    anim_prof = profile.animator3d or Animator3DProfile()
    preset_eff = (anim_prof.preset or preset).strip().lower()
    argv = _animator3d_game_pack_argv(abin, rigged_glb, animated_glb, preset=preset_eff, gpu_ids=gpu_ids)
    console.print(f"[cyan]⏳ Animation[/cyan] {row.id} (preset={preset_eff}) ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "animator3d", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Animation[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "animator3d game-pack falhou"
        rec["status"] = "error"
        rec["error"] = err
        preview = merge_subprocess_output(r, max_chars=4000) or err
        console.print(f"[red]animator3d game-pack falhou[/red] {row.id}: {preview}")
        return True
    if not animated_glb.is_file():
        rec["status"] = "error"
        rec["error"] = "animator3d não produziu GLB animado"
        console.print(f"[red]animator3d sem GLB animado[/red] {row.id}")
        return True
    rec["animated_mesh_path"] = _path_for_log(animated_glb, manifest_dir)
    return False


def _part3d_profile_effective(profile: GameProfile, row: ManifestRow | None = None) -> Part3DProfile:
    """Profile Part3D com possíveis overrides por linha do manifest."""
    base = profile.part3d or Part3DProfile()
    if row is None:
        return base
    steps = row.part3d_steps if row.part3d_steps is not None else base.steps
    octree = row.part3d_octree_resolution if row.part3d_octree_resolution is not None else base.octree_resolution
    seg_only = row.part3d_segment_only if row.part3d_segment_only is not None else base.segment_only
    return Part3DProfile(
        octree_resolution=octree,
        steps=steps,
        num_chunks=base.num_chunks,
        segment_only=seg_only,
        no_cpu_offload=base.no_cpu_offload,
        verbose=base.verbose,
        parts_suffix=base.parts_suffix,
        segmented_suffix=base.segmented_suffix,
        quantization=base.quantization,
        no_quantize_dit=base.no_quantize_dit,
        torch_compile=base.torch_compile,
        no_attention_slicing=base.no_attention_slicing,
        low_vram_mode=base.low_vram_mode,
    )


def _part3d_stem_suffix(raw: str | None, default: str) -> str:
    s = (raw or "").strip()
    if not s:
        s = default
    return s if s.startswith("_") else f"_{s}"


def _part3d_output_paths(mesh_final: Path, p3: Part3DProfile) -> tuple[Path, Path]:
    ps = _part3d_stem_suffix(p3.parts_suffix, "_parts")
    ss = _part3d_stem_suffix(p3.segmented_suffix, "_segmented")
    parts = mesh_final.with_name(f"{mesh_final.stem}{ps}.glb")
    segmented = mesh_final.with_name(f"{mesh_final.stem}{ss}.glb")
    return parts, segmented


def _part3d_decompose_argv(
    part3d_bin: str,
    mesh_in: Path,
    out_parts: Path,
    out_seg: Path,
    p3: Part3DProfile,
    seed: int | None,
    gpu_ids: list[int] | None = None,
) -> list[str]:
    args = [
        part3d_bin,
        "decompose",
        str(mesh_in),
        "-o",
        str(out_parts),
        "--output-segmented",
        str(out_seg),
    ]
    if p3.octree_resolution is not None:
        args.extend(["--octree-resolution", str(p3.octree_resolution)])
    if p3.steps is not None:
        args.extend(["--steps", str(p3.steps)])
    if p3.num_chunks is not None:
        args.extend(["--num-chunks", str(p3.num_chunks)])
    if seed is not None:
        args.extend(["--seed", str(seed)])
    if p3.no_cpu_offload:
        args.append("--no-cpu-offload")
    if p3.segment_only:
        args.append("--segment-only")
    if p3.verbose:
        args.append("-v")
    # --- Otimizações de VRAM ---
    if p3.quantization:
        args.extend(["--quantization", p3.quantization])
    if p3.no_quantize_dit:
        args.append("--no-quantize-dit")
    if p3.torch_compile:
        args.append("--torch-compile")
    if p3.no_attention_slicing:
        args.append("--no-attention-slicing")
    if p3.low_vram_mode:
        args.append("--low-vram-mode")
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    return args


def _lod_output_paths(mesh_path: Path, basename: str) -> list[Path]:
    """Espera-se: {mesh_dir}/{basename}_lod0.glb … _lod2.glb."""
    d = mesh_path.parent
    return [d / f"{basename}_lod{i}.glb" for i in range(3)]


def _lod_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    with_lod: bool,
    gpu_ids: list[int] | None = None,
) -> bool:
    """Corre ``text3d lod`` para gerar triplet LOD. Devolve True se falhou."""
    if not with_lod or not row.generate_lod or not row.generate_3d:
        return False
    if not mesh_final.is_file():
        return False
    from .profile import LODProfile

    lod_prof = profile.lod
    if lod_prof is None:
        lod_prof = LODProfile()
    text3d_bin = resolve_binary("TEXT3D_BIN", "text3d")
    basename = row.id.replace("/", "_")
    out_dir = mesh_final.parent
    argv = [
        text3d_bin,
        "lod",
        str(mesh_final),
        "-o",
        str(out_dir),
        "--basename",
        basename,
        "--lod1-ratio",
        str(lod_prof.lod1_ratio),
        "--lod2-ratio",
        str(lod_prof.lod2_ratio),
        "--min-faces-lod1",
        str(lod_prof.min_faces_lod1),
        "--min-faces-lod2",
        str(lod_prof.min_faces_lod2),
    ]
    if lod_prof.meshfix:
        argv.append("--meshfix")
    if gpu_ids:
        argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    console.print(f"[cyan]⏳ LOD[/cyan] {row.id} ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "lod", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ LOD[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "text3d lod falhou"
        rec["status"] = "error"
        rec["error"] = err
        console.print(f"[red]LOD falhou[/red] {row.id}: {err[:200]}")
        return True
    paths = _lod_output_paths(mesh_final, basename)
    rec["lod_paths"] = [_path_for_log(p, manifest_dir) for p in paths if p.is_file()]

    bpy_python = _resolve_bpy_python()
    bpy_script = _bpy_simplify_script_path()
    if bpy_python and bpy_script:
        for lod_path in paths:
            if not lod_path.is_file():
                continue
            tmp = lod_path.with_name(f"{lod_path.stem}_clean{lod_path.suffix}")
            argv = [bpy_python, str(bpy_script), str(lod_path), "-o", str(tmp), "--clean-only"]
            r_clean = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
            if r_clean.returncode == 0 and tmp.is_file():
                tmp.replace(lod_path)

    return False


def _collision_output_path(mesh_path: Path) -> Path:
    """Espera-se: {mesh_dir}/{stem}_collision.glb."""
    return mesh_path.parent / f"{mesh_path.stem}_collision.glb"


def _collision_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    with_collision: bool,
    gpu_ids: list[int] | None = None,
) -> bool:
    """Corre ``text3d collision`` para gerar mesh de colisão. Devolve True se falhou."""
    if not with_collision or not row.generate_collision or not row.generate_3d:
        return False
    if not mesh_final.is_file():
        return False
    from .profile import CollisionProfile

    coll_prof = profile.collision
    if coll_prof is None:
        coll_prof = CollisionProfile()
    text3d_bin = resolve_binary("TEXT3D_BIN", "text3d")
    coll_out = _collision_output_path(mesh_final)
    argv = [
        text3d_bin,
        "collision",
        str(mesh_final),
        "-o",
        str(coll_out),
        "--max-faces",
        str(coll_prof.max_faces),
    ]
    if not coll_prof.convex_hull:
        argv.append("--no-convex-hull")
    if gpu_ids:
        argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    console.print(f"[cyan]⏳ Collision[/cyan] {row.id} ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "collision", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Collision[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "text3d collision falhou"
        rec["status"] = "error"
        rec["error"] = err
        console.print(f"[red]Collision falhou[/red] {row.id}: {err[:200]}")
        return True
    rec["collision_path"] = _path_for_log(coll_out, manifest_dir)
    return False


def _part3d_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    part3d_bin: str | None,
    with_parts: bool,
    has_parts_profile: bool = False,
    gpu_ids: list[int] | None = None,
) -> bool:
    """Corre ``part3d decompose`` após GLB do Text3D. Devolve True se falhou."""
    if not with_parts or not _row_wants_parts(row, has_parts_profile) or not part3d_bin:
        return False
    if not row.generate_3d:
        return False
    if not mesh_final.is_file():
        return False
    p3 = _part3d_profile_effective(profile, row)  # ← Usa overrides por linha
    out_parts, out_seg = _part3d_output_paths(mesh_final, p3)
    seed = _seed_for_row(profile, f"{row.id}:part3d")
    argv = _part3d_decompose_argv(part3d_bin, mesh_final, out_parts, out_seg, p3, seed, gpu_ids=gpu_ids)
    console.print(f"[cyan]⏳ Part3D[/cyan] {row.id} ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "part3d", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Part3D[/green] {row.id} ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "part3d falhou"
        rec["status"] = "error"
        rec["error"] = err
        preview = merge_subprocess_output(r, max_chars=4000) or err
        console.print(f"[red]part3d falhou[/red] {row.id}: {preview}")
        return True
    if p3.segment_only:
        if not out_seg.is_file():
            rec["status"] = "error"
            rec["error"] = "part3d não produziu mesh segmentada"
            console.print(f"[red]part3d sem mesh segmentada[/red] {row.id}")
            return True
        rec["segmented_mesh_path"] = _path_for_log(out_seg, manifest_dir)
        return False
    if not out_parts.is_file():
        rec["status"] = "error"
        rec["error"] = "part3d não produziu GLB de partes"
        console.print(f"[red]part3d sem GLB de partes[/red] {row.id}")
        return True
    rec["parts_mesh_path"] = _path_for_log(out_parts, manifest_dir)
    if out_seg.is_file():
        rec["segmented_mesh_path"] = _path_for_log(out_seg, manifest_dir)
    return False


def _texture_project_pipeline_failed(
    animator3d_bin: str,
    mesh_final: Path,
    out_parts: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    gpu_ids: list[int] | None = None,
) -> bool:
    """Projeta textura do modelo original nas partes via ``animator3d texture-project``."""
    out_textured = out_parts.with_name(f"{out_parts.stem}_textured{out_parts.suffix}")
    if out_textured.is_file():
        rec["parts_textured_mesh_path"] = _path_for_log(out_textured, manifest_dir)
        return False
    argv = [animator3d_bin, "texture-project", str(mesh_final), str(out_parts), "-o", str(out_textured)]
    console.print("[cyan]⏳ Texture-project[/cyan] partes ...")
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    elapsed = time.perf_counter() - t0
    _timing_append(rec, "texture_project", elapsed)
    if r.returncode == 0:
        console.print(f"[green]✓ Texture-project[/green] ({elapsed:.1f}s)")
    if r.returncode != 0:
        err = merge_subprocess_output(r) or "texture-project falhou"
        console.print(f"[yellow]texture-project falhou[/yellow] {rec.get('id', '?')}: {err[:200]}")
        return True
    rec["parts_textured_mesh_path"] = _path_for_log(out_textured, manifest_dir)
    return False


def _post_text3d_mesh_extras(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    part3d_bin: str | None,
    with_parts: bool,
    rigging3d_bin: str | None,
    with_rig: bool,
    with_animate: bool,
    animator3d_bin: str | None = None,
    has_rigging_profile: bool = False,
    has_parts_profile: bool = False,
    gpu_ids: list[int] | None = None,
    with_lod: bool = False,
    with_collision: bool = False,
) -> bool:
    """Define mesh_path, part3d, rigging3d, animator3d. Devolve True se algum passo falhou."""
    rec["mesh_path"] = _path_for_log(mesh_final, manifest_dir)
    lod_fail = _lod_pipeline_failed(
        profile,
        row,
        mesh_final,
        rec,
        manifest_dir,
        child_env,
        with_lod=with_lod,
        gpu_ids=gpu_ids,
    )
    coll_fail = _collision_pipeline_failed(
        profile,
        row,
        mesh_final,
        rec,
        manifest_dir,
        child_env,
        with_collision=with_collision,
        gpu_ids=gpu_ids,
    )
    part3d_fail = _part3d_pipeline_failed(
        profile,
        row,
        mesh_final,
        rec,
        manifest_dir,
        child_env,
        part3d_bin,
        with_parts,
        has_parts_profile=has_parts_profile,
        gpu_ids=gpu_ids,
    )
    if (
        not part3d_fail
        and with_parts
        and _row_wants_parts(row, has_parts_profile)
        and animator3d_bin
        and mesh_final.is_file()
    ):
        p3 = _part3d_profile_effective(profile, row)
        out_parts, _out_seg = _part3d_output_paths(mesh_final, p3)
        if out_parts.is_file():
            tp_fail = _texture_project_pipeline_failed(
                animator3d_bin,
                mesh_final,
                out_parts,
                rec,
                manifest_dir,
                child_env,
                gpu_ids=gpu_ids,
            )
            if tp_fail:
                console.print(f"[yellow]texture-project falhou (não bloqueante)[/yellow] {row.id}")
    rig_mesh_in = mesh_final
    rec["rig_input_path"] = _path_for_log(rig_mesh_in, manifest_dir)
    rig_fail = _rigging3d_pipeline_failed(
        profile,
        row,
        rig_mesh_in,
        rec,
        manifest_dir,
        child_env,
        rigging3d_bin,
        with_rig,
        has_rigging_profile=has_rigging_profile,
        gpu_ids=gpu_ids,
    )
    if lod_fail or coll_fail or part3d_fail or rig_fail:
        return True
    rg = profile.rigging3d
    sfx = rg.output_suffix if rg else "_rigged"
    rig_out = _rigging3d_output_path(rig_mesh_in, sfx)
    anim_out = _animator3d_output_path(rig_out)
    return _animator3d_game_pack_failed(
        profile,
        row,
        rig_out,
        anim_out,
        rec,
        manifest_dir,
        child_env,
        with_animate,
        with_rig,
        has_rigging_profile=has_rigging_profile,
        gpu_ids=gpu_ids,
    )


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
    if p3.low_vram_mode:
        args.append("--low-vram-mode")
    if p3.smooth:
        args.append("--smooth")
    else:
        args.append("--no-smooth")
    if p3.smooth_passes is not None:
        args.extend(["--smooth-passes", str(p3.smooth_passes)])
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
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
    try:
        import trimesh

        m = trimesh.load(str(mesh_path), force="mesh")
        current_faces = m.faces.shape[0]
    except Exception:
        return False
    if current_faces <= target:
        return False
    if current_faces < target * 1.1:
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
    rec["remesh_ratio"] = round(target / m.faces.shape[0], 4)
    rec["remesh_faces_before"] = m.faces.shape[0]
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
    try:
        import trimesh

        m = trimesh.load(str(mesh_path), force="mesh")
        current_faces = m.faces.shape[0]
    except Exception:
        return False
    if current_faces <= target:
        return False
    if current_faces < target * 1.1:
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


def _bpy_simplify_to_target(
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
    if not row.category:
        return False
    fr = effective_face_ratio(profile, row) if profile else 1.0
    target = get_target_faces(row.category, face_ratio=fr)
    if target <= 0:
        return False
    try:
        import trimesh

        m = trimesh.load(str(mesh_path), force="mesh")
        current_faces = m.faces.shape[0]
    except Exception:
        return False
    if current_faces <= target:
        return False
    if current_faces < target * 1.1:
        return False

    bpy_python = _resolve_bpy_python()
    bpy_script = _bpy_simplify_script_path()

    if bpy_python and bpy_script:
        console.print(f"[cyan]⏳ Simplify (bpy)[/cyan] {row.id} ({current_faces:,} → ~{target:,} faces)")

        simplified = mesh_path.parent / f"{mesh_path.stem}_simplified{mesh_path.suffix}"
        argv = [bpy_python, str(bpy_script), str(mesh_path), "-o", str(simplified), "--target-faces", str(target)]
        r = run_cmd(argv, extra_env=child_env, cwd=cwd)
        if r.returncode == 0 and simplified.is_file():
            simplified.replace(mesh_path)
            rec["simplify_method"] = "bpy"
            rec["simplify_ratio"] = round(target / current_faces, 4)
            rec["simplify_faces_before"] = current_faces
            console.print(f"[green]✓ Simplify (bpy)[/green] {row.id}")
            return False

        err = merge_subprocess_output(r) or "bpy simplify falhou"
        console.print(f"[yellow]bpy simplify falhou, a usar text3d fallback[/yellow] {row.id}: {err[:200]}")

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
    if t3.low_vram:
        args.append("--low-vram")
    if t3.mc_level is not None:
        args.extend(["--mc-level", str(t3.mc_level)])
    if t3.guidance is not None:
        args.extend(["--guidance", str(t3.guidance)])
    if t3.allow_shared_gpu:
        args.append("--allow-shared-gpu")
    if not t3.gpu_kill_others:
        args.append("--no-gpu-kill-others")
    if t3.full_gpu:
        args.append("--t2d-full-gpu")
    args.extend(["--export-origin", t3.export_origin])
    if gpu_ids:
        args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
    return args
