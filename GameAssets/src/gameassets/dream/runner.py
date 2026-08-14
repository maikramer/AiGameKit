"""Orquestra batch + skymap2d + handoff + scaffold do projeto VibeGame."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .emitter import emit_all
from .planlint import SEVERITY_ERROR, asset_stage_chain, validate_plan
from .planner import DreamPlan
from .terrain_stage import TerrainConfig, TerrainStage

console = Console()


def _print_plan_summary(plan: DreamPlan, *, max_assets: int = 8) -> dict[str, Any]:
    """Header de provenance + tabela de assets com a cadeia completa de stages."""
    source_badge = {
        "fallback": "[yellow]fallback[/yellow]",
        "cache": "[cyan]cache[/cyan]",
    }.get(plan.source, f"[green]{plan.source or 'plan'}[/green]")
    if plan.source == "refine-failed":
        source_badge = "[red]refine-failed[/red]"

    header_bits = [f"source: {source_badge}"]
    if plan.source_detail:
        header_bits.append(f"({plan.source_detail})")
    if plan.seed is not None:
        header_bits.append(f"seed: [bold]{plan.seed}[/bold]")
    console.print("  " + " — ".join(header_bits))

    table = Table(title="Plano — assets x stages", box=box.SIMPLE, title_justify="left")
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("kind", no_wrap=True)
    table.add_column("stages")
    for a in plan.assets:
        chain = " → ".join(asset_stage_chain(a)) or "[dim]—[/dim]"
        table.add_row(a.id, a.kind, chain)
    if plan.assets:
        console.print(table)

    if plan.terrain is not None and plan.terrain.enabled:
        console.print(
            f"  terrain: [bold]on[/bold] — {plan.terrain.prompt or '(sem prompt)'}"
            f" (world {plan.terrain.world_size}m, height {plan.terrain.max_height}m)"
        )

    lint_report: dict[str, Any] = {"repairs": list(plan.repairs), "issues": []}

    if plan.repairs:
        rep_table = Table(title="Auto-reparos do plano (pós-LLM)", box=box.SIMPLE, title_justify="left")
        rep_table.add_column("repair", style="yellow")
        for r in plan.repairs:
            rep_table.add_row(r)
        console.print(rep_table)

    residual = validate_plan(plan, max_assets=max_assets)
    lint_report["issues"] = [i.to_dict() for i in residual]
    if residual:
        issues_table = Table(title="Lint residual (não reparável)", box=box.SIMPLE, title_justify="left")
        issues_table.add_column("sev", no_wrap=True)
        issues_table.add_column("código", no_wrap=True)
        issues_table.add_column("mensagem")
        for i in residual:
            sev = "[red]ERROR[/red]" if i.severity == SEVERITY_ERROR else "[yellow]WARN[/yellow]"
            issues_table.add_row(sev, i.code, (f"[{i.asset_id}] " if i.asset_id else "") + i.message)
        console.print(issues_table)

    return lint_report


# ---------------------------------------------------------------------------
# Scaffold: package.json + vite.config.ts para projecto Vite standalone
# ---------------------------------------------------------------------------

_PACKAGE_JSON_TEMPLATE = """\
{{
  "name": "{name}",
  "private": true,
  "type": "module",
  "scripts": {{
    "dev": "vite",
    "build": "vite build",
    "preview": "vite preview"
  }},
  "dependencies": {{
    "vibegame": "latest"
  }},
  "devDependencies": {{
    "vite": "^5.0.0"
  }}
}}
"""

_VITE_CONFIG = """\
import {{ defineConfig }} from 'vite';

