#!/usr/bin/env python3
"""GameAssets — CLI principal."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table

from gamedev_shared.profiler.session import ProfilerSession
from gamedev_shared.skill_install import install_my_skill

from . import __version__
from .batch_guard import batch_directory_lock, query_gpu_free_mib, subprocess_gpu_env
from .cli_rich import click
from .manifest import ManifestRow, effective_image_source, load_manifest
from .presets import get_preset, load_presets_bundle
from .profile import (
    GameProfile,
    Part3DProfile,
    Rigging3DProfile,
    Text2SoundProfile,
    Texture2DProfile,
    load_profile,
)
from .prompt_builder import build_audio_prompt, build_prompt
from .runner import merge_subprocess_output, resolve_binary, run_cmd
from .templates import GAME_YAML, MANIFEST_CSV

console = Console()

EPILOG = """
Exemplo rápido:
  gameassets init
  gameassets prompts --profile game.yaml --manifest manifest.csv
  gameassets batch --profile game.yaml --manifest manifest.csv --with-3d

Preset só num ficheiro teu (ex.: galaxy_orbital em presets-local.yaml):
  gameassets batch --profile game.yaml --manifest manifest.csv --with-3d \\
    --presets-local presets-local.yaml --log run.jsonl

Define TEXT2D_BIN / TEXT3D_BIN / PAINT3D_BIN (se ``text3d.texture``) / RIGGING3D_BIN / PART3D_BIN.
Text3D gera só geometria; textura e PBR no GLB vêm do ``paint3d`` (Hunyuan3D-Paint 2.1).
Com image_source: texture2d: TEXTURE2D_BIN e, se texture2d.materialize,
MATERIALIZE_BIN (ou texture2d.materialize_bin) — só para mapas PBR a partir da imagem difusa.
Com generate_audio no CSV: TEXT2SOUND_BIN se text2sound não estiver no PATH.
"""


def _seed_for_row(profile: GameProfile, row_id: str) -> int | None:
    if profile.seed_base is None:
        return None
    h = zlib.adler32(row_id.encode("utf-8")) & 0x7FFFFFFF
    return profile.seed_base + h


def _safe_row_dirname(row_id: str) -> str:
    """Parte do id do manifest segura para nome de pasta (ex.: Props/crate → Props__crate_01)."""
    return row_id.replace("/", "__").replace("\\", "_")


def _text2sound_profile_effective(profile: GameProfile) -> Text2SoundProfile:
    """Opções Text2Sound do perfil ou defaults."""
    return profile.text2sound or Text2SoundProfile()


def _audio_path_for_row(profile: GameProfile, row: ManifestRow) -> Path:
    """Ficheiro de áudio final (extensão do perfil text2sound)."""
    ts = _text2sound_profile_effective(profile)
    ext = (ts.audio_format or "wav").lower().strip().lstrip(".")
    root = Path(profile.output_dir)
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            sub = Path(*parts[:-1])
            base = parts[-1]
            dir_ = root / sub
        else:
            dir_ = root
            base = rid
        return dir_ / f"{base}.{ext}"
    return root / profile.audio_subdir / f"{rid}.{ext}"


def _audio_path_for_row_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> Path:
    rel = _audio_path_for_row(profile, row)
    if rel.is_absolute():
        return rel.resolve()
    return (manifest_dir / rel).resolve()


def _append_text2sound_profile_args(ts: Text2SoundProfile, argv: list[str]) -> None:
    """Extensões do perfil para `text2sound generate`."""
    if ts.duration is not None:
        argv.extend(["-d", str(ts.duration)])
    if ts.steps is not None:
        argv.extend(["-s", str(ts.steps)])
    if ts.cfg_scale is not None:
        argv.extend(["-c", str(ts.cfg_scale)])
    fmt = (ts.audio_format or "wav").lower().strip().lstrip(".")
    argv.extend(["-f", fmt])
    if ts.preset and ts.preset.lower() != "none":
        argv.extend(["-p", ts.preset])
    if ts.sigma_min is not None:
        argv.extend(["--sigma-min", str(ts.sigma_min)])
    if ts.sigma_max is not None:
        argv.extend(["--sigma-max", str(ts.sigma_max)])
    if ts.sampler:
        argv.extend(["--sampler", ts.sampler])
    if ts.trim is not None:
        argv.append("--trim" if ts.trim else "--no-trim")
    if ts.model_id:
        argv.extend(["-m", ts.model_id])
    if ts.half_precision is True:
        argv.append("--half")
    elif ts.half_precision is False:
        argv.append("--no-half")


def _texture2d_profile_effective(profile: GameProfile) -> Texture2DProfile:
    """Opções Texture2D do perfil ou defaults (para linhas CSV texture2d sem bloco no YAML)."""
    return profile.texture2d or Texture2DProfile()


def _row_uses_texture2d(profile: GameProfile, row: ManifestRow) -> bool:
    return effective_image_source(profile, row) == "texture2d"


def _texture2d_material_maps_path(profile: GameProfile, row: ManifestRow) -> Path:
    """Destino dos mapas PBR (Materialize CLI) quando a linha usa texture2d."""
    tt = _texture2d_profile_effective(profile)
    sub = tt.materialize_maps_subdir or "pbr_maps"
    return Path(profile.output_dir) / sub / _safe_row_dirname(row.id)


def _texture2d_material_maps_path_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> Path:
    rel = _texture2d_material_maps_path(profile, row)
    if rel.is_absolute():
        return rel.resolve()
    return (manifest_dir / rel).resolve()


def _paths_for_row_manifest(
    profile: GameProfile,
    manifest_dir: Path,
    row: ManifestRow,
) -> tuple[Path, Path]:
    """
    PNG/GLB absolutos. O perfil usa muitas vezes output_dir: '.' — sem isto, caminhos relativos
    dependem do CWD do processo e o Text3D pode ler ficheiros errados (GPU “parada”).
    """
    img, mesh = _paths_for_row(profile, row)
    img = (manifest_dir / img).resolve() if not img.is_absolute() else img.resolve()
    mesh = (manifest_dir / mesh).resolve() if not mesh.is_absolute() else mesh.resolve()
    return img, mesh


def _path_for_log(path: Path, manifest_dir: Path) -> str:
    """Caminho para run.jsonl: relativo ao manifest quando possível."""
    try:
        return str(path.resolve().relative_to(manifest_dir.resolve()))
    except ValueError:
        return str(path.resolve())


def _install_file(src: Path, dst: Path) -> None:
    """Copia ficheiro para destino final (pasta do jogo); cria pais se necessário."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _install_maps_dir(src: Path, dst: Path) -> None:
    """Copia mapas PBR de uma pasta de trabalho (tmp) para a pasta final no jogo."""
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for p in src.iterdir():
        if p.is_file():
            shutil.copy2(p, dst / p.name)
        elif p.is_dir():
            _install_maps_dir(p, dst / p.name)


