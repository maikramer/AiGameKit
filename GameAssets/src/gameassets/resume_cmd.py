"""resume_cmd click command."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from .batch_guard import subprocess_gpu_env
from .categories import get_target_faces
from .cli_rich import click
from .helpers import (
    _append_text2d_profile_args,
    _append_texture2d_profile_args,
    _build_context,
    _materialize_diffuse_argv,
    _resolve_manifest_path,
    _resolve_materialize_bin_texture2d,
    _row_wants_animate,
    _row_wants_rig,
    _safe_row_dirname,
    _seed_for_manifest_row,
    _texture2d_material_maps_path_manifest,
    _texture2d_profile_effective,
    effective_face_ratio,
)
from .manifest import apply_row_text3d_overrides, effective_image_source, row_mc_level
from .omni_ctrl import omni_to_batch_item, prepare_shape_for_generation, shape_omni_stale
from .param_optimizer import optimize_text3d_for_target, should_optimize_text3d
from .paths import (
    _ROW_DONE,
    _ROW_NEED_ANIMATE,
    _ROW_NEED_IMAGE,
    _ROW_NEED_PAINT,
    _ROW_NEED_RIG,
    _ROW_NEED_SHAPE,
    _animator3d_output_path,
    _classify_row_state_master,
    _install_file,
    _painted_path,
    _paths_for_row_manifest,
    _rigging3d_output_path,
    _shape_existing,
    _shape_path,
)
from .pipeline import (
    _post_text3d_mesh_extras,
    _resolve_animator3d_bin,
    _simplify_to_target,
    _texture_subprocess_argv,
    _try_paint3d_bin,
    ensure_to_paint_for_paint,
    resolve_row_omni,
)
from .profile import Paint3DProfile
from .prompt_builder import build_prompt
from .runner import merge_subprocess_output, resolve_binary, run_cmd
from .ums_batch import (
    run_paint_wave_or_fallback,
    run_shape_wave_or_fallback,
    run_text2d_wave_or_fallback,
    run_texture2d_wave_or_fallback,
)
from .ums_coord import MasterDeferQueue, apply_ums_child_env

console = Console()


@click.command("resume")
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
    "--log",
    "log_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Ficheiro JSONL de log",
)
@click.option("--dry-run", is_flag=True, help="Mostra plano sem executar")
@click.option("--fail-fast", is_flag=True, help="Parar no primeiro erro")
@click.option(
    "--work-dir",
    type=click.Path(file_okay=False, path_type=Path),
    default=None,
    help="Pasta de trabalho persistente para shapes (defeito: .gameassets_work/ junto ao manifest)",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Regenerar tudo (passa --force aos sub-commands).",
)
@click.option(
    "--gpu-ids",
    "gpu_ids_str",
    default=None,
    help=("IDs de GPU para multi-GPU (ex.: '0,1'). Propaga --gpu-ids e CUDA_VISIBLE_DEVICES aos subprocessos."),
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
def resume_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    log_path: Path | None,
    dry_run: bool,
    fail_fast: bool,
    work_dir: Path | None,
    force: bool,
    gpu_ids_str: str | None,
    no_dashboard: bool,
    ums_stream: bool,
    no_ums: bool,
) -> None:
    """Batch inteligente: analisa o estado de cada asset e executa apenas as fases pendentes.

    \b
    Detecta automaticamente por item:
      - PNG em falta  → text2d / texture2d
      - shape em falta → text3d generate (shape)
      - paint em falta → paint3d texture (GLB final com PBR)
      - tudo OK       → skip
    """
    gpu_ids: list[int] | None = None
    if gpu_ids_str:
        try:
            gpu_ids = [int(x.strip()) for x in gpu_ids_str.split(",")]
        except ValueError as _err:
            raise click.ClickException("--gpu-ids deve ser lista separada por vírgulas (ex.: '0,1')") from _err

    profile, rows, _bundle, preset = _build_context(profile_path, manifest_path, presets_local)
    any_row_wants_paint = any(r.generate_3d and r.generate_paint for r in rows)
    if any_row_wants_paint and profile.paint3d is None:
        profile.paint3d = Paint3DProfile()

    manifest_path = _resolve_manifest_path(manifest_path)
    manifest_dir = manifest_path.resolve().parent
    t3_opts = profile.text3d
    p3: Paint3DProfile | None = profile.paint3d

    want_texture = any_row_wants_paint
    has_rigging_profile = False
    want_rig = any(r.generate_rig for r in rows if r.generate_3d)
    want_animate = want_rig and (
        profile.animator3d is not None or any(r.generate_animate for r in rows if r.generate_3d)
    )

    try:
        text2d_bin: str | None = resolve_binary("TEXT2D_BIN", "text2d")
    except FileNotFoundError:
        text2d_bin = None
    try:
        texture2d_bin: str | None = resolve_binary("TEXTURE2D_BIN", "texture2d")
    except FileNotFoundError:
        texture2d_bin = None
    try:
        text3d_bin: str | None = resolve_binary("TEXT3D_BIN", "text3d")
    except FileNotFoundError:
        text3d_bin = None
    paint3d_bin: str | None = None
    if any_row_wants_paint:
        paint3d_bin = _try_paint3d_bin()
    rigging3d_bin: str | None = None
    if want_rig:
        try:
            rigging3d_bin = resolve_binary("RIGGING3D_BIN", "rigging3d")
        except FileNotFoundError:
            rigging3d_bin = None
    animator3d_bin: str | None = None
    if want_animate:
        animator3d_bin = _resolve_animator3d_bin()

    work_dir = manifest_dir / ".gameassets_work" if work_dir is None else work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    child_env = dict(subprocess_gpu_env(gpu_ids=gpu_ids))
    apply_ums_child_env(child_env, ums_stream=ums_stream, no_ums=no_ums)
    if not no_ums:
        try:
            from gamedev_shared.model_server import ensure_ums_running

            ensure_ums_running()
        except Exception:
            pass

    log_file = None
    if log_path:
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    def append_log(rec: dict) -> None:
        if log_file:
            log_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log_file.flush()

    rg = profile.rigging3d
    rig_sfx = rg.output_suffix if rg else "_rigged"

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not row.generate_3d:
            continue
        img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
        row_work = work_dir / _safe_row_dirname(row.id)

        rig_out = _rigging3d_output_path(mesh_final, rig_sfx)
        anim_out = _animator3d_output_path(rig_out)
        row_wants_rig = _row_wants_rig(row, has_rigging_profile)
        row_wants_animate = _row_wants_animate(row, want_rig, has_rigging_profile)

        master_state: str | None = None
        # Master pipeline is the only path: usa classificador do DAG novo.
        _omni = resolve_row_omni(profile, row, manifest_dir=manifest_dir)

        _shape_p = _shape_existing(mesh_final)
        _t3_r = profile.text3d
        _omni_stale = bool(
            _shape_p is not None
            and shape_omni_stale(
                _shape_p,
                _omni,
                category=row.category or None,
                bounds_mode=getattr(_t3_r, "bounds_mode", None) if _t3_r else None,
                mc_level=row_mc_level(row, getattr(_t3_r, "mc_level", None) if _t3_r else None),
                seed=row.seed,
            )
        )
        master_state = _classify_row_state_master(
            img_final=img_final,
            mesh_final=mesh_final,
            want_texture=row.generate_paint,
            wants_rig=row_wants_rig,
            wants_animate=row_wants_animate,
            wants_lod=row.generate_lod,
            wants_collision=row.generate_collision,
            omni_stale=_omni_stale,
        )
        # Mapeia para os 6 buckets clássicos usados pelo planeador/loop
        # do resume_cmd. O bucket determina onde a execução vai retomar:
        # need_paint cobre topology-fix/bake-master/lod_gen (todos
        # rodam dentro do master pipeline, que é despachado a partir do
        # passo paint).
        _master_to_legacy = {
            "need_image": _ROW_NEED_IMAGE,
            "need_shape": _ROW_NEED_SHAPE,
            "need_topology_fix": _ROW_NEED_PAINT,
            "need_paint": _ROW_NEED_PAINT,
            "need_bake_master": _ROW_NEED_PAINT,
            "need_lod_gen": _ROW_NEED_PAINT,
            "need_collision": _ROW_NEED_PAINT,
            "need_rig_hi": _ROW_NEED_RIG,
            "need_rig": _ROW_NEED_RIG,
            "need_transfer": _ROW_NEED_RIG,
            "need_animate_lod": _ROW_NEED_ANIMATE,
            "need_animate": _ROW_NEED_ANIMATE,
            "need_validate": _ROW_DONE,
            _ROW_DONE: _ROW_DONE,
        }
        state = _master_to_legacy.get(master_state, _ROW_NEED_PAINT)

        items.append(
            {
                "idx": idx,
                "row": row,
                "state": state,
                "master_state": master_state,
                "img_final": img_final,
                "mesh_final": mesh_final,
                "row_work": row_work,
                "rig_out": rig_out,
                "anim_out": anim_out,
                "wants_rig": row_wants_rig,
                "wants_animate": row_wants_animate,
            }
        )

    # --- Relatório ---
    counts: dict[str, int] = {
        _ROW_NEED_IMAGE: 0,
        _ROW_NEED_SHAPE: 0,
        _ROW_NEED_PAINT: 0,
        _ROW_NEED_RIG: 0,
        _ROW_NEED_ANIMATE: 0,
        _ROW_DONE: 0,
    }
    for it in items:
        counts[it["state"]] = counts.get(it["state"], 0) + 1

    plan_table = Table(title="[bold]Plano de execução[/bold]", box=box.ROUNDED, show_header=True)
    plan_table.add_column("Fase", style="bold")
    plan_table.add_column("Pendentes", justify="right")
    plan_table.add_column("Ação")
    need_img_items = [it for it in items if it["state"] == _ROW_NEED_IMAGE]
    srcs = {effective_image_source(profile, it["row"]) for it in need_img_items}
    if len(srcs) > 1:
        img_label = "text2d/texture2d"
    elif "texture2d" in srcs:
        img_label = "texture2d"
    else:
        img_label = "text2d"
    plan_table.add_row(
        f"1. Imagem ({img_label})",
        str(counts[_ROW_NEED_IMAGE]),
        f"{img_label} generate" if counts[_ROW_NEED_IMAGE] > 0 else "[green]OK[/green]",
    )
    shape_pending = counts[_ROW_NEED_SHAPE] + counts[_ROW_NEED_IMAGE]
    plan_table.add_row(
        "2. Shape (hunyuan)",
        str(shape_pending),
        "text3d generate --from-image" if shape_pending > 0 else "[green]OK[/green]",
    )
    paint_pending = counts[_ROW_NEED_PAINT] + counts[_ROW_NEED_SHAPE] + counts[_ROW_NEED_IMAGE]
    paint_label = "paint3d texture"
    plan_table.add_row(
        "3. Paint (textura + PBR no GLB)",
        str(paint_pending),
        paint_label if paint_pending > 0 else "[green]OK[/green]",
    )
    rig_pending = sum(
        1
        for it in items
        if it["wants_rig"] and it["state"] in (_ROW_NEED_IMAGE, _ROW_NEED_SHAPE, _ROW_NEED_PAINT, _ROW_NEED_RIG)
    )
    if want_rig:
        plan_table.add_row(
            "4. Rigging",
            str(rig_pending),
            "rigging3d pipeline" if rig_pending > 0 else "[green]OK[/green]",
        )
    animate_pending = sum(
        1
        for it in items
        if it["wants_animate"]
        and it["state"] in (_ROW_NEED_IMAGE, _ROW_NEED_SHAPE, _ROW_NEED_PAINT, _ROW_NEED_RIG, _ROW_NEED_ANIMATE)
    )
    if want_animate:
        plan_table.add_row(
            "5. Animation",
            str(animate_pending),
            "animator3d game-pack" if animate_pending > 0 else "[green]OK[/green]",
        )
    plan_table.add_row("[green]Concluídos[/green]", str(counts[_ROW_DONE]), "[green]skip[/green]")
    console.print(plan_table)

    if all(it["state"] == _ROW_DONE for it in items):
        console.print("[bold green]Todos os assets estão completos.[/bold green]")
        return

    if counts[_ROW_NEED_IMAGE] > 0:
        need_texture2d = any(
            effective_image_source(profile, it["row"]) == "texture2d" for it in items if it["state"] == _ROW_NEED_IMAGE
        )
        need_text2d = any(
            effective_image_source(profile, it["row"]) == "text2d" for it in items if it["state"] == _ROW_NEED_IMAGE
        )
        if need_texture2d and not texture2d_bin:
            console.print("[yellow]AVISO: texture2d não encontrado — linhas texture2d serão saltadas.[/yellow]")
        if need_text2d and not text2d_bin:
            console.print("[yellow]AVISO: text2d não encontrado — linhas text2d serão saltadas.[/yellow]")
    if (counts[_ROW_NEED_SHAPE] + counts[_ROW_NEED_PAINT]) > 0 and not text3d_bin:
        raise click.ClickException("text3d não encontrado. Define TEXT3D_BIN ou instala o pacote.")
    if items and want_texture and not paint3d_bin:
        raise click.ClickException("Perfil com paint3d requer paint3d no PATH ou PAINT3D_BIN.")
    if counts[_ROW_NEED_RIG] > 0 and not rigging3d_bin:
        console.print("[yellow]AVISO: rigging3d não encontrado — rigging será saltado.[/yellow]")
    if counts[_ROW_NEED_ANIMATE] > 0 and not animator3d_bin:
        console.print("[yellow]AVISO: animator3d não encontrado — animação será saltada.[/yellow]")

    if dry_run:
        for it in items:
            if it["state"] != _ROW_DONE:
                console.print(f"  [yellow]{it['state']}[/yellow] {it['row'].id}")
        return

    continue_on_error = not fail_fast
    failures = 0
    master_q = MasterDeferQueue()
    deferred_done: set[str] = set()
    items_by_id = {it["row"].id: it for it in items}

    def _next_state_after_paint(it: dict[str, Any], row: Any) -> str:
        return _ROW_NEED_RIG if it["wants_rig"] else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)

    def _after_paint_ok(it: dict[str, Any], row: Any, rec: dict[str, Any], painted_out: Path | None = None) -> None:
        # Painted em _intermediate/; não instalar bare id.glb (entregável = *_lodN).
        it["state"] = _next_state_after_paint(it, row)
        master_q.enqueue(rec, it["mesh_final"], row)

    def _finalize_deferred(
        rec: dict[str, Any],
        mesh_f: Path,
        row_m: Any,
        *,
        on_progress_line: Any = None,
    ) -> None:
        nonlocal failures
        it = items_by_id.get(row_m.id)
        if it is None:
            return
        wr = bool(it.get("wants_rig"))
        wa = bool(it.get("wants_animate"))
        if not wr and not wa and not getattr(profile, "master_pipeline", True):
            deferred_done.add(row_m.id)
            return
        rec_m: dict[str, Any] = {"id": row_m.id}
        failed = _post_text3d_mesh_extras(
            profile,
            row_m,
            mesh_f,
            rec_m,
            manifest_dir,
            child_env,
            rigging3d_bin,
            with_rig=wr,
            with_animate=wa,
            animator3d_bin=animator3d_bin,
            has_rigging_profile=has_rigging_profile,
            gpu_ids=gpu_ids,
            with_lod=bool(row_m.generate_lod),
            with_collision=bool(row_m.generate_collision),
            on_progress_line=on_progress_line,
        )
        if failed:
            failures += 1
            append_log(rec_m)
            if not continue_on_error:
                raise click.Abort()
        else:
            if wr or wa:
                it["state"] = _ROW_DONE
            deferred_done.add(row_m.id)
            append_log(rec_m)

    def _drain_master_deferred(*, on_progress_line: Any = None) -> None:
        if not master_q.items:
            return

        def _finalize(rec: dict[str, Any], mesh_f: Path, row_m: Any) -> None:
            _finalize_deferred(rec, mesh_f, row_m, on_progress_line=on_progress_line)

        master_q.drain(_finalize)

    if not no_dashboard:
        # === Dashboard TUI path ===
        from gamedev_shared.subprocess_utils import run_cmd_streaming

        from .dashboard import BatchDashboard

        asset_ids = [it["row"].id for it in items]
        _pipeline_stages: list[str] = []
        if counts[_ROW_NEED_IMAGE] > 0:
            need_img_check = [it for it in items if it["state"] == _ROW_NEED_IMAGE]
            srcs_check = {effective_image_source(profile, it["row"]) for it in need_img_check}
            if len(srcs_check) > 1:
                _pipeline_stages.append("Image (Text2D/Texture2D)")
            elif "texture2d" in srcs_check:
                _pipeline_stages.append("Image (Texture2D)")
            else:
                _pipeline_stages.append("Image (Text2D)")
        if counts[_ROW_NEED_SHAPE] + counts[_ROW_NEED_IMAGE] > 0:
            _pipeline_stages.append("Shape")
        if want_texture and (counts[_ROW_NEED_PAINT] + counts[_ROW_NEED_SHAPE] + counts[_ROW_NEED_IMAGE]) > 0:
            _pipeline_stages.append("Paint")
        if want_rig:
            _pipeline_stages.append("Rigging")
        if want_animate:
            _pipeline_stages.append("Animation")
        pipeline_desc = " → ".join(_pipeline_stages) if _pipeline_stages else "N/A"

        asset_pipelines: dict[str, list[str]] = {}
        for it in items:
            row = it["row"]
            stages: list[str] = []
            if it["state"] in (_ROW_NEED_IMAGE, _ROW_NEED_SHAPE, _ROW_NEED_PAINT, _ROW_NEED_RIG, _ROW_NEED_ANIMATE):
                src = effective_image_source(profile, row)
                stages.append("Texture2D" if src == "texture2d" else "Text2D")
            if it["state"] in (_ROW_NEED_SHAPE, _ROW_NEED_PAINT, _ROW_NEED_RIG, _ROW_NEED_ANIMATE):
                stages.append("Shape")
            if it["state"] in (_ROW_NEED_PAINT, _ROW_NEED_RIG, _ROW_NEED_ANIMATE) and row.generate_paint:
                p3_style = (p3.style or "hunyuan").strip().lower() if p3 else "hunyuan"
                stages.append("Paint3D quick" if p3_style in ("solid", "perlin") else "Paint3D texture")
            if it["state"] in (_ROW_NEED_RIG, _ROW_NEED_ANIMATE) and it["wants_rig"]:
                stages.append("Rigging3D")
            if it["state"] == _ROW_NEED_ANIMATE and it["wants_animate"]:
                stages.append("Animator3D")
            if it["state"] == _ROW_DONE:
                stages = ["Complete"]
            asset_pipelines[row.id] = stages

        def _resume_fn(dash: BatchDashboard) -> None:  # type: ignore[name-defined]
            nonlocal failures

            # --- Fase 1: Imagens ---
            need_img = [it for it in items if it["state"] == _ROW_NEED_IMAGE]
            if need_img:
                img_mixed = (
                    len({effective_image_source(profile, x["row"]) for x in need_img}) > 1 if need_img else False
                )
                img_phase = (
                    "Text2D / Texture2D"
                    if img_mixed
                    else (
                        "Texture2D"
                        if need_img and effective_image_source(profile, need_img[0]["row"]) == "texture2d"
                        else "Text2D"
                    )
                )
                dash.set_phase(img_phase, len(need_img))
                t2d_need = [it for it in need_img if effective_image_source(profile, it["row"]) == "text2d"]
                tex_need = [it for it in need_img if effective_image_source(profile, it["row"]) == "texture2d"]
                ums_img_done: set[str] = set()

                if t2d_need and text2d_bin:
                    t2d_items_r = []
                    for it in t2d_need:
                        row = it["row"]
                        prompt_2d = build_prompt(profile, preset, row, for_3d=False)
                        item: dict[str, Any] = {
                            "id": row.id,
                            "prompt": prompt_2d,
                            "output": str(it["img_final"]),
                        }
                        seed = _seed_for_manifest_row(profile, row)
                        if seed is not None:
                            item["seed"] = seed
                        t2d_items_r.append(item)
                    _t2 = profile.text2d
                    _kw: dict[str, Any] = {
                        "manifest_dir": manifest_dir,
                        "no_ums": no_ums,
                        "ums_stream": ums_stream,
                        "gpu_ids": gpu_ids,
                        "quality": profile.generation,
                    }
                    if _t2:
                        if _t2.width is not None:
                            _kw["width"] = _t2.width
                        if _t2.height is not None:
                            _kw["height"] = _t2.height
                        if _t2.steps is not None:
                            _kw["steps"] = _t2.steps
                        if _t2.guidance_scale is not None:
                            _kw["guidance"] = _t2.guidance_scale
                    ums_t2d = run_text2d_wave_or_fallback(
                        t2d_items_r,
                        on_progress=lambda r: dash.feed_event(r.asset_id, "text2d", r.status, phase="generating"),
                        **_kw,
                    )
                    if ums_t2d is not None:
                        by_id = {str(x.get("id")): x for x in ums_t2d}
                        for it in t2d_need:
                            row = it["row"]
                            ir = by_id.get(row.id, {})
                            ums_img_done.add(row.id)
                            if ir.get("status") in ("ok", "skipped") and it["img_final"].is_file():
                                dash.feed_event(row.id, "text2d", "ok", phase="generating")
                                it["state"] = _ROW_NEED_SHAPE
                            else:
                                failures += 1
                                dash.feed_event(
                                    row.id, "text2d", "error", error=ir.get("error") or "image generation failed"
                                )
                                if not continue_on_error:
                                    raise click.Abort()
                            dash.advance_phase()

                if tex_need and texture2d_bin:
                    tt_line = _texture2d_profile_effective(profile)
                    tex_items_r = []
                    for it in tex_need:
                        row = it["row"]
                        prompt_2d = build_prompt(profile, preset, row, for_3d=False)
                        item = {"id": row.id, "prompt": prompt_2d, "output": str(it["img_final"])}
                        seed = _seed_for_manifest_row(profile, row)
                        if seed is not None:
                            item["seed"] = seed
                        tex_items_r.append(item)
                    ums_tex = run_texture2d_wave_or_fallback(
                        tex_items_r,
                        manifest_dir=manifest_dir,
                        no_ums=no_ums,
                        ums_stream=ums_stream,
                        gpu_ids=gpu_ids,
                        width=int(tt_line.width or 512),
                        height=int(tt_line.height or 512),
                        steps=int(tt_line.steps or 20),
                        guidance=float(tt_line.guidance_scale or 7.5),
                        negative_prompt=tt_line.negative_prompt,
                        preset=tt_line.preset,
                        model_id=tt_line.model_id,
                        on_progress=lambda r: dash.feed_event(r.asset_id, "texture2d", r.status, phase="generating"),
                    )
                    if ums_tex is not None:
                        by_id = {str(x.get("id")): x for x in ums_tex}
                        for it in tex_need:
                            row = it["row"]
                            ir = by_id.get(row.id, {})
                            ums_img_done.add(row.id)
                            if ir.get("status") in ("ok", "skipped") and it["img_final"].is_file():
                                mat_ok = True
                                if tt_line.materialize:
                                    try:
                                        mat_b = _resolve_materialize_bin_texture2d(tt_line)
                                        maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                        maps_dst.mkdir(parents=True, exist_ok=True)
                                        margv = _materialize_diffuse_argv(mat_b, tt_line, it["img_final"], maps_dst)
                                        r_m = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                        if r_m.returncode != 0:
                                            failures += 1
                                            mat_ok = False
                                    except FileNotFoundError:
                                        failures += 1
                                        mat_ok = False
                                if mat_ok:
                                    dash.feed_event(row.id, "texture2d", "ok", phase="generating")
                                    it["state"] = _ROW_NEED_SHAPE
                                else:
                                    dash.feed_event(row.id, "texture2d", "error", error="materialize failed")
                                    if not continue_on_error:
                                        raise click.Abort()
                            else:
                                failures += 1
                                dash.feed_event(
                                    row.id,
                                    "texture2d",
                                    "error",
                                    error=ir.get("error") or "image generation failed",
                                )
                                if not continue_on_error:
                                    raise click.Abort()
                            dash.advance_phase()

                for it in need_img:
                    if it["row"].id in ums_img_done:
                        continue
                    row = it["row"]
                    src = effective_image_source(profile, row)
                    tt_line = _texture2d_profile_effective(profile)
                    it["row_work"].mkdir(parents=True, exist_ok=True)
                    tmp_img = it["row_work"] / f"image.{profile.image_ext}"
                    prompt_2d = build_prompt(profile, preset, row, for_3d=False)
                    if src == "texture2d":
                        img_bin = texture2d_bin
                        if not img_bin:
                            failures += 1
                            dash.advance_phase()
                            continue
                        argv = [img_bin, "generate", prompt_2d, "-o", str(tmp_img)]
                        _append_texture2d_profile_args(tt_line, argv, quality=profile.generation)
                    else:
                        img_bin = text2d_bin
                        if not img_bin:
                            failures += 1
                            dash.advance_phase()
                            continue
                        argv = [img_bin, "generate", prompt_2d, "-o", str(tmp_img)]
                        _append_text2d_profile_args(profile, argv)
                        if gpu_ids:
                            argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                    if no_ums:
                        argv.append("--no-ums")
                    seed = _seed_for_manifest_row(profile, row)
                    if seed is not None:
                        argv.extend(["--seed", str(seed)])
                    tool_short = "texture2d" if src == "texture2d" else "text2d"
                    dash.feed_event(row.id, tool_short, "progress", phase="generating", percent=0)
                    r = run_cmd_streaming(
                        argv,
                        extra_env=child_env,
                        cwd=manifest_dir,
                        on_stdout_line=dash.feed_line,
                    )
                    if r.returncode == 0 and tmp_img.is_file():
                        _install_file(tmp_img, it["img_final"])
                        dash.feed_event(row.id, tool_short, "ok", phase="generating")
                        mat_ok = True
                        if src == "texture2d" and tt_line.materialize:
                            try:
                                mat_b = _resolve_materialize_bin_texture2d(tt_line)
                            except FileNotFoundError:
                                failures += 1
                                mat_ok = False
                            if mat_ok:
                                maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                maps_dst.mkdir(parents=True, exist_ok=True)
                                margv = _materialize_diffuse_argv(mat_b, tt_line, it["img_final"], maps_dst)
                                r_m = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                if r_m.returncode != 0:
                                    failures += 1
                                    mat_ok = False
                        if mat_ok:
                            it["state"] = _ROW_NEED_SHAPE
                    else:
                        failures += 1
                        dash.feed_event(row.id, tool_short, "error", error="image generation failed")
                        if not continue_on_error:
                            raise click.Abort()
                    dash.advance_phase()

            # --- Fase 2: Shape (batch) ---
            need_shape = [it for it in items if it["state"] == _ROW_NEED_SHAPE]
            if need_shape and text3d_bin:
                _ps = (p3.style or "hunyuan").strip().lower() if p3 else "hunyuan"
                dash.set_phase("Shape", len(need_shape))

                shape_manifest_items: list[dict[str, Any]] = []
                shape_item_map: dict[str, int] = {}
                for i, it in enumerate(need_shape):
                    row = it["row"]
                    _omni_row = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                    _shape_out = _shape_existing(it["mesh_final"]) or _shape_path(it["mesh_final"])
                    # UMS wave / generate-batch saltam outputs existentes: apaga
                    # shape stale (+ derivados) antes de enfileirar, senão o
                    # resume avançava com a mesh antiga.
                    prepare_shape_for_generation(
                        _shape_out,
                        _omni_row,
                        force=force,
                        category=row.category or None,
                        bounds_mode=getattr(t3_opts, "bounds_mode", None) if t3_opts else None,
                        mc_level=getattr(t3_opts, "mc_level", None) if t3_opts else None,
                        seed=row.seed,
                    )
                    seed = _seed_for_manifest_row(profile, row)
                    item_d: dict[str, Any] = {
                        "id": row.id,
                        "image": str(it["img_final"]),
                        "output": str(_shape_path(it["mesh_final"])),
                    }
                    if seed is not None:
                        item_d["seed"] = seed
                    if row.seed is not None:
                        item_d["seed_fingerprint"] = row.seed
                    if row.category:
                        item_d["category"] = row.category
                    # Sem isto UMS cai em bbox default e humanoids "engordam".
                    _omni_d = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                    item_d.update(omni_to_batch_item(_omni_d))
                    if t3_opts and should_optimize_text3d(t3_opts) and row.category:
                        fr = effective_face_ratio(profile, row)
                        target = get_target_faces(row.category, face_ratio=fr)
                        opts = optimize_text3d_for_target(target)
                        item_d["steps"] = opts.steps
                        item_d["octree_resolution"] = opts.octree_resolution
                        item_d["num_chunks"] = opts.num_chunks
                    # Overrides text3d: do manifest ganham do optimize/hw-auto.
                    apply_row_text3d_overrides(item_d, row)
                    shape_manifest_items.append(item_d)
                    shape_item_map[row.id] = i

                if shape_manifest_items:
                    s_manifest_path = work_dir / "resume_shape_manifest.json"
                    s_manifest_path.write_text(json.dumps(shape_manifest_items, indent=2))
                    t3 = t3_opts
                    _shape_kw: dict[str, Any] = {
                        "manifest_dir": manifest_dir,
                        "no_ums": no_ums,
                        "ums_stream": ums_stream,
                        "gpu_ids": gpu_ids,
                        "quality": profile.generation,
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
                        on_progress=lambda r: dash.feed_event(r.asset_id, "text3d", r.status, phase="shape"),
                        **_shape_kw,
                    )

                    r = None
                    if ums_shape_results is None:
                        batch_args = [text3d_bin, "generate-batch", str(s_manifest_path)]
                        batch_args.extend(["--quality", profile.generation or "medium"])
                        if force:
                            batch_args.append("--force")
                        if no_ums:
                            batch_args.append("--no-ums")
                        if t3:
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
                            if t3.allow_shared_gpu:
                                batch_args.append("--allow-shared-gpu")
                            if not t3.gpu_kill_others:
                                batch_args.append("--no-gpu-kill-others")
                            batch_args.extend(["--export-origin", t3.export_origin])
                        if gpu_ids:
                            batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                        r = run_cmd_streaming(
                            batch_args,
                            extra_env=child_env,
                            cwd=manifest_dir,
                            on_stdout_line=dash.feed_line,
                        )
                        shape_item_results: list[dict[str, Any]] = []
                        jsonl_output = r.stdout.strip() if r.stdout else ""
                        for line in jsonl_output.split("\n"):
                            if not line.strip():
                                continue
                            try:
                                shape_item_results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                    else:
                        shape_item_results = ums_shape_results

                    for item_result in shape_item_results:
                        if item_result.get("status") == "progress":
                            continue
                        item_id = item_result.get("id", "")
                        item_idx = shape_item_map.get(item_id)
                        if item_idx is None:
                            continue
                        it = need_shape[item_idx]
                        row = it["row"]
                        if item_result.get("status") in ("ok", "skipped"):
                            it["state"] = (
                                _ROW_NEED_PAINT
                                if row.generate_paint
                                else (
                                    _ROW_NEED_RIG
                                    if it["wants_rig"]
                                    else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                                )
                            )
                        else:
                            failures += 1
                            err = item_result.get("error", "shape falhou")
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                raise click.Abort()
                        dash.advance_phase()

                    if (
                        r is not None
                        and r.returncode != 0
                        and not any(it["state"] in (_ROW_NEED_PAINT, _ROW_DONE) for it in need_shape)
                    ):
                        pass  # batch-level failure already handled per-item

            # --- Fase 3: Paint ---
            need_paint = [it for it in items if it["state"] == _ROW_NEED_PAINT]
            if need_paint and paint3d_bin:
                _ps = (p3.style or "hunyuan").strip().lower() if p3 else "hunyuan"
                if _ps in ("solid", "perlin"):
                    dash.set_phase("Paint (quick)", len(need_paint))
                    for it in need_paint:
                        row = it["row"]
                        painted_out = _painted_path(it["mesh_final"])
                        try:
                            assert text3d_bin is not None
                            mesh_paint = ensure_to_paint_for_paint(
                                it["mesh_final"],
                                text3d_bin=text3d_bin,
                                profile=profile,
                                child_env=child_env,
                                manifest_dir=manifest_dir,
                                force=force,
                                row=row,
                            )
                        except Exception as exc:
                            failures += 1
                            err = f"topology-fix pré-paint: {exc}"
                            dash.feed_event(row.id, "paint3d", "error", error=err)
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                raise click.Abort() from exc
                            dash.advance_phase()
                            continue
                        t_tex = _texture_subprocess_argv(
                            paint3d_bin,
                            profile,
                            mesh_paint,
                            it["img_final"],
                            painted_out,
                            row_id=row.id,
                            row=row,
                            gpu_ids=gpu_ids,
                        )
                        dash.feed_event(row.id, "paint3d", "progress", phase="quick", percent=0)
                        r = run_cmd_streaming(
                            t_tex,
                            extra_env=child_env,
                            cwd=manifest_dir,
                            on_stdout_line=dash.feed_line,
                        )
                        if r.returncode == 0 and painted_out.is_file():
                            rec_ok = {"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])}
                            _after_paint_ok(it, row, rec_ok, painted_out)
                            dash.feed_event(row.id, "paint3d", "ok", phase="quick")
                            append_log(rec_ok)
                        else:
                            failures += 1
                            err = merge_subprocess_output(r, max_chars=200) or "paint falhou"
                            dash.feed_event(row.id, "paint3d", "error", error=err)
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                raise click.Abort()
                        dash.advance_phase()
                else:
                    dash.set_phase("Paint (texture)", len(need_paint))
                    paint_manifest_items: list[dict[str, Any]] = []
                    paint_item_map: dict[str, int] = {}
                    for i, it in enumerate(need_paint):
                        row = it["row"]
                        try:
                            assert text3d_bin is not None
                            mesh_paint = ensure_to_paint_for_paint(
                                it["mesh_final"],
                                text3d_bin=text3d_bin,
                                profile=profile,
                                child_env=child_env,
                                manifest_dir=manifest_dir,
                                force=force,
                                row=row,
                            )
                        except Exception as exc:
                            failures += 1
                            err = f"topology-fix pré-paint: {exc}"
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                raise click.Abort() from exc
                            continue
                        paint_manifest_items.append(
                            {
                                "id": row.id,
                                "mesh": str(mesh_paint),
                                "image": str(it["img_final"]),
                                "output": str(_painted_path(it["mesh_final"])),
                            }
                        )
                        paint_item_map[row.id] = i

                    if paint_manifest_items:
                        paint_manifest_path = work_dir / "resume_paint_manifest.json"
                        paint_manifest_path.write_text(json.dumps(paint_manifest_items, indent=2))
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
                            on_progress=lambda r: dash.feed_event(r.asset_id, "paint3d", r.status, phase="texture"),
                            **_paint_kw,
                        )

                        r = None
                        if ums_paint_results is None:
                            batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                            if force:
                                batch_args.append("--force")
                            if no_ums:
                                batch_args.append("--no-ums")
                            batch_args.extend(["--quality", profile.generation or "medium"])
                            if t3_opts:
                                if t3_opts.allow_shared_gpu:
                                    batch_args.append("--allow-shared-gpu")
                                if not t3_opts.gpu_kill_others:
                                    batch_args.append("--no-gpu-kill-others")
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

                            r = run_cmd_streaming(
                                batch_args,
                                extra_env=child_env,
                                cwd=manifest_dir,
                                on_stdout_line=dash.feed_line,
                            )
                            paint_item_results: list[dict[str, Any]] = []
                            for line in (r.stdout.strip() if r.stdout else "").split("\n"):
                                if not line.strip():
                                    continue
                                try:
                                    paint_item_results.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
                        else:
                            paint_item_results = ums_paint_results

                        for item_result in paint_item_results:
                            if item_result.get("status") == "progress":
                                continue
                            item_id = item_result.get("id", "")
                            item_idx = paint_item_map.get(item_id)
                            if item_idx is None:
                                continue
                            it = need_paint[item_idx]
                            row = it["row"]
                            if item_result.get("status") in ("ok", "skipped"):
                                painted_out = _painted_path(it["mesh_final"])
                                rec_ok = {"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])}
                                _after_paint_ok(it, row, rec_ok, painted_out if painted_out.is_file() else None)
                                append_log(rec_ok)
                            else:
                                failures += 1
                                err = item_result.get("error", "paint falhou")
                                append_log({"id": row.id, "status": "error", "error": err})
                                if not continue_on_error:
                                    raise click.Abort()
                            dash.advance_phase()

                        if r is not None and r.returncode != 0:
                            pass  # batch-level failure already handled per-item

            _drain_master_deferred(on_progress_line=dash.feed_line)

            # --- Fase 3.5: Simplify (bpy decimate) após Paint, antes de Rigging ---
            simplify_items = [
                it
                for it in items
                if it["state"] in (_ROW_NEED_RIG, _ROW_NEED_ANIMATE, _ROW_DONE) and it["mesh_final"].is_file()
            ]
            if simplify_items and text3d_bin:
                dash.set_phase("Simplify", len(simplify_items))
                for it in simplify_items:
                    row = it["row"]
                    rec: dict[str, Any] = {"id": row.id}
                    dash.feed_event(row.id, "simplify", "progress", phase="decimating", percent=0)
                    _simplify_to_target(
                        it["mesh_final"],
                        row,
                        text3d_bin,
                        profile=profile,
                        run_cmd=run_cmd,
                        child_env=child_env,
                        cwd=manifest_dir,
                        manifest_dir=manifest_dir,
                        rec=rec,
                    )
                    dash.feed_event(row.id, "simplify", "ok", phase="decimating")
                    dash.advance_phase()

            # --- Fase 3.6: Master (bake/LOD/collision) para assets sem rig ---
            # Buildings/props: state=DONE após paint; sem isto o bake-master nunca corre.
            if getattr(profile, "master_pipeline", True) and text3d_bin:
                master_done = [
                    it
                    for it in items
                    if it["state"] == _ROW_DONE
                    and it["mesh_final"].is_file()
                    and not it.get("wants_rig")
                    and it["row"].id not in deferred_done
                ]
                if master_done:
                    dash.set_phase("Master", len(master_done))
                    for it in master_done:
                        row = it["row"]
                        rec_m: dict[str, Any] = {"id": row.id}
                        dash.feed_event(row.id, "master", "progress", phase="bake", percent=0)
                        failed = _post_text3d_mesh_extras(
                            profile,
                            row,
                            it["mesh_final"],
                            rec_m,
                            manifest_dir,
                            child_env,
                            rigging3d_bin,
                            with_rig=False,
                            with_animate=False,
                            animator3d_bin=animator3d_bin,
                            has_rigging_profile=has_rigging_profile,
                            gpu_ids=gpu_ids,
                            with_lod=bool(row.generate_lod),
                            with_collision=bool(row.generate_collision),
                            on_progress_line=dash.feed_line,
                        )
                        if failed:
                            failures += 1
                            dash.feed_event(row.id, "master", "error", error=rec_m.get("error", "master"))
                            append_log(rec_m)
                            if not continue_on_error:
                                raise click.Abort()
                        else:
                            dash.feed_event(row.id, "master", "ok", phase="bake")
                            append_log(rec_m)
                        dash.advance_phase()

            # --- Fase 4: Rigging ---
            need_rig = [it for it in items if it["state"] == _ROW_NEED_RIG]
            if need_rig and rigging3d_bin:
                dash.set_phase("Rigging", len(need_rig))
                for it in need_rig:
                    if it["row"].id in deferred_done:
                        dash.advance_phase()
                        continue
                    row = it["row"]
                    rec: dict[str, Any] = {"id": row.id}
                    dash.feed_event(row.id, "rigging3d", "progress", phase="rigging", percent=0)
                    rig_failed = _post_text3d_mesh_extras(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        with_rig=True,
                        with_animate=bool(it["wants_animate"]),
                        animator3d_bin=animator3d_bin,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                        with_lod=bool(row.generate_lod),
                        with_collision=bool(row.generate_collision),
                        on_progress_line=dash.feed_line,
                    )
                    if rig_failed:
                        failures += 1
                        dash.feed_event(
                            row.id,
                            "rigging3d",
                            "error",
                            error=rec.get("error", "rigging failed"),
                        )
                        append_log(rec)
                        if not continue_on_error:
                            raise click.Abort()
                    else:
                        dash.feed_event(row.id, "rigging3d", "ok", phase="rigging")
                        it["state"] = _ROW_DONE
                        append_log(rec)
                    dash.advance_phase()

            # --- Fase 5: Animation ---
            # Animate já corre dentro do master (fase 4) quando wants_animate;
            # este bucket só apanha leftovers classificados como need_animate_lod.
            need_anim = [it for it in items if it["state"] == _ROW_NEED_ANIMATE]
            if need_anim and animator3d_bin:
                dash.set_phase("Animation", len(need_anim))
                for it in need_anim:
                    if it["row"].id in deferred_done:
                        dash.advance_phase()
                        continue
                    row = it["row"]
                    rec: dict[str, Any] = {"id": row.id}
                    dash.feed_event(row.id, "animator3d", "progress", phase="animation", percent=0)
                    anim_failed = _post_text3d_mesh_extras(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        with_rig=True,
                        with_animate=True,
                        animator3d_bin=animator3d_bin,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                        with_lod=bool(row.generate_lod),
                        with_collision=bool(row.generate_collision),
                        on_progress_line=dash.feed_line,
                    )
                    if anim_failed:
                        failures += 1
                        dash.feed_event(
                            row.id,
                            "animator3d",
                            "error",
                            error=rec.get("error", "animation failed"),
                        )
                        append_log(rec)
                        if not continue_on_error:
                            raise click.Abort()
                    else:
                        dash.feed_event(row.id, "animator3d", "ok", phase="animation")
                        it["state"] = _ROW_DONE
                        append_log(rec)
                    dash.advance_phase()

            dash.finish()

        app = BatchDashboard(
            game_title=profile.title or "",
            asset_ids=asset_ids,
            pipeline_desc=pipeline_desc,
            asset_pipelines=asset_pipelines,
            batch_fn=_resume_fn,
        )
        app.run()
    else:
        # === Existing Progress bar flow (unchanged) ===

        # --- Fase 1: Imagens ---
        need_img = [it for it in items if it["state"] == _ROW_NEED_IMAGE]
        img_mixed = len({effective_image_source(profile, x["row"]) for x in need_img}) > 1 if need_img else False
        img_phase = (
            "Text2D / Texture2D"
            if img_mixed
            else (
                "Texture2D"
                if need_img and effective_image_source(profile, need_img[0]["row"]) == "texture2d"
                else "Text2D"
            )
        )
        if need_img:
            console.print(f"\n[bold cyan]Fase 1: {img_phase} ({len(need_img)} imagens)[/bold cyan]")
            ums_img_done_p: set[str] = set()
            t2d_need_p = [it for it in need_img if effective_image_source(profile, it["row"]) == "text2d"]
            tex_need_p = [it for it in need_img if effective_image_source(profile, it["row"]) == "texture2d"]
            if t2d_need_p and text2d_bin:
                t2d_items_rp = [
                    {
                        "id": it["row"].id,
                        "prompt": build_prompt(profile, preset, it["row"], for_3d=False),
                        "output": str(it["img_final"]),
                        **({"seed": s} if (s := _seed_for_manifest_row(profile, it["row"])) is not None else {}),
                    }
                    for it in t2d_need_p
                ]
                ums_t2d_rp = run_text2d_wave_or_fallback(
                    t2d_items_rp,
                    manifest_dir=manifest_dir,
                    no_ums=no_ums,
                    ums_stream=ums_stream,
                    gpu_ids=gpu_ids,
                    quality=profile.generation,
                )
                if ums_t2d_rp is not None:
                    by_id = {str(x.get("id")): x for x in ums_t2d_rp}
                    for it in t2d_need_p:
                        ums_img_done_p.add(it["row"].id)
                        ir = by_id.get(it["row"].id, {})
                        if ir.get("status") in ("ok", "skipped") and it["img_final"].is_file():
                            it["state"] = _ROW_NEED_SHAPE
                            console.print(f"  [green]OK[/green] {it['row'].id} (text2d UMS)")
                        else:
                            failures += 1
                            console.print(f"  [red]FAIL[/red] {it['row'].id}: {ir.get('error') or 'text2d falhou'}")
                            if not continue_on_error:
                                raise click.Abort()
            if tex_need_p and texture2d_bin:
                tt_line = _texture2d_profile_effective(profile)
                tex_items_rp = [
                    {
                        "id": it["row"].id,
                        "prompt": build_prompt(profile, preset, it["row"], for_3d=False),
                        "output": str(it["img_final"]),
                        **({"seed": s} if (s := _seed_for_manifest_row(profile, it["row"])) is not None else {}),
                    }
                    for it in tex_need_p
                ]
                ums_tex_rp = run_texture2d_wave_or_fallback(
                    tex_items_rp,
                    manifest_dir=manifest_dir,
                    no_ums=no_ums,
                    ums_stream=ums_stream,
                    gpu_ids=gpu_ids,
                    width=int(tt_line.width or 512),
                    height=int(tt_line.height or 512),
                    steps=int(tt_line.steps or 20),
                    guidance=float(tt_line.guidance_scale or 7.5),
                    negative_prompt=tt_line.negative_prompt,
                    preset=tt_line.preset,
                    model_id=tt_line.model_id,
                )
                if ums_tex_rp is not None:
                    by_id = {str(x.get("id")): x for x in ums_tex_rp}
                    for it in tex_need_p:
                        ums_img_done_p.add(it["row"].id)
                        ir = by_id.get(it["row"].id, {})
                        if ir.get("status") in ("ok", "skipped") and it["img_final"].is_file():
                            mat_ok = True
                            if tt_line.materialize:
                                try:
                                    mat_b = _resolve_materialize_bin_texture2d(tt_line)
                                    maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, it["row"])
                                    maps_dst.mkdir(parents=True, exist_ok=True)
                                    margv = _materialize_diffuse_argv(mat_b, tt_line, it["img_final"], maps_dst)
                                    if run_cmd(margv, extra_env=child_env, cwd=manifest_dir).returncode != 0:
                                        mat_ok = False
                                        failures += 1
                                except FileNotFoundError:
                                    mat_ok = False
                                    failures += 1
                            if mat_ok:
                                it["state"] = _ROW_NEED_SHAPE
                                console.print(f"  [green]OK[/green] {it['row'].id} (texture2d UMS)")
                            else:
                                console.print(f"  [red]FAIL[/red] {it['row'].id} (materialize)")
                                if not continue_on_error:
                                    raise click.Abort()
                        else:
                            failures += 1
                            console.print(f"  [red]FAIL[/red] {it['row'].id}: {ir.get('error') or 'texture2d falhou'}")
                            if not continue_on_error:
                                raise click.Abort()
            remain_img = [it for it in need_img if it["row"].id not in ums_img_done_p]
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(f"[cyan]{img_phase}[/cyan]", total=max(len(remain_img), 1))
                if not remain_img:
                    progress.update(task, completed=1)
                for it in remain_img:
                    row = it["row"]
                    src = effective_image_source(profile, row)
                    tt_line = _texture2d_profile_effective(profile)
                    row_label = "Texture2D" if src == "texture2d" else "Text2D"
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · {row_label}")
                    it["row_work"].mkdir(parents=True, exist_ok=True)
                    tmp_img = it["row_work"] / f"image.{profile.image_ext}"
                    prompt_2d = build_prompt(profile, preset, row, for_3d=False)
                    if src == "texture2d":
                        img_bin = texture2d_bin
                        if not img_bin:
                            failures += 1
                            console.print(f"  [red]FAIL[/red] {row.id} (texture2d não encontrado)")
                            progress.advance(task)
                            continue
                        argv = [img_bin, "generate", prompt_2d, "-o", str(tmp_img)]
                        _append_texture2d_profile_args(tt_line, argv, quality=profile.generation)
                    else:
                        img_bin = text2d_bin
                        if not img_bin:
                            failures += 1
                            console.print(f"  [red]FAIL[/red] {row.id} (text2d não encontrado)")
                            progress.advance(task)
                            continue
                        argv = [img_bin, "generate", prompt_2d, "-o", str(tmp_img)]
                        _append_text2d_profile_args(profile, argv)
                        if gpu_ids:
                            argv.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])
                    if no_ums:
                        argv.append("--no-ums")
                    seed = _seed_for_manifest_row(profile, row)
                    if seed is not None:
                        argv.extend(["--seed", str(seed)])
                    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
                    if r.returncode == 0 and tmp_img.is_file():
                        _install_file(tmp_img, it["img_final"])
                        mat_ok = True
                        if src == "texture2d" and tt_line.materialize:
                            try:
                                mat_b = _resolve_materialize_bin_texture2d(tt_line)
                            except FileNotFoundError as e:
                                failures += 1
                                mat_ok = False
                                console.print(f"  [red]FAIL[/red] {row.id} (materialize): {e}")
                            if mat_ok:
                                maps_dst = _texture2d_material_maps_path_manifest(profile, manifest_dir, row)
                                maps_dst.mkdir(parents=True, exist_ok=True)
                                margv = _materialize_diffuse_argv(mat_b, tt_line, it["img_final"], maps_dst)
                                r_m = run_cmd(margv, extra_env=child_env, cwd=manifest_dir)
                                if r_m.returncode != 0:
                                    failures += 1
                                    mat_ok = False
                                    err_m = merge_subprocess_output(r_m, max_chars=200) or "?"
                                    console.print(f"  [red]FAIL[/red] {row.id} (materialize): {err_m}")
                        if mat_ok:
                            it["state"] = _ROW_NEED_SHAPE
                            console.print(f"  [green]OK[/green] {row.id}")
                    else:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}")
                        if not continue_on_error:
                            break
                    progress.advance(task)

        # --- Fase 2: Shape (batch) ---
        need_shape = [it for it in items if it["state"] == _ROW_NEED_SHAPE]
        if need_shape and text3d_bin:
            _ps = (p3.style or "hunyuan").strip().lower() if p3 else "hunyuan"
            console.print(f"\n[bold cyan]Fase 2: Shape ({len(need_shape)} meshes)[/bold cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Shape (batch)[/cyan]", total=len(need_shape))

                shape_manifest_items: list[dict[str, Any]] = []
                shape_item_map: dict[str, int] = {}
                for i, it in enumerate(need_shape):
                    row = it["row"]
                    _omni_row = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                    _shape_out = _shape_existing(it["mesh_final"]) or _shape_path(it["mesh_final"])
                    prepare_shape_for_generation(
                        _shape_out,
                        _omni_row,
                        force=force,
                        category=row.category or None,
                        bounds_mode=getattr(t3_opts, "bounds_mode", None) if t3_opts else None,
                        mc_level=getattr(t3_opts, "mc_level", None) if t3_opts else None,
                        seed=row.seed,
                    )
                    seed = _seed_for_manifest_row(profile, row)
                    item: dict[str, Any] = {
                        "id": row.id,
                        "image": str(it["img_final"]),
                        "output": str(_shape_path(it["mesh_final"])),
                    }
                    if seed is not None:
                        item["seed"] = seed
                    if row.seed is not None:
                        item["seed_fingerprint"] = row.seed
                    if row.category:
                        item["category"] = row.category
                    # Sem isto UMS cai em bbox default e humanoids "engordam".
                    _omni_item = resolve_row_omni(profile, row, manifest_dir=manifest_dir)
                    item.update(omni_to_batch_item(_omni_item))
                    if t3_opts and should_optimize_text3d(t3_opts) and row.category:
                        fr = effective_face_ratio(profile, row)
                        target = get_target_faces(row.category, face_ratio=fr)
                        opts = optimize_text3d_for_target(target)
                        item["steps"] = opts.steps
                        item["octree_resolution"] = opts.octree_resolution
                        item["num_chunks"] = opts.num_chunks
                    apply_row_text3d_overrides(item, row)
                    shape_manifest_items.append(item)
                    shape_item_map[row.id] = i

                if shape_manifest_items:
                    manifest_path = work_dir / "resume_shape_manifest.json"
                    manifest_path.write_text(json.dumps(shape_manifest_items, indent=2))
                    t3 = t3_opts
                    _shape_kw: dict[str, Any] = {
                        "manifest_dir": manifest_dir,
                        "no_ums": no_ums,
                        "ums_stream": ums_stream,
                        "gpu_ids": gpu_ids,
                        "quality": profile.generation,
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

                    ums_shape_results = run_shape_wave_or_fallback(shape_manifest_items, **_shape_kw)

                    r = None
                    if ums_shape_results is None:
                        batch_args = [text3d_bin, "generate-batch", str(manifest_path)]
                        batch_args.extend(["--quality", profile.generation or "medium"])
                        if force:
                            batch_args.append("--force")
                        if no_ums:
                            batch_args.append("--no-ums")
                        if t3:
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
                            if t3.allow_shared_gpu:
                                batch_args.append("--allow-shared-gpu")
                            if not t3.gpu_kill_others:
                                batch_args.append("--no-gpu-kill-others")
                            batch_args.extend(["--export-origin", t3.export_origin])
                        if gpu_ids:
                            batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                        r = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                        shape_item_results: list[dict[str, Any]] = []
                        jsonl_output = r.stdout.strip() if r.stdout else ""
                        for line in jsonl_output.split("\n"):
                            if not line.strip():
                                continue
                            try:
                                shape_item_results.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                    else:
                        shape_item_results = ums_shape_results

                    for item_result in shape_item_results:
                        if item_result.get("status") == "progress":
                            continue
                        item_id = item_result.get("id", "")
                        item_idx = shape_item_map.get(item_id)
                        if item_idx is None:
                            continue
                        it = need_shape[item_idx]
                        row = it["row"]
                        if item_result.get("status") in ("ok", "skipped"):
                            it["state"] = (
                                _ROW_NEED_PAINT
                                if row.generate_paint
                                else (
                                    _ROW_NEED_RIG
                                    if it["wants_rig"]
                                    else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                                )
                            )
                            console.print(f"  [green]OK[/green] {row.id}")
                        else:
                            failures += 1
                            err = item_result.get("error", "shape falhou")
                            console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                break
                        progress.advance(task)

                    if (
                        r is not None
                        and r.returncode != 0
                        and not any(it["state"] in (_ROW_NEED_PAINT, _ROW_DONE) for it in need_shape)
                    ):
                        console.print(f"[red]text3d generate-batch falhou (código {r.returncode})[/red]")
                        if r.stderr:
                            console.print(f"[dim]{r.stderr[:2000]}[/dim]")

                    # Ensure task is fully advanced (batch may fail without JSONL output)
                    while progress.tasks[task].completed < progress.tasks[task].total:
                        progress.advance(task)

        # --- Fase 3: Paint ---
        need_paint = [it for it in items if it["state"] == _ROW_NEED_PAINT]
        if need_paint and paint3d_bin:
            _ps = (p3.style or "hunyuan").strip().lower() if p3 else "hunyuan"
            console.print(f"\n[bold cyan]Fase 3: Paint ({len(need_paint)} texturas)[/bold cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                if _ps in ("solid", "perlin"):
                    # Quick paint: per-row (lightweight, no AI model)
                    task = progress.add_task("[cyan]Quick Paint[/cyan]", total=len(need_paint))
                    for it in need_paint:
                        row = it["row"]
                        progress.update(task, description=f"[cyan]{row.id}[/cyan] · quick paint")
                        painted_out = _painted_path(it["mesh_final"])
                        try:
                            assert text3d_bin is not None
                            mesh_paint = ensure_to_paint_for_paint(
                                it["mesh_final"],
                                text3d_bin=text3d_bin,
                                profile=profile,
                                child_env=child_env,
                                manifest_dir=manifest_dir,
                                force=force,
                                row=row,
                            )
                        except Exception as exc:
                            failures += 1
                            err = f"topology-fix pré-paint: {exc}"
                            console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                break
                            progress.advance(task)
                            continue
                        t_tex = _texture_subprocess_argv(
                            paint3d_bin,
                            profile,
                            mesh_paint,
                            it["img_final"],
                            painted_out,
                            row_id=row.id,
                            row=row,
                            gpu_ids=gpu_ids,
                        )
                        r = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                        if r.returncode == 0 and painted_out.is_file():
                            rec_ok = {"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])}
                            _after_paint_ok(it, row, rec_ok, painted_out)
                            append_log(rec_ok)
                            console.print(f"  [green]OK[/green] {row.id}")
                        else:
                            failures += 1
                            err = merge_subprocess_output(r, max_chars=200) or "paint falhou"
                            console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                break
                        progress.advance(task)
                else:
                    task = progress.add_task("[cyan]Paint (batch)[/cyan]", total=len(need_paint))
                    paint_manifest_items: list[dict[str, Any]] = []
                    paint_item_map: dict[str, int] = {}
                    for i, it in enumerate(need_paint):
                        row = it["row"]
                        try:
                            assert text3d_bin is not None
                            mesh_paint = ensure_to_paint_for_paint(
                                it["mesh_final"],
                                text3d_bin=text3d_bin,
                                profile=profile,
                                child_env=child_env,
                                manifest_dir=manifest_dir,
                                force=force,
                                row=row,
                            )
                        except Exception as exc:
                            failures += 1
                            err = f"topology-fix pré-paint: {exc}"
                            console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                            append_log({"id": row.id, "status": "error", "error": err})
                            if not continue_on_error:
                                break
                            continue
                        paint_manifest_items.append(
                            {
                                "id": row.id,
                                "mesh": str(mesh_paint),
                                "image": str(it["img_final"]),
                                "output": str(_painted_path(it["mesh_final"])),
                            }
                        )
                        paint_item_map[row.id] = i

                    if paint_manifest_items:
                        paint_manifest_path = work_dir / "resume_paint_manifest.json"
                        paint_manifest_path.write_text(json.dumps(paint_manifest_items, indent=2))
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

                        ums_paint_results = run_paint_wave_or_fallback(paint_manifest_items, **_paint_kw)

                        r = None
                        if ums_paint_results is None:
                            batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                            if force:
                                batch_args.append("--force")
                            if no_ums:
                                batch_args.append("--no-ums")
                            batch_args.extend(["--quality", profile.generation or "medium"])
                            if t3_opts:
                                if t3_opts.allow_shared_gpu:
                                    batch_args.append("--allow-shared-gpu")
                                if not t3_opts.gpu_kill_others:
                                    batch_args.append("--no-gpu-kill-others")
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

                            r = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                            paint_item_results: list[dict[str, Any]] = []
                            for line in (r.stdout.strip() if r.stdout else "").split("\n"):
                                if not line.strip():
                                    continue
                                try:
                                    paint_item_results.append(json.loads(line))
                                except json.JSONDecodeError:
                                    continue
                        else:
                            paint_item_results = ums_paint_results

                        for item_result in paint_item_results:
                            if item_result.get("status") == "progress":
                                continue
                            item_id = item_result.get("id", "")
                            item_idx = paint_item_map.get(item_id)
                            if item_idx is None:
                                continue
                            it = need_paint[item_idx]
                            row = it["row"]
                            if item_result.get("status") in ("ok", "skipped"):
                                painted_out = _painted_path(it["mesh_final"])
                                rec_ok = {"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])}
                                _after_paint_ok(it, row, rec_ok, painted_out if painted_out.is_file() else None)
                                append_log(rec_ok)
                                console.print(f"  [green]OK[/green] {row.id}")
                            else:
                                failures += 1
                                err = item_result.get("error", "paint falhou")
                                console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                                append_log({"id": row.id, "status": "error", "error": err})
                                if not continue_on_error:
                                    break
                            progress.advance(task)

                        if r is not None and r.returncode != 0:
                            err_batch = merge_subprocess_output(r, max_chars=200) or "paint3d texture-batch falhou"
                            console.print(f"[red]paint3d texture-batch erro[/red]: {err_batch}")

            _drain_master_deferred()

        # --- Fase 3.5: Simplify ---
        simplify_items = [
            it
            for it in items
            if it["state"] in (_ROW_NEED_RIG, _ROW_NEED_ANIMATE, _ROW_DONE) and it["mesh_final"].is_file()
        ]
        if simplify_items and text3d_bin:
            console.print(f"\n[bold cyan]Fase 3.5: Simplify ({len(simplify_items)} meshes)[/bold cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Simplify[/cyan]", total=len(simplify_items))
                for it in simplify_items:
                    row = it["row"]
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · simplify")
                    rec: dict[str, Any] = {"id": row.id}
                    _simplify_to_target(
                        it["mesh_final"],
                        row,
                        text3d_bin,
                        profile=profile,
                        run_cmd=run_cmd,
                        child_env=child_env,
                        cwd=manifest_dir,
                        manifest_dir=manifest_dir,
                        rec=rec,
                    )
                    console.print(f"  [green]OK[/green] {row.id}")
                    progress.advance(task)

        # --- Fase 3.6: Master (bake-master → LOD → collision → validate) ---
        # Paint mapeia need_bake_master→need_paint; após texture há que correr o DAG.
        if getattr(profile, "master_pipeline", True) and text3d_bin:
            master_items = [
                it
                for it in items
                if it["state"] in (_ROW_NEED_RIG, _ROW_NEED_ANIMATE, _ROW_DONE)
                and it["mesh_final"].is_file()
                and it["row"].id not in deferred_done
            ]
            if master_items:
                console.print(f"\n[bold cyan]Fase 3.6: Master pipeline ({len(master_items)})[/bold cyan]")
                for it in master_items:
                    row = it["row"]
                    rec_m: dict[str, Any] = {"id": row.id}
                    failed = _post_text3d_mesh_extras(
                        profile,
                        row,
                        it["mesh_final"],
                        rec_m,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        with_rig=bool(it.get("wants_rig")),
                        with_animate=bool(it.get("wants_animate")),
                        animator3d_bin=animator3d_bin,
                        has_rigging_profile=profile.rigging3d is not None,
                        gpu_ids=gpu_ids,
                        with_lod=True,
                        with_collision=True,
                    )
                    if failed:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}: {rec_m.get('error', 'master')[:160]}")
                        append_log({"id": row.id, "status": "error", "error": rec_m.get("error", "master")})
                        if not continue_on_error:
                            break
                    else:
                        console.print(f"  [green]OK[/green] {row.id} master")
                        append_log({"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"]), "master": True})

        # --- Fase 4: Rigging ---
        need_rig = [it for it in items if it["state"] == _ROW_NEED_RIG]
        if need_rig and rigging3d_bin:
            console.print(f"\n[bold cyan]Fase 4: Rigging ({len(need_rig)} modelos)[/bold cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Rigging[/cyan]", total=len(need_rig))
                for it in need_rig:
                    if it["row"].id in deferred_done:
                        progress.advance(task)
                        continue
                    row = it["row"]
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · rigging")
                    rec: dict[str, Any] = {"id": row.id}
                    rig_failed = _post_text3d_mesh_extras(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        with_rig=True,
                        with_animate=bool(it["wants_animate"]),
                        animator3d_bin=animator3d_bin,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                        with_lod=bool(row.generate_lod),
                        with_collision=bool(row.generate_collision),
                    )
                    if rig_failed:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}: {rec.get('error', '')[:120]}")
                        append_log(rec)
                        if not continue_on_error:
                            break
                    else:
                        it["state"] = _ROW_DONE
                        console.print(f"  [green]OK[/green] {row.id}")
                        append_log(rec)
                    progress.advance(task)

        # --- Fase 5: Animation ---
        need_anim = [it for it in items if it["state"] == _ROW_NEED_ANIMATE]
        if need_anim and animator3d_bin:
            console.print(f"\n[bold cyan]Fase 5: Animation ({len(need_anim)} modelos)[/bold cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task("[cyan]Animation[/cyan]", total=len(need_anim))
                for it in need_anim:
                    if it["row"].id in deferred_done:
                        progress.advance(task)
                        continue
                    row = it["row"]
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · animation")
                    rec: dict[str, Any] = {"id": row.id}
                    anim_failed = _post_text3d_mesh_extras(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        with_rig=True,
                        with_animate=True,
                        animator3d_bin=animator3d_bin,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                        with_lod=bool(row.generate_lod),
                        with_collision=bool(row.generate_collision),
                    )
                    if anim_failed:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}: {rec.get('error', '')[:120]}")
                        append_log(rec)
                        if not continue_on_error:
                            break
                    else:
                        it["state"] = _ROW_DONE
                        console.print(f"  [green]OK[/green] {row.id}")
                        append_log(rec)
                    progress.advance(task)

    if log_file:
        log_file.close()

    # --- Resumo final ---
    done_count = sum(1 for it in items if it["state"] == _ROW_DONE)
    console.print(
        f"\n[bold green]Concluídos: {done_count}/{len(items)}[/bold green]  [red]Falhas: {failures}[/red]"
        if failures
        else ""
    )
    if failures:
        sys.exit(1)