export default defineConfig({{
  server: {{ open: process.env.BROWSER !== 'none' }},
}});
"""


def _safe_name(title: str) -> str:
    return title.lower().replace(" ", "-").replace("_", "-")[:40] or "dream-game"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_dream(
    plan: DreamPlan,
    output_dir: Path,
    *,
    with_sky: bool = True,
    with_audio: bool = True,
    dry_run: bool = False,
    fail_fast: bool = True,
    max_assets: int = 8,
) -> dict[str, Any]:
    """Executa o pipeline completo ou dry-run (só ficheiros, sem GPU)."""
    output_dir = output_dir.resolve()
    project_dir = output_dir / _safe_name(plan.title)

    batch_dir = project_dir / "_batch"
    public_dir = project_dir / "public"
    src_dir = project_dir / "src"

    report: dict[str, Any] = {
        "project_dir": str(project_dir),
        "dry_run": dry_run,
        "steps": [],
        "plan": {
            "title": plan.title,
            "genre": plan.genre,
            "source": plan.source,
            "source_detail": plan.source_detail,
            "seed": plan.seed,
        },
    }

    def _step(name: str, ok: bool = True, detail: str = "") -> None:
        report["steps"].append({"name": name, "ok": ok, "detail": detail})
        tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        console.print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))

    console.print(Panel(f"[bold]{plan.title}[/bold] — {plan.genre}", title="Dream", border_style="cyan"))

    # --- 0. Resumo do plano: provenance + stages + lint (antes de queimar GPU) ---
    report["lint"] = _print_plan_summary(plan, max_assets=max_assets)

    # --- 1. Emitir ficheiros do batch ---
    batch_dir.mkdir(parents=True, exist_ok=True)
    emit_paths = emit_all(plan, batch_dir, with_sky=with_sky, with_audio=with_audio)
    _step("emit batch files", detail=f"{len(emit_paths)} ficheiros em {batch_dir}")

    # --- 2. Guardar dream_plan.json ---
    plan_path = batch_dir / "dream_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    _step("save dream_plan.json")

    if dry_run:
        # Scaffold mínimo para dry-run (sem bun install, sem batch)
        _scaffold_project(plan, project_dir, batch_dir, src_dir, public_dir, with_sky=with_sky)
        _step("scaffold project (dry-run)", detail=str(project_dir))
        report["plan_path"] = str(plan_path)
        console.print(
            Panel(
                f"[cyan]dry-run[/cyan] — ficheiros em [bold]{project_dir}[/bold]\n"
                "Para gerar assets, corre:\n"
                f"  cd {batch_dir}\n"
                f"  gameassets batch --profile game.yaml --manifest manifest.yaml\n"
                f"  gameassets handoff --profile game.yaml --manifest manifest.yaml --public-dir {public_dir}\n"
                "\n"
                "Para iterar no plano antes de gerar:\n"
                f'  gameassets dream refine {plan_path} "add a dragon boss"\n'
                f"  gameassets dream explain {plan_path}",
                border_style="green",
                title="Dream (dry-run)",
            )
        )
        return report

    # --- 3. gameassets batch ---
    batch_flags: list[str] = []  # auto-detection from manifest handles everything
    if not with_audio or not any(a.generate_audio for a in plan.assets):
        batch_flags.append("--skip-audio")

    batch_argv = [
        sys.executable,
        "-m",
        "gameassets",
        "batch",
        "--profile",
        str(batch_dir / "game.yaml"),
        "--manifest",
        str(batch_dir / "manifest.yaml"),
        *batch_flags,
    ]
    dream_env = dict(os.environ)
    dream_env.setdefault("VRAMD_PRIORITY", "batch")
    console.print(f"[dim]$ {' '.join(batch_argv)}[/dim]")
    rc = subprocess.call(batch_argv, cwd=str(batch_dir), env=dream_env)
    ok = rc == 0
    _step("gameassets batch", ok=ok, detail=f"exit {rc}")
    if not ok and fail_fast:
        return report

    # --- 3b. Terrain generation (se terrain enabled) ---
    terrain_enabled = plan.terrain is not None and plan.terrain.enabled
    if terrain_enabled:
        tp = plan.terrain
        terrain_dir = public_dir / "assets" / "terrain"
        terrain_dir.mkdir(parents=True, exist_ok=True)
        try:
            tcfg = TerrainConfig(
                seed=tp.seed or 42,
                prompt=tp.prompt,
                world_size=tp.world_size,
                max_height=tp.max_height,
                size=tp.size,
                river_threshold=tp.river_threshold,
                erosion_particles=tp.erosion_particles,
                lake_min_area=tp.lake_min_area,
                lake_max_count=tp.lake_max_count,
            )
            stage = TerrainStage()
            result = stage.run(tcfg, terrain_dir)
            _step("terrain generation", detail=f"{result.heightmap_path.name} + {result.metadata_path.name}")
        except Exception as exc:
            _step("terrain generation", ok=False, detail=str(exc))
            if fail_fast:
                return report

    # --- 4. skymap2d generate (se sky_prompt) ---
    if with_sky and plan.sky_prompt:
        sky_dir = public_dir / "assets" / "sky"
        sky_dir.mkdir(parents=True, exist_ok=True)
        sky_out = sky_dir / "sky.png"
        try:
            from ..runner import resolve_binary

            skymap_bin = resolve_binary("SKYMAP2D_BIN", "skymap2d")
        except FileNotFoundError:
            skymap_bin = None

        if skymap_bin:
            sky_argv = [skymap_bin, "generate", plan.sky_prompt, "-o", str(sky_out)]
            try:
                from ..helpers import _append_skymap2d_profile_args, _skymap2d_profile_effective
                from ..profile import load_profile

                emitted_profile = load_profile(batch_dir / "game.yaml")
                sky_eff = _skymap2d_profile_effective(emitted_profile)
                _append_skymap2d_profile_args(sky_eff, sky_argv, quality=emitted_profile.generation)
            except Exception as exc:
                console.print(f"[dim]skymap2d profile args skipped: {exc}[/dim]")
            console.print(f"[dim]$ {' '.join(sky_argv)}[/dim]")
            rc_sky = subprocess.call(sky_argv, env=dream_env)
            _step("skymap2d generate", ok=rc_sky == 0, detail=f"exit {rc_sky}")
        else:
            _step("skymap2d generate", ok=False, detail="skymap2d not found; sky skipped")

    # --- 4b. text2icon generate (UI icons, se icon_prompts) ---
    if plan.icon_prompts:
        icon_dir = public_dir / "assets" / "icons"
        icon_dir.mkdir(parents=True, exist_ok=True)
        try:
            from ..runner import resolve_binary

            text2icon_bin = resolve_binary("TEXT2ICON_BIN", "text2icon")
        except FileNotFoundError:
            text2icon_bin = None

        if text2icon_bin:
            from aigamekit_shared.path_utils import safe_filename as _icon_slug

            try:
                from ..helpers import _append_text2icon_profile_args, _text2icon_profile_effective
                from ..profile import load_profile

                emitted_profile = load_profile(batch_dir / "game.yaml")
                icon_eff = _text2icon_profile_effective(emitted_profile)
                _icon_quality = emitted_profile.generation
            except Exception as exc:
                console.print(f"[dim]text2icon profile args skipped: {exc}[/dim]")
                icon_eff = None
                _icon_quality = "medium"

            ok_icons = 0
            for _ip in plan.icon_prompts:
                _slug = _icon_slug(_ip)
                _icon_out = icon_dir / f"{_slug}.png"
                _icon_argv = [text2icon_bin, "generate", _ip, "-o", str(_icon_out)]
                if icon_eff is not None:
                    _append_text2icon_profile_args(icon_eff, _icon_argv, quality=_icon_quality)
                console.print(f"[dim]$ {' '.join(_icon_argv)}[/dim]")
                rc_icon = subprocess.call(_icon_argv, env=dream_env)
                if rc_icon == 0:
                    ok_icons += 1
            _step(
                "text2icon generate",
                ok=ok_icons == len(plan.icon_prompts),
                detail=f"{ok_icons}/{len(plan.icon_prompts)} icons",
            )
        else:
            _step("text2icon generate", ok=False, detail="text2icon not found; icons skipped")

    # --- 5. gameassets handoff ---
    public_dir.mkdir(parents=True, exist_ok=True)
    handoff_argv = [
        sys.executable,
        "-m",
        "gameassets",
        "handoff",
        "--profile",
        str(batch_dir / "game.yaml"),
        "--manifest",
        str(batch_dir / "manifest.yaml"),
        "--public-dir",
        str(public_dir),
    ]
    if any(a.generate_3d for a in plan.assets):
        handoff_argv.append("--with-textures")
    console.print(f"[dim]$ {' '.join(handoff_argv)}[/dim]")
    rc_ho = subprocess.call(handoff_argv, cwd=str(batch_dir))
    _step("gameassets handoff", ok=rc_ho == 0, detail=f"exit {rc_ho}")

    # --- 6. Scaffold projecto Vite ---
    _scaffold_project(plan, project_dir, batch_dir, src_dir, public_dir, with_sky=with_sky)
    _step("scaffold project", detail=str(project_dir))

    console.print(
        Panel(
            f"[green]Projecto gerado em[/green] [bold]{project_dir}[/bold]\n\n"
            f"  cd {project_dir}\n"
            "  bun install   # ou npm install\n"
            "  bun run dev",
            border_style="green",
            title="Dream",
        )
    )
    return report


def _scaffold_project(
    plan: DreamPlan,
    project_dir: Path,
    batch_dir: Path,
    src_dir: Path,
    public_dir: Path,
    *,
    with_sky: bool,
) -> None:
    """Cria package.json, vite.config.ts, src/main.ts, index.html."""
    project_dir.mkdir(parents=True, exist_ok=True)
    src_dir.mkdir(parents=True, exist_ok=True)
    public_dir.mkdir(parents=True, exist_ok=True)

    pkg = project_dir / "package.json"
    if not pkg.exists():
        pkg.write_text(
            _PACKAGE_JSON_TEMPLATE.format(name=_safe_name(plan.title)),
            encoding="utf-8",
        )

    vite_cfg = project_dir / "vite.config.ts"
    if not vite_cfg.exists():
        vite_cfg.write_text(_VITE_CONFIG, encoding="utf-8")

    main_src = batch_dir / "main.ts"
    main_dst = src_dir / "main.ts"
    if main_src.is_file():
        shutil.copy2(main_src, main_dst)

    index_src = batch_dir / "index.html"
    index_dst = project_dir / "index.html"
    if index_src.is_file():
        shutil.copy2(index_src, index_dst)