def _append_text2d_profile_args(profile: GameProfile, argv: list[str]) -> None:
    """Extensões do perfil (resolução, VRAM) para `text2d generate`."""
    t2 = profile.text2d
    if not t2:
        return
    if t2.width is not None:
        argv.extend(["-W", str(t2.width)])
    if t2.height is not None:
        argv.extend(["-H", str(t2.height)])
    if t2.low_vram:
        argv.append("--low-vram")
    if t2.cpu:
        argv.append("--cpu")


def _append_texture2d_profile_args(tt: Texture2DProfile, argv: list[str]) -> None:
    """Extensões do perfil para `texture2d generate`."""
    if tt.width is not None:
        argv.extend(["-W", str(tt.width)])
    if tt.height is not None:
        argv.extend(["-H", str(tt.height)])
    if tt.steps is not None:
        argv.extend(["-s", str(tt.steps)])
    if tt.guidance_scale is not None:
        argv.extend(["-g", str(tt.guidance_scale)])
    if tt.negative_prompt:
        argv.extend(["-n", tt.negative_prompt])
    if tt.preset and tt.preset.lower() != "none":
        argv.extend(["-p", tt.preset])
    if tt.cfg_scale is not None:
        argv.extend(["--cfg-scale", str(tt.cfg_scale)])
    if tt.lora_strength is not None:
        argv.extend(["--lora-strength", str(tt.lora_strength)])
    if tt.model_id:
        argv.extend(["-m", tt.model_id])


def _resolve_materialize_bin_texture2d(tt: Texture2DProfile) -> str:
    """Binário Materialize para o fluxo texture2d (override no perfil ou MATERIALIZE_BIN)."""
    if tt.materialize_bin:
        return tt.materialize_bin
    return resolve_binary("MATERIALIZE_BIN", "materialize")


def _materialize_diffuse_argv(
    materialize_bin: str,
    tt: Texture2DProfile,
    diffuse_path: Path,
    output_dir: Path,
) -> list[str]:
    """Invocação materialize <difuso> -o <dir> (mapas PBR a partir de imagem)."""
    args = [materialize_bin, str(diffuse_path), "-o", str(output_dir)]
    fmt = tt.materialize_format or "png"
    args.extend(["-f", fmt])
    args.extend(["-q", str(tt.materialize_quality)])
    if tt.materialize_verbose:
        args.append("-v")
    return args


def _timing_append(rec: dict[str, Any], key: str, seconds: float) -> None:
    t = rec.setdefault("timings_sec", {})
    t[key] = round(seconds, 4)


def _paths_for_row(profile: GameProfile, row: ManifestRow) -> tuple[Path, Path]:
    root = Path(profile.output_dir)
    ext = profile.image_ext
    rid = row.id
    if profile.path_layout == "flat":
        parts = rid.split("/")
        if len(parts) >= 2:
            sub = Path(*parts[:-1])
            base = parts[-1]
            dir_ = root / sub
        else:
            dir_ = root
            base = rid
        img = dir_ / f"{base}.{ext}"
        mesh = dir_ / f"{base}.glb"
    else:
        img = root / profile.images_subdir / f"{rid}.{ext}"
        mesh = root / profile.meshes_subdir / f"{rid}.glb"
    return img, mesh


def _rigging3d_output_path(mesh_final: Path, suffix: str) -> Path:
    """ex.: ``hero.glb`` + ``_rigged`` → ``hero_rigged.glb``."""
    s = (suffix or "_rigged").strip()
    if s and not s.startswith("_"):
        s = f"_{s}"
    if not s:
        s = "_rigged"
    return mesh_final.with_name(f"{mesh_final.stem}{s}.glb")


def _rigging3d_pipeline_argv(
    rigging3d_bin: str,
    mesh_in: Path,
    mesh_out: Path,
    *,
    seed: int | None,
    rig_profile: Rigging3DProfile | None,
) -> list[str]:
    args = [
        rigging3d_bin,
        "pipeline",
        "--input",
        str(mesh_in),
        "--output",
        str(mesh_out),
    ]
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
) -> bool:
    """Corre ``rigging3d pipeline`` após Text3D (GLB base ou ``*_parts.glb`` se parts+rig). Devolve True se falhou."""
    if not with_rig or not row.generate_rig or not rigging3d_bin:
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
    )
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    _timing_append(rec, "rigging3d", time.perf_counter() - t0)
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


def _part3d_profile_effective(profile: GameProfile, row: ManifestRow | None = None) -> Part3DProfile:
    """Profile Part3D com possíveis overrides por linha do manifest."""
    base = profile.part3d or Part3DProfile()
    if row is None:
        return base
    # Aplicar overrides da linha (se definidos)
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
    return args


