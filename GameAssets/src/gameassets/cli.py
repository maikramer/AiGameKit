#!/usr/bin/env python3
"""GameAssets — CLI principal."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gamedev_shared.skill_install import install_my_skill

from . import __version__
from .batch_guard import query_gpu_free_mib
from .categories import get_target_faces
from .cli_rich import click
from .helpers import (  # noqa: F401
    _audio_path_for_row_manifest,
    _build_context,
    _resolve_manifest_path,
    _seed_for_row,
    _texture2d_material_maps_path_manifest,
    _texture2d_profile_effective,
)
from .mesh_reorigin import collect_glb_paths, filter_excluded_paths, reorigin_glb_file
from .paths import _paths_for_row, _paths_for_row_manifest, _rigging3d_output_path  # noqa: F401
from .pipeline import (  # noqa: F401
    _paint3d_texture_argv,
    _part3d_output_paths,
    _part3d_profile_effective,
    _rigging3d_pipeline_argv,
    _text3d_argv,
    _texture_subprocess_argv,
)
from .presets import load_presets_bundle
from .runner import merge_subprocess_output, resolve_binary, run_cmd
from .templates import GAME_YAML, MANIFEST_YAML

console = Console()

EPILOG = """
Exemplo rápido:
  gameassets init
  gameassets prompts --profile game.yaml --manifest manifest.yaml
  gameassets batch --profile game.yaml --manifest manifest.yaml --with-3d
  gameassets batch --dry-run --dry-run-json plan.json --profile game.yaml --manifest manifest.yaml
  gameassets handoff --profile game.yaml --manifest manifest.yaml --public-dir ../my-game/public
  gameassets dream "platformer 3D com cristais num mundo de nuvens" --dry-run
  gameassets dream "idle clicker de fazenda" --llm-provider openai --output-dir ./mygame
  gameassets mesh reorigin-feet ../my-game/public

Preset só num ficheiro teu (ex.: galaxy_orbital em presets-local.yaml):
  gameassets batch --profile game.yaml --manifest manifest.yaml --with-3d \\
    --presets-local presets-local.yaml --log run.jsonl

