#!/usr/bin/env python3
"""
Text3D - CLI Principal

Text-to-3D: Text2D (texto → imagem) + Hunyuan3D-2.1 SDNQ INT4 (imagem → mesh).
"""

import math
import os
import sys
import time
from pathlib import Path
from typing import Literal

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.hf import hf_home_display_rich

from . import defaults as _defaults
from .cli_rich import click
from .cursor_skill_install import install_agent_skill
from .generator import HunyuanTextTo3DGenerator
from .utils.env import ensure_pytorch_cuda_alloc_conf
from .utils.memory import (
    clear_cuda_memory,
    enforce_exclusive_gpu,
    format_bytes,
    kill_gpu_compute_processes_aggressive,
)

console = Console()

DEFAULT_OUTPUT_DIR = Path("outputs")


def _env_allow_shared_gpu() -> bool:
    return os.environ.get("TEXT3D_ALLOW_SHARED_GPU", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _gpu_kill_others_effective(cli_wants: bool) -> bool:
    """TEXT3D_GPU_KILL_OTHERS=0 desliga; =1 força; vazio segue o CLI."""
    v = os.environ.get("TEXT3D_GPU_KILL_OTHERS", "").strip().lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return True
    return cli_wants


DEFAULT_MESH_DIR = DEFAULT_OUTPUT_DIR / "meshes"


def ensure_dirs():
    DEFAULT_MESH_DIR.mkdir(parents=True, exist_ok=True)


@click.group()
@click.version_option(version="0.1.0", prog_name="text3d")
@click.option("--verbose", "-v", is_flag=True, help="Modo verbose com logs detalhados")
@click.pass_context
def cli(ctx, verbose):
    """
    Text3D — mesh 3D a partir de texto (só geometria: Text2D → Hunyuan3D-2.1 SDNQ INT4 → repair → remesh).

    Textura e PBR: usa o CLI **paint3d** ou um batch **gameassets** com perfil text3d.texture.

    \b
        text3d generate "um robô futurista" -o robo.glb
        text3d generate "carro" --preset hq -o carro.glb
        text3d generate -i ref.png -o mesh.glb
        text3d doctor
        text3d repair-ground modelo.glb --y-up-flip-x-180 --no-keep-largest
        text3d -v generate "prompt"
        text3d info
    """
    ensure_pytorch_cuda_alloc_conf()
    ctx.ensure_object(dict)
    ctx.obj["VERBOSE"] = verbose
    # Não criar outputs/meshes aqui: só quando --output omite caminho (usa pasta por defeito).


@cli.group("skill")
def skill_group() -> None:
    """Agent Skills Cursor (instalação no projeto do jogo)."""


@skill_group.command("install")
@click.option(
    "--target",
    "-t",
    type=click.Path(file_okay=False, writable=True, path_type=Path),
    default=".",
    help="Raiz do projeto do jogo (cria .cursor/skills/text3d/)",
)
@click.option("--force", is_flag=True, help="Sobrescrever SKILL.md existente")
def skill_install_cmd(target: Path, force: bool) -> None:
    """Copia SKILL.md para .cursor/skills/text3d/."""
    try:
        dest = install_agent_skill(target, force=force)
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


@cli.command()
@click.argument("prompt", required=False)
@click.option(
    "--from-image",
    "-i",
    "from_image",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Imagem já gerada: só corre Hunyuan3D (sem Text2D).",
)
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída (.glb, .ply, .obj)")
@click.option(
    "--format",
    "-f",
    "output_format",
    default="glb",
    type=click.Choice(["glb", "ply", "obj"]),
    help="Formato de saída",
)
@click.option("--cpu", is_flag=True, help="Forçar CPU (muito mais lento)")
@click.option(
    "--low-vram",
    is_flag=True,
    help="Força Hunyuan3D em CPU (muito lento). O perfil por defeito já é para ~6GB VRAM em CUDA.",
)
@click.option(
    "--image-width",
    "-W",
    default=_defaults.DEFAULT_T2D_WIDTH,
    show_default=True,
    type=int,
    help="Largura Text2D (ex.: 1024 em GPU grande)",
)
@click.option(
    "--image-height",
    "-H",
    default=_defaults.DEFAULT_T2D_HEIGHT,
    show_default=True,
    type=int,
    help="Altura Text2D (ex.: 1024 em GPU grande)",
)
@click.option(
    "--t2d-steps",
    default=_defaults.DEFAULT_T2D_STEPS,
    show_default=True,
    type=int,
    help="Passos de inferência Text2D",
)
@click.option(
    "--t2d-guidance",
    default=_defaults.DEFAULT_T2D_GUIDANCE,
    show_default=True,
    type=float,
    help="Guidance Text2D (recomendado ~1.0 para SDNQ)",
)
@click.option(
    "--model",
    "-m",
    "text2d_model_id",
    default=None,
    help="Modelo Hugging Face Text2D (default: env TEXT2D_MODEL_ID ou Disty0)",
)
@click.option(
    "--t2d-full-gpu",
    is_flag=True,
    help="Text2D inteiro na GPU (precisa ~12GB+ VRAM). O defeito usa CPU offload no FLUX.",
)
@click.option("--seed", type=int, default=None, help="Seed para Text2D e Hunyuan (mesmo valor)")
@click.option(
    "--steps",
    "-s",
    default=_defaults.DEFAULT_HY_STEPS,
    show_default=True,
    type=int,
    help=f"Passos Hunyuan3D (alta qualidade HF: {_defaults.HUNYUAN_HQ_STEPS})",
)
@click.option(
    "--guidance",
    "-gs",
    default=_defaults.DEFAULT_HY_GUIDANCE,
    show_default=True,
    type=float,
    help="Guidance Hunyuan3D (image-to-3D)",
)
@click.option(
    "--octree-resolution",
    default=_defaults.DEFAULT_OCTREE_RESOLUTION,
    show_default=True,
    type=int,
    help=(f"Octree Hunyuan (VRAM no decode). HQ em GPU grande: {_defaults.HUNYUAN_HQ_OCTREE}"),
)
@click.option(
    "--num-chunks",
    default=_defaults.DEFAULT_NUM_CHUNKS,
    show_default=True,
    type=int,
    help=(f"Chunks extração de superfície. HQ: {_defaults.HUNYUAN_HQ_NUM_CHUNKS}"),
)
@click.option(
    "--preset",
    type=click.Choice(["fast", "balanced", "hq"]),
    default=None,
    help=(
        "Perfil Hunyuan (steps + octree + chunks): fast (rápido, menos VRAM), "
        "balanced (defeito), hq (alta qualidade, GPU grande). "
        "Substitui --steps, --octree-resolution e --num-chunks."
    ),
)
@click.option(
    "--mc-level",
    default=_defaults.DEFAULT_MC_LEVEL,
    show_default=True,
    type=float,
    help="Nível marching cubes Hunyuan (0 = defeito; ajustes finos à iso-superfície).",
)
@click.option(
    "--no-mesh-repair",
    "no_mesh_repair",
    is_flag=True,
    default=False,
    help="Desliga pós-processo: maior componente conexa + merge de vértices (ilhas/pés soltos).",
)
@click.option(
    "--no-remove-plates",
    "no_remove_plates",
    is_flag=True,
    default=False,
    help=(
        "Desliga remoção automática de backing plates (artefatos de chão na base). "
        "Por defeito, placas são detectadas e removidas com reparo pymeshlab + fillet."
    ),
)
@click.option(
    "--no-ground-shadow-removal",
    "no_ground_shadow_removal",
    is_flag=True,
    default=False,
    help="Não remove disco/placa de sombra na base (heurística geométrica; defeito: ligado).",
)
@click.option(
    "--ground-shadow-aggressive",
    "ground_shadow_aggressive",
    is_flag=True,
    default=False,
    help="Anti-sombra forte (cilindro na base + peel; só para cascas enormes).",
)
@click.option(
    "--ground-shadow-very-aggressive",
    "ground_shadow_very_aggressive",
    is_flag=True,
    default=False,
    help="Anti-sombra EXTREMO para pedestais. Flood-fill + silhueta.",
)
@click.option(
    "--mesh-smooth",
    default=_defaults.DEFAULT_MESH_SMOOTH,
    show_default=True,
    type=int,
    help="Suavização Laplaciana (1-2 reduz aspereza; pode arredondar detalhes finos).",
)
@click.option(
    "--remesh/--no-remesh",
    default=_defaults.DEFAULT_REMESH,
    show_default=True,
    help="Isotropic remeshing: reconstrói topologia, fecha buracos, elimina spikes. Requer pymeshlab.",
)
@click.option(
    "--remesh-resolution",
    default=_defaults.DEFAULT_REMESH_RESOLUTION,
    show_default=True,
    type=int,
    help="Resolução do remeshing (~nº subdivisões na diagonal). Maior = mais detalhe.",
)
@click.option(
    "--model-subfolder",
    default=_defaults.DEFAULT_SUBFOLDER,
    show_default=True,
    help="Subpasta do modelo Hunyuan3D shape (ex.: hunyuan3d-dit-v2-1).",
)
@click.option(
    "--sdnq-preset",
    default=HunyuanTextTo3DGenerator.DEFAULT_SDNQ_PRESET,
    show_default=True,
    type=click.Choice(["sdnq-uint8", "sdnq-int8", "sdnq-int4", "sdnq-fp8", "none"]),
    help="Preset SDNQ para quantização do DiT (none = sem quantização, mais VRAM).",
)
@click.option(
    "-v",
    "--verbose",
    "generate_verbose",
    is_flag=True,
    help="Logs detalhados (equivale a: text3d -v generate ...)",
)
@click.option(
    "--allow-shared-gpu",
    "allow_shared_gpu",
    is_flag=True,
    help="Permite GPU com outros processos (desliga verificação: ~300 MiB máx. já ocupados).",
)
@click.option(
    "--gpu-kill-others/--no-gpu-kill-others",
    "gpu_kill_others",
    default=True,
    help="Termina outros processos GPU (nvidia-smi) antes de inferir; defeito: ligado.",
)
@click.option(
    "--export-rotation-x-deg",
    "export_rotation_x_deg",
    type=float,
    default=None,
    help=(
        "Rotação X ao gravar mesh (graus). Defeito interno: +90 (Hunyuan→Y-up). Sobrescreve TEXT3D_EXPORT_ROTATION_X_*."
    ),
)
@click.option(
    "--export-origin",
    "export_origin",
    type=click.Choice(["feet", "center", "none"]),
    default=_defaults.DEFAULT_EXPORT_ORIGIN,
    show_default=True,
    help=(
        "Origem após rotação Y-up: feet=pés no chão (Y=0) e centro em XZ (Godot/Blender); "
        "center=centro da caixa em (0,0,0); none=não mover. Sobrescreve TEXT3D_EXPORT_ORIGIN."
    ),
)
@click.option(
    "--save-reference-image",
    "save_reference_image",
    is_flag=True,
    default=False,
    help=(
        "Guarda a imagem usada no image-to-3D: com prompt Text2D → PNG <stem>_text2d.png junto ao -o; "
        "com --from-image copia a entrada para <stem>_input.png. "
        "Serve para ver sombras de contacto / 'pratos' na rede antes do Hunyuan3D."
    ),
)
@click.option(
    "--no-prompt-optimize",
    "no_prompt_optimize",
    is_flag=True,
    default=False,
    help=(
        "Desativa a otimização automática de prompts. Por defeito o sistema adiciona "
        "termos como 'no ground plane', 'no contact shadow' para evitar placas na base. "
        "Use esta flag para controlo total do prompt (prompts avançados)."
    ),
)
@click.option(
    "--max-retries",
    "max_retries",
    default=3,
    show_default=True,
    type=int,
    help=(
        "Tentativas máximas de geração. Verifica qualidade da mesh (backing plates, "
        "flat cutouts) e regenera com seed aleatória se falhar. 1 = sem retry."
    ),
)
@click.option(
    "--profile",
    "prof_profile",
    is_flag=True,
    help="Medir tempos, CPU, RAM e VRAM (JSONL: GAMEDEV_PROFILE_LOG; SQLite automático).",
)
@click.pass_context
def generate(
    ctx,
    prompt,
    from_image,
    output,
    output_format,
    cpu,
    low_vram,
    image_width,
    image_height,
    t2d_steps,
    t2d_guidance,
    text2d_model_id,
    t2d_full_gpu,
    seed,
    steps,
    guidance,
    octree_resolution,
    num_chunks,
    preset,
    mc_level,
    no_mesh_repair,
    no_remove_plates,
    no_ground_shadow_removal,
    ground_shadow_aggressive,
    ground_shadow_very_aggressive,
    mesh_smooth,
    remesh,
    remesh_resolution,
    generate_verbose,
    allow_shared_gpu,
    gpu_kill_others,
    model_subfolder,
    sdnq_preset,
    export_origin,
    export_rotation_x_deg,
    save_reference_image,
    no_prompt_optimize,
    max_retries,
    prof_profile,
):
    """Gera 3D: PROMPT (Text2D → Hunyuan) ou --from-image (só Hunyuan)."""
    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    verbose = bool(ctx.obj.get("VERBOSE")) or generate_verbose

    if preset is not None:
        pv = _defaults.PRESET_HUNYUAN[preset]
        steps = pv["steps"]
        octree_resolution = pv["octree"]
        num_chunks = pv["chunks"]

    if not from_image and not (prompt and str(prompt).strip()):
        raise click.UsageError("Indica um PROMPT em texto ou --from-image /path/to.png")

    allow_shared = bool(allow_shared_gpu) or _env_allow_shared_gpu()
    gpu_kill = _gpu_kill_others_effective(bool(gpu_kill_others))
    if not cpu and gpu_kill:
        console.print(
            Panel(
                "[bold]Terminar processos GPU alvo[/bold] (SIGTERM → espera → SIGKILL se vivo)\n"
                "[dim]Mantém o PID atual e Xorg / gnome-shell / Wayland. "
                "Desliga com [bold]--no-gpu-kill-others[/bold] ou TEXT3D_GPU_KILL_OTHERS=0[/dim]",
                border_style="yellow",
            )
        )
        for line in kill_gpu_compute_processes_aggressive(exclude_pid=os.getpid()):
            console.print(f"[dim]{line}[/dim]")
        clear_cuda_memory()
        time.sleep(0.5)
    if not cpu:
        try:
            enforce_exclusive_gpu(allow_shared=allow_shared)
        except RuntimeError as e:
            raise click.ClickException(str(e)) from e

    info_table = Table(show_header=False, box=box.SIMPLE)
    if from_image:
        info_table.add_row("[bold]Entrada[/bold]", f"[cyan]{from_image}[/cyan] (só Hunyuan3D)")
    else:
        info_table.add_row("[bold]Prompt[/bold]", f"[cyan]{prompt}[/cyan]")
        info_table.add_row("[bold]Imagem intermédia[/bold]", f"{image_width}x{image_height}")
        t2d_note = "CPU offload" if not t2d_full_gpu else "GPU inteira"
        info_table.add_row(
            "[bold]Text2D[/bold]",
            f"steps={t2d_steps}, guidance={t2d_guidance} ({t2d_note})",
        )
    hy_line = f"steps={steps}, guidance={guidance}"
    if preset:
        hy_line += f" [preset={preset}]"
    info_table.add_row("[bold]Hunyuan3D[/bold]", hy_line)
    info_table.add_row("[bold]Octree / chunks[/bold]", f"{octree_resolution} / {num_chunks}")
    if mc_level != 0.0:
        info_table.add_row("[bold]mc_level[/bold]", str(mc_level))
    if not from_image:
        opt_label = "desligada" if no_prompt_optimize else "ativa (anti-placa)"
        info_table.add_row("[bold]Otimização prompt[/bold]", opt_label)
    if max_retries > 1:
        info_table.add_row("[bold]Auto-retry[/bold]", f"até {max_retries}x (verifica placas/flat)")
    rep = "desligado" if no_mesh_repair else "maior componente + merge"
    if not no_mesh_repair and not no_ground_shadow_removal:
        rep += ", anti-sombra base"
        if ground_shadow_very_aggressive:
            rep += " (EXTREMO)"
        elif ground_shadow_aggressive:
            rep += " (agressivo)"
    if remesh and not no_mesh_repair:
        rep += f", remesh(res={remesh_resolution})"
    if mesh_smooth > 0 and not no_mesh_repair:
        rep += f", smooth={mesh_smooth}"
    if not no_remove_plates:
        rep += ", anti-placa (detect+cut+fillet)"
    info_table.add_row("[bold]Pós-mesh[/bold]", rep)
    info_table.add_row("[bold]Formato[/bold]", output_format.upper())
    info_table.add_row(
        "[bold]Export[/bold]",
        f"origem={export_origin}"
        + (f", rotação X={export_rotation_x_deg}°" if export_rotation_x_deg is not None else ""),
    )
    info_table.add_row("[bold]Modo[/bold]", "economia VRAM" if low_vram else "normal")

    console.print(Panel(info_table, title="[bold green]Configuração", border_style="green"))

    prof_log_p = env_profile_log_path()
    prof_log = Path(prof_log_p) if prof_log_p else None
    prof_params = {
        "preset": preset,
        "steps": steps,
        "guidance": guidance,
        "octree_resolution": octree_resolution,
        "num_chunks": num_chunks,
        "mesh_smooth": mesh_smooth,
        "remesh": remesh,
        "remesh_resolution": remesh_resolution,
        "model_subfolder": model_subfolder,
        "from_image": bool(from_image),
    }

    try:
        with ProfilerSession(
            "text3d",
            log_path=prof_log,
            cli_profile=prof_profile,
            model_id=model_subfolder,
            params=prof_params,
        ) as _prof:
            if export_rotation_x_deg is not None:
                _defaults.set_export_rotation_x_rad_override(math.radians(float(export_rotation_x_deg)))
            try:
                with console.status("[bold yellow]A preparar gerador...", spinner="dots"):
                    generator = HunyuanTextTo3DGenerator(
                        device="cpu" if cpu else None,
                        low_vram_mode=low_vram,
                        verbose=verbose,
                        hunyuan_subfolder=model_subfolder,
                        sdnq_preset="" if sdnq_preset == "none" else sdnq_preset,
                    )

                if output is None:
                    ensure_dirs()
                    timestamp = int(time.time())
                    if from_image:
                        stem = Path(from_image).stem[:30]
                        safe = "".join(c if c.isalnum() else "_" for c in stem)
                    else:
                        safe = "".join(c if c.isalnum() else "_" for c in prompt[:30])
                    output = DEFAULT_MESH_DIR / f"{safe}_{timestamp}.{output_format}"
                else:
                    output = Path(output)

                start_time = time.time()

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    if from_image:
                        if max_retries > 1:
                            task = progress.add_task(
                                f"[cyan]Hunyuan3D imagem → mesh (até {max_retries} tentativas)...",
                                total=None,
                            )

                            def _on_retry_img(attempt, new_seed, quality):
                                issues = ", ".join(quality.get("issues", []))
                                console.print(f"[yellow]Tentativa {attempt} falhou: {issues}. Retry...[/yellow]")

                            result = generator.generate_from_image_with_quality_check(
                                from_image,
                                max_retries=max_retries,
                                hy_seed=seed,
                                on_retry=_on_retry_img,
                                num_inference_steps=steps,
                                guidance_scale=guidance,
                                octree_resolution=octree_resolution,
                                num_chunks=num_chunks,
                                mc_level=mc_level,
                            )
                        else:
                            task = progress.add_task("[cyan]Hunyuan3D (imagem → mesh)...", total=None)
                            result = generator.generate_from_image(
                                from_image,
                                num_inference_steps=steps,
                                guidance_scale=guidance,
                                octree_resolution=octree_resolution,
                                num_chunks=num_chunks,
                                hy_seed=seed,
                                mc_level=mc_level,
                            )
                    else:
                        _gen_kwargs = dict(
                            t2d_width=image_width,
                            t2d_height=image_height,
                            t2d_steps=t2d_steps,
                            t2d_guidance=t2d_guidance,
                            text2d_model_id=text2d_model_id,
                            num_inference_steps=steps,
                            guidance_scale=guidance,
                            octree_resolution=octree_resolution,
                            num_chunks=num_chunks,
                            hy_seed=seed,
                            mc_level=mc_level,
                            t2d_full_gpu=t2d_full_gpu,
                            optimize_prompt=not no_prompt_optimize,
                        )

                        if max_retries > 1:
                            task = progress.add_task(
                                f"[cyan]Text2D → Hunyuan3D (até {max_retries} tentativas)...",
                                total=None,
                            )

                            def _on_retry(attempt, new_seed, quality):
                                issues = ", ".join(quality.get("issues", []))
                                console.print(
                                    f"[yellow]  Tentativa {attempt} falhou: {issues}."
                                    f" Retry com seed {new_seed}...[/yellow]"
                                )

                            result, ref_img = generator.generate_with_quality_check(
                                prompt=prompt,
                                max_retries=max_retries,
                                t2d_seed=seed,
                                return_reference_image=True,
                                on_retry=_on_retry,
                                **_gen_kwargs,
                            )
                        else:
                            task = progress.add_task("[cyan]Text2D → Hunyuan3D...", total=None)
                            result, ref_img = generator.generate(
                                prompt=prompt,
                                t2d_seed=seed,
                                return_reference_image=True,
                                **_gen_kwargs,
                            )

                        if save_reference_image:
                            out_png = output.parent / f"{output.stem}_text2d.png"
                            out_png.parent.mkdir(parents=True, exist_ok=True)
                            ref_img.save(str(out_png), format="PNG")
                            console.print(f"[dim]Imagem Text2D (rede Hunyuan): [cyan]{out_png.resolve()}[/cyan][/dim]")

                    progress.update(task, description="[green]Concluído")

                if save_reference_image and from_image:
                    import shutil

                    src = Path(from_image)
                    out_copy = output.parent / f"{output.stem}_input{src.suffix.lower() or '.png'}"
                    out_copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(from_image, out_copy)
                    console.print(f"[dim]Imagem de entrada copiada: [cyan]{out_copy.resolve()}[/cyan][/dim]")

                if not no_mesh_repair:
                    from .utils.mesh_repair import repair_mesh

                    result = repair_mesh(
                        result,
                        keep_largest=True,
                        merge_vertices=True,
                        remove_ground_shadow=not no_ground_shadow_removal,
                        ground_artifact_mesh_space="hunyuan",
                        ground_shadow_aggressive=ground_shadow_aggressive and not ground_shadow_very_aggressive,
                        ground_shadow_very_aggressive=ground_shadow_very_aggressive,
                        smooth_iterations=max(0, mesh_smooth),
                        remesh=remesh,
                        remesh_resolution=remesh_resolution,
                    )

                if not no_remove_plates:
                    from .utils.mesh_repair import remove_backing_plates

                    result, plate_info = remove_backing_plates(result)
                    if plate_info["plates_removed"] > 0:
                        console.print(
                            f"[dim]Placas removidas: {plate_info['plates_removed']}, "
                            f"componentes: {plate_info['components_removed']}[/dim]"
                        )
                    if plate_info["needs_discard"]:
                        console.print(
                            "[yellow]Aviso: mesh tem placa conectada irrecuperável "
                            "(considere regenerar com seed diferente)[/yellow]"
                        )

                from .utils.export import save_mesh

                mesh_path = save_mesh(result, output, format=output_format, origin_mode=export_origin)
                mp = Path(mesh_path).resolve()
                try:
                    sz = format_bytes(mp.stat().st_size)
                except OSError:
                    sz = "?"
                console.print(Rule("[bold green]Resultado", style="green"))
                console.print(f"[bold green]✓[/bold green] Mesh: [cyan]{mp}[/cyan] [dim]({sz})[/dim]")

                elapsed = time.time() - start_time
                console.print(f"\n[dim]Tempo total: {elapsed:.1f}s[/dim]")
                console.print("[bold green]Sucesso.[/bold green]")

            finally:
                _defaults.set_export_rotation_x_rad_override(None)
    except Exception as e:
        console.print(f"\n[bold red]✗ Erro:[/bold red] {e!s}")
        if verbose:
            console.print_exception()
        sys.exit(1)


@cli.command("doctor")
def doctor():
    """Verifica ambiente: PyTorch, CUDA e VRAM."""
    from .utils.memory import (
        DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB,
        get_system_info,
        gpu_bytes_in_use,
    )

    console.print(
        Panel.fit(
            "[bold]text3d doctor[/bold] — PyTorch, CUDA",
            border_style="blue",
        )
    )
    info_data = get_system_info()
    table = Table(title="[bold blue]Diagnóstico", box=box.ROUNDED)
    table.add_column("Item", style="cyan", no_wrap=True)
    table.add_column("Estado", style="green")

    alloc = os.environ.get("PYTORCH_CUDA_ALLOC_CONF", "")
    table.add_row(
        "PYTORCH_CUDA_ALLOC_CONF",
        alloc or "[dim](defeito: expandable_segments ao iniciar o CLI)[/dim]",
    )
    table.add_row("PyTorch", info_data.get("torch_version", "N/A"))
    table.add_row("CUDA (torch)", str(info_data.get("cuda_available", False)))
    if info_data.get("cuda_available"):
        table.add_row("CUDA (versão runtime)", info_data.get("cuda_version", "N/A"))
        for i, gpu in enumerate(info_data.get("gpus", [])):
            table.add_row(
                f"GPU {i}",
                f"{gpu['name']} — {format_bytes(gpu['total_memory'])} total, {format_bytes(gpu['free_memory'])} livre",
            )
        used = gpu_bytes_in_use(0)
        if used is not None:
            table.add_row(
                "Política GPU exclusiva",
                f"~{used / (1024**2):.0f} MiB em uso agora — "
                f"generate recusa se > {DEFAULT_EXCLUSIVE_GPU_MAX_USED_MIB} MiB "
                f"(ou TEXT3D_ALLOW_SHARED_GPU=1 / --allow-shared-gpu)",
            )

    console.print(table)
    console.print(
        Panel(
            "[dim]Perfis: --preset fast | balanced | hq. "
            "Desempenho: o CLI define PYTORCH_CUDA_ALLOC_CONF se estiver vazio. "
            "Textura/PBR: [bold]paint3d[/bold] ou [bold]gameassets batch[/bold] com text3d.texture.[/dim]",
            border_style="dim",
        )
    )


@cli.command()
def info():
    """Informações do sistema e GPU."""
    from .utils.memory import get_system_info

    console.print(
        Panel.fit(
            "[bold]text3d info[/bold] — GPU, cache e pastas de saída",
            border_style="blue",
        )
    )
    info_data = get_system_info()

    table = Table(title="[bold blue]Sistema", box=box.ROUNDED)
    table.add_column("Componente", style="cyan", no_wrap=True)
    table.add_column("Valor", style="green")

    table.add_row("Python", info_data.get("python_version", "N/A"))
    table.add_row("PyTorch", info_data.get("torch_version", "N/A"))
    table.add_row("CUDA", str(info_data.get("cuda_available", False)))

    if info_data.get("cuda_available"):
        table.add_row("CUDA (versão)", info_data.get("cuda_version", "N/A"))
        for i, gpu in enumerate(info_data.get("gpus", [])):
            table.add_row(f"GPU {i}", f"{gpu['name']}")
            table.add_row("  └ VRAM total", format_bytes(gpu["total_memory"]))
            table.add_row("  └ VRAM livre", format_bytes(gpu["free_memory"]))

    table.add_row("Saída padrão", str(DEFAULT_OUTPUT_DIR.absolute()))
    table.add_row("HF_HOME (cache Hub)", hf_home_display_rich())
    console.print(table)

    if info_data.get("cuda_available"):
        total_vram = sum(g["total_memory"] for g in info_data.get("gpus", []))
        if total_vram < 6 * 1024**3:
            console.print(
                Panel(
                    "[yellow]VRAM modesta: os defeitos do CLI já são conservadores "
                    "(ver text3d.defaults). Se der OOM, baixa --octree-resolution / "
                    "--num-chunks ou usa --low-vram (Hunyuan em CPU).[/yellow]",
                    title="Aviso",
                    border_style="yellow",
                )
            )


@cli.command("repair-ground")
@click.argument("input_glb", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(),
    default=None,
    help="Ficheiro GLB de saída (defeito: sobrescreve o de entrada).",
)
@click.option(
    "--mesh-space",
    type=click.Choice(["hunyuan", "y_up"]),
    default="y_up",
    show_default=True,
    help="hunyuan: malha no espaço Hunyuan3D; y_up: já orientada como no Godot.",
)
@click.option(
    "--y-up-flip-x-180",
    "y_up_flip_x_180",
    is_flag=True,
    help="Rota 180° em X antes do peel (modelo gravado de cabeça para baixo).",
)
@click.option(
    "--no-keep-largest",
    is_flag=True,
    help="Não descartar ilhas (malhas com muitas componentes, ex.: Paint).",
)
@click.option(
    "--no-small-islands",
    "no_small_islands",
    is_flag=True,
    help="Não remover fragmentos flutuantes (ilhas minúsculas).",
)
@click.option(
    "--fill-holes-max-edges",
    "fill_holes_max_edges",
    default=16,
    show_default=True,
    type=int,
    help="Fechar buracos com contorno até N arestas (0 = desligar).",
)
@click.option(
    "--ground-shadow-aggressive",
    "ground_shadow_aggressive",
    is_flag=True,
    default=False,
    help="Anti-sombra forte (cilindro + peel; cascas grandes na base).",
)
@click.option(
    "--ground-shadow-very-aggressive",
    "ground_shadow_very_aggressive",
    is_flag=True,
    default=False,
    help="Anti-sombra EXTREMO — flood-fill + análise de silhueta para pedestais grudados.",
)
@click.option(
    "--remesh/--no-remesh",
    default=_defaults.DEFAULT_REMESH,
    show_default=True,
    help="Isotropic remeshing: reconstrói topologia, fecha buracos, elimina spikes. Requer pymeshlab.",
)
@click.option(
    "--remesh-resolution",
    default=_defaults.DEFAULT_REMESH_RESOLUTION,
    show_default=True,
    type=int,
    help="Resolução do remeshing (~nº subdivisões na diagonal).",
)
def repair_ground_cmd(
    input_glb: Path,
    output,
    mesh_space: Literal["hunyuan", "y_up"],
    y_up_flip_x_180: bool,
    no_keep_largest: bool,
    no_small_islands: bool,
    fill_holes_max_edges: int,
    ground_shadow_aggressive: bool,
    ground_shadow_very_aggressive: bool,
    remesh: bool,
    remesh_resolution: int,
):
    """Pós-processa um GLB: anti-sombra na base (e opcionalmente corrige orientação)."""
    import trimesh as tm

    from .utils.mesh_repair import repair_mesh

    flip = math.pi if y_up_flip_x_180 else 0.0
    try:
        loaded = tm.load(str(input_glb))
        if isinstance(loaded, tm.Scene):
            mesh = loaded.to_geometry()
        elif isinstance(loaded, tm.Trimesh):
            mesh = loaded
        else:
            raise click.ClickException(f"Tipo de mesh não suportado: {type(loaded)}")

        out = repair_mesh(
            mesh,
            keep_largest=not no_keep_largest,
            merge_vertices=True,
            remove_ground_shadow=True,
            ground_artifact_mesh_space=mesh_space,
            ground_artifact_y_up_flip_x_rad=flip,
            ground_shadow_aggressive=ground_shadow_aggressive and not ground_shadow_very_aggressive,
            ground_shadow_very_aggressive=ground_shadow_very_aggressive,
            remove_small_island_fragments=not no_small_islands,
            fill_small_holes_max_edges=max(0, int(fill_holes_max_edges)),
            smooth_iterations=0,
            remesh=remesh,
            remesh_resolution=remesh_resolution,
        )

        out_path = Path(output) if output else input_glb
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out.export(str(out_path), file_type="glb")
        try:
            sz = format_bytes(out_path.stat().st_size)
        except OSError:
            sz = "?"
        console.print(Rule("[bold green]repair-ground", style="green"))
        console.print(f"[bold green]✓[/bold green] [cyan]{out_path.resolve()}[/cyan] [dim]({sz})[/dim]")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), help="Ficheiro de saída")