def _part3d_pipeline_failed(
    profile: GameProfile,
    row: ManifestRow,
    mesh_final: Path,
    rec: dict[str, Any],
    manifest_dir: Path,
    child_env: dict[str, str],
    part3d_bin: str | None,
    with_parts: bool,
) -> bool:
    """Corre ``part3d decompose`` após GLB do Text3D. Devolve True se falhou."""
    if not with_parts or not row.generate_parts or not part3d_bin:
        return False
    if not row.generate_3d:
        return False
    if not mesh_final.is_file():
        return False
    p3 = _part3d_profile_effective(profile, row)  # ← Usa overrides por linha
    out_parts, out_seg = _part3d_output_paths(mesh_final, p3)
    seed = _seed_for_row(profile, f"{row.id}:part3d")
    argv = _part3d_decompose_argv(part3d_bin, mesh_final, out_parts, out_seg, p3, seed)
    t0 = time.perf_counter()
    r = run_cmd(argv, extra_env=child_env, cwd=manifest_dir)
    _timing_append(rec, "part3d", time.perf_counter() - t0)
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
) -> bool:
    """Define mesh_path, part3d, rigging3d. Devolve True se algum passo falhou."""
    rec["mesh_path"] = _path_for_log(mesh_final, manifest_dir)
    part3d_fail = _part3d_pipeline_failed(
        profile,
        row,
        mesh_final,
        rec,
        manifest_dir,
        child_env,
        part3d_bin,
        with_parts,
    )
    rig_mesh_in = mesh_final
    if not part3d_fail and with_rig and row.generate_rig and with_parts and row.generate_parts:
        p3 = _part3d_profile_effective(profile, row)
        out_parts, _out_seg = _part3d_output_paths(mesh_final, p3)
        if not p3.segment_only and out_parts.is_file():
            rig_mesh_in = out_parts
            rec["rig_input_path"] = _path_for_log(rig_mesh_in, manifest_dir)
    return part3d_fail or _rigging3d_pipeline_failed(
        profile,
        row,
        rig_mesh_in,
        rec,
        manifest_dir,
        child_env,
        rigging3d_bin,
        with_rig,
    )


def _build_context(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
) -> tuple[GameProfile, list[ManifestRow], dict[str, Any], dict[str, Any]]:
    profile = load_profile(profile_path)
    rows = load_manifest(manifest_path)
    bundle = load_presets_bundle(presets_local)
    preset = get_preset(bundle, profile.style_preset)
    return profile, rows, bundle, preset


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