Define TEXT2D_BIN / TEXT3D_BIN / PAINT3D_BIN (se ``text3d.texture``) / RIGGING3D_BIN / ANIMATOR3D_BIN /
PART3D_BIN.
Text3D gera só geometria; textura e PBR no GLB vêm do ``paint3d`` (Hunyuan3D-Paint 2.1).
Com image_source: texture2d: TEXTURE2D_BIN e, se texture2d.materialize,
MATERIALIZE_BIN (ou texture2d.materialize_bin) — só para mapas PBR a partir da imagem difusa.
Com generate_audio no manifest: TEXT2SOUND_BIN se text2sound não estiver no PATH.
"""


@click.group(epilog=EPILOG)
@click.version_option(version=__version__, prog_name="gameassets")
def main() -> None:
    """Batch de prompts e assets alinhados ao estilo do teu jogo."""


@main.group("skill")
def skill_group() -> None:
    """Agent Skills Cursor (instalação no projeto do jogo)."""


@skill_group.command("install")
@click.option(
    "--target",
    "-t",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Raiz do projeto do jogo (cria .cursor/skills/gameassets/)",
)
@click.option("--force", is_flag=True, help="Sobrescrever SKILL.md existente")
def skill_install_cmd(target: Path, force: bool) -> None:
    """Copia SKILL.md para .cursor/skills/gameassets/."""
    try:
        dest = install_my_skill(vars(), target, force=force)
    except FileNotFoundError as e:
        raise click.ClickException(str(e)) from e
    except FileExistsError as e:
        raise click.ClickException(f"{e} — usa --force para substituir.") from e
    console.print(
        Panel(
            f"Skill copiada para [bold cyan]{dest}[/bold cyan]",
            title="[bold green]OK[/bold green]",
            border_style="green",
        )
    )


@main.group("mesh")
def mesh_group() -> None:
    """Operações em meshes GLB (origem, etc.)."""


@mesh_group.command("reorigin-feet")
@click.argument(
    "path",
    type=click.Path(exists=True, file_okay=True, dir_okay=True, path_type=Path),
)
@click.option(
    "--recursive/--no-recursive",
    "recursive",
    default=True,
    help="Se PATH for pasta, processar subpastas (defeito: sim).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Listar ficheiros .glb sem gravar.",
)
@click.option(
    "--exclude",
    "excludes",
    multiple=True,
    help="Não processar: padrão fnmatch no nome do ficheiro (ex.: hero.glb, *player*). Repetir para vários.",
)
def mesh_reorigin_feet_cmd(path: Path, recursive: bool, dry_run: bool, excludes: tuple[str, ...]) -> None:
    """Reposiciona cada GLB para convenção *pés*: base da caixa em Y=0 e centro em XZ (glTF Y-up).

    Move a **cena inteira** (um único deslocamento por ficheiro). Malhas com armature/animação
    podem ficar incorrectas; preferir props estáticos.

    Requer ``trimesh`` (dependência do GameAssets).
    """
    paths = collect_glb_paths(path, recursive=recursive)
    paths = filter_excluded_paths(paths, excludes)
    if not paths:
        raise click.ClickException("Nenhum ficheiro .glb encontrado (ou extensão não suportada).")
    if dry_run:
        for p in paths:
            console.print(f"[dim]{p}[/dim]")
        console.print(f"[green]{len(paths)} ficheiro(s) (dry-run).[/green]")
        return
    ok = 0
    for p in paths:
        try:
            reorigin_glb_file(p)
            console.print(f"[green]OK[/green] {p}")
            ok += 1
        except Exception as e:
            console.print(f"[red]Erro[/red] {p}: {e}")
    if ok != len(paths):
        raise click.ClickException(f"Falharam {len(paths) - ok} de {len(paths)} ficheiros.")
    console.print(Panel(f"[bold green]{ok}[/bold green] GLB(s) actualizados.", border_style="green"))


@main.command("init")
@click.option(
    "--path",
    "target_dir",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Diretório onde criar game.yaml e manifest.yaml",
)
@click.option("--force", is_flag=True, help="Sobrescrever ficheiros existentes")
def init_cmd(target_dir: Path, force: bool) -> None:
    """Cria game.yaml e manifest.yaml de exemplo."""
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    gy = target_dir / "game.yaml"
    my = target_dir / "manifest.yaml"
    if gy.exists() and not force:
        raise click.ClickException(f"Já existe {gy} (usa --force para sobrescrever)")
    if my.exists() and not force:
        raise click.ClickException(f"Já existe {my} (usa --force para sobrescrever)")
    gy.write_text(GAME_YAML, encoding="utf-8")
    my.write_text(MANIFEST_YAML, encoding="utf-8")
    console.print(
        Panel(
            f"Criados [bold cyan]{gy}[/bold cyan] e [bold cyan]{my}[/bold cyan].\n\n"
            "Seguinte: edita o perfil, preenche o manifest, depois "
            "[bold]gameassets prompts[/bold] ou [bold]gameassets batch[/bold].",
            title="[bold green]init[/bold green]",
            border_style="green",
        )
    )


@main.command("info")
def info_cmd() -> None:
    """Mostra versão, binários resolvidos no PATH / *_BIN e VRAM livre (se nvidia-smi)."""
    table = Table(title="[bold]gameassets info[/bold]", box=box.ROUNDED)
    table.add_column("Ferramenta", style="cyan", no_wrap=True)
    table.add_column("Binário", style="green")

    def row(name: str, env: str, exe: str) -> None:
        try:
            p = resolve_binary(env, exe)
        except FileNotFoundError:
            p = "[dim](não encontrado)[/dim]"
        table.add_row(name, str(p))

    console.print(Panel.fit(f"[bold]gameassets[/bold] {__version__}", border_style="blue"))
    row("text2d", "TEXT2D_BIN", "text2d")
    row("texture2d", "TEXTURE2D_BIN", "texture2d")
    row("skymap2d", "SKYMAP2D_BIN", "skymap2d")
    row("text2sound", "TEXT2SOUND_BIN", "text2sound")
    row("text3d", "TEXT3D_BIN", "text3d")
    row("paint3d", "PAINT3D_BIN", "paint3d")
    row("part3d", "PART3D_BIN", "part3d")
    row("rigging3d", "RIGGING3D_BIN", "rigging3d")
    row("animator3d", "ANIMATOR3D_BIN", "animator3d")
    row("materialize", "MATERIALIZE_BIN", "materialize")
    console.print(table)

    free_mib = query_gpu_free_mib()
    if free_mib is not None:
        console.print(
            Panel(
                f"VRAM livre (nvidia-smi, GPU 0): [bold]{free_mib}[/bold] MiB",
                border_style="dim",
            )
        )
    else:
        console.print(
            Panel(
                "[dim]VRAM: nvidia-smi não disponível ou sem dados.[/dim]",
                border_style="dim",
            )
        )


@main.command("prompts")
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="game.yaml",
    help="Ficheiro de perfil YAML",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(dir_okay=False, path_type=Path),
    default="manifest",
    help="YAML com id, idea e colunas opcionais",
)
@click.option(
    "--presets-local",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="YAML opcional para sobrescrever/estender presets",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="Se definido, grava prompts em JSONL (uma linha por id)",
)
def prompts_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    output: Path | None,
) -> None:
    """Mostra (ou grava) os prompts finais sem usar GPU."""
    from .prompt_builder import build_audio_prompt, build_prompt

    profile, rows, _bundle, preset = _build_context(profile_path, manifest_path, presets_local)
    entries: list[dict[str, Any]] = []
    for row in rows:
        prompt_2d = build_prompt(profile, preset, row, for_3d=False)
        prompt_3d_hint = build_prompt(profile, preset, row, for_3d=True)
        prompt_audio = build_audio_prompt(profile, preset, row)
        entries.append(
            {
                "id": row.id,
                "prompt": prompt_2d,
                "prompt_3d_hint": prompt_3d_hint,
                "prompt_audio": prompt_audio,
                "generate_3d": row.generate_3d,
                "generate_audio": row.generate_audio,
                "generate_rig": row.generate_rig,
                "generate_animate": row.generate_animate,
                "category": row.category or "",
                "target_faces": get_target_faces(row.category) if row.category else None,
            }
        )
    if output:
        with output.open("w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        console.print(
            Panel(
                f"[green]Gravado[/green] [bold]{output}[/bold] — {len(entries)} linha(s).",
                title="prompts",
                border_style="green",
            )
        )
        return
    table = Table(
        title="[bold]Prompts[/bold] (pré-visualização)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("id", style="cyan", no_wrap=True)
    table.add_column("cat", style="yellow", no_wrap=True, max_width=10)
    table.add_column("faces", justify="right", style="dim")
    table.add_column("3D?", justify="center")
    table.add_column("áudio?", justify="center")
    table.add_column("rig?", justify="center")
    table.add_column("anim?", justify="center")
    table.add_column("prompt (início)", overflow="ellipsis", max_width=50)
    for e in entries:
        p = e["prompt"]
        preview = p if len(p) <= 50 else p[:47] + "..."
        flag3 = "sim" if e["generate_3d"] else "não"
        flag_a = "sim" if e["generate_audio"] else "não"
        flag_r = "sim" if e["generate_rig"] else "não"
        flag_anim = "sim" if e["generate_animate"] else "não"
        cat = e["category"] or "-"
        tf = str(e["target_faces"]) if e["target_faces"] else "-"
        table.add_row(e["id"], cat, tf, flag3, flag_a, flag_r, flag_anim, preview)
    console.print(table)


@main.command("handoff")
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="game.yaml",
    help="Ficheiro de perfil YAML (output_dir, layouts)",
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
    "--public-dir",
    "public_dir",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Pasta public/ do projecto Vite (cria assets/models, audio, …)",
)
@click.option(
    "--copy/--symlink",
    "use_copy",
    default=True,
    help="Copiar ficheiros (defeito) ou criar symlinks para o output_dir do batch",
)
@click.option(
    "--prefer-animated/--no-prefer-animated",
    "prefer_animated",
    default=True,
    help="Preferir *_animated.glb se existir no disco (prioridade sobre rigado/parts/base)",
)
@click.option(
    "--prefer-rigged/--no-prefer-rigged",
    "prefer_rigged",
    default=True,
    help="Preferir GLB rigado se existir no disco",
)
@click.option(
    "--prefer-parts/--no-prefer-parts",
    "prefer_parts",
    default=False,
    help="Preferir *_parts.glb (ordem: animado > rigado > parts > base)",
)
@click.option(
    "--with-textures/--no-with-textures",
    "with_textures",
    default=False,
    help="Copiar também PNGs 2D para assets/textures/",
)
@click.option(
    "--audio-format",
    type=click.Choice(["copy", "wav", "ogg"]),
    default="copy",
    help="Audio format for handoff (copy/ogg)",
)
@click.option("--sfx-sample-rate", type=int, default=22050, show_default=True, help="Sample rate for SFX in ogg mode")
@click.option("--bgm-sample-rate", type=int, default=44100, show_default=True, help="Sample rate for BGM in ogg mode")
@click.option(
    "--dry-run",
    is_flag=True,
    help="Mostra o manifest JSON sem escrever ficheiros",
)
def handoff_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    public_dir: Path,
    use_copy: bool,
    prefer_animated: bool,
    prefer_rigged: bool,
    prefer_parts: bool,
    with_textures: bool,
    audio_format: str,
    sfx_sample_rate: int,
    bgm_sample_rate: int,
    dry_run: bool,
) -> None:
    """Copia GLB/áudio do ``output_dir`` do perfil para ``public/assets`` e grava ``gameassets_handoff.json``."""
    from .handoff_export import handoff_command_impl

    resolved = _resolve_manifest_path(manifest_path)
    if not resolved.is_file():
        raise click.ClickException(f"Manifest não encontrado: {manifest_path} (tentado {resolved})")

    handoff_command_impl(
        profile_path,
        resolved,
        presets_local,
        public_dir,
        copy=use_copy,
        prefer_animated=prefer_animated,
        prefer_rigged=prefer_rigged,
        prefer_parts=prefer_parts,
        with_textures=with_textures,
        audio_format=audio_format,
        sfx_sample_rate=sfx_sample_rate,
        bgm_sample_rate=bgm_sample_rate,
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# debug — ferramentas visuais para agentes IA
# ---------------------------------------------------------------------------


@main.group("debug")
def debug_group() -> None:
    """Ferramentas de debugging visual para agentes IA (screenshots, inspect, compare, bundle)."""


def _extract_json_from_output(text: str) -> dict[str, Any]:
    """Extrai o primeiro objecto JSON válido de stdout misturado com logs (usa raw_decode)."""
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = dec.raw_decode(text[i:])
            if isinstance(obj, dict):
                return obj
            return {"_json_value": obj}
        except json.JSONDecodeError:
            continue
    return {
        "_parse_error": True,
        "raw_preview": text[:8000] if len(text) > 8000 else text,
    }


@debug_group.command("screenshot")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option(
    "--views", default="front,three_quarter,right,back", show_default=True, help="Vistas separadas por virgula."
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolucao px.")
@click.option("--show-bones", is_flag=True, help="Mostrar armature wireframe.")
@click.option("--frame", default=None, type=int, help="Um frame para todas as vistas.")
@click.option(
    "--frame-list",
    "frame_list",
    default=None,
    type=str,
    help="Varios frames (ex.: 1,36,72) para animacao — ficheiros view_fNNNN.png.",
)
def debug_screenshot(
    input_path: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    show_bones: bool,
    frame: int | None,
    frame_list: str | None,
) -> None:
    """Gera screenshots multi-angulo de um GLB (invoca animator3d)."""
    from .pipeline import _resolve_animator3d_bin

    abin = _resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d nao encontrado.[/red] Define ANIMATOR3D_BIN ou instala Animator3D.")
        sys.exit(1)

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_debug"

    argv = [
        abin,
        "screenshot",
        str(input_path),
        "--output-dir",
        str(output_dir),
        "--views",
        views,
        "--resolution",
        str(resolution),
    ]
    if show_bones:
        argv.append("--show-bones")
    if frame_list:
        argv.extend(["--frame-list", frame_list])
    elif frame is not None:
        argv.extend(["--frame", str(frame)])

    r = run_cmd(argv)
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000) or "animator3d screenshot falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)

    report = _extract_json_from_output(r.stdout)
    report_path = output_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    n = len(report.get("screenshots", []))
    console.print(f"[green]{n} screenshots[/green] em {output_dir}")
    console.print(json.dumps(report, indent=2, ensure_ascii=False))


@debug_group.command("bundle")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option(
    "--views",
    default="front,three_quarter,right,back,low_front,worm",
    show_default=True,
    help="Vistas (inclui low_front e worm por defeito).",
)
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolucao px.")
@click.option("--show-bones", is_flag=True, help="Wireframe do armature nos screenshots.")
@click.option("--frame", default=None, type=int, help="Frame unico para screenshots.")
@click.option("--frame-list", "frame_list", default=None, type=str, help="Varios frames (animacao).")
def debug_bundle(
    input_path: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    show_bones: bool,
    frame: int | None,
    frame_list: str | None,
) -> None:
    """Pacote único para agentes: inspect JSON + screenshots + bundle.json com metadados."""
    from .pipeline import _resolve_animator3d_bin

    abin = _resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d nao encontrado.[/red] Define ANIMATOR3D_BIN ou instala Animator3D.")
        sys.exit(1)

    if output_dir is None:
        output_dir = input_path.parent / f"{input_path.stem}_agent_bundle"
    output_dir.mkdir(parents=True, exist_ok=True)

    inspect_path = output_dir / "inspect.json"
    argv_in = [abin, "inspect", str(input_path), "--json-out"]
    r_in = run_cmd(argv_in)
    if r_in.returncode != 0:
        err = merge_subprocess_output(r_in, max_chars=2000) or "inspect falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)
    inspect_data = _extract_json_from_output(r_in.stdout)
    inspect_path.write_text(json.dumps(inspect_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    shot_dir = output_dir / "screenshots"
    argv_sh = [
        abin,
        "screenshot",
        str(input_path),
        "--output-dir",
        str(shot_dir),
        "--views",
        views,
        "--resolution",
        str(resolution),
    ]
    if show_bones:
        argv_sh.append("--show-bones")
    if frame_list:
        argv_sh.extend(["--frame-list", frame_list])
    elif frame is not None:
        argv_sh.extend(["--frame", str(frame)])

    r_sh = run_cmd(argv_sh)
    if r_sh.returncode != 0:
        err = merge_subprocess_output(r_sh, max_chars=2000) or "screenshot falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)
    shot_report = _extract_json_from_output(r_sh.stdout)
    (output_dir / "screenshot_report.json").write_text(
        json.dumps(shot_report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    bundle: dict[str, Any] = {
        "tool": "gameassets.debug.bundle",
        "gameassets_version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(input_path.resolve()),
        "input_size_bytes": input_path.stat().st_size if input_path.is_file() else 0,
        "inspect_path": str(inspect_path),
        "screenshot_dir": str(shot_dir),
        "screenshot_report_path": str(output_dir / "screenshot_report.json"),
        "inspect": inspect_data,
        "screenshots": shot_report.get("screenshots", []),
        "world_bounds": shot_report.get("world_bounds"),
        "mesh": shot_report.get("mesh"),
        "animations": shot_report.get("animations"),
    }
    bundle_path = output_dir / "bundle.json"
    bundle_path.write_text(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    console.print(f"[green]Bundle:[/green] {bundle_path}")
    console.print(f"  inspect → {inspect_path}")
    console.print(f"  screenshots → {shot_dir} ({len(bundle['screenshots'])} imagens)")
    sys.stdout.write(json.dumps(bundle, indent=2, ensure_ascii=False) + "\n")


@debug_group.command("inspect")
@click.argument("input_path", type=click.Path(exists=True, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None, help="Guardar JSON em ficheiro.")
def debug_inspect(input_path: Path, output: Path | None) -> None:
    """Mostra metadados de armature/mesh/animacao em JSON (via animator3d)."""
    from .pipeline import _resolve_animator3d_bin

    abin = _resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d nao encontrado.[/red]")
        sys.exit(1)

    argv = [abin, "inspect", str(input_path), "--json-out"]
    r = run_cmd(argv)
    if r.returncode != 0:
        err = merge_subprocess_output(r, max_chars=2000) or "animator3d inspect falhou"
        console.print(f"[red]Erro:[/red] {err}")
        sys.exit(1)

    data = _extract_json_from_output(r.stdout)

    data["file_size_bytes"] = input_path.stat().st_size if input_path.is_file() else 0
    data["input"] = str(input_path)

    out_str = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(out_str)
        console.print(f"[green]Guardado:[/green] {output}")
    else:
        sys.stdout.write(out_str)


@debug_group.command("compare")
@click.argument("file_a", type=click.Path(exists=True, path_type=Path))
@click.argument("file_b", type=click.Path(exists=True, path_type=Path))
@click.option("--output-dir", "-o", type=click.Path(path_type=Path), default=None, help="Pasta destino.")
@click.option("--views", default="front,three_quarter", show_default=True, help="Vistas para comparar.")
@click.option("--resolution", "-r", default=512, show_default=True, type=int, help="Resolucao px.")
@click.option(
    "--with-inspect",
    "with_inspect",
    is_flag=True,
    help="Incluir inspect JSON por modelo (ossos, meshes, bounds) no diff_report.",
)
def debug_compare(
    file_a: Path,
    file_b: Path,
    output_dir: Path | None,
    views: str,
    resolution: int,
    with_inspect: bool,
) -> None:
    """Compara dois modelos lado a lado (screenshots + report JSON)."""
    from .pipeline import _resolve_animator3d_bin

    abin = _resolve_animator3d_bin()
    if not abin:
        console.print("[red]animator3d nao encontrado.[/red]")
        sys.exit(1)

    if output_dir is None:
        output_dir = file_a.parent / f"{file_a.stem}_vs_{file_b.stem}"
    output_dir.mkdir(parents=True, exist_ok=True)
    dir_a = output_dir / "a"
    dir_b = output_dir / "b"

    inspect_side: dict[str, Any] = {}
    if with_inspect:
        for label, fpath in [("a", file_a), ("b", file_b)]:
            r_i = run_cmd([abin, "inspect", str(fpath), "--json-out"])
            if r_i.returncode == 0:
                inspect_side[label] = _extract_json_from_output(r_i.stdout)
            else:
                inspect_side[label] = {"_error": merge_subprocess_output(r_i, max_chars=500)}

    reports = {}
    for label, fpath, d in [("a", file_a, dir_a), ("b", file_b, dir_b)]:
        argv = [
            abin,
            "screenshot",
            str(fpath),
            "--output-dir",
            str(d),
            "--views",
            views,
            "--resolution",
            str(resolution),
            "--show-bones",
        ]
        r = run_cmd(argv)
        if r.returncode != 0:
            console.print(
                f"[red]Erro ao gerar screenshots de {label}:[/red] {merge_subprocess_output(r, max_chars=500)}"
            )
            sys.exit(1)
        reports[label] = _extract_json_from_output(r.stdout)

    side_by_side_paths = []
    try:
        from PIL import Image

        view_list = [v.strip() for v in views.split(",") if v.strip()]
        for vn in view_list:
            pa = dir_a / f"{vn}.png"
            pb = dir_b / f"{vn}.png"
            if pa.is_file() and pb.is_file():
                img_a = Image.open(pa)
                img_b = Image.open(pb)
                w = img_a.width + img_b.width + 4
                h = max(img_a.height, img_b.height)
                combined = Image.new("RGBA", (w, h), (30, 30, 30, 255))
                combined.paste(img_a, (0, 0))
                combined.paste(img_b, (img_a.width + 4, 0))
                out_path = output_dir / f"compare_{vn}.png"
                combined.save(out_path)
                side_by_side_paths.append({"view": vn, "path": str(out_path)})
    except ImportError:
        console.print("[yellow]Pillow nao instalado — side-by-side nao gerado.[/yellow]")

    diff_report: dict[str, Any] = {
        "file_a": str(file_a),
        "file_b": str(file_b),
        "report_a": reports.get("a", {}),
        "report_b": reports.get("b", {}),
        "side_by_side": side_by_side_paths,
    }
    if inspect_side:
        diff_report["inspect"] = inspect_side
    diff_path = output_dir / "diff_report.json"
    diff_path.write_text(json.dumps(diff_report, indent=2, ensure_ascii=False) + "\n")
    console.print(f"[green]Comparacao:[/green] {output_dir}")
    n = len(side_by_side_paths)
    console.print(f"  {n} imagens side-by-side, report em {diff_path}")


@main.command("validate")
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
@click.option("--max-poly-count", type=int, default=100_000, show_default=True, help="Maximum face count per mesh")
@click.option("--max-file-size-mb", type=float, default=50.0, show_default=True, help="Maximum file size in MB")
def validate_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    max_poly_count: int,
    max_file_size_mb: float,
) -> None:
    """Validate generated assets against quality thresholds."""
    from .validator import validate_row

    profile, rows, _bundle, _preset = _build_context(profile_path, manifest_path, presets_local)
    manifest_dir = manifest_path.parent.resolve()

    results = []
    for row in rows:
        r = validate_row(row, profile, manifest_dir, max_poly_count=max_poly_count, max_file_size_mb=max_file_size_mb)
        results.append(r)

    total = len(results)
    errors = sum(len(r.errors) for r in results)
    warnings = sum(len(r.warnings) for r in results)
    ok_count = sum(1 for r in results if r.ok)

    table = Table(title="Validação de Assets", box=box.SIMPLE, show_header=True)
    table.add_column("ID", style="bold")
    table.add_column("Status")
    table.add_column("Erros", style="red")
    table.add_column("Avisos", style="yellow")

    for r in results:
        status = "[green]✓[/green]" if r.ok else "[red]✗[/red]"
        err_text = "; ".join(r.errors) if r.errors else "—"
        warn_text = "; ".join(r.warnings) if r.warnings else "—"
        table.add_row(r.row_id, status, err_text, warn_text)

    console.print(table)
    console.print(f"\n[bold]{ok_count}/{total}[/bold] assets OK, {errors} erros, {warnings} avisos")

    if errors:
        sys.exit(1)


@main.command("dream")
@click.argument("description")
@click.option("--output-dir", type=Path, default=".", help="Pasta raiz onde o projecto será criado.")
@click.option(
    "--llm-provider", default="openai", type=click.Choice(["openai", "huggingface", "stdin"]), help="Provider LLM."
)
@click.option("--llm-model", default=None, help="Modelo LLM (ex.: gpt-4o-mini, meta-llama/Llama-3.1-8B-Instruct).")
@click.option("--llm-api-key", default=None, help="API key (override OPENAI_API_KEY).")
@click.option("--llm-base-url", default=None, help="Base URL (OpenAI-compatible).")
@click.option("--style-preset", default=None, help="Override do preset de estilo.")
@click.option("--max-assets", default=8, type=int, help="Número máximo de assets.")
@click.option("--with-audio/--no-audio", default=True, help="Incluir assets de áudio.")
@click.option("--with-sky/--no-sky", default=True, help="Gerar sky equirectangular.")
@click.option("--terrain/--no-terrain", default=None, help="Enable/disable terrain (default: auto via LLM plan).")
@click.option("--terrain-seed", default=None, type=int, help="Override terrain seed.")
@click.option("--terrain-size", default=None, type=int, help="Heightmap resolution (default: 1024).")
@click.option("--terrain-world-size", default=None, type=float, help="World size in meters (default: 256).")
@click.option("--terrain-max-height", default=None, type=float, help="Max terrain height (default: 50).")
@click.option("--presets-local", type=Path, default=None, help="Ficheiro de presets local.")
@click.option("--dry-run", is_flag=True, default=False, help="Gerar ficheiros sem executar batch/sky (sem GPU).")
@click.option("--plan-json", type=Path, default=None, help="Exportar dream_plan.json para este caminho.")
@click.option(
    "--low-vram",
    is_flag=True,
    help="Modo baixa VRAM: propaga --low-vram a todos os sub-tools.",
)
def dream_cmd(
    description: str,
    output_dir: Path,
    llm_provider: str,
    llm_model: str | None,
    llm_api_key: str | None,
    llm_base_url: str | None,
    style_preset: str | None,
    max_assets: int,
    with_audio: bool,
    with_sky: bool,
    terrain: bool | None,
    terrain_seed: int | None,
    terrain_size: int | None,
    terrain_world_size: float | None,
    terrain_max_height: float | None,
    presets_local: Path | None,
    dry_run: bool,
    plan_json: Path | None,
    low_vram: bool,
) -> None:
    """Da ideia ao jogo: gera assets, cena e projecto Vite com IA.

    DESCRIPTION é a descrição do jogo em linguagem natural.
    """
    from .dream.planner import plan_game
    from .dream.runner import run_dream

    bundle = load_presets_bundle(presets_local)
    preset_names = sorted(bundle.keys())

    plan = plan_game(
        description,
        preset_names=preset_names,
        style_preset=style_preset,
        max_assets=max_assets,
        with_audio=with_audio,
        with_sky=with_sky,
        provider=llm_provider,
        model=llm_model,
        api_key=llm_api_key,
        base_url=llm_base_url,
        plan_json_path=str(plan_json) if plan_json else None,
    )

    if terrain is not None:
        from .dream.planner import TerrainPlan

        if terrain and plan.terrain is None:
            plan.terrain = TerrainPlan(enabled=True)
        elif not terrain and plan.terrain is not None:
            plan.terrain.enabled = False
    if plan.terrain is not None:
        if terrain_seed is not None:
            plan.terrain.seed = terrain_seed
        if terrain_size is not None:
            plan.terrain.size = terrain_size
        if terrain_world_size is not None:
            plan.terrain.world_size = terrain_world_size
        if terrain_max_height is not None:
            plan.terrain.max_height = terrain_max_height

    report = run_dream(
        plan,
        output_dir,
        with_sky=with_sky,
        with_audio=with_audio,
        dry_run=dry_run,
        low_vram=low_vram,
    )

    if plan_json:
        console.print(f"[cyan]Plan JSON:[/cyan] {plan_json}")

    if dry_run:
        console.print("[cyan]dry-run:[/cyan] nenhum asset gerado (sem GPU).")
    else:
        ok_count = sum(1 for s in report.get("steps", []) if s.get("ok"))
        total = len(report.get("steps", []))
        console.print(f"[green]{ok_count}/{total} passos OK.[/green]")


# --- Register extracted commands ---
from .batch_cmd import batch_cmd  # noqa: E402
from .resume_cmd import resume_cmd  # noqa: E402

main.add_command(batch_cmd)
main.add_command(resume_cmd)


if __name__ == "__main__":
    main()
