"""batch_cmd click command."""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from gamedev_shared.path_utils import safe_filename as _safe_slug
from gamedev_shared.profiler.session import ProfilerSession
from gamedev_shared.subprocess_utils import run_cmd_streaming

from .batch_guard import batch_directory_lock, detect_gpu_ids, query_gpu_free_mib, subprocess_gpu_env
from .categories import get_target_faces
from .cli_rich import click
from .helpers import (
    _append_gpu_kill_flag,
    _append_quality,
    _append_skymap2d_profile_args,
    _append_terrain3d_profile_args,
    _append_text2d_profile_args,
    _append_text2icon_profile_args,
    _append_texture2d_profile_args,
    _audio_path_for_row_manifest,
    _build_context,
    _dry_run_emit,
    _dry_run_header,
    _materialize_diffuse_argv,
    _resolve_manifest_path,
    _resolve_materialize_bin_texture2d,
    _resolve_skymap2d_bin,
    _resolve_terrain3d_bin,
    _resolve_text2icon_bin,
    _row_uses_texture2d,
    _row_wants_animate,
    _row_wants_audio,
    _row_wants_rig,
    _safe_row_dirname,
    _seed_for_manifest_row,
    _seed_for_row,
    _skymap2d_profile_effective,
    _terrain3d_profile_effective,
    _text2icon_profile_effective,
    _text2sound_args_for_row,
    _text2sound_profile_effective,
    _texture2d_material_maps_path,
    _texture2d_material_maps_path_manifest,
    _texture2d_profile_effective,
    _timing_append,
    effective_face_ratio,
)
from .manifest import ManifestRow, apply_row_text3d_overrides, effective_image_source, row_mc_level
from .omni_ctrl import omni_to_batch_item, prepare_shape_for_generation
from .param_optimizer import (
    optimize_text3d_for_target,
    should_optimize_text3d,
)
from .paths import (
    _ROW_DONE,
    _animator3d_output_path,
    _classify_row_state,
    _clean_existing,
    _install_file,
    _painted_existing,
    _painted_path,
    _path_for_log,
    _paths_for_row_manifest,
    _rigging3d_output_path,
    _shape_existing,
    _shape_path,
)
from .pipeline import (
    _animator3d_game_pack_argv,
    _post_text3d_mesh_extras,
    _resolve_animator3d_bin,
    _rigging3d_pipeline_argv,
    _simplify_to_target,
    _text3d_argv,
    _texture_subprocess_argv,
    ensure_to_paint_for_paint,
    resolve_row_omni,
)
from .profile import (
    Animator3DProfile,
    Paint3DProfile,
)
from .prompt_builder import build_audio_prompt, build_prompt
from .runner import merge_subprocess_output, resolve_binary, run_cmd
from .ums_batch import (
    run_paint_wave_or_fallback,
    run_shape_wave_or_fallback,
    run_skymap2d_wave_or_fallback,
    run_terrain3d_wave_or_fallback,
    run_text2d_wave_or_fallback,
    run_text2icon_wave_or_fallback,
    run_text2sound_wave_or_fallback,
    run_texture2d_wave_or_fallback,
)
from .ums_coord import MasterDeferQueue, apply_ums_child_env

console = Console()