@main.command("init")
@click.option(
    "--path",
    "target_dir",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Diretório onde criar game.yaml e manifest.csv",
)
@click.option("--force", is_flag=True, help="Sobrescrever ficheiros existentes")
def init_cmd(target_dir: Path, force: bool) -> None:
    """Cria game.yaml e manifest.csv de exemplo."""
    target_dir = target_dir.resolve()
    target_dir.mkdir(parents=True, exist_ok=True)
    gy = target_dir / "game.yaml"
    mc = target_dir / "manifest.csv"
    if gy.exists() and not force:
        raise click.ClickException(f"Já existe {gy} (usa --force para sobrescrever)")
    if mc.exists() and not force:
        raise click.ClickException(f"Já existe {mc} (usa --force para sobrescrever)")
    gy.write_text(GAME_YAML, encoding="utf-8")
    mc.write_text(MANIFEST_CSV, encoding="utf-8")
    console.print(
        Panel(
            f"Criados [bold cyan]{gy}[/bold cyan] e [bold cyan]{mc}[/bold cyan].\n\n"
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
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="manifest.csv",
    help="CSV com id, idea e colunas opcionais",
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
    table.add_column("3D?", justify="center")
    table.add_column("áudio?", justify="center")
    table.add_column("rig?", justify="center")
    table.add_column("prompt (início)", overflow="ellipsis", max_width=64)
    for e in entries:
        p = e["prompt"]
        preview = p if len(p) <= 64 else p[:61] + "..."
        flag3 = "sim" if e["generate_3d"] else "não"
        flag_a = "sim" if e["generate_audio"] else "não"
        flag_r = "sim" if e["generate_rig"] else "não"
        table.add_row(e["id"], flag3, flag_a, flag_r, preview)
    console.print(table)


@main.command("batch")
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="game.yaml",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="manifest.csv",
)
@click.option(
    "--presets-local",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--with-3d",
    "with_3d",
    is_flag=True,
    help="Gera GLB quando generate_3d=true no manifest",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Mostra comandos sem executar",
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
    help="Não avisar quando a VRAM livre (nvidia-smi) estiver baixa.",
)
@click.option(
    "--skip-text2d",
    "skip_text2d",
    is_flag=True,
    help=(
        "Não gerar imagens 2D (Text2D ou Texture2D): usa PNG já em output_dir "
        "(exige --with-3d; valida PNG por linha com generate_3d)."
    ),
)
@click.option(
    "--skip-audio",
    "skip_audio",
    is_flag=True,
    help="Não gerar áudio Text2Sound (ignora coluna generate_audio).",
)
@click.option(
    "--with-rig",
    "with_rig",
    is_flag=True,
    help="Após Text3D, corre rigging3d pipeline nas linhas com generate_rig=true (GLB rigado).",
)
@click.option(
    "--with-parts",
    "with_parts",
    is_flag=True,
    help="Após Text3D, corre part3d decompose nas linhas com generate_parts=true (partes semânticas).",
)
@click.option(
    "--profile-tools",
    is_flag=True,
    help="Activar profiling (CPU/RAM/GPU) em paint3d e part3d via GAMEDEV_PROFILE.",
)
@click.option(
    "--profile-log",
    "profile_tools_log",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="JSONL para spans (defeito: gameassets_profile.jsonl na pasta do manifest).",
)
def batch_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    with_3d: bool,
    dry_run: bool,
    fail_fast: bool,
    log_path: Path | None,
    skip_batch_lock: bool,
    skip_gpu_preflight: bool,
    skip_text2d: bool,
    skip_audio: bool,
    with_rig: bool,
    with_parts: bool,
    profile_tools: bool,
    profile_tools_log: Path | None,
) -> None:
    """Gera imagens (e opcionalmente meshes) para cada linha do manifest."""
    profile, rows, _bundle, preset = _build_context(profile_path, manifest_path, presets_local)

    if skip_text2d and not with_3d:
        raise click.ClickException("--skip-text2d só é válido com --with-3d (PNGs já existem; só corre Text3D).")

    if not with_3d and any(r.generate_3d for r in rows):
        console.print(
            Panel(
                "[yellow]Há linhas com generate_3d=true mas 3D está desligado.[/yellow]\n"
                "Usa [bold]--with-3d[/bold] para gerar meshes.",
                title="Aviso",
                border_style="yellow",
            )
        )

    if not with_3d and any(r.generate_rig for r in rows):
        console.print(
            Panel(
                "[yellow]Há linhas com generate_rig=true mas --with-3d está desligado.[/yellow]\n"
                "O rigging3d só corre após Text3D; activa [bold]--with-3d[/bold] e generate_3d.",
                title="Aviso",
                border_style="yellow",
            )
        )

    if not with_rig and any(r.generate_rig for r in rows):
        console.print(
            Panel(
                "[yellow]Há linhas com generate_rig=true mas o rig está desligado.[/yellow]\n"
                "Usa [bold]--with-rig[/bold] para correr rigging3d após o GLB do Text3D.",
                title="Aviso",
                border_style="yellow",
            )
        )

    if not with_3d and any(r.generate_parts for r in rows):
        console.print(
            Panel(
                "[yellow]Há linhas com generate_parts=true mas --with-3d está desligado.[/yellow]\n"
                "O Part3D corre após o GLB do Text3D; activa [bold]--with-3d[/bold] e generate_3d.",
                title="Aviso",
                border_style="yellow",
            )
        )

    if not with_parts and any(r.generate_parts for r in rows):
        console.print(
            Panel(
                "[yellow]Há linhas com generate_parts=true mas Part3D está desligado.[/yellow]\n"
                "Usa [bold]--with-parts[/bold] para correr part3d após o GLB do Text3D.",
                title="Aviso",
                border_style="yellow",
            )
        )

    def _row_sources() -> tuple[bool, bool]:
        any_t2d = False
        any_tex = False
        for r in rows:
            src = effective_image_source(profile, r)
            if src == "text2d":
                any_t2d = True
            elif src == "texture2d":
                any_tex = True
            # skymap2d: pipeline próprio (Skymap2D); não usar binário texture2d
        return any_t2d, any_tex

    any_text2d_row, any_texture2d_row = _row_sources()

    text2d_bin: str | None = None
    texture2d_bin: str | None = None
    if not skip_text2d:
        if any_text2d_row:
            try:
                text2d_bin = resolve_binary("TEXT2D_BIN", "text2d")
            except FileNotFoundError as e:
                raise click.ClickException(str(e)) from e
        if any_texture2d_row:
            try:
                texture2d_bin = resolve_binary("TEXTURE2D_BIN", "texture2d")
            except FileNotFoundError as e:
                raise click.ClickException(str(e)) from e
    text3d_bin: str | None = None
    paint3d_bin: str | None = None
    if with_3d:
        try:
            text3d_bin = resolve_binary("TEXT3D_BIN", "text3d")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e
        t3_chk = profile.text3d
        if t3_chk and t3_chk.texture:
            try:
                paint3d_bin = resolve_binary("PAINT3D_BIN", "paint3d")
            except FileNotFoundError as e:
                raise click.ClickException("Perfil com text3d.texture requer paint3d no PATH ou PAINT3D_BIN.") from e

    rigging3d_bin: str | None = None
    if with_rig and any(r.generate_rig for r in rows):
        try:
            rigging3d_bin = resolve_binary("RIGGING3D_BIN", "rigging3d")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    part3d_bin: str | None = None
    if with_parts and any(r.generate_parts for r in rows):
        try:
            part3d_bin = resolve_binary("PART3D_BIN", "part3d")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

    any_audio_row = any(r.generate_audio for r in rows)
    text2sound_bin: str | None = None
    if any_audio_row and not skip_audio:
        try:
            text2sound_bin = resolve_binary("TEXT2SOUND_BIN", "text2sound")
        except FileNotFoundError as e:
            raise click.ClickException(str(e)) from e

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
        t3_meta = profile.text3d
        if t3_meta and t3_meta.texture:
            meta.add_row("paint3d", paint3d_bin or "[red]em falta[/red]")
        else:
            meta.add_row("paint3d", "[dim](não necessário — sem textura 3D)[/dim]")
    meta.add_row("rigging3d", rigging3d_bin or "[dim](desligado)[/dim]")
    meta.add_row("part3d", part3d_bin or "[dim](desligado)[/dim]")
    meta.add_row("Modo", "[cyan]dry-run[/cyan]" if dry_run else "execução")
    if profile_tools:
        _plog = profile_tools_log or (manifest_path.parent / "gameassets_profile.jsonl")
        meta.add_row("Profiler (GPU)", f"activo → [dim]{_plog.resolve()}[/dim]")
    if skip_text2d:
        ord_skip: list[str] = ["Geração 2D omitida"]
        if any_audio_row and not skip_audio:
            ord_skip.append("Text2Sound (linhas generate_audio)")
        if with_3d:
            ord_skip.append("Text3D (só generate_3d, PNG no output_dir)")
            if with_parts:
                ord_skip.append("Part3D (generate_parts)")
        meta.add_row("Ordem", " → ".join(ord_skip))
    elif any_texture2d_row or any_text2d_row:
        if any_texture2d_row and any_text2d_row:
            ord_parts = ["Geração 2D por linha (text2d e/ou texture2d)"]
        elif any_texture2d_row:
            ord_parts = ["Texture2D (todas as linhas)"]
        else:
            ord_parts = ["Text2D (todas as linhas)"]
        if tt_eff.materialize and any_texture2d_row:
            ord_parts.append("Materialize (mapas PBR nas linhas texture2d)")
        if any_audio_row and not skip_audio:
            ord_parts.append("Text2Sound (linhas generate_audio)")
        if with_3d:
            ord_parts.append("Text3D (só generate_3d)")
            if with_parts:
                ord_parts.append("Part3D (generate_parts)")
        meta.add_row("Ordem", " → ".join(ord_parts))
    else:
        ord_tail = "Text2D (todas as linhas)"
        if any_audio_row and not skip_audio:
            ord_tail += " → Text2Sound (linhas generate_audio)"
        if with_3d:
            ord_tail += " → Text3D (só generate_3d, imagens já gravadas)"
            if with_parts:
                ord_tail += " → Part3D (generate_parts)"
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
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)

    def append_log(rec: dict[str, Any]) -> None:
        if log_path is None:
            return
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    if dry_run:
        if not skip_text2d:
            if any_texture2d_row and any_text2d_row:
                p1_title = "--- Fase 1: Text2D / Texture2D (por linha) ---"
            elif any_texture2d_row:
                p1_title = "--- Fase 1: Texture2D (todas as linhas) ---"
            else:
                p1_title = "--- Fase 1: Text2D (todas as linhas) ---"
            console.print(f"[dim]{p1_title}[/dim]")
            for row in rows:
                prompt = build_prompt(profile, preset, row, for_3d=False)
                img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                seed = _seed_for_row(profile, row.id)
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
                    _append_texture2d_profile_args(tt_line, t2d_args)
                else:
                    t2d_args = [
                        text2d_bin or "",
                        "generate",
                        prompt,
                        "-o",
                        str(img_path),
                    ]
                    if seed is not None:
                        t2d_args.extend(["--seed", str(seed)])
                    _append_text2d_profile_args(profile, t2d_args)
                console.print(f"[dim]{' '.join(t2d_args)}[/dim]")
                if _row_uses_texture2d(profile, row) and tt_line.materialize:
                    maps_ph = _texture2d_material_maps_path(profile, row)
                    try:
                        mbin_dr = _resolve_materialize_bin_texture2d(tt_line)
                    except FileNotFoundError:
                        mbin_dr = "materialize"
                    margv = _materialize_diffuse_argv(mbin_dr, tt_line, img_path, maps_ph)
                    console.print(f"[dim]{' '.join(margv)}[/dim]")
                if not skip_audio and text2sound_bin and row.generate_audio:
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
                    _append_text2sound_profile_args(ts_line, argv_au)
                    console.print(f"[dim]{' '.join(argv_au)}[/dim]")
        else:
            console.print("[dim]--- Text2D omitido (--skip-text2d) ---[/dim]")
            if not skip_audio and text2sound_bin and any(r.generate_audio for r in rows):
                console.print("[dim]--- Text2Sound (generate_audio; PNG em output_dir) ---[/dim]")
                for row in rows:
                    if not row.generate_audio:
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
                    _append_text2sound_profile_args(ts_line, argv_au)
                    console.print(f"[dim]{' '.join(argv_au)}[/dim]")
        if with_3d and text3d_bin and any(r.generate_3d for r in rows):
            t3d = profile.text3d
            phased = bool(t3d and t3d.texture)
            if phased:
                console.print("[dim]--- Text3D + paint3d: shape → texture (PBR no GLB via Paint 2.1) ---[/dim]")
                for row in rows:
                    if not row.generate_3d:
                        continue
                    img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                    seed = _seed_for_row(profile, row.id)
                    tw = "<tmp>/shape.glb"
                    a1 = _text3d_argv(
                        text3d_bin,
                        profile,
                        img_path,
                        Path(tw),
                        row,
                        shape_only=True,
                    )
                    if seed is not None:
                        a1 = [*a1, "--seed", str(seed)]
                    console.print(f"[dim]{' '.join(a1)}[/dim]")
                    pbin = paint3d_bin or "paint3d"
                    a2 = _texture_subprocess_argv(
                        pbin,
                        profile,
                        Path(tw),
                        img_path,
                        mesh_path,
                    )
                    console.print(f"[dim]{' '.join(a2)}[/dim]")
            else:
                console.print("[dim]--- Fase 2: Text3D (generate_3d=true) ---[/dim]")
                for row in rows:
                    if not row.generate_3d:
                        continue
                    img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                    seed = _seed_for_row(profile, row.id)
                    t3d_args = _text3d_argv(text3d_bin, profile, img_path, mesh_path, row)
                    if seed is not None:
                        t3d_args.extend(["--seed", str(seed)])
                    console.print(f"[dim]{' '.join(t3d_args)}[/dim]")
        if with_parts and part3d_bin and any(r.generate_3d and r.generate_parts for r in rows):
            console.print("[dim]--- Part3D (após GLB Text3D; generate_parts=true) ---[/dim]")
            for row in rows:
                if not row.generate_3d or not row.generate_parts:
                    continue
                _img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                p3 = _part3d_profile_effective(profile, row)
                out_p, out_s = _part3d_output_paths(mesh_path, p3)
                seed = _seed_for_row(profile, f"{row.id}:part3d")
                pa = _part3d_decompose_argv(part3d_bin, mesh_path, out_p, out_s, p3, seed)
                console.print(f"[dim]{' '.join(pa)}[/dim]")
        if with_rig and rigging3d_bin and any(r.generate_3d and r.generate_rig for r in rows):
            console.print("[dim]--- Rigging3D (entrada: *_parts.glb se parts+rig; senão GLB base) ---[/dim]")
            for row in rows:
                if not row.generate_3d or not row.generate_rig:
                    continue
                _img_path, mesh_path = _paths_for_row_manifest(profile, manifest_dir, row)
                seed = _seed_for_row(profile, row.id)
                rg = profile.rigging3d
                sfx = rg.output_suffix if rg else "_rigged"
                rig_in = mesh_path
                p3_row = _part3d_profile_effective(profile, row)
                if with_parts and row.generate_parts and not p3_row.segment_only:
                    out_p, _ = _part3d_output_paths(mesh_path, p3_row)
                    rig_in = out_p
                rig_out = _rigging3d_output_path(rig_in, sfx)
                rg_args = _rigging3d_pipeline_argv(
                    rigging3d_bin,
                    rig_in,
                    rig_out,
                    seed=seed,
                    rig_profile=rg,
                )
                console.print(f"[dim]{' '.join(rg_args)}[/dim]")
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

    child_env = subprocess_gpu_env()
    if profile_tools:
        child_env = dict(child_env)
        child_env["GAMEDEV_PROFILE"] = "1"
        child_env["GAMEDEV_PROFILE_TOOL"] = "gameassets"
        plog = profile_tools_log or (manifest_path.parent / "gameassets_profile.jsonl")
        child_env["GAMEDEV_PROFILE_LOG"] = str(plog.resolve())

    _batch_params = {
        "rows": len(rows),
        "with_3d": with_3d,
        "with_rig": with_rig,
        "with_parts": with_parts,
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
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(bar_width=None),
                TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
                TimeElapsedColumn(),
                console=console,
            ) as progress:
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
                        "audio_path": None,
                        "error": None,
                        "audio_error": None,
                        "timings_sec": {},
                    }

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
                        defer_audio = row.generate_audio and not skip_audio and bool(text2sound_bin)
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
                        seed = _seed_for_row(profile, row.id)
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
                            _append_texture2d_profile_args(tt_line, t2d_args)
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
                        defer_audio = row.generate_audio and not skip_audio and bool(text2sound_bin)
                        if do_3d and text3d_bin:
                            pending_3d_indices.append(idx)
                        else:
                            if not defer_audio:
                                append_log(rec)
                    finally:
                        shutil.rmtree(row_work, ignore_errors=True)
                        progress.advance(task1)

                if not skip_audio and text2sound_bin and any(r.generate_audio for r in rows):
                    au_indices = [i for i, r in enumerate(rows) if r.generate_audio and results[i]["status"] == "ok"]
                    if au_indices:
                        task_au = progress.add_task(
                            "[cyan]Fase 1b: Text2Sound[/cyan]",
                            total=len(au_indices),
                        )
                        for idx in au_indices:
                            row = rows[idx]
                            rec = results[idx]
                            progress.update(
                                task_au,
                                description=f"[cyan]{row.id}[/cyan] · Text2Sound",
                            )
                            ts_line = _text2sound_profile_effective(profile)
                            ext = (ts_line.audio_format or "wav").lower().strip().lstrip(".")
                            audio_tmp = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_audio.{ext}"
                            audio_final = _audio_path_for_row_manifest(profile, manifest_dir, row)
                            prompt_a = build_audio_prompt(profile, preset, row)
                            argv_au = [
                                text2sound_bin,
                                "generate",
                                prompt_a,
                                "-o",
                                str(audio_tmp),
                            ]
                            seed_a = _seed_for_row(profile, f"{row.id}:audio")
                            if seed_a is not None:
                                argv_au.extend(["--seed", str(seed_a)])
                            _append_text2sound_profile_args(ts_line, argv_au)
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
                    if not row.generate_audio or skip_audio or not text2sound_bin:
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
                            "`nvidia-smi` deve mostrar VRAM livre. Em ~6 GB, "
                            "[bold]text3d.low_vram: true[/bold] no [cyan]game.yaml[/cyan] "
                            "evita OOM (malha pode ser mais grosseira). "
                            "Com [bold]text3d.texture[/bold], o batch corre: shape (text3d) → "
                            "paint3d texture (PBR no GLB), libertando VRAM entre passos.",
                            border_style="yellow",
                            title="Antes do 3D",
                        )
                    )
                    t3_opts = profile.text3d
                    use_phased = bool(t3_opts and t3_opts.texture)

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
                            part3d_bin,
                            with_parts,
                            rigging3d_bin,
                            with_rig,
                        ):
                            failures += 1

                    if use_phased:
                        task_shape = progress.add_task(
                            "[cyan]Text3D: shape (todos)[/cyan]",
                            total=len(pending_3d_indices),
                        )
                        shape_ok: list[int] = []
                        for idx in pending_3d_indices:
                            row = rows[idx]
                            rec = results[idx]
                            progress.update(
                                task_shape,
                                description=f"[cyan]{row.id}[/cyan] · shape",
                            )
                            row_work = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_3d"
                            row_work.mkdir(parents=True, exist_ok=True)
                            try:
                                img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_shape = row_work / "shape.glb"
                                seed = _seed_for_row(profile, row.id)
                                t3d_args = _text3d_argv(
                                    text3d_bin,
                                    profile,
                                    img_final,
                                    mesh_shape,
                                    row,
                                    shape_only=True,
                                )
                                if seed is not None:
                                    t3d_args.extend(["--seed", str(seed)])
                                t_shape = time.perf_counter()
                                r3 = run_cmd(t3d_args, extra_env=child_env, cwd=manifest_dir)
                                _timing_append(rec, "text3d_shape", time.perf_counter() - t_shape)
                                if r3.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r3) or "text3d generate (shape) falhou"
                                    rec["status"] = "error"
                                    rec["error"] = err
                                    preview = merge_subprocess_output(r3, max_chars=4000) or err
                                    console.print(f"[red]text3d shape falhou[/red] {row.id}: {preview}")
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                elif not mesh_shape.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "text3d não produziu shape.glb"
                                    console.print(f"[red]text3d sem shape.glb[/red] {row.id}")
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                else:
                                    shape_ok.append(idx)
                            finally:
                                if idx not in shape_ok:
                                    shutil.rmtree(row_work, ignore_errors=True)
                                progress.advance(task_shape)

                        task_paint = progress.add_task(
                            "[cyan]paint3d texture (todos)[/cyan]",
                            total=len(shape_ok),
                        )
                        for idx in shape_ok:
                            row = rows[idx]
                            rec = results[idx]
                            progress.update(
                                task_paint,
                                description=f"[cyan]{row.id}[/cyan] · Paint",
                            )
                            row_work = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_3d"
                            mesh_shape = row_work / "shape.glb"
                            img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                            try:
                                assert paint3d_bin is not None
                                t_tex = _texture_subprocess_argv(
                                    paint3d_bin,
                                    profile,
                                    mesh_shape,
                                    img_final,
                                    mesh_final,
                                )
                                t_paint = time.perf_counter()
                                r4 = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                                _timing_append(rec, "paint3d_texture", time.perf_counter() - t_paint)
                                if r4.returncode != 0:
                                    failures += 1
                                    err = merge_subprocess_output(r4) or "paint3d texture falhou"
                                    rec["status"] = "error"
                                    rec["error"] = err
                                    preview = merge_subprocess_output(r4, max_chars=4000) or err
                                    console.print(f"[red]texture (paint) falhou[/red] {row.id}: {preview}")
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                elif not mesh_final.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "texture não produziu GLB"
                                    console.print(f"[red]texture sem GLB[/red] {row.id}")
                                    append_log(rec)
                                    if not continue_on_error:
                                        raise click.Abort()
                                else:
                                    _finalize_mesh_ok(rec, mesh_final, row)
                                    append_log(rec)
                                    if not continue_on_error and rec["status"] == "error":
                                        raise click.Abort()
                            finally:
                                shutil.rmtree(row_work, ignore_errors=True)
                                progress.advance(task_paint)
                    else:
                        task2 = progress.add_task(
                            "[cyan]Fase 2: Text3D[/cyan]",
                            total=len(pending_3d_indices),
                        )
                        for idx in pending_3d_indices:
                            row = rows[idx]
                            rec = results[idx]
                            progress.update(task2, description=f"[cyan]{row.id}[/cyan] · Text3D")
                            row_work = batch_tmp / f"{idx:04d}_{_safe_row_dirname(row.id)}_3d"
                            row_work.mkdir(parents=True, exist_ok=True)
                            try:
                                img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
                                mesh_tmp = row_work / "out.glb"
                                seed = _seed_for_row(profile, row.id)

                                t3d_args = _text3d_argv(
                                    text3d_bin,
                                    profile,
                                    img_final,
                                    mesh_tmp,
                                    row,
                                )
                                if seed is not None:
                                    t3d_args.extend(["--seed", str(seed)])
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
                                elif not mesh_tmp.is_file():
                                    failures += 1
                                    rec["status"] = "error"
                                    rec["error"] = "text3d não produziu ficheiro GLB"
                                    console.print(f"[red]text3d sem GLB[/red] {row.id}")
                                else:
                                    _install_file(mesh_tmp, mesh_final)
                                    if _post_text3d_mesh_extras(
                                        profile,
                                        row,
                                        mesh_final,
                                        rec,
                                        manifest_dir,
                                        child_env,
                                        part3d_bin,
                                        with_parts,
                                        rigging3d_bin,
                                        with_rig,
                                    ):
                                        failures += 1
                                append_log(rec)
                                if not continue_on_error and rec["status"] == "error":
                                    raise click.Abort()
                            finally:
                                shutil.rmtree(row_work, ignore_errors=True)
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