@click.option("--rotate", "-r", is_flag=True, help="Aplicar rotação de orientação")
def convert(input_file, output, rotate):
    """Converte mesh entre formatos (PLY, OBJ, GLB)."""
    from .utils.export import convert_mesh

    input_path = Path(input_file)
    if output is None:
        output = input_path.with_suffix(".glb")

    try:
        with console.status(f"[yellow]A converter {input_path.suffix} → {Path(output).suffix}..."):
            convert_mesh(input_path, output, rotate=rotate)
        outp = Path(output).resolve()
        try:
            sz = format_bytes(outp.stat().st_size)
        except OSError:
            sz = "?"
        console.print(Rule("[bold green]Concluído", style="green"))
        console.print(f"[bold green]✓[/bold green] [cyan]{outp}[/cyan] [dim]({sz})[/dim]")
    except Exception as e:
        console.print(f"[bold red]✗[/bold red] {e}")
        sys.exit(1)


@cli.command("gpu-processes")
def gpu_processes_cmd() -> None:
    """Lista processos na GPU (via nvidia-smi) — útil quando a verificação de VRAM exclusiva falha."""
    import shutil
    import subprocess

    if not shutil.which("nvidia-smi"):
        console.print(
            "[yellow]Comando [bold]nvidia-smi[/bold] não encontrado no PATH. "
            "Com driver NVIDIA instalado, costuma estar em /usr/bin.[/yellow]"
        )
        sys.exit(1)
    try:
        r = subprocess.run(
            ["nvidia-smi"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except OSError as e:
        raise click.ClickException(f"Falha ao executar nvidia-smi: {e}") from e
    except subprocess.TimeoutExpired:
        raise click.ClickException("nvidia-smi excedeu o tempo limite.") from None

    console.print(
        Panel.fit(
            "[bold]Uso da GPU[/bold] — procura a secção [bold]Processes[/bold] (PID, nome, memória)",
            border_style="cyan",
        )
    )
    console.print(r.stdout, end="")
    if r.stderr:
        console.print(f"[dim]{r.stderr}[/dim]")
    if r.returncode != 0:
        console.print(f"[yellow]nvidia-smi saiu com código {r.returncode}[/yellow]")

    console.print()
    console.print(
        Panel(
            "[bold]Parar um processo[/bold]\n"
            "• Na tabela [bold]Processes[/bold], anota o [bold]PID[/bold] da linha que consome VRAM.\n"
            "• [bold]kill PID[/bold] — pedido amigável; [bold]kill -9 PID[/bold] — forçar se não sair.\n"
            "• Sessões antigas de Python/Text2D/Text3D: [bold]pgrep -af 'text2d|text3d'[/bold] "
            "e [bold]pgrep -af python[/bold] (cuidado a não matar o que precisas).\n"
            "• Godot, browsers (WebGPU), outros modelos IA: fecha a app em vez de kill se possível.\n"
            "[dim]Em [bold]text3d generate[/bold], por defeito [bold]--gpu-kill-others[/bold] "
            "termina processos listados aqui (exceto display). Desliga com [bold]--no-gpu-kill-others[/bold].\n"
            "Se a VRAM continua alta sem processos na lista, reiniciar o PC limpa o driver; "
            "ou [bold]TEXT3D_ALLOW_SHARED_GPU=1[/bold] só se aceitares OOM.[/dim]",
            border_style="dim",
            title="Dica",
        )
    )


@cli.command()
def models():
    """Modelos usados pelo Text3D."""
    table = Table(title="[bold blue]Modelos", box=box.ROUNDED)
    table.add_column("Componente", style="cyan")
    table.add_column("Descrição", style="magenta")
    table.add_column("Notas", style="dim")

    table.add_row(
        "Text2D",
        "FLUX.2 Klein (SDNQ) — texto → imagem",
        "Pacote text2d no monorepo",
    )
    table.add_row(
        "Hunyuan3D-2.1",
        "Image-to-3D (subpasta hunyuan3d-dit-v2-1, SDNQ INT4)",
        "hy3dgen; licença Tencent Hunyuan Community",
    )
    table.add_row(
        "Hunyuan3D-Paint",
        "Textura multivista (delight + paint)",
        "CLI [bold]paint3d[/bold] ou [bold]gameassets[/bold] (não faz parte do text3d)",
    )

    console.print(table)
    console.print(
        Panel(
            "[dim]Primeira execução: descarrega pesos do Hugging Face (~vários GB).\n"
            "Cache: ~/.cache/huggingface/hub/[/dim]",
            title="Nota",
            border_style="dim",
        )
    )


def main():
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