@click.command("batch")
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="game.yaml",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default="manifest",
)
@click.option(
    "--presets-local",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--no-3d",
    is_flag=True,
    default=False,
    help="Skip 3D generation even if manifest has generate_3d=true.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Mostra comandos sem executar",
)
@click.option(
    "--dry-run-json",
    "dry_run_json",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Com --dry-run, grava plano JSON (fases e argv por linha) para agentes/CI.",
)
@click.option(
    "--fail-fast",
    is_flag=True,
    help="Parar no primeiro erro (defeito: continuar)",
)
@click.option(
    "--log",
    "log_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Ficheiro JSONL com um registo por asset",
)
@click.option(
    "--skip-batch-lock",
    is_flag=True,
    help="Permite dois batches na mesma pasta (não recomendado: disputa de VRAM / OOM).",
)
@click.option(
    "--skip-gpu-preflight",
    is_flag=True,
    help="Não avisar quando a VRAM livre (NVML) estiver baixa.",
)
@click.option(
    "--skip-text2d",
    "skip_text2d",
    is_flag=True,
    help=(
        "Não gerar imagens 2D (Text2D ou Texture2D): usa PNG já em output_dir "
        "(exige geração 3D; valida PNG por linha com generate_3d)."
    ),
)
@click.option(
    "--skip-audio",
    "skip_audio",
    is_flag=True,
    help="Não gerar áudio Text2Sound (ignora coluna generate_audio).",
)
@click.option(
    "--no-rig",
    is_flag=True,
    default=False,
    help="Skip rigging even if rigging3d is configured.",
)
@click.option(
    "--no-animate",
    is_flag=True,
    default=False,
    help="Skip animation even for rigged models.",
)
@click.option(
    "--no-lod",
    is_flag=True,
    default=False,
    help="Skip LOD generation even if enabled in manifest/profile",
)
@click.option(
    "--no-collision",
    is_flag=True,
    default=False,
    help="Skip collision mesh generation even if enabled",
)
@click.option(
    "--no-terrain",
    is_flag=True,
    default=False,
    help="Skip terrain generation even if terrain3d is configured in game.yaml.",
)
@click.option(
    "--no-skymap",
    is_flag=True,
    default=False,
    help="Skip skymap generation even if skymap2d is configured in game.yaml.",
)
@click.option(
    "--no-icons",
    is_flag=True,
    default=False,
    help="Skip UI icon generation even if text2icon is configured in game.yaml.",
)
@click.option(
    "--profile-tools",
    is_flag=True,
    help="Activar profiling (CPU/RAM/GPU) em paint3d via GAMEDEV_PROFILE.",
)
@click.option(
    "--profile-log",
    "profile_tools_log",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="JSONL para spans (defeito: gameassets_profile.jsonl na pasta do manifest).",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Regenerar tudo, mesmo outputs que já existem (passa --force aos sub-commands).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help=("IDs de GPU (ex.: '0,1'). Defeito: auto-deteta todas as GPUs via NVML."),
)
@click.option(
    "--no-dashboard",
    is_flag=True,
    help="Usar barras de progresso simples em vez do dashboard TUI",
)
@click.option(
    "--ums-stream",
    is_flag=True,
    default=False,
    help="Propaga GAMEDEV_UMS_STREAM=1 aos subprocessos (eventos UMS; só com verbose/ruído OK).",
)
@click.option(
    "--no-ums",
    is_flag=True,
    default=False,
    help="Desliga UMS (auto-start off + waves GPU via subprocess CLI; tools recebem --no-ums).",
)
@click.option(
    "--plain",
    is_flag=True,
    help="Plain text output (no Rich/TUI, for scripts and headless)",
)
def batch_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    no_3d: bool,
    dry_run: bool,
    dry_run_json: Path | None,
    fail_fast: bool,
    log_path: Path | None,
    skip_batch_lock: bool,
    skip_gpu_preflight: bool,
    skip_text2d: bool,
    skip_audio: bool,
    no_rig: bool,
    no_animate: bool,
    no_lod: bool,
    no_collision: bool,
    no_terrain: bool,
    no_skymap: bool,
    no_icons: bool,
    profile_tools: bool,
    profile_tools_log: Path | None,
    force: bool,
    gpu_ids_str: str | None,
    no_dashboard: bool,
    ums_stream: bool,
    no_ums: bool,
    plain: bool,
) -> None:
    """Gera imagens (e opcionalmente meshes) para cada linha do manifest."""
    if plain:
        no_dashboard = True
        global console
        console = Console(no_color=True, force_terminal=False, width=999)

    profile, rows, _bundle, preset = _build_context(profile_path, manifest_path, presets_local)
    manifest_path = _resolve_manifest_path(manifest_path)

    with_3d = not no_3d and any(r.generate_3d for r in rows)
    with_rig = not no_rig and with_3d and any(r.generate_rig for r in rows)
    with_animate = not no_animate and with_rig and any(r.generate_animate for r in rows)
    with_lod = not no_lod and with_3d and any(r.generate_lod for r in rows)
    with_collision = not no_collision and with_3d and any(r.generate_collision for r in rows)
    has_rigging_profile = False
    has_audio_profile = False

    any_row_wants_paint = any(r.generate_3d and r.generate_paint for r in rows)
    if any_row_wants_paint and profile.paint3d is None:
        profile.paint3d = Paint3DProfile()

    gpu_ids: list[int] | None = None
    if gpu_ids_str:
        try:
            gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")]
        except ValueError as _err:
            raise click.ClickException("--gpu-ids deve ser lista separada por vírgulas (ex.: '0,1')") from _err
    else:
        gpu_ids = detect_gpu_ids()

    if dry_run_json is not None and not dry_run:
        raise click.ClickException("--dry-run-json requer --dry-run")

    if skip_text2d and not with_3d:
        raise click.ClickException("--skip-text2d requires 3D generation (generate_3d in manifest).")

    def _row_sources() -> tuple[bool, bool]:
        any_t2d = False
        any_tex = False
        for r in rows:
            if not r.generate_3d:
                continue
            src = effective_image_source(profile, r)
            if src == "text2d":
                any_t2d = True
            elif src == "texture2d":
                any_tex = True
            # skymap2d: pipeline próprio (Skymap2D); não usar binário texture2d
        return any_t2d, any_tex

    any_text2d_row, any_texture2d_row = _row_sources()

    def _resolve_bin(env_var: str, tool_name: str) -> str:
        try:
            return resolve_binary(env_var, tool_name)
        except FileNotFoundError:
            if dry_run:
                return tool_name
            raise click.ClickException(f"{tool_name} não encontrado no PATH (defina {env_var})") from None

    text2d_bin: str | None = None
    texture2d_bin: str | None = None
    if not skip_text2d:
        if any_text2d_row:
            text2d_bin = _resolve_bin("TEXT2D_BIN", "text2d")
        if any_texture2d_row:
            texture2d_bin = _resolve_bin("TEXTURE2D_BIN", "texture2d")
    text3d_bin: str | None = None
    paint3d_bin: str | None = None
    if with_3d:
        text3d_bin = _resolve_bin("TEXT3D_BIN", "text3d")
        if any_row_wants_paint:
            paint3d_bin = _resolve_bin("PAINT3D_BIN", "paint3d")

    rigging3d_bin: str | None = None
    if with_rig and any(_row_wants_rig(r, has_rigging_profile) for r in rows):
        rigging3d_bin = _resolve_bin("RIGGING3D_BIN", "rigging3d")

    animator3d_bin: str | None = None
    if with_animate and any(r.generate_3d and _row_wants_animate(r, with_rig, has_rigging_profile) for r in rows):
        animator3d_bin = _resolve_animator3d_bin()
        if not animator3d_bin:
            if dry_run:
                animator3d_bin = "animator3d"
            elif with_animate:
                raise click.ClickException(
                    "Comando não encontrado: 'animator3d'. Instala Animator3D ou define ANIMATOR3D_BIN."
                ) from None

    any_audio_row = any(_row_wants_audio(r, has_audio_profile) for r in rows)
    text2sound_bin: str | None = None
    if any_audio_row and not skip_audio:
        text2sound_bin = _resolve_bin("TEXT2SOUND_BIN", "text2sound")

    # --- Scene-level tools: Terrain3D and Skymap2D ---
    with_terrain = not no_terrain and profile.terrain3d is not None
    terrain3d_bin: str | None = None
    if with_terrain:
        try:
            terrain3d_bin = _resolve_terrain3d_bin()
        except FileNotFoundError:
            if dry_run:
                terrain3d_bin = "terrain3d"
            else:
                raise click.ClickException("terrain3d não encontrado (defina TERRAIN3D_BIN)") from None

    with_skymap = not no_skymap and profile.skymap2d is not None and bool(_skymap2d_profile_effective(profile).prompt)
    skymap2d_bin: str | None = None
    if with_skymap:
        try:
            skymap2d_bin = _resolve_skymap2d_bin()
        except FileNotFoundError:
            if dry_run:
                skymap2d_bin = "skymap2d"
            else:
                raise click.ClickException("skymap2d não encontrado (defina SKYMAP2D_BIN)") from None

    # text2icon (scene-level ícones de UI via Sana Sprint)
    with_icons = not no_icons and profile.text2icon is not None and bool(_text2icon_profile_effective(profile).prompts)
    text2icon_bin: str | None = None
    if with_icons:
        try:
            text2icon_bin = _resolve_text2icon_bin()
        except FileNotFoundError:
            if dry_run:
                text2icon_bin = "text2icon"
            else:
                raise click.ClickException("text2icon não encontrado (defina TEXT2ICON_BIN)") from None

    meta = Table(show_header=False, box=box.SIMPLE, title="[bold]Batch[/bold]")
    meta.add_row("Perfil", str(profile_path.resolve()))
    meta.add_row("Manifest", str(manifest_path.resolve()))
    meta.add_row("Linhas", str(len(rows)))
    tt_eff = _texture2d_profile_effective(profile)
    if skip_text2d:
        img_pipeline = "[dim]omitido[/dim] (PNG existentes)"
    elif any_texture2d_row and any_text2d_row:
        img_pipeline = f"misto: text2d ({text2d_bin or '?'}) + texture2d ({texture2d_bin or '?'})"
        if tt_eff.materialize:
            img_pipeline += " → materialize (PBR em linhas texture2d)"
    elif any_texture2d_row:
        img_pipeline = f"texture2d ({texture2d_bin or ''})"
        if tt_eff.materialize:
            img_pipeline += " → materialize (PBR)"
    else:
        img_pipeline = text2d_bin or ""
    meta.add_row("Imagem (2D)", img_pipeline)
    if any_audio_row:
        meta.add_row(
            "text2sound",
            "[dim]omitido (--skip-audio)[/dim]" if skip_audio else (text2sound_bin or ""),
        )
    meta.add_row("text3d", text3d_bin or "[dim](desligado)[/dim]")
    if with_3d:
        if any_row_wants_paint:
            meta.add_row("paint3d", paint3d_bin or "[red]em falta[/red]")
        else:
            meta.add_row("paint3d", "[dim](nenhuma linha com paint no pipeline)[/dim]")
    meta.add_row("rigging3d", rigging3d_bin or "[dim](desligado)[/dim]")
    meta.add_row("animator3d", animator3d_bin or "[dim](desligado)[/dim]")
    meta.add_row("terrain3d", terrain3d_bin or "[dim](desligado)[/dim]")
    meta.add_row("skymap2d", skymap2d_bin or "[dim](desligado)[/dim]")
    meta.add_row("text2icon", text2icon_bin or "[dim](desligado)[/dim]")
    meta.add_row("Modo", "[cyan]dry-run[/cyan]" if dry_run else "execução")
    if gpu_ids:
        meta.add_row("GPUs", ",".join(str(g) for g in gpu_ids))
    if profile_tools:
        _plog = profile_tools_log or (manifest_path.parent / "gameassets_profile.jsonl")
        meta.add_row("Profiler (GPU)", f"activo → [dim]{_plog.resolve()}[/dim]")
    # Build Ordem string — terrain/skymap prefix, then existing pipeline
    _ord_prefix: list[str] = []
    if with_terrain:
        _ord_prefix.append("Terrain3D")
    if with_skymap:
        _ord_prefix.append("Skymap2D")
    if with_icons:
        _ord_prefix.append("Text2Icon")
    if skip_text2d:
        ord_skip: list[str] = [*_ord_prefix, "Geração 2D omitida"]
        if any_audio_row and not skip_audio:
            ord_skip.append("Text2Sound (linhas generate_audio)")
        if with_3d:
            ord_skip.append("Text3D (só generate_3d, PNG no output_dir)")
            if with_rig:
                ord_skip.append("Rigging3D (generate_rig)")
            if with_animate:
                ord_skip.append("Animator3D game-pack")
        meta.add_row("Ordem", " → ".join(ord_skip))
    elif any_texture2d_row or any_text2d_row:
        if any_texture2d_row and any_text2d_row:
            ord_parts = [*_ord_prefix, "Geração 2D por linha (text2d e/ou texture2d)"]
        elif any_texture2d_row:
            ord_parts = [*_ord_prefix, "Texture2D (todas as linhas)"]
        else:
            ord_parts = [*_ord_prefix, "Text2D (todas as linhas)"]
        if tt_eff.materialize and any_texture2d_row:
            ord_parts.append("Materialize (mapas PBR nas linhas texture2d)")
        if any_audio_row and not skip_audio:
            ord_parts.append("Text2Sound (linhas generate_audio)")
        if with_3d:
            ord_parts.append("Text3D (só generate_3d)")
            if with_rig:
                ord_parts.append("Rigging3D (generate_rig)")
            if with_animate:
                ord_parts.append("Animator3D game-pack")
        meta.add_row("Ordem", " → ".join(ord_parts))
    else:
        ord_tail = " → ".join([*_ord_prefix, "Text2D (todas as linhas)"])
        if any_audio_row and not skip_audio:
            ord_tail += " → Text2Sound (linhas generate_audio)"
        if with_3d:
            ord_tail += " → Text3D (só generate_3d, imagens já gravadas)"
            if with_rig:
                ord_tail += " → Rigging3D (generate_rig)"
            if with_animate:
                ord_tail += " → Animator3D game-pack"
        meta.add_row("Ordem", ord_tail)
    lock_path = manifest_path.parent / ".gameassets_batch.lock"
    meta.add_row(
        "Lock",
        "[dim]desligado[/dim]" if (dry_run or skip_batch_lock) else f"{lock_path}",
    )
    console.print(Panel(meta, border_style="cyan", title="[bold]Plano[/bold]"))

    manifest_dir = manifest_path.parent.resolve()

    continue_on_error = not fail_fast
    failures = 0
    # Log detalhado por defeito (manifest/logs/batch-detail.jsonl) + pipeline_trace Shared.
    # Append-only: truncar no start apagava evidência de falhas anteriores.
    if log_path is None and not dry_run:
        log_path = manifest_dir / "logs" / "batch-detail.jsonl"
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch(exist_ok=True)
    with contextlib.suppress(Exception):
        from gamedev_shared.pipeline_trace import default_log_path, session_id, trace_event

        _trace_p = default_log_path()
        console.print(f"[dim]pipeline_trace[/dim] {_trace_p}  session={session_id()}  batch_log={log_path or '(off)'}")
        trace_event(
            "batch_start",
            profile=str(profile_path) if profile_path else None,
            manifest=str(manifest_path),
            log_path=str(log_path) if log_path else None,
            force=bool(force),
            skip_text2d=bool(skip_text2d),
            skip_audio=bool(skip_audio),
        )
    if log_path is not None:
        with contextlib.suppress(Exception):
            from gamedev_shared.pipeline_trace import session_id as _sid

            with log_path.open("a", encoding="utf-8") as _lf:
                _lf.write(
                    json.dumps(
                        {
                            "event": "batch_session",
                            "session": _sid(),
                            "manifest": str(manifest_path),
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

    def append_log(rec: dict[str, Any]) -> None:
        if log_path is None:
            return
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        with contextlib.suppress(Exception):
            from gamedev_shared.pipeline_trace import trace_event

            trace_event("batch_record", **rec)

    if dry_run:
        dry_plan: list[dict[str, Any]] | None = [] if dry_run_json else None
        # Scene-level: Terrain3D + Skymap2D
        if with_terrain and terrain3d_bin:
            ter_eff = _terrain3d_profile_effective(profile)
            ter_out_dir = Path(profile.output_dir) / "terrain"
            ter_argv = [terrain3d_bin, "generate", "--output", str(ter_out_dir / "heightmap.png")]
            if ter_eff.prompt:
                ter_argv.extend(["--prompt", ter_eff.prompt])
            _append_terrain3d_profile_args(ter_eff, ter_argv)
            _dry_run_emit(dry_plan, phase="Terrain3D", row_id=None, argv=ter_argv)
        if with_skymap and skymap2d_bin:
            sky_eff = _skymap2d_profile_effective(profile)
            sky_out_dir = Path(profile.output_dir) / "sky"
            sky_argv = [skymap2d_bin, "generate", sky_eff.prompt or "", "-o", str(sky_out_dir / "sky.png")]
            _append_skymap2d_profile_args(sky_eff, sky_argv, quality=profile.generation)
            _dry_run_emit(dry_plan, phase="Skymap2D", row_id=None, argv=sky_argv)
        if with_icons and text2icon_bin:
            icon_eff = _text2icon_profile_effective(profile)
            icon_out_dir = Path(profile.output_dir) / "icons"
            for icon_prompt in icon_eff.prompts:
                icon_slug = _safe_slug(icon_prompt)
                icon_argv = [text2icon_bin, "generate", icon_prompt, "-o", str(icon_out_dir / f"{icon_slug}.png")]
                _append_text2icon_profile_args(icon_eff, icon_argv, quality=profile.generation)
                _dry_run_emit(dry_plan, phase="Text2Icon", row_id=icon_slug, argv=icon_argv)
        if not skip_text2d:
            if any_texture2d_row and any_text2d_row:
                p1_title = "--- Fase 1: Text2D / Texture2D (por linha) ---"
            elif any_texture2d_row:
                p1_title = "--- Fase 1: Texture2D (todas as linhas) ---"
            else:
                p1_title = "--- Fase 1: Text2D (todas as linhas) ---"
            _dry_run_header(dry_plan, p1_title)
            dry_text2d_items: list[dict[str, Any]] = []
            for row in rows:
                if row.generate_3d:
                    prompt = build_prompt(profile, preset, row, for_3d=False)
                    img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                    seed = _seed_for_manifest_row(profile, row)
                    tt_line = _texture2d_profile_effective(profile)
                    if _row_uses_texture2d(profile, row):
                        t2d_args = [
                            texture2d_bin or "",
                            "generate",
                            prompt,
                            "-o",
                            str(img_path),
                        ]
                        if seed is not None:
                            t2d_args.extend(["--seed", str(seed)])
                        _append_texture2d_profile_args(tt_line, t2d_args, quality=profile.generation)
                        _dry_run_emit(dry_plan, phase=p1_title, row_id=row.id, argv=t2d_args)
                        if tt_line.materialize:
                            maps_ph = _texture2d_material_maps_path(profile, row)
                            try:
                                mbin_dr = _resolve_materialize_bin_texture2d(tt_line)
                            except FileNotFoundError:
                                mbin_dr = "materialize"
                            margv = _materialize_diffuse_argv(mbin_dr, tt_line, img_path, maps_ph)
                            _dry_run_emit(dry_plan, phase=p1_title + " materialize", row_id=row.id, argv=margv)
                    else:
                        dry_item: dict[str, Any] = {"id": row.id, "prompt": prompt, "output": str(img_path)}
                        if seed is not None:
                            dry_item["seed"] = seed
                        dry_text2d_items.append(dry_item)
            if dry_text2d_items:
                batch_argv = [text2d_bin or "", "generate-batch", "<text2d_manifest.json>"]
                _append_text2d_profile_args(profile, batch_argv)
                if force:
                    batch_argv.append("--force")
                if gpu_ids:
                    batch_argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                _dry_run_emit(dry_plan, phase="text2d generate-batch", row_id="(batch)", argv=batch_argv)
            for row in rows:
                if not skip_audio and text2sound_bin and _row_wants_audio(row, has_audio_profile):
                    ts_line = _text2sound_profile_effective(profile)
                    audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                    prompt_a = build_audio_prompt(profile, preset, row)
                    argv_au = [
                        text2sound_bin,
                        "generate",
                        prompt_a,
                        "-o",
                        str(audio_final),
                    ]
                    seed_a = _seed_for_row(profile, f"{row.id}:audio")
                    if seed_a is not None:
                        argv_au.extend(["--seed", str(seed_a)])
                    _text2sound_args_for_row(ts_line, row, argv_au)
                    _dry_run_emit(dry_plan, phase=p1_title + " text2sound", row_id=row.id, argv=argv_au)
        else:
            _dry_run_header(dry_plan, "--- Text2D omitido (--skip-text2d) ---")
            if not skip_audio and text2sound_bin and any(_row_wants_audio(r, has_audio_profile) for r in rows):
                _dry_run_header(
                    dry_plan,
                    "--- Text2Sound (generate_audio; PNG em output_dir) ---",
                )
                for row in rows:
                    if not _row_wants_audio(row, has_audio_profile):
                        continue
                    ts_line = _text2sound_profile_effective(profile)
                    audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                    prompt_a = build_audio_prompt(profile, preset, row)
                    argv_au = [
                        text2sound_bin,
                        "generate",
                        prompt_a,
                        "-o",
                        str(audio_final),
                    ]
                    seed_a = _seed_for_row(profile, f"{row.id}:audio")
                    if seed_a is not None:
                        argv_au.extend(["--seed", str(seed_a)])
                    _text2sound_args_for_row(ts_line, row, argv_au)
                    _dry_run_emit(
                        dry_plan,
                        phase="text2sound (skip-text2d)",
                        row_id=row.id,
                        argv=argv_au,
                    )
        if with_3d and text3d_bin and any(r.generate_3d for r in rows):
            t3d = profile.text3d
            phased = any_row_wants_paint
            ps3 = (profile.paint3d.style if profile.paint3d else None) or "hunyuan"
            quick_paint = ps3 in ("solid", "perlin")
            if phased:
                _dry_run_header(
                    dry_plan,
                    "--- Text3D + paint3d: shape → quick (cor / Perlin) ---"
                    if quick_paint
                    else "--- Text3D + paint3d: shape-batch → texture-batch (PBR no GLB via Paint 2.1) ---",
                )
                phase_paint = "paint3d quick" if quick_paint else "paint3d texture-batch"

                # Shape batch dry-run
                shape_argv = [text3d_bin, "generate-batch", "<shape_manifest.json>"]
                _append_quality(shape_argv, profile)
                if t3d:
                    if not should_optimize_text3d(t3d):
                        if t3d.preset:
                            shape_argv.extend(["--preset", t3d.preset])
                        if t3d.steps is not None:
                            shape_argv.extend(["--steps", str(t3d.steps)])
                        if t3d.octree_resolution is not None:
                            shape_argv.extend(["--octree-resolution", str(t3d.octree_resolution)])
                        if t3d.num_chunks is not None:
                            shape_argv.extend(["--num-chunks", str(t3d.num_chunks)])
                    if t3d.allow_shared_gpu:
                        shape_argv.append("--allow-shared-gpu")
                    _append_gpu_kill_flag(shape_argv, t3d.gpu_kill_others)
                if force:
                    shape_argv.append("--force")
                if gpu_ids:
                    shape_argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                _dry_run_emit(
                    dry_plan,
                    phase="text3d generate-batch",
                    row_id="(batch)",
                    argv=shape_argv,
                )

                if quick_paint:
                    # Quick paint stays per-row in dry-run
                    pbin = paint3d_bin or "paint3d"
                    for row in rows:
                        if not row.generate_3d or not row.generate_paint:
                            continue
                        img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                        tw = "<tmp>/shape.glb"
                        a2 = _texture_subprocess_argv(
                            pbin,
                            profile,
                            Path(tw),
                            img_path,
                            mesh_path,
                            row_id=row.id,
                            row=row,
                            gpu_ids=gpu_ids,
                        )
                        _dry_run_emit(
                            dry_plan,
                            phase=phase_paint,
                            row_id=row.id,
                            argv=a2,
                        )
                else:
                    # Paint batch dry-run
                    paint_argv = [paint3d_bin or "paint3d", "texture-batch", "<paint_manifest.json>"]
                    _append_quality(paint_argv, profile)
                    if t3d:
                        if t3d.allow_shared_gpu:
                            paint_argv.append("--allow-shared-gpu")
                        _append_gpu_kill_flag(paint_argv, t3d.gpu_kill_others)
                    if force:
                        paint_argv.append("--force")
                    if gpu_ids:
                        paint_argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                    _dry_run_emit(
                        dry_plan,
                        phase="paint3d texture-batch",
                        row_id="(batch)",
                        argv=paint_argv,
                    )
            else:
                _dry_run_header(dry_plan, "--- Fase 2: Text3D (generate_3d=true) ---")
                for row in rows:
                    if not row.generate_3d:
                        continue
                    img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                    seed = _seed_for_manifest_row(profile, row)
                    t3d_args = _text3d_argv(
                        text3d_bin,
                        profile,
                        img_path,
                        mesh_path,
                        row,
                        gpu_ids=gpu_ids,
                        manifest_dir=manifest_dir,
                    )
                    if seed is not None:
                        t3d_args.extend(["--seed", str(seed)])
                    _dry_run_emit(
                        dry_plan,
                        phase="text3d",
                        row_id=row.id,
                        argv=t3d_args,
                    )
        if with_rig and rigging3d_bin and any(r.generate_3d and _row_wants_rig(r, has_rigging_profile) for r in rows):
            _dry_run_header(
                dry_plan,
                "--- Rigging3D (entrada: GLB base) ---",
            )
            for row in rows:
                if not row.generate_3d or not _row_wants_rig(row, has_rigging_profile):
                    continue
                _img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                seed = _seed_for_row(profile, row.id)
                rg = profile.rigging3d
                sfx = rg.output_suffix if rg else "_rigged"
                rig_in = mesh_path
                rig_out = _rigging3d_output_path(rig_in, sfx)
                rg_args = _rigging3d_pipeline_argv(
                    rigging3d_bin,
                    rig_in,
                    rig_out,
                    seed=seed,
                    rig_profile=rg,
                    gpu_ids=gpu_ids,
                )
                _dry_run_emit(dry_plan, phase="rigging3d", row_id=row.id, argv=rg_args)
        if (
            with_animate
            and animator3d_bin
            and any(r.generate_3d and _row_wants_animate(r, with_rig, has_rigging_profile) for r in rows)
        ):
            _dry_run_header(
                dry_plan,
                "--- Animator3D game-pack (após rig; auto-detectado de manifest + game.yaml) ---",
            )
            for row in rows:
                if not row.generate_3d or not _row_wants_animate(row, with_rig, has_rigging_profile):
                    continue
                _img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                rg = profile.rigging3d
                sfx = rg.output_suffix if rg else "_rigged"
                rig_in = mesh_path
                rig_out = _rigging3d_output_path(rig_in, sfx)
                anim_out = _animator3d_output_path(rig_out)
                anim_prof = profile.animator3d or Animator3DProfile()
                preset = (anim_prof.preset or "humanoid").strip().lower()
                ap_args = _animator3d_game_pack_argv(animator3d_bin, rig_out, anim_out, preset=preset)
                _dry_run_emit(dry_plan, phase="animator3d", row_id=row.id, argv=ap_args)
        if dry_run_json is not None and dry_plan is not None:
            payload = {
                "version": 1,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "profile": str(profile_path.resolve()),
                "manifest": str(manifest_path.resolve()),
                "options": {
                    "with_3d": with_3d,
                    "with_rig": with_rig,
                    "with_animate": with_animate,
                    "skip_text2d": skip_text2d,
                    "skip_audio": skip_audio,
                },
                "steps": dry_plan,
            }
            dry_run_json.parent.mkdir(parents=True, exist_ok=True)
            dry_run_json.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            console.print(
                Panel(
                    f"[green]dry-run[/green] — plano JSON: [bold]{dry_run_json}[/bold] ({len(dry_plan)} passo(s))",
                    border_style="green",
                    title="Batch",
                )
            )
        else:
            console.print(Panel("[green]dry-run concluído[/green]", border_style="green", title="Batch"))
        return

    if not rows:
        console.print("[yellow]Manifest sem linhas.[/yellow]")
        return

    if not skip_gpu_preflight:
        free_mib = query_gpu_free_mib()
        if free_mib is not None and free_mib < 1800:
            console.print(
                Panel(
                    f"[yellow]VRAM livre na GPU 0: ~{free_mib} MiB[/yellow] (recomendável ≥2 GiB "
                    "para Text2D/Text2Sound/Text3D sem OOM). Fecha outro [bold]gameassets batch[/bold], o "
                    "[bold]Godot[/bold], ou [bold]text3d[/bold]/[bold]text2sound[/bold] órfão; ou activa "
                    "[bold]text2d.cpu: true[/bold] no perfil. "
                    "O batch define [dim]PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True[/dim] "
                    "se ainda não estiver no ambiente.",
                    title="Aviso GPU",
                    border_style="yellow",
                )
            )

    # Garantir que o UMS (Unified Model Server) está a correr — as subprocess
    # invocations das tools (text2d, text3d, paint3d, rigging3d) delegam para o
    # UMS se estiver activo, reutilizando modelos quentes (~7s vs ~20s cold-start).
    # Graceful: se falhar, continuar sem UMS (cada subprocess carrega do zero).
    if not skip_gpu_preflight:
        try:
            from gamedev_shared.model_server import ensure_ums_running

            ensure_ums_running()
        except Exception:
            pass  # sem UMS — continuar com subprocess cold-start

    child_env = dict(subprocess_gpu_env(gpu_ids=gpu_ids))
    # Pedidos GPU via UMS ficam atrás de CLIs interactivas (afinidade/prioridade).
    apply_ums_child_env(child_env, ums_stream=ums_stream, no_ums=no_ums)
    if profile_tools:
        child_env["GAMEDEV_PROFILE"] = "1"
        child_env["GAMEDEV_PROFILE_TOOL"] = "gameassets"
        plog = profile_tools_log or (manifest_path.parent / "gameassets_profile.jsonl")
        child_env["GAMEDEV_PROFILE_LOG"] = str(plog.resolve())

    _batch_params = {
        "rows": len(rows),
        "with_3d": with_3d,
        "with_rig": with_rig,
        "with_animate": with_animate,
        "skip_text2d": skip_text2d,
        "skip_audio": skip_audio,
        "dry_run": dry_run,
    }

    with (
        ProfilerSession(
            "gameassets",
            cli_profile=profile_tools,
            params=_batch_params,
        ),
        batch_directory_lock(manifest_path, skip=skip_batch_lock),
    ):
        batch_tmp = Path(tempfile.mkdtemp(prefix="gameassets_"))
        try:
            if not no_dashboard:
                # === Dashboard TUI path ===
                from .dashboard import BatchDashboard

                asset_ids = [row.id for row in rows]
                _pipeline_stages: list[str] = []
                if not skip_text2d:
                    if any_text2d_row and any_texture2d_row:
                        _pipeline_stages.append("Text2D/Texture2D")
                    elif any_texture2d_row:
                        _pipeline_stages.append("Texture2D")
                    else:
                        _pipeline_stages.append("Text2D")
                if any_audio_row and not skip_audio:
                    _pipeline_stages.append("Text2Sound")
                if with_3d:
                    _pipeline_stages.append("Text3D")
                if with_3d and any_row_wants_paint:
                    _pipeline_stages.append("Paint3D")
                if with_rig:
                    _pipeline_stages.append("Rigging3D")
                if with_animate:
                    _pipeline_stages.append("Animator3D")
                pipeline_desc = " → ".join(_pipeline_stages)

                def _batch_fn(dash: BatchDashboard) -> None:  # type: ignore[name-defined]
                    nonlocal failures
                    results_d: list[dict[str, Any]] = []
                    pending_3d_d: list[int] = []

                    # --- Pre-classify rows to enable skip logic ---
                    row_states_d: dict[int, str] = {}
                    if not force:
                        for _ci, _cr in enumerate(rows):
                            _ci_img, _ci_mesh = _paths_for_row_manifest(profile, manifest_dir, _cr)
                            _want_tex = _cr.generate_paint
                            _ci_rig = _rigging3d_output_path(
                                _ci_mesh, (profile.rigging3d.output_suffix if profile.rigging3d else None) or "_rigged"
                            )
                            _ci_anim = _animator3d_output_path(_ci_rig)
                            _wants_rig = _row_wants_rig(_cr, has_rigging_profile)
                            _wants_an = _row_wants_animate(_cr, with_rig, has_rigging_profile)
                            _lod0 = _ci_mesh.parent / f"{_ci_mesh.stem}_lod0.glb"
                            _coll = _ci_mesh.parent / f"{_ci_mesh.stem}_collision.glb"
                            row_states_d[_ci] = _classify_row_state(
                                img_final=_ci_img,
                                mesh_final=_ci_mesh,
                                rig_out=_ci_rig,
                                anim_out=_ci_anim,
                                want_texture=_want_tex,
                                wants_rig=_wants_rig,
                                wants_animate=_wants_an,
                                wants_lod=_cr.generate_lod,
                                wants_collision=_cr.generate_collision,
                                lod0_path=_lod0,
                                collision_path=_coll,
                            )

                    # Skip fully-done items: advance all phases and mark skipped in dashboard
                    done_indices_d: set[int] = set()
                    if not force and row_states_d:
                        for _di, _ds in row_states_d.items():
                            if _ds == _ROW_DONE:
                                done_indices_d.add(_di)

                    # --- Pre-phase: Terrain3D (scene-level, single-shot) ---
                    if with_terrain and terrain3d_bin:
                        ter_eff = _terrain3d_profile_effective(profile)
                        ter_out_dir = Path(profile.output_dir) / "terrain"
                        ter_out_dir.mkdir(parents=True, exist_ok=True)
                        ter_out = ter_out_dir / "heightmap.png"
                        ter_item: dict[str, Any] = {"id": "terrain", "output": str(ter_out)}
                        if ter_eff.prompt:
                            ter_item["prompt"] = ter_eff.prompt
                        if ter_eff.seed is not None:
                            ter_item["seed"] = ter_eff.seed
                        if ter_eff.size is not None:
                            ter_item["size"] = ter_eff.size
                        if ter_eff.world_size is not None:
                            ter_item["world_size"] = ter_eff.world_size
                        if ter_eff.max_height is not None:
                            ter_item["max_height"] = ter_eff.max_height
                        if ter_eff.device:
                            ter_item["device"] = ter_eff.device
                        if ter_eff.dtype:
                            ter_item["dtype"] = ter_eff.dtype
                        if ter_eff.cache_size:
                            ter_item["cache_size"] = ter_eff.cache_size
                        if ter_eff.coarse_window is not None:
                            ter_item["coarse_window"] = ter_eff.coarse_window
                        dash.set_phase("Terrain3D", 1)
                        dash.update_asset("terrain", "running", "A gerar terreno...")
                        t_ter = time.perf_counter()
                        ums_ter = run_terrain3d_wave_or_fallback(
                            [ter_item],
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                        )
                        if ums_ter is None:
                            ter_argv = [terrain3d_bin, "generate", "--output", str(ter_out)]
                            if ter_eff.prompt:
                                ter_argv.extend(["--prompt", ter_eff.prompt])
                            _append_terrain3d_profile_args(ter_eff, ter_argv)
                            if gpu_ids:
                                ter_argv.extend(["--device", f"cuda:{gpu_ids[0]}"])
                            if no_ums:
                                ter_argv.append("--no-ums")
                            r_ter = run_cmd(ter_argv, extra_env=child_env, cwd=manifest_dir)
                            _timing_append({"id": "terrain"}, "terrain3d_sec", time.perf_counter() - t_ter)
                            if r_ter.returncode != 0:
                                dash.update_asset(
                                    "terrain", "error", merge_subprocess_output(r_ter) or "terrain3d falhou"
                                )
                                failures += 1
                                if not continue_on_error:
                                    raise click.Abort()
                            else:
                                dash.update_asset("terrain", "ok", str(ter_out))
                        else:
                            _timing_append({"id": "terrain"}, "terrain3d_sec", time.perf_counter() - t_ter)
                            _tr = ums_ter[0] if ums_ter else {"status": "error", "error": "terrain3d wave vazio"}
                            if _tr.get("status") in ("ok", "skipped"):
                                dash.update_asset("terrain", "ok", str(ter_out))
                            else:
                                dash.update_asset("terrain", "error", _tr.get("error") or "terrain3d falhou")
                                failures += 1
                                if not continue_on_error:
                                    raise click.Abort()

                    # --- Pre-phase: Skymap2D (scene-level, single-shot) ---
                    if with_skymap and skymap2d_bin:
                        sky_eff = _skymap2d_profile_effective(profile)
                        sky_out_dir = Path(profile.output_dir) / "sky"
                        sky_out_dir.mkdir(parents=True, exist_ok=True)
                        sky_out = sky_out_dir / "sky.png"
                        sky_item: dict[str, Any] = {
                            "id": "skymap",
                            "prompt": sky_eff.prompt or "",
                            "output": str(sky_out),
                        }
                        if sky_eff.width is not None:
                            sky_item["width"] = sky_eff.width
                        if sky_eff.height is not None:
                            sky_item["height"] = sky_eff.height
                        if sky_eff.steps is not None:
                            sky_item["steps"] = sky_eff.steps
                        if sky_eff.guidance_scale is not None:
                            sky_item["guidance"] = sky_eff.guidance_scale
                        if sky_eff.negative_prompt:
                            sky_item["negative_prompt"] = sky_eff.negative_prompt
                        if sky_eff.cfg_scale is not None:
                            sky_item["cfg_scale"] = sky_eff.cfg_scale
                        if sky_eff.lora_strength is not None:
                            sky_item["lora_strength"] = sky_eff.lora_strength
                        if sky_eff.preset:
                            sky_item["preset"] = sky_eff.preset
                        dash.set_phase("Skymap2D", 1)
                        dash.update_asset("skymap", "running", "A gerar skymap...")
                        t_sky = time.perf_counter()
                        ums_sky = run_skymap2d_wave_or_fallback(
                            [sky_item],
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                            width=int(sky_eff.width or 2048),
                            height=int(sky_eff.height or 1024),
                            steps=int(sky_eff.steps or 28),
                            guidance=float(sky_eff.guidance_scale or 3.5),
                            negative_prompt=sky_eff.negative_prompt,
                            cfg_scale=sky_eff.cfg_scale,
                            lora_strength=sky_eff.lora_strength,
                            preset=sky_eff.preset,
                        )
                        if ums_sky is None:
                            sky_argv = [skymap2d_bin, "generate", sky_eff.prompt or "", "-o", str(sky_out)]
                            _append_skymap2d_profile_args(sky_eff, sky_argv, quality=profile.generation)
                            if no_ums:
                                sky_argv.append("--no-ums")
                            r_sky = run_cmd(sky_argv, extra_env=child_env, cwd=manifest_dir)
                            _timing_append({"id": "skymap"}, "skymap2d_sec", time.perf_counter() - t_sky)
                            if r_sky.returncode != 0:
                                dash.update_asset(
                                    "skymap", "error", merge_subprocess_output(r_sky) or "skymap2d falhou"
                                )
                                failures += 1
                                if not continue_on_error:
                                    raise click.Abort()
                            else:
                                dash.update_asset("skymap", "ok", str(sky_out))
                        else:
                            _timing_append({"id": "skymap"}, "skymap2d_sec", time.perf_counter() - t_sky)
                            _sr = ums_sky[0] if ums_sky else {"status": "error", "error": "skymap2d wave vazio"}
                            if _sr.get("status") in ("ok", "skipped"):
                                dash.update_asset("skymap", "ok", str(sky_out))
                            else:
                                dash.update_asset("skymap", "error", _sr.get("error") or "skymap2d falhou")
                                failures += 1
                                if not continue_on_error:
                                    raise click.Abort()

                    # --- Pre-phase: Text2Icon (scene-level, UI icons) ---
                    if with_icons and text2icon_bin:
                        icon_eff = _text2icon_profile_effective(profile)
                        icon_out_dir = Path(profile.output_dir) / "icons"
                        icon_out_dir.mkdir(parents=True, exist_ok=True)
                        dash.set_phase("Text2Icon", len(icon_eff.prompts))
                        icon_items_d: list[dict[str, Any]] = []
                        for _icon_prompt in icon_eff.prompts:
                            _icon_slug = _safe_slug(_icon_prompt)
                            _icon_out = icon_out_dir / f"{_icon_slug}.png"
                            _ii: dict[str, Any] = {
                                "id": f"icon-{_icon_slug}",
                                "prompt": _icon_prompt,
                                "output": str(_icon_out),
                            }
                            if icon_eff.width is not None:
                                _ii["width"] = icon_eff.width
                            if icon_eff.height is not None:
                                _ii["height"] = icon_eff.height
                            if icon_eff.steps is not None:
                                _ii["steps"] = icon_eff.steps
                            if icon_eff.guidance_scale is not None:
                                _ii["guidance"] = icon_eff.guidance_scale
                            if icon_eff.transparent:
                                _ii["transparent"] = True
                            if icon_eff.model_id:
                                _ii["model_id"] = icon_eff.model_id
                            icon_items_d.append(_ii)
                        ums_icons = run_text2icon_wave_or_fallback(
                            icon_items_d,
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                            width=int(icon_eff.width or 512),
                            height=int(icon_eff.height or 512),
                            steps=int(icon_eff.steps or 2),
                            guidance=float(icon_eff.guidance_scale or 4.5),
                            transparent=bool(icon_eff.transparent),
                            on_progress=lambda r: dash.update_asset(
                                r.asset_id,
                                "ok" if r.status in ("ok", "skipped") else "error",
                                r.output or r.error or "",
                            ),
                        )
                        if ums_icons is None:
                            for _icon_idx, _icon_prompt in enumerate(icon_eff.prompts):
                                _icon_slug = _safe_slug(_icon_prompt)
                                _icon_out = icon_out_dir / f"{_icon_slug}.png"
                                _icon_label = f"ícone {_icon_idx + 1}/{len(icon_eff.prompts)}"
                                dash.update_asset(f"icon-{_icon_slug}", "running", f"A gerar {_icon_label}...")
                                _icon_argv = [text2icon_bin, "generate", _icon_prompt, "-o", str(_icon_out)]
                                _append_text2icon_profile_args(icon_eff, _icon_argv, quality=profile.generation)
                                if no_ums:
                                    _icon_argv.append("--no-ums")
                                _t_icon = time.perf_counter()
                                _r_icon = run_cmd(_icon_argv, extra_env=child_env, cwd=manifest_dir)
                                _timing_append(
                                    {"id": f"icon-{_icon_slug}"}, "text2icon_sec", time.perf_counter() - _t_icon
                                )
                                if _r_icon.returncode != 0:
                                    _icon_err = merge_subprocess_output(_r_icon) or "text2icon falhou"
                                    dash.update_asset(f"icon-{_icon_slug}", "error", _icon_err)
                                    failures += 1
                                    if not continue_on_error:
                                        raise click.Abort()
                                else:
                                    dash.update_asset(f"icon-{_icon_slug}", "ok", str(_icon_out))
                        else:
                            for _ir in ums_icons:
                                _aid = str(_ir.get("id", ""))
                                if _ir.get("status") in ("ok", "skipped"):
                                    dash.update_asset(_aid, "ok", str(_ir.get("output") or ""))
                                else:
                                    dash.update_asset(_aid, "error", _ir.get("error") or "text2icon falhou")
                                    failures += 1
                                    if not continue_on_error:
                                        raise click.Abort()

                    # --- Phase 1: 2D images ---
                    if skip_text2d:
                        f1_name = "PNGs existentes"
                    elif any_text2d_row and any_texture2d_row:
                        f1_name = "Text2D / Texture2D"
                    elif any_texture2d_row:
                        f1_name = "Texture2D"
                    else:
                        f1_name = "Text2D"
                    dash.set_phase(f1_name, len(rows))

                    text2d_batch_done_d: dict[int, dict[str, Any]] = {}
                    img_skipped_d: set[int] = set()
                    if not skip_text2d and any_text2d_row and text2d_bin:
                        t2d_items_d: list[dict[str, Any]] = []
                        t2d_indices_d: list[int] = []
                        for _bi, _brow in enumerate(rows):
                            if not _brow.generate_3d or _row_uses_texture2d(profile, _brow):
                                continue
                            _bimg, _ = _paths_for_row_manifest(profile, manifest_dir, _brow)
                            if not force and _bimg.is_file():
                                img_skipped_d.add(_bi)
                                continue
                            _bprompt = build_prompt(profile, preset, _brow, for_3d=False)
                            _bseed = _seed_for_manifest_row(profile, _brow)
                            _bitem_d: dict[str, Any] = {"id": _brow.id, "prompt": _bprompt, "output": str(_bimg)}
                            if _bseed is not None:
                                _bitem_d["seed"] = _bseed
                            t2d_items_d.append(_bitem_d)
                            t2d_indices_d.append(_bi)

                        if t2d_items_d:
                            t2d_manifest_path = batch_tmp / "text2d_manifest.json"
                            t2d_manifest_path.write_text(json.dumps(t2d_items_d, indent=2), encoding="utf-8")
                            _t2 = profile.text2d
                            _t2d_kw: dict[str, Any] = {
                                "manifest_dir": manifest_dir,
                                "no_ums": no_ums,
                                "ums_stream": ums_stream,
                                "gpu_ids": gpu_ids,
                                "quality": profile.generation,
                            }
                            if _t2:
                                if _t2.width is not None:
                                    _t2d_kw["width"] = _t2.width
                                if _t2.height is not None:
                                    _t2d_kw["height"] = _t2.height
                                if _t2.steps is not None:
                                    _t2d_kw["steps"] = _t2.steps
                                if _t2.guidance_scale is not None:
                                    _t2d_kw["guidance"] = _t2.guidance_scale
                            ums_t2d = run_text2d_wave_or_fallback(
                                t2d_items_d,
                                on_progress=lambda r: dash.feed_event(
                                    r.asset_id, "text2d", r.status, phase="generating"
                                ),
                                **_t2d_kw,
                            )
                            if ums_t2d is None:
                                batch_args_d = [text2d_bin, "generate-batch", str(t2d_manifest_path)]
                                if force:
                                    batch_args_d.append("--force")
                                _append_text2d_profile_args(profile, batch_args_d)
                                if gpu_ids:
                                    batch_args_d.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                                if no_ums:
                                    batch_args_d.append("--no-ums")
                                r_batch_d = run_cmd(batch_args_d, extra_env=child_env, cwd=manifest_dir)
                                for _line in (r_batch_d.stdout or "").strip().splitlines():
                                    try:
                                        _ir = json.loads(_line.strip())
                                    except (json.JSONDecodeError, AttributeError):
                                        continue
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(t2d_items_d, t2d_indices_d, strict=True):
                                        if _mi["id"] == _bid:
                                            text2d_batch_done_d[_bidx] = _ir
                                            break
                                if r_batch_d.returncode != 0:
                                    for _mi, _bidx in zip(t2d_items_d, t2d_indices_d, strict=True):
                                        if _bidx not in text2d_batch_done_d:
                                            text2d_batch_done_d[_bidx] = {
                                                "id": _mi["id"],
                                                "status": "error",
                                                "error": merge_subprocess_output(r_batch_d) or "text2d batch falhou",
                                            }
                            else:
                                for _ir in ums_t2d:
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(t2d_items_d, t2d_indices_d, strict=True):
                                        if _mi["id"] == _bid:
                                            text2d_batch_done_d[_bidx] = _ir
                                            break

                    texture2d_batch_done_d: dict[int, dict[str, Any]] = {}
                    if not skip_text2d and any_texture2d_row and texture2d_bin:
                        tex_items_d: list[dict[str, Any]] = []
                        tex_indices_d: list[int] = []
                        tt_wave = _texture2d_profile_effective(profile)
                        for _bi, _brow in enumerate(rows):
                            if not _brow.generate_3d or not _row_uses_texture2d(profile, _brow):
                                continue
                            if _bi in done_indices_d or _bi in img_skipped_d:
                                continue
                            _bimg, _ = _paths_for_row_manifest(profile, manifest_dir, _brow)
                            if not force and _bimg.is_file():
                                img_skipped_d.add(_bi)
                                continue
                            _bprompt = build_prompt(profile, preset, _brow, for_3d=False)
                            _bseed = _seed_for_manifest_row(profile, _brow)
                            _bitem_d = {"id": _brow.id, "prompt": _bprompt, "output": str(_bimg)}
                            if _bseed is not None:
                                _bitem_d["seed"] = _bseed
                            tex_items_d.append(_bitem_d)
                            tex_indices_d.append(_bi)
                        if tex_items_d:
                            ums_tex = run_texture2d_wave_or_fallback(
                                tex_items_d,
                                manifest_dir=manifest_dir,
                                no_ums=no_ums,
                                ums_stream=ums_stream,
                                gpu_ids=gpu_ids,
                                width=int(tt_wave.width or 512),
                                height=int(tt_wave.height or 512),
                                steps=int(tt_wave.steps or 20),
                                guidance=float(tt_wave.guidance_scale or 7.5),
                                negative_prompt=tt_wave.negative_prompt,
                                preset=tt_wave.preset,
                                model_id=tt_wave.model_id,
                                on_progress=lambda r: dash.feed_event(
                                    r.asset_id, "texture2d", r.status, phase="generating"
                                ),
                            )
                            if ums_tex is not None:
                                for _ir in ums_tex:
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(tex_items_d, tex_indices_d, strict=True):
                                        if _mi["id"] == _bid:
                                            texture2d_batch_done_d[_bidx] = _ir
                                            break

                    for idx, row in enumerate(rows):
                        img_final, _mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                        rec_d: dict[str, Any] = {
                            "id": row.id,
                            "status": "ok",
                            "image_path": _path_for_log(img_final, manifest_dir),
                            "mesh_path": None,
                            "parts_mesh_path": None,
                            "segmented_mesh_path": None,
                            "rig_mesh_path": None,
                            "animated_mesh_path": None,
                            "audio_path": None,
                            "error": None,
                            "audio_error": None,
                            "timings_sec": {},
                        }

                        if idx in done_indices_d:
                            results_d.append(rec_d)
                            append_log(rec_d)
                            dash.feed_line(json.dumps({"id": row.id, "status": "skipped"}))
                            dash.advance_phase()
                            continue

                        if idx in img_skipped_d:
                            results_d.append(rec_d)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_d.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec_d)
                            dash.advance_phase()
                            continue

                        if skip_text2d:
                            if row.generate_3d and with_3d and not img_final.is_file():
                                failures += 1
                                rec_d["status"] = "error"
                                rec_d["error"] = f"PNG em falta (esperado: {img_final})"
                                results_d.append(rec_d)
                                append_log(rec_d)
                                if not continue_on_error:
                                    raise click.Abort()
                                dash.advance_phase()
                                continue
                            results_d.append(rec_d)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_d.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec_d)
                            dash.advance_phase()
                            continue

                        if not row.generate_3d:
                            results_d.append(rec_d)
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if not defer_audio:
                                append_log(rec_d)
                            dash.advance_phase()
                            continue

                        if idx in text2d_batch_done_d:
                            ir_d = text2d_batch_done_d[idx]
                            if ir_d.get("status") in ("ok", "skipped"):
                                if ir_d.get("status") == "ok":
                                    _timing_append(rec_d, "image_text2d", ir_d.get("seconds", 0))
                                if not img_final.is_file():
                                    failures += 1
                                    rec_d["status"] = "error"
                                    rec_d["error"] = "text2d não produziu ficheiro de imagem"
                                    results_d.append(rec_d)
                                    append_log(rec_d)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    dash.advance_phase()
                                    continue
                            else:
                                failures += 1
                                rec_d["status"] = "error"
                                rec_d["error"] = ir_d.get("error", "text2d falhou")
                                results_d.append(rec_d)
                                append_log(rec_d)
                                if not continue_on_error:
                                    raise click.Abort()
                                dash.advance_phase()
                                continue
                            results_d.append(rec_d)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_d.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec_d)
                            dash.advance_phase()
                            continue

                        if idx in texture2d_batch_done_d:
                            ir_d = texture2d_batch_done_d[idx]
                            rec_d["texture2d_api"] = True
                            tt_line = _texture2d_profile_effective(profile)
                            if ir_d.get("status") in ("ok", "skipped"):
                                if ir_d.get("status") == "ok":
                                    _timing_append(rec_d, "image_texture2d", ir_d.get("seconds", 0))
                                if not img_final.is_file():
                                    failures += 1
                                    rec_d["status"] = "error"
                                    rec_d["error"] = "texture2d não produziu ficheiro de imagem"
                                    results_d.append(rec_d)
                                    append_log(rec_d)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    dash.advance_phase()
                                    continue
                                if tt_line.materialize:
                                    try:
                                        mat_bin = _resolve_materialize_bin_texture2d(tt_line)
                                    except FileNotFoundError as e:
                                        failures += 1
                                        rec_d["status"] = "error"
                                        rec_d["error"] = str(e)
                                        results_d.append(rec_d)
                                        append_log(rec_d)
                                        if not continue_on_error:
                                            raise click.Abort() from None
                                        dash.advance_phase()
                                        continue
                                    maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                    maps_dst.mkdir(parents=True, exist_ok=True)
                                    margv = _materialize_diffuse_argv(mat_bin, tt_line, img_final, maps_dst)
                                    t_mat_d = time.perf_counter()
                                    r_mat_d = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                    _timing_append(rec_d, "materialize_diffuse", time.perf_counter() - t_mat_d)
                                    if r_mat_d.returncode != 0:
                                        failures += 1
                                        rec_d["status"] = "error"
                                        rec_d["error"] = merge_subprocess_output(r_mat_d) or "materialize falhou"
                                        results_d.append(rec_d)
                                        append_log(rec_d)
                                        if not continue_on_error:
                                            raise click.Abort()
                                        dash.advance_phase()
                                        continue
                            else:
                                failures += 1
                                rec_d["status"] = "error"
                                rec_d["error"] = ir_d.get("error", "texture2d falhou")
                                results_d.append(rec_d)
                                append_log(rec_d)
                                if not continue_on_error:
                                    raise click.Abort()
                                dash.advance_phase()
                                continue
                            results_d.append(rec_d)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_d.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec_d)
                            dash.advance_phase()
                            continue

                        row_work_d = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}"
                        row_work_d.mkdir(parents=True, exist_ok=True)
                        try:
                            prompt = build_prompt(profile, preset, row, for_3d=False)
                            ext = profile.image_ext
                            img_tmp_d = row_work_d / f"ref.{ext}"
                            seed = _seed_for_manifest_row(profile, row)
                            tt_line = _texture2d_profile_effective(profile)
                            if _row_uses_texture2d(profile, row):
                                rec_d["texture2d_api"] = True
                                t2d_args = [texture2d_bin or "", "generate", prompt, "-o", str(img_tmp_d)]
                                if seed is not None:
                                    t2d_args.extend(["--seed", str(seed)])
                                _append_texture2d_profile_args(tt_line, t2d_args, quality=profile.generation)
                                if no_ums:
                                    t2d_args.append("--no-ums")
                                tool_fail = "texture2d falhou"
                                tool_empty = "texture2d não produziu ficheiro de imagem"
                            else:
                                t2d_args = [text2d_bin or "", "generate", prompt, "-o", str(img_tmp_d)]
                                if seed is not None:
                                    t2d_args.extend(["--seed", str(seed)])
                                _append_text2d_profile_args(profile, t2d_args)
                                if gpu_ids:
                                    t2d_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                                if no_ums:
                                    t2d_args.append("--no-ums")
                                tool_fail = "text2d falhou"
                                tool_empty = "text2d não produziu ficheiro de imagem"

                            tool_short = "texture2d" if _row_uses_texture2d(profile, row) else "text2d"

                            dash.feed_event(row.id, tool_short, "progress", phase="generating", percent=0)
                            t_img_d = time.perf_counter()
                            r2d = run_cmd_streaming(
                                t2d_args,
                                extra_env=child_env,
                                cwd=manifest_dir,
                                on_stdout_line=dash.feed_line,
                            )
                            elapsed_img = time.perf_counter() - t_img_d
                            _timing_append(
                                rec_d,
                                "image_texture2d" if _row_uses_texture2d(profile, row) else "image_text2d",
                                elapsed_img,
                            )
                            if r2d.returncode != 0:
                                failures += 1
                                err = merge_subprocess_output(r2d) or tool_fail
                                rec_d["status"] = "error"
                                rec_d["error"] = err
                                dash.feed_event(row.id, tool_short, "error", error=err)
                                results_d.append(rec_d)
                                append_log(rec_d)
                                if not continue_on_error:
                                    raise click.Abort()
                                continue

                            if not img_tmp_d.is_file():
                                failures += 1
                                rec_d["status"] = "error"
                                rec_d["error"] = tool_empty
                                dash.feed_event(row.id, tool_short, "error", error=tool_empty)
                                results_d.append(rec_d)
                                append_log(rec_d)
                                if not continue_on_error:
                                    raise click.Abort()
                                continue

                            _install_file(img_tmp_d, img_final)
                            dash.feed_event(row.id, tool_short, "ok", phase="generating", seconds=elapsed_img)

                            if _row_uses_texture2d(profile, row) and tt_line.materialize:
                                try:
                                    mat_bin = _resolve_materialize_bin_texture2d(tt_line)
                                except FileNotFoundError as e:
                                    failures += 1
                                    rec_d["status"] = "error"
                                    rec_d["error"] = str(e)
                                    results_d.append(rec_d)
                                    append_log(rec_d)
                                    if not continue_on_error:
                                        raise click.Abort() from None
                                    continue
                                maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                maps_dst.mkdir(parents=True, exist_ok=True)
                                margv = _materialize_diffuse_argv(mat_bin, tt_line, img_final, maps_dst)
                                dash.feed_event(row.id, "materialize", "progress", phase="pbr_maps", percent=0)
                                t_mat_d = time.perf_counter()
                                r_mat_d = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                elapsed_mat = time.perf_counter() - t_mat_d
                                _timing_append(rec_d, "materialize_diffuse", elapsed_mat)
                                if r_mat_d.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r_mat_d) or "materialize falhou"
                                    rec_d["status"] = "error"
                                    rec_d["error"] = err
                                    results_d.append(rec_d)
                                    append_log(rec_d)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    continue

                            results_d.append(rec_d)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_d.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec_d)
                        finally:
                            shutil.rmtree(row_work_d, ignore_errors=True)
                            dash.advance_phase()

                    # --- Phase 1b: Text2Sound ---
                    if not skip_audio and text2sound_bin and any(_row_wants_audio(r, has_audio_profile) for r in rows):
                        au_indices_d = [
                            i
                            for i, r in enumerate(rows)
                            if _row_wants_audio(r, has_audio_profile) and results_d[i]["status"] == "ok"
                        ]
                        if au_indices_d:
                            dash.set_phase("Text2Sound", len(au_indices_d))
                            ts_line = _text2sound_profile_effective(profile)
                            au_items_d: list[dict[str, Any]] = []
                            au_finals_d: dict[str, Path] = {}
                            for idx in au_indices_d:
                                row = rows[idx]
                                ext = (ts_line.audio_format or "wav").lower().strip().lstrip(".")
                                audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                                prompt_a = build_audio_prompt(profile, preset, row)
                                _ai: dict[str, Any] = {
                                    "id": row.id,
                                    "prompt": prompt_a,
                                    "output": str(audio_final),
                                }
                                seed_a = _seed_for_row(profile, f"{row.id}:audio")
                                if seed_a is not None:
                                    _ai["seed"] = seed_a
                                if row.audio_duration is not None:
                                    _ai["duration"] = row.audio_duration
                                elif ts_line.duration is not None:
                                    _ai["duration"] = ts_line.duration
                                if row.audio_steps is not None:
                                    _ai["steps"] = row.audio_steps
                                elif ts_line.steps is not None:
                                    _ai["steps"] = ts_line.steps
                                if row.audio_cfg_scale is not None:
                                    _ai["cfg_scale"] = row.audio_cfg_scale
                                elif ts_line.cfg_scale is not None:
                                    _ai["cfg_scale"] = ts_line.cfg_scale
                                cat = row.category if row.category else ts_line.category
                                if cat:
                                    _ai["category"] = cat
                                if ts_line.quality:
                                    _ai["quality"] = ts_line.quality
                                if ts_line.half_precision is not None:
                                    _ai["half_precision"] = ts_line.half_precision
                                au_items_d.append(_ai)
                                au_finals_d[row.id] = audio_final
                            ums_au = run_text2sound_wave_or_fallback(
                                au_items_d,
                                manifest_dir=manifest_dir,
                                no_ums=no_ums,
                                ums_stream=ums_stream,
                                gpu_ids=gpu_ids,
                                duration=float(ts_line.duration or 10.0),
                                steps=int(ts_line.steps or 100),
                                cfg_scale=float(ts_line.cfg_scale or 7.0),
                                half_precision=ts_line.half_precision,
                                quality=ts_line.quality,
                                category=ts_line.category,
                                on_progress=lambda r: dash.feed_event(
                                    r.asset_id, "text2sound", r.status, phase="generating"
                                ),
                            )
                            if ums_au is not None:
                                _au_by_id = {str(x.get("id")): x for x in ums_au}
                                for idx in au_indices_d:
                                    row = rows[idx]
                                    rec_d = results_d[idx]
                                    _ir = _au_by_id.get(row.id, {})
                                    if _ir.get("status") in ("ok", "skipped") and au_finals_d[row.id].is_file():
                                        if _ir.get("status") == "ok":
                                            _timing_append(rec_d, "text2sound", _ir.get("seconds", 0))
                                        rec_d["audio_path"] = _path_for_log(au_finals_d[row.id], manifest_dir)
                                        dash.feed_event(row.id, "text2sound", "ok", phase="generating")
                                    else:
                                        rec_d["audio_error"] = _ir.get("error") or "text2sound falhou"
                                        dash.feed_event(
                                            row.id,
                                            "text2sound",
                                            "error",
                                            phase="generating",
                                            error=rec_d["audio_error"],
                                        )
                                    dash.advance_phase()
                            else:
                                for idx in au_indices_d:
                                    row = rows[idx]
                                    rec_d = results_d[idx]
                                    ext = (ts_line.audio_format or "wav").lower().strip().lstrip(".")
                                    audio_tmp_d = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_audio.{ext}"
                                    audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                                    prompt_a = build_audio_prompt(profile, preset, row)
                                    argv_au = [text2sound_bin, "generate", prompt_a, "-o", str(audio_tmp_d)]
                                    seed_a = _seed_for_row(profile, f"{row.id}:audio")
                                    if seed_a is not None:
                                        argv_au.extend(["--seed", str(seed_a)])
                                    _text2sound_args_for_row(ts_line, row, argv_au)
                                    if no_ums:
                                        argv_au.append("--no-ums")
                                    dash.feed_event(row.id, "text2sound", "progress", phase="generating", percent=0)
                                    t_au_d = time.perf_counter()
                                    r_au_d = run_cmd_streaming(
                                        argv_au,
                                        extra_env=child_env,
                                        cwd=manifest_dir,
                                        on_stdout_line=dash.feed_line,
                                    )
                                    elapsed_au = time.perf_counter() - t_au_d
                                    _timing_append(rec_d, "text2sound", elapsed_au)
                                    if r_au_d.returncode == 0 and audio_tmp_d.is_file():
                                        _install_file(audio_tmp_d, audio_final)
                                        rec_d["audio_path"] = _path_for_log(audio_final, manifest_dir)
                                        dash.feed_event(
                                            row.id, "text2sound", "ok", phase="generating", seconds=elapsed_au
                                        )
                                    else:
                                        err_au = merge_subprocess_output(r_au_d) or "text2sound falhou"
                                        rec_d["audio_error"] = err_au
                                        dash.feed_event(row.id, "text2sound", "error", phase="generating", error=err_au)
                                    dash.advance_phase()

                    for idx in range(len(rows)):
                        row = rows[idx]
                        if not _row_wants_audio(row, has_audio_profile) or skip_audio or not text2sound_bin:
                            continue
                        if results_d[idx]["status"] != "ok":
                            continue
                        if idx in pending_3d_d:
                            continue
                        append_log(results_d[idx])

                    # --- Phase 2: Text3D ---
                    if with_3d and text3d_bin and pending_3d_d:
                        use_phased_d = any_row_wants_paint

                        master_q_d = MasterDeferQueue()

                        def _finalize_mesh_ok_d(
                            rec_m: dict[str, Any],
                            mesh_f: Path,
                            row_m: ManifestRow,
                        ) -> None:
                            nonlocal failures
                            if _post_text3d_mesh_extras(
                                profile,
                                row_m,
                                mesh_f,
                                rec_m,
                                manifest_dir,
                                child_env,
                                rigging3d_bin,
                                with_rig,
                                with_animate,
                                animator3d_bin=animator3d_bin,
                                has_rigging_profile=has_rigging_profile,
                                gpu_ids=gpu_ids,
                                with_lod=with_lod,
                                with_collision=with_collision,
                                on_progress_line=None,
                            ):
                                failures += 1

                        def _queue_master_d(
                            rec_m: dict[str, Any],
                            mesh_f: Path,
                            row_m: ManifestRow,
                        ) -> None:
                            master_q_d.enqueue(rec_m, mesh_f, row_m)

                        if use_phased_d:
                            # === SHAPE BATCH ===
                            shape_items_d: list[dict[str, Any]] = []
                            shape_idx_map_d: dict[str, int] = {}
                            shape_skipped_d: set[int] = set()

                            for idx in pending_3d_d:
                                row = rows[idx]
                                img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                mesh_clean_d = _clean_existing(mesh_f)
                                omni_d = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                                _t3_d = profile.text3d
                                if not prepare_shape_for_generation(
                                    mesh_shape,
                                    omni_d,
                                    force=force,
                                    category=row.category or None,
                                    clean_glb=mesh_clean_d,
                                    bounds_mode=getattr(_t3_d, "bounds_mode", None) if _t3_d else None,
                                    mc_level=row_mc_level(row, getattr(_t3_d, "mc_level", None) if _t3_d else None),
                                    seed=row.seed,
                                    octree_resolution=getattr(_t3_d, "octree_resolution", None) if _t3_d else None,
                                ):
                                    # Shape fresco / clean resume — não enfileirar.
                                    shape_skipped_d.add(idx)
                                    shape_idx_map_d[row.id] = idx
                                    continue
                                seed = _seed_for_manifest_row(profile, row)
                                item_d: dict[str, Any] = {"id": row.id, "image": str(img_f), "output": str(mesh_shape)}
                                if seed is not None:
                                    item_d["seed"] = seed
                                if row.seed is not None:
                                    item_d["seed_fingerprint"] = row.seed
                                if use_phased_d:
                                    item_d["skip_remesh"] = True
                                if row.category:
                                    item_d["category"] = row.category
                                item_d.update(omni_to_batch_item(omni_d))
                                t3 = profile.text3d
                                if t3 and should_optimize_text3d(t3) and row.category:
                                    fr = effective_face_ratio(profile, row)
                                    target = get_target_faces(row.category, face_ratio=fr)
                                    opts = optimize_text3d_for_target(target)
                                    item_d["steps"] = opts.steps
                                    item_d["octree_resolution"] = opts.octree_resolution
                                    item_d["num_chunks"] = opts.num_chunks
                                apply_row_text3d_overrides(item_d, row)
                                shape_items_d.append(item_d)
                                shape_idx_map_d[row.id] = idx

                            shape_ok_d: list[int] = []
                            finalized_d: set[int] = set()

                            if shape_items_d:
                                dash.set_phase("Text3D shape", len(shape_items_d))
                                shape_manifest_path = batch_tmp / "shape_manifest.json"
                                shape_manifest_path.write_text(json.dumps(shape_items_d, indent=2))

                                t3 = profile.text3d
                                _shape_kw: dict[str, Any] = {
                                    "manifest_dir": manifest_dir,
                                    "no_ums": no_ums,
                                    "ums_stream": ums_stream,
                                    "gpu_ids": gpu_ids,
                                    "quality": getattr(profile, "generation", None),
                                    "export_origin": t3.export_origin if t3 else "feet",
                                }
                                if t3:
                                    if t3.steps is not None:
                                        _shape_kw["steps"] = t3.steps
                                    if t3.guidance is not None:
                                        _shape_kw["guidance"] = t3.guidance
                                    if t3.octree_resolution is not None:
                                        _shape_kw["octree_resolution"] = t3.octree_resolution
                                    if t3.num_chunks is not None:
                                        _shape_kw["num_chunks"] = t3.num_chunks
                                    if t3.mc_level is not None:
                                        _shape_kw["mc_level"] = t3.mc_level
                                    if getattr(t3, "bounds_mode", None):
                                        _shape_kw["bounds_mode"] = t3.bounds_mode
                                    if getattr(t3, "sdnq_preset", None):
                                        _shape_kw["sdnq_preset"] = t3.sdnq_preset

                                ums_shape_results = run_shape_wave_or_fallback(
                                    shape_items_d,
                                    on_progress=lambda r: dash.feed_event(
                                        r.asset_id, "text3d", r.status, phase="shape"
                                    ),
                                    **_shape_kw,
                                )

                                if ums_shape_results is None:
                                    batch_args = [text3d_bin, "generate-batch", str(shape_manifest_path)]
                                    _append_quality(batch_args, profile)
                                    if force:
                                        batch_args.append("--force")
                                    if no_ums:
                                        batch_args.append("--no-ums")
                                    if t3:
                                        if not should_optimize_text3d(t3):
                                            explicit_h = (
                                                t3.steps is not None
                                                or t3.octree_resolution is not None
                                                or t3.num_chunks is not None
                                            )
                                            if t3.preset and not explicit_h:
                                                batch_args.extend(["--preset", t3.preset])
                                            if t3.steps is not None:
                                                batch_args.extend(["--steps", str(t3.steps)])
                                            if t3.octree_resolution is not None:
                                                batch_args.extend(["--octree-resolution", str(t3.octree_resolution)])
                                            if t3.num_chunks is not None:
                                                batch_args.extend(["--num-chunks", str(t3.num_chunks)])
                                        if t3.model_subfolder:
                                            batch_args.extend(["--model-subfolder", t3.model_subfolder])
                                        if t3.mc_level is not None:
                                            batch_args.extend(["--mc-level", str(t3.mc_level)])
                                        if getattr(t3, "bounds_mode", None):
                                            batch_args.extend(["--bounds-mode", str(t3.bounds_mode)])
                                        if t3.guidance is not None:
                                            batch_args.extend(["--guidance", str(t3.guidance)])
                                        if t3.allow_shared_gpu:
                                            batch_args.append("--allow-shared-gpu")
                                        _append_gpu_kill_flag(batch_args, t3.gpu_kill_others)
                                        if t3.full_gpu:
                                            batch_args.append("--t2d-full-gpu")
                                        batch_args.extend(["--export-origin", t3.export_origin])
                                    if gpu_ids:
                                        batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                                    r3d = run_cmd_streaming(
                                        batch_args,
                                        extra_env=child_env,
                                        cwd=manifest_dir,
                                        on_stdout_line=dash.feed_line,
                                    )
                                    jsonl_out = r3d.stdout.strip() if r3d.stdout else ""
                                    shape_item_results: list[dict[str, Any]] = []
                                    for line in jsonl_out.split("\n"):
                                        if not line.strip():
                                            continue
                                        try:
                                            shape_item_results.append(json.loads(line))
                                        except json.JSONDecodeError:
                                            continue
                                else:
                                    shape_item_results = ums_shape_results

                                for item_result in shape_item_results:
                                    item_id = item_result.get("id", "")
                                    idx = shape_idx_map_d.get(item_id)
                                    if idx is None:
                                        continue
                                    rec_d = results_d[idx]
                                    _st = item_result.get("status", "")

                                    if _st == "progress":
                                        continue

                                    if _st in ("ok", "skipped"):
                                        shape_ok_d.append(idx)
                                        if _st == "ok":
                                            _timing_append(rec_d, "text3d_shape", item_result.get("seconds", 0))
                                            rec_d["shape_faces"] = item_result.get("faces", 0)
                                    else:
                                        failures += 1
                                        rec_d["status"] = "error"
                                        rec_d["error"] = item_result.get("error", "text3d shape falhou")
                                        append_log(rec_d)
                                        if not continue_on_error:
                                            raise click.Abort()
                                    dash.advance_phase()

                            if shape_skipped_d:
                                dash.set_phase("Text3D shape", len(shape_skipped_d))
                                for idx in shape_skipped_d:
                                    shape_ok_d.append(idx)
                                    row = rows[idx]
                                    dash.feed_event(row.id, "text3d", "skipped", phase="shape")
                                    dash.advance_phase()

                            shape_ok_paint_d = [i for i in shape_ok_d if rows[i].generate_paint]

                            # === PAINT BATCH ===
                            _ps = (profile.paint3d.style or "hunyuan").strip().lower() if profile.paint3d else "hunyuan"

                            if shape_ok_paint_d:
                                if _ps in ("solid", "perlin"):
                                    dash.set_phase("Paint3D quick", len(shape_ok_paint_d))
                                    for idx in shape_ok_paint_d:
                                        row = rows[idx]
                                        rec_d = results_d[idx]
                                        img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                        mesh_painted = _painted_existing(mesh_f) or _painted_path(mesh_f)
                                        try:
                                            assert paint3d_bin is not None
                                            assert text3d_bin is not None
                                            mesh_paint = ensure_to_paint_for_paint(
                                                mesh_f,
                                                text3d_bin=text3d_bin,
                                                profile=profile,
                                                child_env=child_env,
                                                manifest_dir=manifest_dir,
                                                force=force,
                                                row=row,
                                            )
                                            tex_argv = _texture_subprocess_argv(
                                                paint3d_bin,
                                                profile,
                                                mesh_paint,
                                                img_f,
                                                mesh_painted,
                                                row_id=row.id,
                                                row=row,
                                                gpu_ids=gpu_ids,
                                            )
                                            dash.feed_event(row.id, "paint3d", "progress", phase="quick", percent=0)
                                            t_qp = time.perf_counter()
                                            r_qp = run_cmd_streaming(
                                                tex_argv,
                                                extra_env=child_env,
                                                cwd=manifest_dir,
                                                on_stdout_line=dash.feed_line,
                                            )
                                            elapsed_qp = time.perf_counter() - t_qp
                                            _timing_append(rec_d, "paint3d_quick", elapsed_qp)
                                            if r_qp.returncode != 0:
                                                failures += 1
                                                err = merge_subprocess_output(r_qp) or "paint3d quick falhou"
                                                rec_d["status"] = "error"
                                                rec_d["error"] = err
                                                dash.feed_event(row.id, "paint3d", "error", error=err)
                                                append_log(rec_d)
                                                if not continue_on_error:
                                                    raise click.Abort()
                                            elif not mesh_painted.is_file():
                                                failures += 1
                                                rec_d["status"] = "error"
                                                rec_d["error"] = "quick paint não produziu GLB"
                                                append_log(rec_d)
                                                if not continue_on_error:
                                                    raise click.Abort()
                                                dash.feed_event(row.id, "paint3d", "error", error="quick paint sem GLB")
                                            else:
                                                # mesh_painted already at correct path
                                                dash.feed_event(
                                                    row.id, "paint3d", "ok", phase="quick", seconds=elapsed_qp
                                                )
                                                _queue_master_d(rec_d, mesh_f, row)
                                                finalized_d.add(idx)
                                                append_log(rec_d)
                                                if not continue_on_error and rec_d["status"] == "error":
                                                    raise click.Abort()
                                        finally:
                                            dash.advance_phase()
                                else:
                                    # Hunyuan paint batch
                                    paint_items_d: list[dict[str, Any]] = []
                                    paint_idx_map_d: dict[str, int] = {}
                                    paint_skipped_d: set[int] = set()
                                    for idx in shape_ok_paint_d:
                                        row = rows[idx]
                                        rec_d = results_d[idx]
                                        img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                        mesh_painted = _painted_existing(mesh_f) or _painted_path(mesh_f)
                                        if not force and mesh_painted.is_file():
                                            paint_skipped_d.add(idx)
                                            paint_idx_map_d[row.id] = idx
                                            continue
                                        try:
                                            assert text3d_bin is not None
                                            mesh_paint = ensure_to_paint_for_paint(
                                                mesh_f,
                                                text3d_bin=text3d_bin,
                                                profile=profile,
                                                child_env=child_env,
                                                manifest_dir=manifest_dir,
                                                force=force,
                                                row=row,
                                            )
                                        except Exception as exc:
                                            failures += 1
                                            rec_d["status"] = "error"
                                            rec_d["error"] = f"topology-fix pré-paint: {exc}"
                                            append_log(rec_d)
                                            if not continue_on_error:
                                                raise click.Abort() from exc
                                            continue
                                        paint_items_d.append(
                                            {
                                                "id": row.id,
                                                "mesh": str(mesh_paint),
                                                "image": str(img_f),
                                                "output": str(mesh_painted),
                                            }
                                        )
                                        paint_idx_map_d[row.id] = idx

                                    if paint_items_d:
                                        dash.set_phase("Paint3D texture", len(paint_items_d))
                                        paint_manifest_path = batch_tmp / "paint_manifest.json"
                                        paint_manifest_path.write_text(json.dumps(paint_items_d, indent=2))

                                        p3 = profile.paint3d
                                        _paint_kw: dict[str, Any] = {
                                            "manifest_dir": manifest_dir,
                                            "no_ums": no_ums,
                                            "ums_stream": ums_stream,
                                            "gpu_ids": gpu_ids,
                                        }
                                        if p3:
                                            if p3.max_views is not None:
                                                _paint_kw["max_views"] = p3.max_views
                                            if p3.view_resolution is not None:
                                                _paint_kw["view_resolution"] = p3.view_resolution
                                            if p3.render_size is not None:
                                                _paint_kw["render_size"] = p3.render_size
                                            if p3.texture_size is not None:
                                                _paint_kw["texture_size"] = p3.texture_size
                                            if p3.bake_exp is not None:
                                                _paint_kw["bake_exp"] = p3.bake_exp
                                            _paint_kw["preserve_origin"] = p3.preserve_origin
                                            _paint_kw["smooth"] = p3.smooth
                                            if p3.smooth_passes is not None:
                                                _paint_kw["smooth_passes"] = p3.smooth_passes

                                        ums_paint_results = run_paint_wave_or_fallback(
                                            paint_items_d,
                                            on_progress=lambda r: dash.feed_event(
                                                r.asset_id, "paint3d", r.status, phase="texture"
                                            ),
                                            **_paint_kw,
                                        )

                                        if ums_paint_results is None:
                                            batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                                            _append_quality(batch_args, profile)
                                            if force:
                                                batch_args.append("--force")
                                            if no_ums:
                                                batch_args.append("--no-ums")
                                            t3 = profile.text3d
                                            if t3:
                                                if t3.allow_shared_gpu:
                                                    batch_args.append("--allow-shared-gpu")
                                                _append_gpu_kill_flag(batch_args, t3.gpu_kill_others)
                                            if p3:
                                                if p3.max_views is not None:
                                                    batch_args.extend(["--max-views", str(p3.max_views)])
                                                if p3.view_resolution is not None:
                                                    batch_args.extend(["--view-resolution", str(p3.view_resolution)])
                                                if p3.render_size is not None:
                                                    batch_args.extend(["--render-size", str(p3.render_size)])
                                                if p3.texture_size is not None:
                                                    batch_args.extend(["--texture-size", str(p3.texture_size)])
                                                if p3.bake_exp is not None:
                                                    batch_args.extend(["--bake-exp", str(p3.bake_exp)])
                                                if not p3.preserve_origin:
                                                    batch_args.append("--no-preserve-origin")
                                                else:
                                                    batch_args.append("--preserve-origin")
                                                if p3.smooth:
                                                    batch_args.append("--smooth")
                                                else:
                                                    batch_args.append("--no-smooth")
                                                if p3.smooth_passes is not None:
                                                    batch_args.extend(["--smooth-passes", str(p3.smooth_passes)])
                                            if gpu_ids:
                                                batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                                            r4d = run_cmd_streaming(
                                                batch_args,
                                                extra_env=child_env,
                                                cwd=manifest_dir,
                                                on_stdout_line=dash.feed_line,
                                            )
                                            paint_item_results: list[dict[str, Any]] = []
                                            for line in (r4d.stdout.strip() if r4d.stdout else "").split("\n"):
                                                if not line.strip():
                                                    continue
                                                try:
                                                    paint_item_results.append(json.loads(line))
                                                except json.JSONDecodeError:
                                                    continue
                                        else:
                                            paint_item_results = ums_paint_results

                                        for item_result in paint_item_results:
                                            item_id = item_result.get("id", "")
                                            idx = paint_idx_map_d.get(item_id)
                                            if idx is None:
                                                continue
                                            rec_d = results_d[idx]
                                            row = rows[idx]
                                            img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                            _pst = item_result.get("status", "")

                                            if _pst == "progress":
                                                continue

                                            if _pst in ("ok", "skipped"):
                                                if _pst == "ok":
                                                    _timing_append(
                                                        rec_d,
                                                        "paint3d_texture",
                                                        item_result.get("seconds", 0),
                                                    )
                                                _queue_master_d(rec_d, mesh_f, row)
                                                finalized_d.add(idx)
                                                append_log(rec_d)
                                                if not continue_on_error and rec_d["status"] == "error":
                                                    raise click.Abort()
                                            else:
                                                failures += 1
                                                rec_d["status"] = "error"
                                                rec_d["error"] = item_result.get("error", "paint3d texture falhou")
                                                append_log(rec_d)
                                                if not continue_on_error:
                                                    raise click.Abort()
                                            dash.advance_phase()

                                    if paint_skipped_d:
                                        dash.set_phase("Paint3D texture", len(paint_skipped_d))
                                        for idx in paint_skipped_d:
                                            row = rows[idx]
                                            rec_d = results_d[idx]
                                            img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                            dash.feed_event(row.id, "paint3d", "skipped", phase="texture")
                                            _queue_master_d(rec_d, mesh_f, row)
                                            finalized_d.add(idx)
                                            append_log(rec_d)
                                            if not continue_on_error and rec_d["status"] == "error":
                                                raise click.Abort()
                                            dash.advance_phase()

                            # Linhas só com shape (sem paint no manifest): seguir com mesh_shape
                            for idx in shape_ok_d:
                                row = rows[idx]
                                if row.generate_paint:
                                    continue
                                rec_d = results_d[idx]
                                if rec_d.get("status") == "error":
                                    continue
                                img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                if not mesh_shape.is_file():
                                    continue
                                rec_d["image_path"] = _path_for_log(img_f, manifest_dir)
                                _queue_master_d(rec_d, mesh_f, row)
                                finalized_d.add(idx)
                                append_log(rec_d)

                            # Master pipeline depois da wave GPU (paint/shape)
                            if master_q_d.items:
                                _n_master = len(master_q_d.items)
                                dash.set_phase("Master pipeline", _n_master)
                                master_q_d.drain(_finalize_mesh_ok_d)
                                for _ in range(_n_master):
                                    dash.advance_phase()

                            # === SIMPLIFY: bpy decimate (fallback text3d) after painting ===
                            painted_ok_d = [i for i in finalized_d if results_d[i]["status"] == "ok"]
                            if painted_ok_d:
                                dash.set_phase("Simplify", len(painted_ok_d))
                                for idx in painted_ok_d:
                                    row = rows[idx]
                                    rec_d = results_d[idx]
                                    _img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_f) or _painted_path(mesh_f)
                                    mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                    simplify_mesh = (
                                        mesh_painted
                                        if mesh_painted.is_file()
                                        else (mesh_shape if mesh_shape.is_file() else mesh_f)
                                    )
                                    dash.feed_event(row.id, "simplify", "progress", phase="decimating", percent=0)
                                    _simplify_to_target(
                                        simplify_mesh,
                                        row,
                                        text3d_bin,
                                        profile=profile,
                                        run_cmd=run_cmd,
                                        child_env=child_env,
                                        cwd=manifest_dir,
                                        manifest_dir=manifest_dir,
                                        rec=rec_d,
                                    )
                                    dash.feed_event(row.id, "simplify", "ok", phase="decimating")
                                    dash.advance_phase()

                            # === CATCH-UP: rig/animate/LOD/collision ===
                            needs_post_d = [i for i in pending_3d_d if i not in finalized_d]
                            if needs_post_d:
                                dash.set_phase("Pós-processamento", len(needs_post_d))
                                for idx in needs_post_d:
                                    row = rows[idx]
                                    rec_d = results_d[idx]
                                    img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_f) or _painted_path(mesh_f)
                                    mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                    actual_mesh = (
                                        mesh_painted
                                        if mesh_painted.is_file()
                                        else (mesh_shape if mesh_shape.is_file() else mesh_f)
                                    )
                                    if not actual_mesh.is_file():
                                        dash.advance_phase()
                                        continue
                                    rec_d["image_path"] = _path_for_log(img_f, manifest_dir)
                                    dash.feed_event(row.id, "post3d", "progress", phase="rig/animate", percent=0)
                                    _finalize_mesh_ok_d(rec_d, mesh_f, row)
                                    if rec_d["status"] == "ok":
                                        dash.feed_event(row.id, "post3d", "ok", phase="rig/animate")
                                    else:
                                        dash.feed_event(
                                            row.id,
                                            "post3d",
                                            "error",
                                            phase="rig/animate",
                                            error=rec_d.get("error", "post-processing falhou"),
                                        )
                                    append_log(rec_d)
                                    if not continue_on_error and rec_d["status"] == "error":
                                        raise click.Abort()
                                    dash.advance_phase()
                        else:
                            # Non-phased Text3D (per-row)
                            dash.set_phase("Text3D", len(pending_3d_d))
                            for idx in pending_3d_d:
                                row = rows[idx]
                                rec_d = results_d[idx]
                                img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                seed = _seed_for_manifest_row(profile, row)
                                t3d_args = _text3d_argv(
                                    text3d_bin,
                                    profile,
                                    img_f,
                                    mesh_shape,
                                    row,
                                    gpu_ids=gpu_ids,
                                    manifest_dir=manifest_dir,
                                )
                                if seed is not None:
                                    t3d_args.extend(["--seed", str(seed)])
                                if row.seed is not None:
                                    t3d_args.extend(["--seed-fingerprint", str(row.seed)])
                                dash.feed_event(row.id, "text3d", "progress", phase="generating", percent=0)
                                t_t3d = time.perf_counter()
                                r3d = run_cmd_streaming(
                                    t3d_args,
                                    extra_env=child_env,
                                    cwd=manifest_dir,
                                    on_stdout_line=dash.feed_line,
                                )
                                elapsed_t3d = time.perf_counter() - t_t3d
                                _timing_append(rec_d, "text3d", elapsed_t3d)
                                if r3d.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r3d) or "text3d falhou"
                                    rec_d["status"] = "error"
                                    rec_d["error"] = err
                                    dash.feed_event(row.id, "text3d", "error", error=err)
                                elif not mesh_shape.is_file():
                                    failures += 1
                                    rec_d["status"] = "error"
                                    rec_d["error"] = "text3d não produziu ficheiro GLB"
                                    dash.feed_event(row.id, "text3d", "error", error=rec_d["error"])
                                else:
                                    # mesh_shape already at correct path
                                    dash.feed_event(row.id, "text3d", "ok", phase="shape", seconds=elapsed_t3d)
                                    _simplify_to_target(
                                        mesh_shape,
                                        row,
                                        text3d_bin,
                                        profile=profile,
                                        run_cmd=run_cmd,
                                        child_env=child_env,
                                        cwd=manifest_dir,
                                        manifest_dir=manifest_dir,
                                        rec=rec_d,
                                    )
                                    if _post_text3d_mesh_extras(
                                        profile,
                                        row,
                                        mesh_shape,
                                        rec_d,
                                        manifest_dir,
                                        child_env,
                                        rigging3d_bin,
                                        with_rig,
                                        with_animate,
                                        animator3d_bin=animator3d_bin,
                                        has_rigging_profile=has_rigging_profile,
                                        gpu_ids=gpu_ids,
                                        with_lod=with_lod,
                                        with_collision=with_collision,
                                        on_progress_line=dash.feed_line,
                                    ):
                                        failures += 1
                                append_log(rec_d)
                                if not continue_on_error and rec_d["status"] == "error":
                                    raise click.Abort()
                                dash.advance_phase()

                    dash.finish()

                asset_pipelines: dict[str, list[str]] = {}
                for row in rows:
                    stages: list[str] = []
                    if not skip_text2d:
                        if _row_uses_texture2d(profile, row):
                            stages.append("Texture2D")
                        else:
                            stages.append("Text2D")
                    if any_audio_row and not skip_audio and _row_wants_audio(row, has_audio_profile):
                        stages.append("Text2Sound")
                    if with_3d and row.generate_3d:
                        if row.generate_paint:
                            stages.append("Text3D shape")
                            p3_style = (
                                (profile.paint3d.style or "hunyuan").strip().lower() if profile.paint3d else "hunyuan"
                            )
                            stages.append("Paint3D quick" if p3_style in ("solid", "perlin") else "Paint3D texture")
                        else:
                            stages.append("Text3D")
                        if with_rig and _row_wants_rig(row, has_rigging_profile):
                            stages.append("Rigging3D")
                        if with_animate and _row_wants_animate(row, with_rig, has_rigging_profile):
                            stages.append("Animator3D")
                    asset_pipelines[row.id] = stages

                app = BatchDashboard(
                    game_title=profile.title or "",
                    asset_ids=asset_ids,
                    pipeline_desc=pipeline_desc,
                    asset_pipelines=asset_pipelines,
                    batch_fn=_batch_fn,
                )
                app.run()
            else:
                # === Existing Progress bar flow (unchanged) ===
                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    BarColumn(bar_width=None),
                    TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                    TimeElapsedColumn(),
                    console=console,
                ) as progress:
                    # --- Pre-phase: Terrain3D (scene-level, single-shot) ---
                    if with_terrain and terrain3d_bin:
                        ter_eff = _terrain3d_profile_effective(profile)
                        ter_out_dir = Path(profile.output_dir) / "terrain"
                        ter_out_dir.mkdir(parents=True, exist_ok=True)
                        ter_out = ter_out_dir / "heightmap.png"
                        ter_item_p: dict[str, Any] = {"id": "terrain", "output": str(ter_out)}
                        if ter_eff.prompt:
                            ter_item_p["prompt"] = ter_eff.prompt
                        if ter_eff.seed is not None:
                            ter_item_p["seed"] = ter_eff.seed
                        if ter_eff.size is not None:
                            ter_item_p["size"] = ter_eff.size
                        if ter_eff.world_size is not None:
                            ter_item_p["world_size"] = ter_eff.world_size
                        if ter_eff.max_height is not None:
                            ter_item_p["max_height"] = ter_eff.max_height
                        if ter_eff.device:
                            ter_item_p["device"] = ter_eff.device
                        progress.console.print("[cyan]Terrain3D[/cyan] — a gerar terreno...")
                        t_ter = time.perf_counter()
                        ums_ter_p = run_terrain3d_wave_or_fallback(
                            [ter_item_p],
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                        )
                        if ums_ter_p is None:
                            ter_argv = [terrain3d_bin, "generate", "--output", str(ter_out)]
                            if ter_eff.prompt:
                                ter_argv.extend(["--prompt", ter_eff.prompt])
                            _append_terrain3d_profile_args(ter_eff, ter_argv)
                            if gpu_ids:
                                ter_argv.extend(["--device", f"cuda:{gpu_ids[0]}"])
                            if no_ums:
                                ter_argv.append("--no-ums")
                            r_ter = run_cmd(ter_argv, extra_env=child_env, cwd=manifest_dir)
                            _t_ter_s = time.perf_counter() - t_ter
                            if r_ter.returncode != 0:
                                failures += 1
                                _ter_err = merge_subprocess_output(r_ter) or ""
                                progress.console.print(f"[red]Terrain3D falhou[/red]: {_ter_err}")
                                if not continue_on_error:
                                    raise click.Abort()
                            else:
                                progress.console.print(f"[green]Terrain3D[/green] OK ({_t_ter_s:.1f}s) → {ter_out}")
                        else:
                            _t_ter_s = time.perf_counter() - t_ter
                            _tr = ums_ter_p[0] if ums_ter_p else {}
                            if _tr.get("status") in ("ok", "skipped"):
                                progress.console.print(f"[green]Terrain3D[/green] OK ({_t_ter_s:.1f}s) → {ter_out}")
                            else:
                                failures += 1
                                progress.console.print(
                                    f"[red]Terrain3D falhou[/red]: {_tr.get('error') or 'terrain3d falhou'}"
                                )
                                if not continue_on_error:
                                    raise click.Abort()

                    # --- Pre-phase: Skymap2D (scene-level, single-shot) ---
                    if with_skymap and skymap2d_bin:
                        sky_eff = _skymap2d_profile_effective(profile)
                        sky_out_dir = Path(profile.output_dir) / "sky"
                        sky_out_dir.mkdir(parents=True, exist_ok=True)
                        sky_out = sky_out_dir / "sky.png"
                        sky_item_p: dict[str, Any] = {
                            "id": "skymap",
                            "prompt": sky_eff.prompt or "",
                            "output": str(sky_out),
                        }
                        progress.console.print("[cyan]Skymap2D[/cyan] — a gerar skymap...")
                        t_sky = time.perf_counter()
                        ums_sky_p = run_skymap2d_wave_or_fallback(
                            [sky_item_p],
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                            width=int(sky_eff.width or 2048),
                            height=int(sky_eff.height or 1024),
                            steps=int(sky_eff.steps or 28),
                            guidance=float(sky_eff.guidance_scale or 3.5),
                            negative_prompt=sky_eff.negative_prompt,
                            cfg_scale=sky_eff.cfg_scale,
                            lora_strength=sky_eff.lora_strength,
                            preset=sky_eff.preset,
                        )
                        if ums_sky_p is None:
                            sky_argv = [skymap2d_bin, "generate", sky_eff.prompt or "", "-o", str(sky_out)]
                            _append_skymap2d_profile_args(sky_eff, sky_argv, quality=profile.generation)
                            if no_ums:
                                sky_argv.append("--no-ums")
                            r_sky = run_cmd(sky_argv, extra_env=child_env, cwd=manifest_dir)
                            _t_sky_s = time.perf_counter() - t_sky
                            if r_sky.returncode != 0:
                                failures += 1
                                _sky_err = merge_subprocess_output(r_sky) or ""
                                progress.console.print(f"[red]Skymap2D falhou[/red]: {_sky_err}")
                                if not continue_on_error:
                                    raise click.Abort()
                            else:
                                progress.console.print(f"[green]Skymap2D[/green] OK ({_t_sky_s:.1f}s) → {sky_out}")
                        else:
                            _t_sky_s = time.perf_counter() - t_sky
                            _sr = ums_sky_p[0] if ums_sky_p else {}
                            if _sr.get("status") in ("ok", "skipped"):
                                progress.console.print(f"[green]Skymap2D[/green] OK ({_t_sky_s:.1f}s) → {sky_out}")
                            else:
                                failures += 1
                                progress.console.print(
                                    f"[red]Skymap2D falhou[/red]: {_sr.get('error') or 'skymap2d falhou'}"
                                )
                                if not continue_on_error:
                                    raise click.Abort()

                    # --- Pre-phase: Text2Icon (scene-level, UI icons) ---
                    if with_icons and text2icon_bin:
                        icon_eff = _text2icon_profile_effective(profile)
                        icon_out_dir = Path(profile.output_dir) / "icons"
                        icon_out_dir.mkdir(parents=True, exist_ok=True)
                        progress.console.print(f"[cyan]Text2Icon[/cyan] — a gerar {len(icon_eff.prompts)} ícone(s)...")
                        icon_items_p = [
                            {
                                "id": f"icon-{_safe_slug(p)}",
                                "prompt": p,
                                "output": str(icon_out_dir / f"{_safe_slug(p)}.png"),
                            }
                            for p in icon_eff.prompts
                        ]
                        ums_icons_p = run_text2icon_wave_or_fallback(
                            icon_items_p,
                            manifest_dir=manifest_dir,
                            no_ums=no_ums,
                            ums_stream=ums_stream,
                            gpu_ids=gpu_ids,
                            width=int(icon_eff.width or 512),
                            height=int(icon_eff.height or 512),
                            steps=int(icon_eff.steps or 2),
                            guidance=float(icon_eff.guidance_scale or 4.5),
                            transparent=bool(icon_eff.transparent),
                        )
                        if ums_icons_p is None:
                            for _icon_prompt in icon_eff.prompts:
                                _icon_slug = _safe_slug(_icon_prompt)
                                _icon_out = icon_out_dir / f"{_icon_slug}.png"
                                _icon_argv = [text2icon_bin, "generate", _icon_prompt, "-o", str(_icon_out)]
                                _append_text2icon_profile_args(icon_eff, _icon_argv, quality=profile.generation)
                                if no_ums:
                                    _icon_argv.append("--no-ums")
                                _t_icon = time.perf_counter()
                                _r_icon = run_cmd(_icon_argv, extra_env=child_env, cwd=manifest_dir)
                                _t_icon_s = time.perf_counter() - _t_icon
                                if _r_icon.returncode != 0:
                                    failures += 1
                                    _icon_err = merge_subprocess_output(_r_icon) or ""
                                    progress.console.print(f"[red]Text2Icon falhou[/red] ({_icon_slug}): {_icon_err}")
                                    if not continue_on_error:
                                        raise click.Abort()
                                else:
                                    progress.console.print(
                                        f"  [green]✓[/green] {_icon_slug} ({_t_icon_s:.1f}s) → {_icon_out}"
                                    )
                        else:
                            for _ir in ums_icons_p:
                                if _ir.get("status") in ("ok", "skipped"):
                                    progress.console.print(f"  [green]✓[/green] {_ir.get('id')} → {_ir.get('output')}")
                                else:
                                    failures += 1
                                    progress.console.print(
                                        f"[red]Text2Icon falhou[/red] ({_ir.get('id')}): {_ir.get('error')}"
                                    )
                                    if not continue_on_error:
                                        raise click.Abort()

                    f1_label = "[cyan]Fase 1: PNGs existentes[/cyan]"
                    if not skip_text2d:
                        if any_texture2d_row and any_text2d_row:
                            f1_label = "[cyan]Fase 1: Text2D / Texture2D[/cyan]"
                        elif any_texture2d_row:
                            f1_label = "[cyan]Fase 1: Texture2D[/cyan]"
                        else:
                            f1_label = "[cyan]Fase 1: Text2D[/cyan]"
                    task1 = progress.add_task(f1_label, total=len(rows))
                    results: list[dict[str, Any]] = []
                    pending_3d_indices: list[int] = []

                    # --- Text2D batch: pre-generate all Text2D images in one subprocess ---
                    # --- Pre-classify rows for skip logic ---
                    done_indices: set[int] = set()
                    skip_img_indices: set[int] = set()
                    for _ci, _crow in enumerate(rows):
                        _ci_img, _ci_mesh = _paths_for_row_manifest(profile, manifest_dir, _crow)
                        _ci_rig_out = _rigging3d_output_path(
                            _ci_mesh, profile.rigging3d.output_suffix if profile.rigging3d else "_rigged"
                        )
                        _ci_anim_out = _animator3d_output_path(_ci_rig_out)
                        _ci_lod0 = _ci_mesh.parent / f"{_ci_mesh.stem}_lod0.glb"
                        _ci_coll = _ci_mesh.parent / f"{_ci_mesh.stem}_collision.glb"
                        _ci_state = _classify_row_state(
                            img_final=_ci_img,
                            mesh_final=_ci_mesh,
                            rig_out=_ci_rig_out,
                            anim_out=_ci_anim_out,
                            want_texture=_crow.generate_paint,
                            wants_rig=_row_wants_rig(_crow, has_rigging_profile),
                            wants_animate=_row_wants_animate(
                                _crow, _row_wants_rig(_crow, has_rigging_profile), has_rigging_profile
                            ),
                            wants_lod=_crow.generate_lod,
                            wants_collision=_crow.generate_collision,
                            lod0_path=_ci_lod0,
                            collision_path=_ci_coll,
                        )
                        if not force and _ci_state == _ROW_DONE:
                            done_indices.add(_ci)
                        elif not force and _ci_img.is_file():
                            skip_img_indices.add(_ci)

                    text2d_batch_done: dict[int, dict[str, Any]] = {}
                    if not skip_text2d and any_text2d_row and text2d_bin:
                        t2d_items: list[dict[str, Any]] = []
                        t2d_indices: list[int] = []
                        for _bi, _brow in enumerate(rows):
                            if not _brow.generate_3d or _row_uses_texture2d(profile, _brow):
                                continue
                            _bimg, _ = _paths_for_row_manifest(profile, manifest_dir, _brow)
                            if not force and _bimg.is_file():
                                continue
                            _bprompt = build_prompt(profile, preset, _brow, for_3d=False)
                            _bseed = _seed_for_manifest_row(profile, _brow)
                            _bitem: dict[str, Any] = {"id": _brow.id, "prompt": _bprompt, "output": str(_bimg)}
                            if _bseed is not None:
                                _bitem["seed"] = _bseed
                            t2d_items.append(_bitem)
                            t2d_indices.append(_bi)

                        if t2d_items:
                            t2d_manifest_path = batch_tmp / "text2d_manifest.json"
                            t2d_manifest_path.write_text(json.dumps(t2d_items, indent=2), encoding="utf-8")
                            progress.update(task1, description="[cyan]Text2D batch[/cyan]")
                            _t2p = profile.text2d
                            _t2d_kw_p: dict[str, Any] = {
                                "manifest_dir": manifest_dir,
                                "no_ums": no_ums,
                                "ums_stream": ums_stream,
                                "gpu_ids": gpu_ids,
                                "quality": profile.generation,
                            }
                            if _t2p:
                                if _t2p.width is not None:
                                    _t2d_kw_p["width"] = _t2p.width
                                if _t2p.height is not None:
                                    _t2d_kw_p["height"] = _t2p.height
                                if _t2p.steps is not None:
                                    _t2d_kw_p["steps"] = _t2p.steps
                                if _t2p.guidance_scale is not None:
                                    _t2d_kw_p["guidance"] = _t2p.guidance_scale
                            ums_t2d_p = run_text2d_wave_or_fallback(t2d_items, **_t2d_kw_p)
                            if ums_t2d_p is None:
                                batch_args = [text2d_bin, "generate-batch", str(t2d_manifest_path)]
                                if force:
                                    batch_args.append("--force")
                                _append_text2d_profile_args(profile, batch_args)
                                if gpu_ids:
                                    batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                                if no_ums:
                                    batch_args.append("--no-ums")
                                r_batch = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                                for _line in (r_batch.stdout or "").strip().splitlines():
                                    try:
                                        _ir = json.loads(_line.strip())
                                    except (json.JSONDecodeError, AttributeError):
                                        continue
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(t2d_items, t2d_indices, strict=True):
                                        if _mi["id"] == _bid:
                                            text2d_batch_done[_bidx] = _ir
                                            break
                                if r_batch.returncode != 0:
                                    for _mi, _bidx in zip(t2d_items, t2d_indices, strict=True):
                                        if _bidx not in text2d_batch_done:
                                            text2d_batch_done[_bidx] = {
                                                "id": _mi["id"],
                                                "status": "error",
                                                "error": merge_subprocess_output(r_batch) or "text2d batch falhou",
                                            }
                            else:
                                for _ir in ums_t2d_p:
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(t2d_items, t2d_indices, strict=True):
                                        if _mi["id"] == _bid:
                                            text2d_batch_done[_bidx] = _ir
                                            break

                    texture2d_batch_done: dict[int, dict[str, Any]] = {}
                    if not skip_text2d and any_texture2d_row and texture2d_bin:
                        tex_items_p: list[dict[str, Any]] = []
                        tex_indices_p: list[int] = []
                        tt_wave_p = _texture2d_profile_effective(profile)
                        for _bi, _brow in enumerate(rows):
                            if not _brow.generate_3d or not _row_uses_texture2d(profile, _brow):
                                continue
                            if _bi in done_indices or _bi in skip_img_indices:
                                continue
                            _bimg, _ = _paths_for_row_manifest(profile, manifest_dir, _brow)
                            if not force and _bimg.is_file():
                                continue
                            _bprompt = build_prompt(profile, preset, _brow, for_3d=False)
                            _bseed = _seed_for_manifest_row(profile, _brow)
                            _bitem = {"id": _brow.id, "prompt": _bprompt, "output": str(_bimg)}
                            if _bseed is not None:
                                _bitem["seed"] = _bseed
                            tex_items_p.append(_bitem)
                            tex_indices_p.append(_bi)
                        if tex_items_p:
                            ums_tex_p = run_texture2d_wave_or_fallback(
                                tex_items_p,
                                manifest_dir=manifest_dir,
                                no_ums=no_ums,
                                ums_stream=ums_stream,
                                gpu_ids=gpu_ids,
                                width=int(tt_wave_p.width or 512),
                                height=int(tt_wave_p.height or 512),
                                steps=int(tt_wave_p.steps or 20),
                                guidance=float(tt_wave_p.guidance_scale or 7.5),
                                negative_prompt=tt_wave_p.negative_prompt,
                                preset=tt_wave_p.preset,
                                model_id=tt_wave_p.model_id,
                            )
                            if ums_tex_p is not None:
                                for _ir in ums_tex_p:
                                    _bid = _ir.get("id", "")
                                    for _mi, _bidx in zip(tex_items_p, tex_indices_p, strict=True):
                                        if _mi["id"] == _bid:
                                            texture2d_batch_done[_bidx] = _ir
                                            break

                    for idx, row in enumerate(rows):
                        img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                        rec: dict[str, Any] = {
                            "id": row.id,
                            "status": "ok",
                            "image_path": _path_for_log(img_final, manifest_dir),
                            "mesh_path": None,
                            "parts_mesh_path": None,
                            "segmented_mesh_path": None,
                            "rig_mesh_path": None,
                            "animated_mesh_path": None,
                            "audio_path": None,
                            "error": None,
                            "audio_error": None,
                            "timings_sec": {},
                        }

                        if idx in done_indices:
                            progress.update(task1, description=f"[dim]⏭ {row.id} — já completo[/dim]")
                            results.append(rec)
                            append_log(rec)
                            progress.advance(task1)
                            continue

                        if idx in skip_img_indices:
                            progress.update(task1, description=f"[dim]⏭ {row.id} — PNG já existe[/dim]")
                            results.append(rec)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_indices.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec)
                            progress.advance(task1)
                            continue

                        if skip_text2d:
                            progress.update(
                                task1,
                                description=f"[cyan]{row.id}[/cyan] · PNG",
                            )
                            if row.generate_3d and with_3d and not img_final.is_file():
                                failures += 1
                                rec["status"] = "error"
                                rec["error"] = f"PNG em falta (esperado: {img_final})"
                                console.print(f"[red]PNG em falta[/red] {row.id}: {img_final}")
                                results.append(rec)
                                append_log(rec)
                                if not continue_on_error:
                                    raise click.Abort()
                                progress.advance(task1)
                                continue
                            results.append(rec)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_indices.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec)
                            progress.advance(task1)
                            continue

                        if not row.generate_3d:
                            progress.update(task1, description=f"[cyan]{row.id}[/cyan] · skip 2D")
                            results.append(rec)
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if not defer_audio:
                                append_log(rec)
                            progress.advance(task1)
                            continue

                        if idx in text2d_batch_done:
                            progress.update(task1, description=f"[cyan]{row.id}[/cyan] · Text2D (batch)")
                            ir = text2d_batch_done[idx]
                            if ir.get("status") in ("ok", "skipped"):
                                if ir.get("status") == "ok":
                                    _timing_append(rec, "image_text2d", ir.get("seconds", 0))
                                if not img_final.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "text2d não produziu ficheiro de imagem"
                                    console.print(f"[red]text2d sem saída[/red] {row.id}")
                                    results.append(rec)
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    progress.advance(task1)
                                    continue
                            else:
                                failures += 1
                                rec["status"] = "error"
                                rec["error"] = ir.get("error", "text2d falhou")
                                console.print(f"[red]text2d falhou[/red] {row.id}: {rec['error']}")
                                results.append(rec)
                                append_log(rec)
                                if not continue_on_error:
                                    raise click.Abort()
                                progress.advance(task1)
                                continue
                            results.append(rec)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_indices.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec)
                            progress.advance(task1)
                            continue

                        if idx in texture2d_batch_done:
                            progress.update(task1, description=f"[cyan]{row.id}[/cyan] · Texture2D (UMS)")
                            ir = texture2d_batch_done[idx]
                            rec["texture2d_api"] = True
                            tt_line = _texture2d_profile_effective(profile)
                            if ir.get("status") in ("ok", "skipped"):
                                if ir.get("status") == "ok":
                                    _timing_append(rec, "image_texture2d", ir.get("seconds", 0))
                                if not img_final.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "texture2d não produziu ficheiro de imagem"
                                    results.append(rec)
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    progress.advance(task1)
                                    continue
                                if tt_line.materialize:
                                    try:
                                        mat_bin = _resolve_materialize_bin_texture2d(tt_line)
                                        maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                        maps_dst.mkdir(parents=True, exist_ok=True)
                                        margv = _materialize_diffuse_argv(mat_bin, tt_line, img_final, maps_dst)
                                        r_mat = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                        if r_mat.returncode != 0:
                                            failures += 1
                                            rec["status"] = "error"
                                            rec["error"] = merge_subprocess_output(r_mat) or "materialize falhou"
                                            results.append(rec)
                                            append_log(rec)
                                            if not continue_on_error:
                                                raise click.Abort()
                                            progress.advance(task1)
                                            continue
                                    except FileNotFoundError as e:
                                        failures += 1
                                        rec["status"] = "error"
                                        rec["error"] = str(e)
                                        results.append(rec)
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort() from None
                                        progress.advance(task1)
                                        continue
                            else:
                                failures += 1
                                rec["status"] = "error"
                                rec["error"] = ir.get("error", "texture2d falhou")
                                results.append(rec)
                                append_log(rec)
                                if not continue_on_error:
                                    raise click.Abort()
                                progress.advance(task1)
                                continue
                            results.append(rec)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_indices.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec)
                            progress.advance(task1)
                            continue

                        gen_label = "Texture2D" if _row_uses_texture2d(profile, row) else "Text2D"
                        progress.update(task1, description=f"[cyan]{row.id}[/cyan] · {gen_label}")
                        row_work = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}"
                        row_work.mkdir(parents=True, exist_ok=True)
                        try:
                            prompt = build_prompt(profile, preset, row, for_3d=False)
                            ext = profile.image_ext
                            img_tmp = row_work / f"ref.{ext}"
                            seed = _seed_for_manifest_row(profile, row)
                            tt_line = _texture2d_profile_effective(profile)
                            if _row_uses_texture2d(profile, row):
                                rec["texture2d_api"] = True
                                t2d_args = [
                                    texture2d_bin or "",
                                    "generate",
                                    prompt,
                                    "-o",
                                    str(img_tmp),
                                ]
                                if seed is not None:
                                    t2d_args.extend(["--seed", str(seed)])
                                _append_texture2d_profile_args(tt_line, t2d_args, quality=profile.generation)
                                if no_ums:
                                    t2d_args.append("--no-ums")
                                tool_fail = "texture2d falhou"
                                tool_empty = "texture2d não produziu ficheiro de imagem"
                                tool_short = "texture2d"
                            else:
                                t2d_args = [
                                    text2d_bin or "",
                                    "generate",
                                    prompt,
                                    "-o",
                                    str(img_tmp),
                                ]
                                if seed is not None:
                                    t2d_args.extend(["--seed", str(seed)])
                                _append_text2d_profile_args(profile, t2d_args)
                                if gpu_ids:
                                    t2d_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                                if no_ums:
                                    t2d_args.append("--no-ums")
                                tool_fail = "text2d falhou"
                                tool_empty = "text2d não produziu ficheiro de imagem"
                                tool_short = "text2d"

                            t_img = time.perf_counter()
                            r2 = run_cmd(t2d_args, extra_env=child_env, cwd=manifest_dir)
                            _timing_append(
                                rec,
                                "image_texture2d" if _row_uses_texture2d(profile, row) else "image_text2d",
                                time.perf_counter() - t_img,
                            )
                            if r2.returncode != 0:
                                failures += 1
                                err = merge_subprocess_output(r2) or tool_fail
                                rec["status"] = "error"
                                rec["error"] = err
                                preview = merge_subprocess_output(r2, max_chars=4000) or err
                                console.print(f"[red]{tool_short} falhou[/red] {row.id}: {preview}")
                                results.append(rec)
                                append_log(rec)
                                if not continue_on_error:
                                    raise click.Abort()
                                continue

                            if not img_tmp.is_file():
                                failures += 1
                                rec["status"] = "error"
                                rec["error"] = tool_empty
                                console.print(f"[red]{tool_short} sem saída[/red] {row.id}")
                                results.append(rec)
                                append_log(rec)
                                if not continue_on_error:
                                    raise click.Abort()
                                continue

                            _install_file(img_tmp, img_final)

                            if _row_uses_texture2d(profile, row) and tt_line.materialize:
                                try:
                                    mat_bin = _resolve_materialize_bin_texture2d(tt_line)
                                except FileNotFoundError as e:
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = str(e)
                                    console.print(f"[red]materialize não encontrado[/red] {row.id}: {e}")
                                    results.append(rec)
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort() from None
                                    continue
                                maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                maps_dst.mkdir(parents=True, exist_ok=True)
                                margv = _materialize_diffuse_argv(mat_bin, tt_line, img_final, maps_dst)
                                t_mat = time.perf_counter()
                                r_mat = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                _timing_append(rec, "materialize_diffuse", time.perf_counter() - t_mat)
                                if r_mat.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r_mat) or "materialize falhou"
                                    rec["status"] = "error"
                                    rec["error"] = err
                                    preview = merge_subprocess_output(r_mat, max_chars=4000) or err
                                    console.print(f"[red]materialize falhou[/red] {row.id}: {preview}")
                                    results.append(rec)
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                    continue

                            results.append(rec)
                            do_3d = with_3d and row.generate_3d
                            defer_audio = (
                                _row_wants_audio(row, has_audio_profile) and not skip_audio and bool(text2sound_bin)
                            )
                            if do_3d and text3d_bin:
                                pending_3d_indices.append(idx)
                            else:
                                if not defer_audio:
                                    append_log(rec)
                        finally:
                            shutil.rmtree(row_work, ignore_errors=True)
                            progress.advance(task1)

                    progress.update(task1, description=f"[cyan]Fase 1 concluída[/cyan] ({len(rows)} itens)")

                    if not skip_audio and text2sound_bin and any(_row_wants_audio(r, has_audio_profile) for r in rows):
                        au_indices = [
                            i
                            for i, r in enumerate(rows)
                            if _row_wants_audio(r, has_audio_profile) and results[i]["status"] == "ok"
                        ]
                        if au_indices:
                            task_au = progress.add_task(
                                "[cyan]Fase 1b: Text2Sound[/cyan]",
                                total=len(au_indices),
                            )
                            ts_line = _text2sound_profile_effective(profile)
                            au_items_p: list[dict[str, Any]] = []
                            au_finals_p: dict[str, Path] = {}
                            for idx in au_indices:
                                row = rows[idx]
                                audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                                prompt_a = build_audio_prompt(profile, preset, row)
                                _ai = {"id": row.id, "prompt": prompt_a, "output": str(audio_final)}
                                seed_a = _seed_for_row(profile, f"{row.id}:audio")
                                if seed_a is not None:
                                    _ai["seed"] = seed_a
                                if row.audio_duration is not None:
                                    _ai["duration"] = row.audio_duration
                                elif ts_line.duration is not None:
                                    _ai["duration"] = ts_line.duration
                                if ts_line.quality:
                                    _ai["quality"] = ts_line.quality
                                if ts_line.half_precision is not None:
                                    _ai["half_precision"] = ts_line.half_precision
                                au_items_p.append(_ai)
                                au_finals_p[row.id] = audio_final
                            ums_au_p = run_text2sound_wave_or_fallback(
                                au_items_p,
                                manifest_dir=manifest_dir,
                                no_ums=no_ums,
                                ums_stream=ums_stream,
                                gpu_ids=gpu_ids,
                                duration=float(ts_line.duration or 10.0),
                                steps=int(ts_line.steps or 100),
                                cfg_scale=float(ts_line.cfg_scale or 7.0),
                                half_precision=ts_line.half_precision,
                                quality=ts_line.quality,
                                category=ts_line.category,
                            )
                            if ums_au_p is not None:
                                _au_by_id = {str(x.get("id")): x for x in ums_au_p}
                                for idx in au_indices:
                                    row = rows[idx]
                                    rec = results[idx]
                                    progress.update(task_au, description=f"[cyan]{row.id}[/cyan] · Text2Sound")
                                    _ir = _au_by_id.get(row.id, {})
                                    if _ir.get("status") in ("ok", "skipped") and au_finals_p[row.id].is_file():
                                        if _ir.get("status") == "ok":
                                            _timing_append(rec, "text2sound", _ir.get("seconds", 0))
                                        rec["audio_path"] = _path_for_log(au_finals_p[row.id], manifest_dir)
                                    else:
                                        rec["audio_error"] = _ir.get("error") or "text2sound falhou"
                                        console.print(f"[red]text2sound falhou[/red] {row.id}: {rec['audio_error']}")
                                    progress.advance(task_au)
                            else:
                                for idx in au_indices:
                                    row = rows[idx]
                                    rec = results[idx]
                                    progress.update(task_au, description=f"[cyan]{row.id}[/cyan] · Text2Sound")
                                    ext = (ts_line.audio_format or "wav").lower().strip().lstrip(".")
                                    audio_tmp = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_audio.{ext}"
                                    audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                                    prompt_a = build_audio_prompt(profile, preset, row)
                                    argv_au = [text2sound_bin, "generate", prompt_a, "-o", str(audio_tmp)]
                                    seed_a = _seed_for_row(profile, f"{row.id}:audio")
                                    if seed_a is not None:
                                        argv_au.extend(["--seed", str(seed_a)])
                                    _text2sound_args_for_row(ts_line, row, argv_au)
                                    if no_ums:
                                        argv_au.append("--no-ums")
                                    t_au = time.perf_counter()
                                    r_au = run_cmd(argv_au, extra_env=child_env, cwd=manifest_dir)
                                    _timing_append(rec, "text2sound", time.perf_counter() - t_au)
                                    if r_au.returncode == 0 and audio_tmp.is_file():
                                        _install_file(audio_tmp, audio_final)
                                        rec["audio_path"] = _path_for_log(audio_final, manifest_dir)
                                    else:
                                        err_au = merge_subprocess_output(r_au) or "text2sound falhou"
                                        rec["audio_error"] = err_au
                                        preview_au = merge_subprocess_output(r_au, max_chars=4000) or err_au
                                        console.print(f"[red]text2sound falhou[/red] {row.id}: {preview_au}")
                                    progress.advance(task_au)

                    for idx, row in enumerate(rows):
                        if not _row_wants_audio(row, has_audio_profile) or skip_audio or not text2sound_bin:
                            continue
                        if results[idx]["status"] != "ok":
                            continue
                        if idx in pending_3d_indices:
                            continue
                        append_log(results[idx])

                    if with_3d and text3d_bin and pending_3d_indices:
                        console.print(
                            Panel(
                                "[bold]Fase 2 (Text3D)[/bold]: fecha o Godot e apps que usem a GPU; "
                                "NVML/`nvidia-smi` deve mostrar VRAM livre. Em ~6 GB, o hw-auto "
                                "aplica SDNQ INT4 automaticamente. Usa [bold]--preset fast[/bold] "
                                "ou reduz steps/octree/chunks se der OOM. "
                                "Com [bold]text3d.texture[/bold], o batch corre: shape (text3d) → "
                                "paint3d texture (PBR no GLB), libertando VRAM entre passos.",
                                border_style="yellow",
                                title="Antes do 3D",
                            )
                        )
                        use_phased = any_row_wants_paint

                        def _finalize_mesh_ok(
                            rec: dict[str, Any],
                            mesh_final: Path,
                            row: ManifestRow,
                        ) -> None:
                            nonlocal failures
                            if _post_text3d_mesh_extras(
                                profile,
                                row,
                                mesh_final,
                                rec,
                                manifest_dir,
                                child_env,
                                rigging3d_bin,
                                with_rig,
                                with_animate,
                                animator3d_bin=animator3d_bin,
                                has_rigging_profile=has_rigging_profile,
                                gpu_ids=gpu_ids,
                                with_lod=with_lod,
                                with_collision=with_collision,
                                on_progress_line=None,
                            ):
                                failures += 1

                        master_q = MasterDeferQueue()

                        def _queue_master(
                            rec: dict[str, Any],
                            mesh_final: Path,
                            row: ManifestRow,
                        ) -> None:
                            from .paths import _canonical_mesh_final

                            master_q.enqueue(rec, _canonical_mesh_final(mesh_final), row)

                        if use_phased:
                            # === SHAPE BATCH ===
                            shape_manifest_items: list[dict[str, Any]] = []
                            shape_idx_map: dict[str, int] = {}
                            shape_ok: list[int] = []
                            finalized_indices: set[int] = set()

                            for idx in pending_3d_indices:
                                row = rows[idx]
                                img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = _shape_existing(mesh_final) or _shape_path(mesh_final)
                                mesh_clean = _clean_existing(mesh_final)

                                omni_row = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                                _t3_p = profile.text3d
                                if not prepare_shape_for_generation(
                                    mesh_shape,
                                    omni_row,
                                    force=force,
                                    category=row.category or None,
                                    clean_glb=mesh_clean,
                                    bounds_mode=getattr(_t3_p, "bounds_mode", None) if _t3_p else None,
                                    mc_level=row_mc_level(row, getattr(_t3_p, "mc_level", None) if _t3_p else None),
                                    seed=row.seed,
                                    octree_resolution=getattr(_t3_p, "octree_resolution", None) if _t3_p else None,
                                ):
                                    # Shape fresco / clean resume — seguir paint sem generate-batch.
                                    shape_ok.append(idx)
                                    finalized_indices.add(idx)
                                    continue

                                seed = _seed_for_manifest_row(profile, row)

                                item: dict[str, Any] = {
                                    "id": row.id,
                                    "image": str(img_final),
                                    "output": str(mesh_shape),
                                }
                                if seed is not None:
                                    item["seed"] = seed
                                if row.seed is not None:
                                    item["seed_fingerprint"] = row.seed
                                item["skip_remesh"] = True
                                if row.category:
                                    item["category"] = row.category
                                item.update(omni_to_batch_item(omni_row))

                                # Per-item params when dynamic optimization is active
                                t3 = profile.text3d
                                if t3 and should_optimize_text3d(t3) and row.category:
                                    fr = effective_face_ratio(profile, row)
                                    target = get_target_faces(row.category, face_ratio=fr)
                                    opts = optimize_text3d_for_target(target)
                                    item["steps"] = opts.steps
                                    item["octree_resolution"] = opts.octree_resolution
                                    item["num_chunks"] = opts.num_chunks
                                apply_row_text3d_overrides(item, row)

                                shape_manifest_items.append(item)
                                shape_idx_map[row.id] = idx

                            if shape_manifest_items:
                                task_shape = progress.add_task(
                                    "[cyan]Text3D: generate-batch (shape)[/cyan]",
                                    total=len(shape_manifest_items),
                                )
                                shape_manifest_path = batch_tmp / "shape_manifest.json"
                                shape_manifest_path.write_text(json.dumps(shape_manifest_items, indent=2))

                                t3 = profile.text3d
                                _shape_kw: dict[str, Any] = {
                                    "manifest_dir": manifest_dir,
                                    "no_ums": no_ums,
                                    "ums_stream": ums_stream,
                                    "gpu_ids": gpu_ids,
                                    "quality": getattr(profile, "generation", None),
                                    "export_origin": t3.export_origin if t3 else "feet",
                                }
                                if t3:
                                    if t3.steps is not None:
                                        _shape_kw["steps"] = t3.steps
                                    if t3.guidance is not None:
                                        _shape_kw["guidance"] = t3.guidance
                                    if t3.octree_resolution is not None:
                                        _shape_kw["octree_resolution"] = t3.octree_resolution
                                    if t3.num_chunks is not None:
                                        _shape_kw["num_chunks"] = t3.num_chunks
                                    if t3.mc_level is not None:
                                        _shape_kw["mc_level"] = t3.mc_level
                                    if getattr(t3, "bounds_mode", None):
                                        _shape_kw["bounds_mode"] = t3.bounds_mode
                                    if getattr(t3, "sdnq_preset", None):
                                        _shape_kw["sdnq_preset"] = t3.sdnq_preset

                                ums_shape_results = run_shape_wave_or_fallback(
                                    shape_manifest_items,
                                    **_shape_kw,
                                )

                                t_shape_total = time.perf_counter()
                                r3 = None
                                if ums_shape_results is None:
                                    batch_args = [text3d_bin, "generate-batch", str(shape_manifest_path)]
                                    _append_quality(batch_args, profile)
                                    if force:
                                        batch_args.append("--force")
                                    if no_ums:
                                        batch_args.append("--no-ums")
                                    if t3:
                                        # Global params only when NOT using per-item optimization
                                        if not should_optimize_text3d(t3):
                                            explicit_hunyuan = (
                                                t3.steps is not None
                                                or t3.octree_resolution is not None
                                                or t3.num_chunks is not None
                                            )
                                            if t3.preset and not explicit_hunyuan:
                                                batch_args.extend(["--preset", t3.preset])
                                            if t3.steps is not None:
                                                batch_args.extend(["--steps", str(t3.steps)])
                                            if t3.octree_resolution is not None:
                                                batch_args.extend(["--octree-resolution", str(t3.octree_resolution)])
                                            if t3.num_chunks is not None:
                                                batch_args.extend(["--num-chunks", str(t3.num_chunks)])
                                        if t3.model_subfolder:
                                            batch_args.extend(["--model-subfolder", t3.model_subfolder])
                                        if t3.mc_level is not None:
                                            batch_args.extend(["--mc-level", str(t3.mc_level)])
                                        if getattr(t3, "bounds_mode", None):
                                            batch_args.extend(["--bounds-mode", str(t3.bounds_mode)])
                                        if t3.guidance is not None:
                                            batch_args.extend(["--guidance", str(t3.guidance)])
                                        if t3.allow_shared_gpu:
                                            batch_args.append("--allow-shared-gpu")
                                        _append_gpu_kill_flag(batch_args, t3.gpu_kill_others)
                                        if t3.full_gpu:
                                            batch_args.append("--t2d-full-gpu")
                                        batch_args.extend(["--export-origin", t3.export_origin])
                                    if gpu_ids:
                                        batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                                    r3 = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                                    jsonl_output = r3.stdout.strip() if r3.stdout else ""
                                    shape_item_results: list[dict[str, Any]] = []
                                    for line in jsonl_output.split("\n"):
                                        if not line.strip():
                                            continue
                                        try:
                                            shape_item_results.append(json.loads(line))
                                        except json.JSONDecodeError:
                                            continue
                                else:
                                    shape_item_results = ums_shape_results

                                _timing_append(
                                    results[0],
                                    "text3d_shape_batch_total",
                                    time.perf_counter() - t_shape_total,
                                )

                                for item_result in shape_item_results:
                                    item_id = item_result.get("id", "")
                                    idx = shape_idx_map.get(item_id)
                                    if idx is None:
                                        continue
                                    rec = results[idx]
                                    row = rows[idx]
                                    _st = item_result.get("status", "")

                                    if _st == "progress":
                                        continue

                                    if _st in ("ok", "skipped"):
                                        shape_ok.append(idx)
                                        if _st == "ok":
                                            _timing_append(rec, "text3d_shape", item_result.get("seconds", 0))
                                            rec["shape_faces"] = item_result.get("faces", 0)
                                    else:
                                        failures += 1
                                        rec["status"] = "error"
                                        rec["error"] = item_result.get("error", "text3d shape falhou")
                                        console.print(f"[red]text3d shape falhou[/red] {row.id}: {rec['error']}")
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort()
                                    progress.advance(task_shape)

                                # Check for overall batch failure (no items succeeded)
                                if r3 is not None and r3.returncode != 0 and not shape_ok:
                                    console.print(f"[red]text3d generate-batch falhou (código {r3.returncode})[/red]")
                                    if r3.stderr:
                                        console.print(f"[dim]{r3.stderr[:2000]}[/dim]")

                                # Ensure task is fully advanced (batch may fail without JSONL output)
                                while progress.tasks[task_shape].completed < progress.tasks[task_shape].total:
                                    progress.advance(task_shape)

                            shape_ok_paint = [i for i in shape_ok if rows[i].generate_paint]

                            # === PAINT BATCH ===
                            _ps = (profile.paint3d.style or "hunyuan").strip().lower() if profile.paint3d else "hunyuan"
                            task_paint = None
                            if shape_ok_paint:
                                _paint_label = (
                                    "paint3d quick (todos)"
                                    if _ps in ("solid", "perlin")
                                    else "paint3d texture-batch (todos)"
                                )
                                task_paint = progress.add_task(
                                    f"[cyan]{_paint_label}[/cyan]",
                                    total=len(shape_ok_paint),
                                )

                            if _ps in ("solid", "perlin"):
                                for idx in shape_ok_paint:
                                    row = rows[idx]
                                    rec = results[idx]
                                    progress.update(task_paint, description=f"[cyan]{row.id}[/cyan] · quick paint")
                                    img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_final) or _painted_path(mesh_final)

                                    from .paths import _lod_path as _lod_path_skip

                                    if _lod_path_skip(mesh_final, 0).is_file() and not force:
                                        _queue_master(rec, mesh_final, row)
                                        finalized_indices.add(idx)
                                        append_log(rec)
                                        progress.advance(task_paint)
                                        continue
                                    if mesh_painted.is_file() and not force:
                                        _queue_master(rec, mesh_painted, row)

                                    assert paint3d_bin is not None
                                    assert text3d_bin is not None
                                    try:
                                        mesh_paint = ensure_to_paint_for_paint(
                                            mesh_final,
                                            text3d_bin=text3d_bin,
                                            profile=profile,
                                            child_env=child_env,
                                            manifest_dir=manifest_dir,
                                            force=force,
                                            row=row,
                                        )
                                    except Exception as exc:
                                        failures += 1
                                        rec["status"] = "error"
                                        rec["error"] = f"topology-fix pré-paint: {exc}"
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort() from exc
                                        progress.advance(task_paint)
                                        continue
                                    t_tex = _texture_subprocess_argv(
                                        paint3d_bin,
                                        profile,
                                        mesh_paint,
                                        img_final,
                                        mesh_painted,
                                        row_id=row.id,
                                        row=row,
                                        gpu_ids=gpu_ids,
                                    )
                                    t_paint = time.perf_counter()
                                    r4 = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                                    _timing_append(rec, "paint3d_quick", time.perf_counter() - t_paint)
                                    if r4.returncode != 0:
                                        failures += 1
                                        err = merge_subprocess_output(r4) or "paint3d quick falhou"
                                        rec["status"] = "error"
                                        rec["error"] = err
                                        preview = merge_subprocess_output(r4, max_chars=4000) or err
                                        console.print(f"[red]paint3d quick falhou[/red] {row.id}: {preview}")
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort()
                                    elif not mesh_painted.is_file():
                                        failures += 1
                                        rec["status"] = "error"
                                        rec["error"] = "quick paint não produziu GLB"
                                        console.print(f"[red]quick paint sem GLB[/red] {row.id}")
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort()
                                    else:
                                        # mesh_painted already at correct path
                                        _queue_master(rec, mesh_painted, row)
                                        finalized_indices.add(idx)
                                        append_log(rec)
                                        if not continue_on_error and rec["status"] == "error":
                                            raise click.Abort()
                                    progress.advance(task_paint)
                            elif shape_ok_paint:
                                paint_manifest_items: list[dict[str, Any]] = []
                                paint_idx_map: dict[str, int] = {}

                                for idx in shape_ok_paint:
                                    row = rows[idx]
                                    rec = results[idx]
                                    img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_final) or _painted_path(mesh_final)

                                    from .paths import _lod_path as _lod_path_skip2

                                    if _lod_path_skip2(mesh_final, 0).is_file() and not force:
                                        _queue_master(rec, mesh_final, row)
                                        finalized_indices.add(idx)
                                        append_log(rec)
                                        if task_paint is not None:
                                            progress.advance(task_paint)
                                        continue
                                    if mesh_painted.is_file() and not force:
                                        _queue_master(rec, mesh_painted, row)

                                    assert text3d_bin is not None
                                    try:
                                        mesh_paint = ensure_to_paint_for_paint(
                                            mesh_final,
                                            text3d_bin=text3d_bin,
                                            profile=profile,
                                            child_env=child_env,
                                            manifest_dir=manifest_dir,
                                            force=force,
                                            row=row,
                                        )
                                    except Exception as exc:
                                        failures += 1
                                        rec["status"] = "error"
                                        rec["error"] = f"topology-fix pré-paint: {exc}"
                                        append_log(rec)
                                        if not continue_on_error:
                                            raise click.Abort() from exc
                                        if task_paint is not None:
                                            progress.advance(task_paint)
                                        continue

                                    paint_manifest_items.append(
                                        {
                                            "id": row.id,
                                            "mesh": str(mesh_paint),
                                            "image": str(img_final),
                                            "output": str(mesh_painted),
                                        }
                                    )
                                    paint_idx_map[row.id] = idx

                                if paint_manifest_items:
                                    paint_manifest_path = batch_tmp / "paint_manifest.json"
                                    paint_manifest_path.write_text(json.dumps(paint_manifest_items, indent=2))

                                    p3 = profile.paint3d
                                    _paint_kw: dict[str, Any] = {
                                        "manifest_dir": manifest_dir,
                                        "no_ums": no_ums,
                                        "ums_stream": ums_stream,
                                        "gpu_ids": gpu_ids,
                                    }
                                    if p3:
                                        if p3.max_views is not None:
                                            _paint_kw["max_views"] = p3.max_views
                                        if p3.view_resolution is not None:
                                            _paint_kw["view_resolution"] = p3.view_resolution
                                        if p3.render_size is not None:
                                            _paint_kw["render_size"] = p3.render_size
                                        if p3.texture_size is not None:
                                            _paint_kw["texture_size"] = p3.texture_size
                                        if p3.bake_exp is not None:
                                            _paint_kw["bake_exp"] = p3.bake_exp
                                        _paint_kw["preserve_origin"] = p3.preserve_origin
                                        _paint_kw["smooth"] = p3.smooth
                                        if p3.smooth_passes is not None:
                                            _paint_kw["smooth_passes"] = p3.smooth_passes

                                    ums_paint_results = run_paint_wave_or_fallback(
                                        paint_manifest_items,
                                        **_paint_kw,
                                    )

                                    t_paint_total = time.perf_counter()
                                    r4 = None
                                    if ums_paint_results is None:
                                        batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                                        _append_quality(batch_args, profile)
                                        if force:
                                            batch_args.append("--force")
                                        if no_ums:
                                            batch_args.append("--no-ums")
                                        t3 = profile.text3d
                                        if t3:
                                            if t3.allow_shared_gpu:
                                                batch_args.append("--allow-shared-gpu")
                                            _append_gpu_kill_flag(batch_args, t3.gpu_kill_others)
                                        if p3:
                                            if p3.max_views is not None:
                                                batch_args.extend(["--max-views", str(p3.max_views)])
                                            if p3.view_resolution is not None:
                                                batch_args.extend(["--view-resolution", str(p3.view_resolution)])
                                            if p3.render_size is not None:
                                                batch_args.extend(["--render-size", str(p3.render_size)])
                                            if p3.texture_size is not None:
                                                batch_args.extend(["--texture-size", str(p3.texture_size)])
                                            if p3.bake_exp is not None:
                                                batch_args.extend(["--bake-exp", str(p3.bake_exp)])
                                            if not p3.preserve_origin:
                                                batch_args.append("--no-preserve-origin")
                                            else:
                                                batch_args.append("--preserve-origin")
                                            if p3.smooth:
                                                batch_args.append("--smooth")
                                            else:
                                                batch_args.append("--no-smooth")
                                            if p3.smooth_passes is not None:
                                                batch_args.extend(["--smooth-passes", str(p3.smooth_passes)])
                                        if gpu_ids:
                                            batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                                        r4 = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                                        paint_item_results: list[dict[str, Any]] = []
                                        for line in (r4.stdout.strip() if r4.stdout else "").split("\n"):
                                            if not line.strip():
                                                continue
                                            try:
                                                paint_item_results.append(json.loads(line))
                                            except json.JSONDecodeError:
                                                continue
                                    else:
                                        paint_item_results = ums_paint_results

                                    _timing_append(
                                        results[0],
                                        "paint3d_batch_total",
                                        time.perf_counter() - t_paint_total,
                                    )

                                    for item_result in paint_item_results:
                                        item_id = item_result.get("id", "")
                                        idx = paint_idx_map.get(item_id)
                                        if idx is None:
                                            continue

                                        rec = results[idx]
                                        row = rows[idx]
                                        img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                        mesh_painted = _painted_existing(mesh_final) or _painted_path(mesh_final)
                                        _pst = item_result.get("status", "")

                                        if _pst == "progress":
                                            continue

                                        if _pst in ("ok", "skipped"):
                                            if _pst == "ok":
                                                _timing_append(
                                                    rec,
                                                    "paint3d_texture",
                                                    item_result.get("seconds", 0),
                                                )
                                            # mesh_painted already at correct path
                                            _queue_master(rec, mesh_painted, row)
                                            finalized_indices.add(idx)
                                            append_log(rec)
                                            if not continue_on_error and rec["status"] == "error":
                                                raise click.Abort()
                                        else:
                                            failures += 1
                                            rec["status"] = "error"
                                            rec["error"] = item_result.get("error", "paint3d texture falhou")
                                            console.print(f"[red]texture (paint) falhou[/red] {row.id}: {rec['error']}")
                                            append_log(rec)
                                            if not continue_on_error:
                                                raise click.Abort()
                                        progress.advance(task_paint)

                                    # Check for overall batch failure
                                    if r4 is not None and r4.returncode != 0:
                                        err_batch = (
                                            merge_subprocess_output(r4, max_chars=200) or "paint3d texture-batch falhou"
                                        )
                                        console.print(f"[red]paint3d texture-batch erro[/red]: {err_batch}")

                            if master_q.items:
                                master_q.drain(_finalize_mesh_ok)

                            # === SIMPLIFY: bpy decimate (fallback text3d) after painting ===
                            painted_ok = [i for i in finalized_indices if results[i]["status"] == "ok"]
                            if painted_ok:
                                task_simplify = progress.add_task(
                                    "[cyan]Simplify[/cyan]",
                                    total=len(painted_ok),
                                )
                                for idx in painted_ok:
                                    row = rows[idx]
                                    rec = results[idx]
                                    _img_f, mesh_f = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_f) or _painted_path(mesh_f)
                                    mesh_shape = _shape_existing(mesh_f) or _shape_path(mesh_f)
                                    simplify_mesh = (
                                        mesh_painted
                                        if mesh_painted.is_file()
                                        else (mesh_shape if mesh_shape.is_file() else mesh_f)
                                    )
                                    progress.update(task_simplify, description=f"[cyan]{row.id}[/cyan] · simplify")
                                    _simplify_to_target(
                                        simplify_mesh,
                                        row,
                                        text3d_bin,
                                        profile=profile,
                                        run_cmd=run_cmd,
                                        child_env=child_env,
                                        cwd=manifest_dir,
                                        manifest_dir=manifest_dir,
                                        rec=rec,
                                    )
                                    progress.advance(task_simplify)

                            # === CATCH-UP: rig/animate/LOD/collision for items whose shape+paint already existed ===
                            needs_post = [i for i in pending_3d_indices if i not in finalized_indices]

                            if needs_post:
                                task_post = progress.add_task(
                                    "[cyan]Pós-processamento (rig/animate/LOD/collision)[/cyan]",
                                    total=len(needs_post),
                                )
                                for idx in needs_post:
                                    row = rows[idx]
                                    rec = results[idx]
                                    img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                    mesh_painted = _painted_existing(mesh_final) or _painted_path(mesh_final)
                                    mesh_shape = _shape_existing(mesh_final) or _shape_path(mesh_final)
                                    actual_mesh = (
                                        mesh_painted
                                        if mesh_painted.is_file()
                                        else (mesh_shape if mesh_shape.is_file() else mesh_final)
                                    )

                                    if not actual_mesh.is_file():
                                        progress.advance(task_post)
                                        continue

                                    progress.update(task_post, description=f"[cyan]{row.id}[/cyan] · pós-processamento")
                                    rec["image_path"] = _path_for_log(img_final, manifest_dir)
                                    _simplify_to_target(
                                        actual_mesh,
                                        row,
                                        text3d_bin,
                                        profile=profile,
                                        run_cmd=run_cmd,
                                        child_env=child_env,
                                        cwd=manifest_dir,
                                        manifest_dir=manifest_dir,
                                        rec=rec,
                                    )
                                    _finalize_mesh_ok(rec, actual_mesh, row)
                                    append_log(rec)
                                    if not continue_on_error and rec["status"] == "error":
                                        raise click.Abort()
                                    progress.advance(task_post)
                        else:
                            task2 = progress.add_task(
                                "[cyan]Fase 2: Text3D[/cyan]",
                                total=len(pending_3d_indices),
                            )
                            for idx in pending_3d_indices:
                                row = rows[idx]
                                rec = results[idx]
                                progress.update(task2, description=f"[cyan]{row.id}[/cyan] · Text3D")
                                img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = _shape_existing(mesh_final) or _shape_path(mesh_final)

                                if not force and mesh_shape.is_file():
                                    actual_mesh = mesh_final if mesh_final.is_file() else mesh_shape
                                    rec["mesh_path"] = _path_for_log(actual_mesh, manifest_dir)
                                    _post_text3d_mesh_extras(
                                        profile,
                                        row,
                                        mesh_final,
                                        rec,
                                        manifest_dir,
                                        child_env,
                                        rigging3d_bin,
                                        with_rig,
                                        with_animate,
                                        animator3d_bin=animator3d_bin,
                                        has_rigging_profile=has_rigging_profile,
                                        gpu_ids=gpu_ids,
                                        with_lod=with_lod,
                                        with_collision=with_collision,
                                        on_progress_line=None,
                                    )
                                    append_log(rec)
                                    progress.advance(task2)
                                    continue

                                seed = _seed_for_manifest_row(profile, row)

                                t3d_args = _text3d_argv(
                                    text3d_bin,
                                    profile,
                                    img_final,
                                    mesh_shape,
                                    row,
                                    gpu_ids=gpu_ids,
                                    manifest_dir=manifest_dir,
                                )
                                if seed is not None:
                                    t3d_args.extend(["--seed", str(seed)])
                                if row.seed is not None:
                                    t3d_args.extend(["--seed-fingerprint", str(row.seed)])
                                t_t3 = time.perf_counter()
                                r3 = run_cmd(t3d_args, extra_env=child_env, cwd=manifest_dir)
                                _timing_append(rec, "text3d", time.perf_counter() - t_t3)
                                if r3.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r3) or "text3d falhou"
                                    rec["status"] = "error"
                                    rec["error"] = err
                                    preview = merge_subprocess_output(r3, max_chars=4000) or err
                                    console.print(f"[red]text3d falhou[/red] {row.id}: {preview}")
                                elif not mesh_shape.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "text3d não produziu ficheiro GLB"
                                    console.print(f"[red]text3d sem GLB[/red] {row.id}")
                                else:
                                    # mesh_shape already at correct path
                                    _simplify_to_target(
                                        mesh_shape,
                                        row,
                                        text3d_bin,
                                        profile=profile,
                                        run_cmd=run_cmd,
                                        child_env=child_env,
                                        cwd=manifest_dir,
                                        manifest_dir=manifest_dir,
                                        rec=rec,
                                    )
                                    if _post_text3d_mesh_extras(
                                        profile,
                                        row,
                                        mesh_final,
                                        rec,
                                        manifest_dir,
                                        child_env,
                                        rigging3d_bin,
                                        with_rig,
                                        with_animate,
                                        animator3d_bin=animator3d_bin,
                                        has_rigging_profile=has_rigging_profile,
                                        gpu_ids=gpu_ids,
                                        with_lod=with_lod,
                                        with_collision=with_collision,
                                        on_progress_line=None,
                                    ):
                                        failures += 1
                                append_log(rec)
                                if not continue_on_error and rec["status"] == "error":
                                    raise click.Abort()
                                progress.advance(task2)
        finally:
            shutil.rmtree(batch_tmp, ignore_errors=True)

        summary = Table(box=box.SIMPLE, show_header=False, title="[bold]Resumo[/bold]")
        summary.add_row("Linhas processadas", str(len(rows)))
        summary.add_row("Falhas", f"[red]{failures}[/red]" if failures else "[green]0[/green]")
        if log_path is not None:
            summary.add_row("Log JSONL", str(log_path))
        console.print(Panel(summary, border_style="dim", title="[bold]Batch[/bold]"))

        if failures:
            console.print(f"[yellow]Concluído com {failures} falha(s).[/yellow]")
            sys.exit(1)
        console.print("[green]Batch concluído com sucesso.[/green]")