def _try_paint3d_bin() -> str | None:
    try:
        return resolve_binary("PAINT3D_BIN", "paint3d")
    except FileNotFoundError:
        return None


def _paint3d_texture_argv(
    paint3d_bin: str,
    profile: GameProfile,
    mesh_in: Path,
    image_path: Path,
    mesh_out: Path,
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
    t3 = profile.text3d
    if not t3:
        return args
    if t3.allow_shared_gpu:
        args.append("--allow-shared-gpu")
    if not t3.gpu_kill_others:
        args.append("--no-gpu-kill-others")
    if t3.full_gpu:
        args.append("--paint-full-gpu")
    if t3.paint_max_views is not None:
        args.extend(["--max-views", str(t3.paint_max_views)])
    if t3.paint_view_resolution is not None:
        args.extend(["--view-resolution", str(t3.paint_view_resolution)])
    # --- Otimizações de VRAM ---
    if t3.paint_low_vram_mode:
        args.append("--low-vram-mode")
    else:
        if t3.paint_quantization:
            args.extend(["--quantization", t3.paint_quantization])
        if t3.paint_tiny_vae:
            args.append("--tiny-vae")
        if t3.paint_torch_compile:
            args.append("--torch-compile")
    return args


def _texture_subprocess_argv(
    paint3d_bin: str,
    profile: GameProfile,
    mesh_in: Path,
    image_path: Path,
    mesh_out: Path,
) -> list[str]:
    return _paint3d_texture_argv(
        paint3d_bin,
        profile,
        mesh_in,
        image_path,
        mesh_out,
    )


def _text3d_argv(
    text3d_bin: str,
    profile: GameProfile,
    image_path: Path,
    mesh_path: Path,
    row: ManifestRow | None = None,
    *,
    shape_only: bool = False,
) -> list[str]:
    """
    ``shape_only=True``: só Hunyuan (imagem → mesh), sem --texture
    (batch em fases: shape → ``paint3d texture``).
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
    if t3.no_mesh_repair:
        args.append("--no-mesh-repair")
    if t3.mesh_smooth is not None:
        args.extend(["--mesh-smooth", str(t3.mesh_smooth)])
    if t3.allow_shared_gpu:
        args.append("--allow-shared-gpu")
    if not t3.gpu_kill_others:
        args.append("--no-gpu-kill-others")
    if t3.full_gpu:
        args.append("--t2d-full-gpu")
    return args


@main.command("resume")
@click.option(
    "--profile",
    "profile_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="game.yaml",
)
@click.option(
    "--manifest",
    "manifest_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default="manifest.csv",
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
def resume_cmd(
    profile_path: Path,
    manifest_path: Path,
    presets_local: Path | None,
    log_path: Path | None,
    dry_run: bool,
    fail_fast: bool,
    work_dir: Path | None,
) -> None:
    """Batch inteligente: analisa o estado de cada asset e executa apenas as fases pendentes.

    \b
    Detecta automaticamente por item:
      - PNG em falta  → text2d / texture2d
      - shape em falta → text3d generate (shape)
      - paint em falta → paint3d texture (GLB final com PBR)
      - tudo OK       → skip
    """

    profile, rows, _bundle, preset = _build_context(profile_path, manifest_path, presets_local)
    manifest_dir = manifest_path.resolve().parent
    t3_opts = profile.text3d

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
    if t3_opts and t3_opts.texture:
        paint3d_bin = _try_paint3d_bin()

    work_dir = manifest_dir / ".gameassets_work" if work_dir is None else work_dir.resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    child_env = subprocess_gpu_env()

    log_file = None
    if log_path:
        log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115

    def append_log(rec: dict) -> None:
        if log_file:
            log_file.write(json.dumps(rec, ensure_ascii=False) + "\n")
            log_file.flush()

    # --- Análise de estado ---
    NEED_IMAGE = "need_image"
    NEED_SHAPE = "need_shape"
    NEED_PAINT = "need_paint"
    DONE = "done"

    want_texture = bool(t3_opts and t3_opts.texture)

    items: list[dict[str, Any]] = []
    for idx, row in enumerate(rows):
        if not row.generate_3d:
            continue
        img_final, mesh_final = _paths_for_row_manifest(profile, manifest_dir, row)
        row_work = work_dir / _safe_row_dirname(row.id)
        shape_path = row_work / "shape.glb"

        if mesh_final.is_file():
            state = DONE
        elif want_texture and shape_path.is_file():
            state = NEED_PAINT
        elif img_final.is_file():
            state = NEED_SHAPE
        else:
            state = NEED_IMAGE

        items.append(
            {
                "idx": idx,
                "row": row,
                "state": state,
                "img_final": img_final,
                "mesh_final": mesh_final,
                "row_work": row_work,
                "shape_path": shape_path,
            }
        )

    # --- Relatório ---
    counts = {NEED_IMAGE: 0, NEED_SHAPE: 0, NEED_PAINT: 0, DONE: 0}
    for it in items:
        counts[it["state"]] += 1

    plan_table = Table(title="[bold]Plano de execução[/bold]", box=box.ROUNDED, show_header=True)
    plan_table.add_column("Fase", style="bold")
    plan_table.add_column("Pendentes", justify="right")
    plan_table.add_column("Ação")
    need_img_items = [it for it in items if it["state"] == NEED_IMAGE]
    srcs = {effective_image_source(profile, it["row"]) for it in need_img_items}
    if len(srcs) > 1:
        img_label = "text2d/texture2d"
    elif "texture2d" in srcs:
        img_label = "texture2d"
    else:
        img_label = "text2d"
    plan_table.add_row(
        f"1. Imagem ({img_label})",
        str(counts[NEED_IMAGE]),
        f"{img_label} generate" if counts[NEED_IMAGE] > 0 else "[green]OK[/green]",
    )
    shape_pending = counts[NEED_SHAPE] + counts[NEED_IMAGE]
    plan_table.add_row(
        "2. Shape (hunyuan)",
        str(shape_pending),
        "text3d generate --from-image" if shape_pending > 0 else "[green]OK[/green]",
    )
    paint_pending = counts[NEED_PAINT] + counts[NEED_SHAPE] + counts[NEED_IMAGE]
    paint_label = "paint3d texture"
    plan_table.add_row(
        "3. Paint (textura + PBR no GLB)",
        str(paint_pending),
        paint_label if paint_pending > 0 else "[green]OK[/green]",
    )
    plan_table.add_row("[green]Concluídos[/green]", str(counts[DONE]), "[green]skip[/green]")
    console.print(plan_table)

    if all(it["state"] == DONE for it in items):
        console.print("[bold green]Todos os assets estão completos.[/bold green]")
        return

    if counts[NEED_IMAGE] > 0:
        need_texture2d = any(
            effective_image_source(profile, it["row"]) == "texture2d" for it in items if it["state"] == NEED_IMAGE
        )
        need_text2d = any(
            effective_image_source(profile, it["row"]) == "text2d" for it in items if it["state"] == NEED_IMAGE
        )
        if need_texture2d and not texture2d_bin:
            console.print("[yellow]AVISO: texture2d não encontrado — linhas texture2d serão saltadas.[/yellow]")
        if need_text2d and not text2d_bin:
            console.print("[yellow]AVISO: text2d não encontrado — linhas text2d serão saltadas.[/yellow]")
    if (counts[NEED_SHAPE] + counts[NEED_PAINT]) > 0 and not text3d_bin:
        raise click.ClickException("text3d não encontrado. Define TEXT3D_BIN ou instala o pacote.")
    if items and want_texture and not paint3d_bin:
        raise click.ClickException("Perfil com text3d.texture requer paint3d no PATH ou PAINT3D_BIN.")

    if dry_run:
        for it in items:
            if it["state"] != DONE:
                console.print(f"  [yellow]{it['state']}[/yellow] {it['row'].id}")
        return

    continue_on_error = not fail_fast
    failures = 0

    # --- Fase 1: Imagens ---
    need_img = [it for it in items if it["state"] == NEED_IMAGE]
    img_mixed = len({effective_image_source(profile, x["row"]) for x in need_img}) > 1 if need_img else False
    img_phase = (
        "Text2D / Texture2D"
        if img_mixed
        else (
            "Texture2D" if need_img and effective_image_source(profile, need_img[0]["row"]) == "texture2d" else "Text2D"
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
                        it["state"] = NEED_SHAPE
                        console.print(f"  [green]OK[/green] {row.id}")
                else:
                    failures += 1
                    console.print(f"  [red]FAIL[/red] {row.id}")
                    if not continue_on_error:
                        break
                progress.advance(task)

    # --- Fase 2: Shape ---
    need_shape = [it for it in items if it["state"] == NEED_SHAPE]
    if need_shape and text3d_bin:
        console.print(f"\n[bold cyan]Fase 2: Shape ({len(need_shape)} meshes)[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Shape[/cyan]", total=len(need_shape))
            for it in need_shape:
                row = it["row"]
                progress.update(task, description=f"[cyan]{row.id}[/cyan] · shape")
                it["row_work"].mkdir(parents=True, exist_ok=True)
                seed = _seed_for_row(profile, row.id)
                t3d_args = _text3d_argv(
                    text3d_bin,
                    profile,
                    it["img_final"],
                    it["shape_path"],
                    row,
                    shape_only=True,
                )
                if seed is not None:
                    t3d_args.extend(["--seed", str(seed)])
                r = run_cmd(t3d_args, extra_env=child_env, cwd=manifest_dir)
                if r.returncode == 0 and it["shape_path"].is_file():
                    it["state"] = NEED_PAINT if want_texture else DONE
                    console.print(f"  [green]OK[/green] {row.id}")
                else:
                    failures += 1
                    err = merge_subprocess_output(r, max_chars=200) or "shape falhou"
                    console.print(f"  [red]FAIL[/red] {row.id}: {err}")
                    append_log({"id": row.id, "status": "error", "error": err})
                    if not continue_on_error:
                        break
                progress.advance(task)

    # --- Fase 3: Paint ---
    need_paint = [it for it in items if it["state"] == NEED_PAINT]
    if need_paint and paint3d_bin:
        console.print(f"\n[bold cyan]Fase 3: Paint ({len(need_paint)} texturas)[/bold cyan]")
        with Progress(
            SpinnerColumn(),
            TextColumn("{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeElapsedColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Paint[/cyan]", total=len(need_paint))
            for it in need_paint:
                row = it["row"]
                progress.update(task, description=f"[cyan]{row.id}[/cyan] · Paint")
                mesh_out = it["mesh_final"]
                t_tex = _texture_subprocess_argv(
                    paint3d_bin,
                    profile,
                    it["shape_path"],
                    it["img_final"],
                    mesh_out,
                )
                r = run_cmd(t_tex, extra_env=child_env, cwd=manifest_dir)
                if r.returncode == 0 and mesh_out.is_file():
                    it["state"] = DONE
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

    if log_file:
        log_file.close()

    # --- Resumo final ---
    done_count = sum(1 for it in items if it["state"] == DONE)
    console.print(
        f"\n[bold green]Concluídos: {done_count}/{len(items)}[/bold green]  [red]Falhas: {failures}[/red]"
        if failures
        else ""
    )
    if failures:
        sys.exit(1)


# ---------------------------------------------------------------------------
# debug — ferramentas visuais para agentes IA
# ---------------------------------------------------------------------------


@main.group("debug")
def debug_group() -> None:
    """Ferramentas de debugging visual para agentes IA (screenshots, inspect, compare, bundle)."""


def _resolve_animator3d_bin() -> str | None:
    return resolve_binary("ANIMATOR3D_BIN", "animator3d")


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


if __name__ == "__main__":
    main()
