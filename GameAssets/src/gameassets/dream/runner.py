"""Orquestra batch + skymap2d + handoff + scaffold do projeto VibeGame."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

from .emitter import emit_all
from .planner import DreamPlan
from .terrain_stage import TerrainConfig, TerrainStage

console = Console()


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
    }

    def _step(name: str, ok: bool = True, detail: str = "") -> None:
        report["steps"].append({"name": name, "ok": ok, "detail": detail})
        tag = "[green]OK[/green]" if ok else "[red]FAIL[/red]"
        console.print(f"  {tag} {name}" + (f" — {detail}" if detail else ""))

    console.print(Panel(f"[bold]{plan.title}[/bold] — {plan.genre}", title="Dream", border_style="cyan"))

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
                f"  gameassets batch --profile game.yaml --manifest manifest.csv "
                f"--with-3d --with-rig --with-animate\n"
                f"  gameassets handoff --profile game.yaml --manifest manifest.csv --public-dir {public_dir}",
                border_style="green",
                title="Dream (dry-run)",
            )
        )
        return report

    # --- 3. gameassets batch ---
    batch_flags = ["--with-3d"]
    if any(a.generate_rig for a in plan.assets):
        batch_flags.append("--with-rig")
        batch_flags.append("--with-animate")
    if any(a.generate_parts for a in plan.assets):
        batch_flags.append("--with-parts")
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
        str(batch_dir / "manifest.csv"),
        *batch_flags,
    ]
    console.print(f"[dim]$ {' '.join(batch_argv)}[/dim]")
    rc = subprocess.call(batch_argv, cwd=str(batch_dir))
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
            console.print(f"[dim]$ {' '.join(sky_argv)}[/dim]")
            rc_sky = subprocess.call(sky_argv)
            _step("skymap2d generate", ok=rc_sky == 0, detail=f"exit {rc_sky}")
        else:
            _step("skymap2d generate", ok=False, detail="skymap2d not found; sky skipped")

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
        str(batch_dir / "manifest.csv"),
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
