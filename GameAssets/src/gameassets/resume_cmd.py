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
    _seed_for_row,
    _texture2d_material_maps_path_manifest,
    _texture2d_profile_effective,
    effective_face_ratio,
)
from .manifest import effective_image_source
from .param_optimizer import optimize_text3d_for_target, should_optimize_text3d
from .paths import (
    _ROW_DONE,
    _ROW_NEED_ANIMATE,
    _ROW_NEED_IMAGE,
    _ROW_NEED_PAINT,
    _ROW_NEED_RIG,
    _ROW_NEED_SHAPE,
    _animator3d_output_path,
    _classify_row_state,
    _install_file,
    _painted_path,
    _paths_for_row_manifest,
    _rigging3d_output_path,
    _shape_path,
)
from .pipeline import (
    _animator3d_game_pack_failed,
    _resolve_animator3d_bin,
    _rigging3d_pipeline_failed,
    _texture_subprocess_argv,
    _try_paint3d_bin,
)
from .profile import Paint3DProfile
from .prompt_builder import build_prompt
from .runner import merge_subprocess_output, resolve_binary, run_cmd

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
    manifest_path = _resolve_manifest_path(manifest_path)
    manifest_dir = manifest_path.resolve().parent
    t3_opts = profile.text3d
    p3: Paint3DProfile | None = profile.paint3d

    want_texture = bool(profile.paint3d)
    has_rigging_profile = profile.rigging3d is not None
    want_rig = has_rigging_profile or any(r.generate_rig for r in rows if r.generate_3d)
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
    if profile.paint3d:
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

    child_env = subprocess_gpu_env(gpu_ids=gpu_ids)

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

        state = _classify_row_state(
            img_final=img_final,
            mesh_final=mesh_final,
            rig_out=rig_out,
            anim_out=anim_out,
            want_texture=want_texture,
            wants_rig=row_wants_rig,
            wants_animate=row_wants_animate,
        )

        items.append(
            {
                "idx": idx,
                "row": row,
                "state": state,
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
    counts = {
        _ROW_NEED_IMAGE: 0,
        _ROW_NEED_SHAPE: 0,
        _ROW_NEED_PAINT: 0,
        _ROW_NEED_RIG: 0,
        _ROW_NEED_ANIMATE: 0,
        _ROW_DONE: 0,
    }
    for it in items:
        counts[it["state"]] += 1

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
                for it in need_img:
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
                        _append_texture2d_profile_args(tt_line, argv)
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
                    seed = _seed_for_row(profile, row.id)
                    if seed is not None:
                        argv.extend(["--seed", str(seed)])
                    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
                    if r.returncode == 0 and tmp_img.is_file():
                        _install_file(tmp_img, it["img_final"])
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
                    seed = _seed_for_row(profile, row.id)
                    item_d: dict[str, Any] = {
                        "id": row.id,
                        "image": str(it["img_final"]),
                        "output": str(_shape_path(it["mesh_final"])),
                    }
                    if seed is not None:
                        item_d["seed"] = seed
                    if t3_opts and should_optimize_text3d(t3_opts) and row.category:
                        fr = effective_face_ratio(profile, row)
                        target = get_target_faces(row.category, face_ratio=fr)
                        opts = optimize_text3d_for_target(target)
                        item_d["steps"] = opts.steps
                        item_d["octree_resolution"] = opts.octree_resolution
                        item_d["num_chunks"] = opts.num_chunks
                    shape_manifest_items.append(item_d)
                    shape_item_map[row.id] = i

                if shape_manifest_items:
                    s_manifest_path = work_dir / "resume_shape_manifest.json"
                    s_manifest_path.write_text(json.dumps(shape_manifest_items, indent=2))
                    batch_args = [text3d_bin, "generate-batch", str(s_manifest_path)]
                    if force:
                        batch_args.append("--force")
                    if t3_opts:
                        if not should_optimize_text3d(t3_opts):
                            explicit_hunyuan = (
                                t3_opts.steps is not None
                                or t3_opts.octree_resolution is not None
                                or t3_opts.num_chunks is not None
                            )
                            if t3_opts.preset and not explicit_hunyuan:
                                batch_args.extend(["--preset", t3_opts.preset])
                            if t3_opts.steps is not None:
                                batch_args.extend(["--steps", str(t3_opts.steps)])
                            if t3_opts.octree_resolution is not None:
                                batch_args.extend(["--octree-resolution", str(t3_opts.octree_resolution)])
                            if t3_opts.num_chunks is not None:
                                batch_args.extend(["--num-chunks", str(t3_opts.num_chunks)])
                        if t3_opts.model_subfolder:
                            batch_args.extend(["--model-subfolder", t3_opts.model_subfolder])
                        if t3_opts.low_vram:
                            batch_args.append("--low-vram")
                        if t3_opts.mc_level is not None:
                            batch_args.extend(["--mc-level", str(t3_opts.mc_level)])
                        if t3_opts.allow_shared_gpu:
                            batch_args.append("--allow-shared-gpu")
                        if not t3_opts.gpu_kill_others:
                            batch_args.append("--no-gpu-kill-others")
                        batch_args.extend(["--export-origin", t3_opts.export_origin])
                    if gpu_ids:
                        batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                    r = run_cmd_streaming(
                        batch_args,
                        extra_env=child_env,
                        cwd=manifest_dir,
                        on_stdout_line=dash.feed_line,
                    )
                    jsonl_output = r.stdout.strip() if r.stdout else ""
                    for line in jsonl_output.split("\n"):
                        if not line.strip():
                            continue
                        try:
                            item_result = json.loads(line)
                        except json.JSONDecodeError:
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
                                if want_texture
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

                    if r.returncode != 0 and not any(it["state"] in (_ROW_NEED_PAINT, _ROW_DONE) for it in need_shape):
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
                        t_tex = _texture_subprocess_argv(
                            paint3d_bin,
                            profile,
                            _shape_path(it["mesh_final"]),
                            it["img_final"],
                            painted_out,
                            row_id=row.id,
                            row=row,
                            gpu_ids=gpu_ids,
                        )
                        r = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                        if r.returncode == 0 and painted_out.is_file():
                            _install_file(painted_out, it["mesh_final"])
                            it["state"] = (
                                _ROW_NEED_RIG
                                if it["wants_rig"]
                                else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                            )
                            append_log({"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])})
                        else:
                            failures += 1
                            err = merge_subprocess_output(r, max_chars=200) or "paint falhou"
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
                        paint_manifest_items.append(
                            {
                                "id": row.id,
                                "mesh": str(_shape_path(it["mesh_final"])),
                                "image": str(it["img_final"]),
                                "output": str(_painted_path(it["mesh_final"])),
                            }
                        )
                        paint_item_map[row.id] = i

                    if paint_manifest_items:
                        paint_manifest_path = work_dir / "resume_paint_manifest.json"
                        paint_manifest_path.write_text(json.dumps(paint_manifest_items, indent=2))
                        batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                        if force:
                            batch_args.append("--force")
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
                            if p3.low_vram_mode:
                                batch_args.append("--low-vram-mode")
                        if gpu_ids:
                            batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                        r = run_cmd_streaming(
                            batch_args,
                            extra_env=child_env,
                            cwd=manifest_dir,
                            on_stdout_line=dash.feed_line,
                        )
                        for line in (r.stdout.strip() if r.stdout else "").split("\n"):
                            if not line.strip():
                                continue
                            try:
                                item_result = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            item_id = item_result.get("id", "")
                            item_idx = paint_item_map.get(item_id)
                            if item_idx is None:
                                continue
                            it = need_paint[item_idx]
                            row = it["row"]
                            if item_result.get("status") in ("ok", "skipped"):
                                painted_out = _painted_path(it["mesh_final"])
                                if painted_out.is_file():
                                    _install_file(painted_out, it["mesh_final"])
                                it["state"] = (
                                    _ROW_NEED_RIG
                                    if it["wants_rig"]
                                    else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                                )
                                append_log({"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])})
                            else:
                                failures += 1
                                err = item_result.get("error", "paint falhou")
                                append_log({"id": row.id, "status": "error", "error": err})
                                if not continue_on_error:
                                    raise click.Abort()
                            dash.advance_phase()

                        if r.returncode != 0:
                            pass  # batch-level failure already handled per-item

            # --- Fase 4: Rigging ---
            need_rig = [it for it in items if it["state"] == _ROW_NEED_RIG]
            if need_rig and rigging3d_bin:
                dash.set_phase("Rigging", len(need_rig))
                for it in need_rig:
                    row = it["row"]
                    rec: dict[str, Any] = {"id": row.id}
                    rig_failed = _rigging3d_pipeline_failed(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        want_rig,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                    )
                    if rig_failed:
                        failures += 1
                        append_log(rec)
                        if not continue_on_error:
                            raise click.Abort()
                    else:
                        it["state"] = _ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE
                        append_log(rec)
                    dash.advance_phase()

            # --- Fase 5: Animation ---
            need_anim = [it for it in items if it["state"] == _ROW_NEED_ANIMATE]
            if need_anim and animator3d_bin:
                dash.set_phase("Animation", len(need_anim))
                for it in need_anim:
                    row = it["row"]
                    rec: dict[str, Any] = {"id": row.id}
                    anim_failed = _animator3d_game_pack_failed(
                        profile,
                        row,
                        it["rig_out"],
                        it["anim_out"],
                        rec,
                        manifest_dir,
                        child_env,
                        want_animate,
                        want_rig,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                    )
                    if anim_failed:
                        failures += 1
                        append_log(rec)
                        if not continue_on_error:
                            raise click.Abort()
                    else:
                        it["state"] = _ROW_DONE
                        append_log(rec)
                    dash.advance_phase()

            dash.finish()

        app = BatchDashboard(
            game_title=profile.title or "",
            asset_ids=asset_ids,
            pipeline_desc=pipeline_desc,
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
            with Progress(
                SpinnerColumn(),
                TextColumn("{task.description}"),
                BarColumn(),
                TextColumn("{task.completed}/{task.total}"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
                task = progress.add_task(f"[cyan]{img_phase}[/cyan]", total=len(need_img))
                for it in need_img:
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
                        _append_texture2d_profile_args(tt_line, argv)
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
                    seed = _seed_for_row(profile, row.id)
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
                    seed = _seed_for_row(profile, row.id)
                    item: dict[str, Any] = {
                        "id": row.id,
                        "image": str(it["img_final"]),
                        "output": str(_shape_path(it["mesh_final"])),
                    }
                    if seed is not None:
                        item["seed"] = seed
                    if t3_opts and should_optimize_text3d(t3_opts) and row.category:
                        fr = effective_face_ratio(profile, row)
                        target = get_target_faces(row.category, face_ratio=fr)
                        opts = optimize_text3d_for_target(target)
                        item["steps"] = opts.steps
                        item["octree_resolution"] = opts.octree_resolution
                        item["num_chunks"] = opts.num_chunks
                    shape_manifest_items.append(item)
                    shape_item_map[row.id] = i

                if shape_manifest_items:
                    manifest_path = work_dir / "resume_shape_manifest.json"
                    manifest_path.write_text(json.dumps(shape_manifest_items, indent=2))
                    batch_args = [text3d_bin, "generate-batch", str(manifest_path)]
                    if force:
                        batch_args.append("--force")
                    if t3_opts:
                        if not should_optimize_text3d(t3_opts):
                            explicit_hunyuan = (
                                t3_opts.steps is not None
                                or t3_opts.octree_resolution is not None
                                or t3_opts.num_chunks is not None
                            )
                            if t3_opts.preset and not explicit_hunyuan:
                                batch_args.extend(["--preset", t3_opts.preset])
                            if t3_opts.steps is not None:
                                batch_args.extend(["--steps", str(t3_opts.steps)])
                            if t3_opts.octree_resolution is not None:
                                batch_args.extend(["--octree-resolution", str(t3_opts.octree_resolution)])
                            if t3_opts.num_chunks is not None:
                                batch_args.extend(["--num-chunks", str(t3_opts.num_chunks)])
                        if t3_opts.model_subfolder:
                            batch_args.extend(["--model-subfolder", t3_opts.model_subfolder])
                        if t3_opts.low_vram:
                            batch_args.append("--low-vram")
                        if t3_opts.mc_level is not None:
                            batch_args.extend(["--mc-level", str(t3_opts.mc_level)])
                        if t3_opts.allow_shared_gpu:
                            batch_args.append("--allow-shared-gpu")
                        if not t3_opts.gpu_kill_others:
                            batch_args.append("--no-gpu-kill-others")
                        batch_args.extend(["--export-origin", t3_opts.export_origin])
                    if gpu_ids:
                        batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                    r = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                    jsonl_output = r.stdout.strip() if r.stdout else ""
                    for line in jsonl_output.split("\n"):
                        if not line.strip():
                            continue
                        try:
                            item_result = json.loads(line)
                        except json.JSONDecodeError:
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
                                if want_texture
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

                    if r.returncode != 0 and not any(it["state"] in (_ROW_NEED_PAINT, _ROW_DONE) for it in need_shape):
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
                        t_tex = _texture_subprocess_argv(
                            paint3d_bin,
                            profile,
                            _shape_path(it["mesh_final"]),
                            it["img_final"],
                            painted_out,
                            row_id=row.id,
                            row=row,
                            gpu_ids=gpu_ids,
                        )
                        r = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                        if r.returncode == 0 and painted_out.is_file():
                            _install_file(painted_out, it["mesh_final"])
                            it["state"] = (
                                _ROW_NEED_RIG
                                if it["wants_rig"]
                                else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                            )
                            append_log({"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])})
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
                        paint_manifest_items.append(
                            {
                                "id": row.id,
                                "mesh": str(_shape_path(it["mesh_final"])),
                                "image": str(it["img_final"]),
                                "output": str(_painted_path(it["mesh_final"])),
                            }
                        )
                        paint_item_map[row.id] = i

                    if paint_manifest_items:
                        paint_manifest_path = work_dir / "resume_paint_manifest.json"
                        paint_manifest_path.write_text(json.dumps(paint_manifest_items, indent=2))
                        batch_args = [paint3d_bin, "texture-batch", str(paint_manifest_path)]
                        if force:
                            batch_args.append("--force")
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
                            if p3.low_vram_mode:
                                batch_args.append("--low-vram-mode")
                        if gpu_ids:
                            batch_args.extend(["--gpu-ids", ",".join(str(g) for g in gpu_ids)])

                        r = run_cmd(batch_args, extra_env=child_env, cwd=manifest_dir)
                        for line in (r.stdout.strip() if r.stdout else "").split("\n"):
                            if not line.strip():
                                continue
                            try:
                                item_result = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            item_id = item_result.get("id", "")
                            item_idx = paint_item_map.get(item_id)
                            if item_idx is None:
                                continue
                            it = need_paint[item_idx]
                            row = it["row"]
                            if item_result.get("status") in ("ok", "skipped"):
                                painted_out = _painted_path(it["mesh_final"])
                                if painted_out.is_file():
                                    _install_file(painted_out, it["mesh_final"])
                                it["state"] = (
                                    _ROW_NEED_RIG
                                    if it["wants_rig"]
                                    else (_ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE)
                                )
                                append_log({"id": row.id, "status": "ok", "mesh_path": str(it["mesh_final"])})
                                console.print(f"  [green]OK[/green] {row.id}")
                            else:
                                failures += 1
                                err = item_result.get("error", "paint falhou")
                                console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                                append_log({"id": row.id, "status": "error", "error": err})
                                if not continue_on_error:
                                    break
                            progress.advance(task)

                        if r.returncode != 0:
                            err_batch = merge_subprocess_output(r, max_chars=200) or "paint3d texture-batch falhou"
                            console.print(f"[red]paint3d texture-batch erro[/red]: {err_batch}")

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
                    row = it["row"]
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · rigging")
                    rec: dict[str, Any] = {"id": row.id}
                    rig_failed = _rigging3d_pipeline_failed(
                        profile,
                        row,
                        it["mesh_final"],
                        rec,
                        manifest_dir,
                        child_env,
                        rigging3d_bin,
                        want_rig,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                    )
                    if rig_failed:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}")
                        append_log(rec)
                        if not continue_on_error:
                            break
                    else:
                        it["state"] = _ROW_NEED_ANIMATE if it["wants_animate"] else _ROW_DONE
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
                    row = it["row"]
                    progress.update(task, description=f"[cyan]{row.id}[/cyan] · animation")
                    rec: dict[str, Any] = {"id": row.id}
                    anim_failed = _animator3d_game_pack_failed(
                        profile,
                        row,
                        it["rig_out"],
                        it["anim_out"],
                        rec,
                        manifest_dir,
                        child_env,
                        want_animate,
                        want_rig,
                        has_rigging_profile=has_rigging_profile,
                        gpu_ids=gpu_ids,
                    )
                    if anim_failed:
                        failures += 1
                        console.print(f"  [red]FAIL[/red] {row.id}")
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
