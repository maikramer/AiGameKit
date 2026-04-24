#!/usr/bin/env python3
"""
Text3D - CLI Principal

Text-to-3D: Text2D (texto → imagem) + Hunyuan3D-2.1 SDNQ INT4 (imagem → mesh).
"""

import atexit
import json
import math
import os
import signal
import sys
import time
from pathlib import Path

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.hf import hf_home_display_rich
from gamedev_shared.skill_install import install_my_skill

from . import defaults as _defaults
from .cli_rich import click
from .generator import HunyuanTextTo3DGenerator
from .utils.env import ensure_pytorch_cuda_alloc_conf
from .utils.memory import (
    clear_cuda_memory,
    enforce_exclusive_gpu,
    format_bytes,
    kill_gpu_compute_processes_aggressive,
)
from .utils.mesh_align_hunyuan import align_glb_plus_z_safe
from .utils.mesh_lod import generate_lod_glb_triplet
from .utils.mesh_simplify_textured import simplify_glb_preserving_texture

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


_batch_generator: HunyuanTextTo3DGenerator | None = None


def _batch_cleanup() -> None:
    """Idempotente: segura chamar de atexit, signal handler e finally."""
    global _batch_generator
    if _batch_generator is not None:
        _batch_generator.unload_hunyuan()
        _batch_generator = None


def _batch_signal_handler(signum: int, frame) -> None:
    _batch_cleanup()
    sys.exit(128 + signum)


atexit.register(_batch_cleanup)


DEFAULT_MESH_DIR = DEFAULT_OUTPUT_DIR / "meshes"


def ensure_dirs():
    DEFAULT_MESH_DIR.mkdir(parents=True, exist_ok=True)


@click.group()
@click.version_option(version="0.1.0", prog_name="text3d")
@click.option("--verbose", "-v", is_flag=True, help="Modo verbose com logs detalhados")
@click.pass_context
def cli(ctx, verbose):
    """
    Text3D — mesh 3D a partir de texto (geometria: Text2D → Hunyuan3D-2.1).

    Textura e PBR: usa o CLI **paint3d** ou um batch **gameassets** com perfil text3d.texture.

    \b
        text3d generate "um robô futurista" -o robo.glb
        text3d generate "carro" --preset hq -o carro.glb
        text3d generate -i ref.png -o mesh.glb
        text3d doctor
        text3d lod modelo.glb -o ./out --basename prop
        text3d simplify-textured pintado.glb -o leve.glb --face-ratio 0.45
        text3d collision modelo.glb -o collision.glb
        text3d align-plus-z modelo.glb -o corrigido.glb
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
    help="Perfil ~6GB VRAM: SDNQ INT4, octree 256, 8000 chunks, 24 steps.",
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
    help=f"Passos Hunyuan3D (low VRAM com --low-vram: {_defaults.LOW_VRAM_STEPS})",
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
    help=(f"Octree Hunyuan (VRAM no decode). low VRAM: {_defaults.LOW_VRAM_OCTREE}"),
)
@click.option(
    "--num-chunks",
    default=_defaults.DEFAULT_NUM_CHUNKS,
    show_default=True,
    type=int,
    help=(f"Chunks extração de superfície. low VRAM: {_defaults.LOW_VRAM_NUM_CHUNKS}"),
)
@click.option(
    "--preset",
    type=click.Choice(["fast", "balanced", "hq"]),
    default="balanced",
    show_default=True,
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
    "--no-remove-bg",
    "no_remove_bg",
    is_flag=True,
    default=False,
    help="Desactivar remoção de fundo com BiRefNet (defeito: remoção activa).",
)
@click.option(
    "--model-subfolder",
    default=_defaults.DEFAULT_SUBFOLDER,
    show_default=True,
    help="Subpasta do modelo Hunyuan3D shape (ex.: hunyuan3d-dit-v2-1).",
)
@click.option(
    "--sdnq-preset",
    default=None,
    type=click.Choice(["sdnq-uint8", "sdnq-int8", "sdnq-int4", "sdnq-fp8", "none"]),
    help=("Preset SDNQ para quantização do DiT. Defeito: none (full precision), ou sdnq-int4 com --low-vram."),
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
    default=False,
    help="DEPRECATED: terminates competing GPU processes; will be removed in a future version. Default: off.",
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
    "--profile",
    "prof_profile",
    is_flag=True,
    help="Medir tempos, CPU, RAM e VRAM (JSONL: GAMEDEV_PROFILE_LOG; SQLite automático).",
)
@click.option(
    "--max-faces",
    "max_faces",
    type=int,
    default=40000,
    show_default=True,
    help="Número máximo de faces (0 = sem redução). Usa quadric edge collapse do PyMeshLab.",
)
@click.option(
    "--gpu-ids",
    "gpu_ids",
    type=str,
    default=None,
    help=(
        "IDs de GPU para multi-GPU (separados por vírgula, ex.: 0,1). "
        "Usa accelerate para dividir pesos do Hunyuan3D entre GPUs."
    ),
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
    no_remove_bg,
    generate_verbose,
    allow_shared_gpu,
    gpu_kill_others,
    model_subfolder,
    sdnq_preset,
    export_origin,
    export_rotation_x_deg,
    save_reference_image,
    no_prompt_optimize,
    prof_profile,
    max_faces,
    gpu_ids,
):
    """Gera 3D: PROMPT (Text2D → Hunyuan) ou --from-image (só Hunyuan)."""
    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    verbose = bool(ctx.obj.get("VERBOSE")) or generate_verbose

    parsed_gpu_ids: list[int] | None = None
    if gpu_ids is not None:
        parsed_gpu_ids = [int(x) for x in gpu_ids.split(",") if x.strip()]

    if preset is not None:
        pv = _defaults.PRESET_HUNYUAN[preset]
        steps = pv["steps"]
        octree_resolution = pv["octree"]
        num_chunks = pv["chunks"]

    # --low-vram: override to the low-VRAM profile (overrides balanced/fast/hq)
    if low_vram:
        steps = _defaults.LOW_VRAM_STEPS
        octree_resolution = _defaults.LOW_VRAM_OCTREE
        num_chunks = _defaults.LOW_VRAM_NUM_CHUNKS
        if sdnq_preset is None:
            sdnq_preset = "sdnq-int4"

    if sdnq_preset is None:
        sdnq_preset = "none"

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

    from gamedev_shared.gpu import warn_if_vram_occupied

    if not cpu:
        warn_if_vram_occupied()

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
    info_table.add_row("[bold]BG removal[/bold]", "desactivada" if no_remove_bg else "BiRefNet")
    if max_faces > 0:
        info_table.add_row("[bold]Max faces[/bold]", f"{max_faces} (PyMeshLab quadric)")
    else:
        info_table.add_row("[bold]Max faces[/bold]", "sem redução")
    info_table.add_row("[bold]Formato[/bold]", output_format.upper())
    info_table.add_row(
        "[bold]Export[/bold]",
        f"origem={export_origin}"
        + (f", rotação X={export_rotation_x_deg}°" if export_rotation_x_deg is not None else ""),
    )
    info_table.add_row("[bold]Modo[/bold]", "economia VRAM" if low_vram else "normal")
    if parsed_gpu_ids:
        info_table.add_row("[bold]Multi-GPU[/bold]", f"IDs: {parsed_gpu_ids} (accelerate dispatch)")

    console.print(Panel(info_table, title="[bold green]Configuração", border_style="green"))

    prof_log_p = env_profile_log_path()
    prof_log = Path(prof_log_p) if prof_log_p else None
    prof_params = {
        "preset": preset,
        "steps": steps,
        "guidance": guidance,
        "octree_resolution": octree_resolution,
        "num_chunks": num_chunks,
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
                        gpu_ids=parsed_gpu_ids,
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
                        task = progress.add_task("[cyan]Hunyuan3D (imagem → mesh)...", total=None)
                        result = generator.generate_from_image(
                            from_image,
                            num_inference_steps=steps,
                            guidance_scale=guidance,
                            octree_resolution=octree_resolution,
                            num_chunks=num_chunks,
                            hy_seed=seed,
                            mc_level=mc_level,
                            remove_bg=not no_remove_bg,
                        )
                    else:
                        task = progress.add_task("[cyan]Text2D → Hunyuan3D...", total=None)
                        result, ref_img = generator.generate(
                            prompt=prompt,
                            t2d_seed=seed,
                            return_reference_image=True,
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
                            remove_bg=not no_remove_bg,
                        )

                        if save_reference_image:
                            out_png = output.parent / f"{output.stem}_text2d.png"
                            out_png.parent.mkdir(parents=True, exist_ok=True)
                            ref_img.save(str(out_png), format="PNG")
                            console.print(f"[dim]Imagem Text2D (rede Hunyuan): [cyan]{out_png.resolve()}[/cyan][/dim]")

                    progress.update(task, description="[green]Concluído")

                if max_faces > 0:
                    from text3d.hy3dshape.postprocessors import FaceReducer

                    face_reducer = FaceReducer()
                    result = face_reducer(result, max_facenum=max_faces)

                if result is not None:
                    from text3d.utils.mesh_lod import prepare_mesh_topology

                    result = prepare_mesh_topology(result)

                if save_reference_image and from_image:
                    import shutil

                    src = Path(from_image)
                    out_copy = output.parent / f"{output.stem}_input{src.suffix.lower() or '.png'}"
                    out_copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(from_image, out_copy)
                    console.print(f"[dim]Imagem de entrada copiada: [cyan]{out_copy.resolve()}[/cyan][/dim]")

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
        get_system_info,
        gpu_bytes_in_use,
        gpu_total_mib,
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
            total = gpu_total_mib(0)
            pct_now = (used / (total * 1024 * 1024) * 100) if total else 0
            table.add_row(
                "Política GPU exclusiva",
                f"~{used / (1024**2):.0f} MiB em uso agora ({pct_now:.0f}%) — "
                f"generate recusa se > 15% da VRAM total "
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


@cli.command("lod")
@click.argument(
    "input_mesh",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--output-dir",
    "-o",
    type=click.Path(file_okay=False, path_type=Path),
    required=True,
    help="Pasta de saída para os três GLB (lod0, lod1, lod2)",
)
@click.option(
    "--basename",
    "-n",
    "basename_opt",
    type=str,
    default=None,
    help="Prefixo dos ficheiros (defeito: nome do ficheiro de entrada sem extensão)",
)
@click.option(
    "--lod1-ratio",
    type=float,
    default=0.42,
    show_default=True,
    help="Rácio aproximado de faces do LOD1 face ao original",
)
@click.option(
    "--lod2-ratio",
    type=float,
    default=0.14,
    show_default=True,
    help="Rácio aproximado de faces do LOD2 face ao original",
)
@click.option(
    "--min-faces-lod1",
    type=int,
    default=500,
    show_default=True,
    help="Mínimo de faces no LOD1",
)
@click.option(
    "--min-faces-lod2",
    type=int,
    default=150,
    show_default=True,
    help="Mínimo de faces no LOD2",
)
@click.option(
    "--meshfix",
    is_flag=True,
    default=False,
    help="Aplicar pymeshfix só ``fill_small_boundaries`` após cada nível (opcional; por defeito desligado)",
)
def lod_cmd(
    input_mesh: Path,
    output_dir: Path,
    basename_opt: str | None,
    lod1_ratio: float,
    lod2_ratio: float,
    min_faces_lod1: int,
    min_faces_lod2: int,
    meshfix: bool,
) -> None:
    """Gera três GLB com níveis de detalhe (LOD0=cheio, LOD1/LOD2 decimados).

    Requer ``fast-simplification`` (dependência do pacote text3d). Saída:
    ``<basename>_lod0.glb``, ``<basename>_lod1.glb``, ``<basename>_lod2.glb``.

    Antes da decimação aplica-se ``prepare_mesh_topology`` (fundir vértices,
    manifold) para reduzir rachas nos LODs; ``<basename>_lod0.glb`` é a mesh corrigida em
    resolução total (podes copiar para substituir o GLB fonte se quiseres alinhar o jogo).

    Por defeito **não** corre pymeshfix (decimação pura). Usa ``--meshfix`` só se precisares
    de fechar buracos pequenos; evita-se ``clean()`` do PyTMesh que destrói LODs decimados.
    """
    stem = basename_opt if basename_opt else input_mesh.stem
    try:
        paths = generate_lod_glb_triplet(
            input_mesh,
            output_dir,
            stem,
            lod1_ratio=lod1_ratio,
            lod2_ratio=lod2_ratio,
            min_faces_lod1=min_faces_lod1,
            min_faces_lod2=min_faces_lod2,
            meshfix=meshfix,
        )
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    console.print(
        Panel(
            "\n".join(f"• [cyan]{p}[/cyan]" for p in paths),
            title="[bold green]LOD gerado[/bold green]",
            border_style="green",
        )
    )


@cli.command("simplify-textured")
@click.argument("input_mesh", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB de saída (uma mesh fundida).",
)
@click.option(
    "--face-ratio",
    type=float,
    default=0.85,
    show_default=True,
    help="Rácio alvo de faces (0–1) face ao original. Ignorado se já ≤ mínimo.",
)
@click.option(
    "--qualitythr",
    type=float,
    default=0.5,
    show_default=True,
    help="Qualidade / erro máximo permitido (PyMeshLab, só malhas texturadas).",
)
@click.option(
    "--extratcoordw",
    type=float,
    default=1.0,
    show_default=True,
    help="Peso extra dos UV na decimação com textura (PyMeshLab).",
)
def simplify_textured_cmd(
    input_mesh: Path,
    output: Path,
    face_ratio: float,
    qualitythr: float,
    extratcoordw: float,
) -> None:
    """Reduz faces num GLB: preserva textura (UV+mapa) via PyMeshLab; sem textura, quadric trimesh.

    Malhas só com cor de vértice uniforme (ex.: placeholder) usam o mesmo rácio sem passo de textura.
    """
    if not 0 < face_ratio <= 1.0:
        raise click.ClickException("--face-ratio deve estar entre 0 e 1")
    try:
        simplify_glb_preserving_texture(
            input_mesh,
            output,
            face_ratio=face_ratio,
            qualitythr=qualitythr,
            extratcoordw=extratcoordw,
        )
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    console.print(
        Rule("[bold green]simplify-textured", style="green"),
    )
    console.print(f"[bold green]✓[/bold green] [cyan]{output.resolve()}[/cyan]")


@cli.command("collision")
@click.argument("input_mesh", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="Output collision GLB")
@click.option("--max-faces", type=int, default=300, show_default=True, help="Target face count for collision mesh")
@click.option(
    "--convex-hull/--no-convex-hull",
    default=True,
    help="Compute convex hull before simplification (default: yes)",
)
def collision_cmd(input_mesh: Path, output: Path, max_faces: int, convex_hull: bool) -> None:
    """Generate a simplified collision mesh from any GLB/OBJ/PLY.

    Produces a low-poly mesh suitable for physics collision in Unity/Godot/Unreal.
    Default: convex hull + quadric decimation to 300 faces.

    \b
    text3d collision modelo.glb -o collision.glb
    text3d collision modelo.glb -o coll.glb --max-faces 500 --no-convex-hull
    """
    from .utils.collision import generate_collision_mesh

    out = generate_collision_mesh(input_mesh, output, max_faces=max_faces, convex_hull=convex_hull)
    console.print(Rule(f"[bold green]collision[/bold green] → {out}", style="green"))


@cli.command("align-plus-z")
@click.argument("input_mesh", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB de saída.",
)
@click.option(
    "--min-height-ratio",
    type=float,
    default=0.25,
    show_default=True,
    help=(
        "Se a altura (AABB Y) após alinhamento for inferior a este factor da original, "
        "mantém o ficheiro sem rotação (ex.: personagens onde a heurística falha)."
    ),
)
def align_plus_z_cmd(
    input_mesh: Path,
    output: Path,
    min_height_ratio: float,
) -> None:
    """Alinha faces ~+Z em baixo ao chão -Y (estilo Hunyuan/cristal); preserva textura no GLB."""
    if not 0 < min_height_ratio <= 1.0:
        raise click.ClickException("--min-height-ratio deve estar entre 0 e 1")
    try:
        align_glb_plus_z_safe(input_mesh, output, min_height_ratio=min_height_ratio)
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    console.print(
        Rule("[bold green]align-plus-z", style="green"),
    )
    console.print(f"[bold green]✓[/bold green] [cyan]{output.resolve()}[/cyan]")


@cli.command("generate-batch")
@click.argument("manifest", type=click.Path(exists=True, dir_okay=False))
@click.option("--output-dir", "-O", type=click.Path(), default=".", help="Diretório base para outputs relativos.")
@click.option("--preset", type=click.Choice(["fast", "balanced", "hq"]), default=None)
@click.option("--steps", type=int, default=None)
@click.option("--guidance", type=float, default=5.0)
@click.option("--octree-resolution", type=int, default=None)
@click.option("--num-chunks", type=int, default=None)
@click.option("--sdnq-preset", type=str, default=None)
@click.option("--model-subfolder", default="hunyuan3d-dit-v2-1")
@click.option("--allow-shared-gpu", is_flag=True)
@click.option("--gpu-kill-others/--no-gpu-kill-others", default=False)
@click.option("--force", is_flag=True, help="Regenerar mesmo se o output já existe.")
@click.option("--gpu-ids", type=str, default=None)
@click.option("-v", "--verbose", "batch_verbose", is_flag=True)
def generate_batch(
    manifest: str,
    output_dir: str,
    preset: str | None,
    steps: int | None,
    guidance: float,
    octree_resolution: int | None,
    num_chunks: int | None,
    sdnq_preset: str | None,
    model_subfolder: str,
    allow_shared_gpu: bool,
    gpu_kill_others: bool,
    gpu_ids: str | None,
    force: bool,
    batch_verbose: bool,
) -> None:
    """Processa lote image-to-3D a partir de manifest JSON (JSONL em stdout)."""
    from .utils.export import save_mesh
    from .utils.mesh_lod import prepare_mesh_topology

    _err = Console(stderr=True)
    manifest_path = Path(manifest).resolve()
    manifest_dir = manifest_path.parent
    out_base = Path(output_dir).resolve()

    with open(manifest_path) as f:
        items = json.load(f)
    if not isinstance(items, list) or not items:
        raise click.ClickException("Manifest deve ser uma lista JSON não-vazia.")
    for i, item in enumerate(items):
        for key in ("id", "image", "output"):
            if key not in item:
                raise click.ClickException(f"Item {i}: campo '{key}' em falta.")

    base_steps = steps
    base_octree = octree_resolution
    base_chunks = num_chunks
    if preset is not None:
        pv = _defaults.PRESET_HUNYUAN[preset]
        base_steps = base_steps if base_steps is not None else pv["steps"]
        base_octree = base_octree if base_octree is not None else pv["octree"]
        base_chunks = base_chunks if base_chunks is not None else pv["chunks"]
    if base_steps is None:
        base_steps = _defaults.DEFAULT_HY_STEPS
    if base_octree is None:
        base_octree = _defaults.DEFAULT_OCTREE_RESOLUTION
    if base_chunks is None:
        base_chunks = _defaults.DEFAULT_NUM_CHUNKS

    parsed_gpu_ids: list[int] | None = None
    if gpu_ids is not None:
        parsed_gpu_ids = [int(x) for x in gpu_ids.split(",") if x.strip()]

    allow_shared = bool(allow_shared_gpu) or _env_allow_shared_gpu()
    gpu_kill = _gpu_kill_others_effective(bool(gpu_kill_others))
    if gpu_kill:
        for line in kill_gpu_compute_processes_aggressive(exclude_pid=os.getpid()):
            _err.print(f"[dim]{line}[/dim]")
        clear_cuda_memory()
        time.sleep(0.5)
    try:
        enforce_exclusive_gpu(allow_shared=allow_shared)
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e

    resolved_sdnq = sdnq_preset if sdnq_preset else ""

    need_load = force or any(not (out_base / it["output"]).is_file() for it in items if "output" in it)

    old_sigterm = signal.signal(signal.SIGTERM, _batch_signal_handler)
    old_sigint = signal.signal(signal.SIGINT, _batch_signal_handler)

    global _batch_generator
    try:
        if need_load:
            with _err.status("[bold yellow]A preparar gerador batch...", spinner="dots"):
                _batch_generator = HunyuanTextTo3DGenerator(
                    verbose=batch_verbose,
                    hunyuan_subfolder=model_subfolder,
                    sdnq_preset=resolved_sdnq,
                    gpu_ids=parsed_gpu_ids,
                )

        _err.print(
            f"[dim]Itens: {len(items)} | preset={preset} "
            f"steps={base_steps} octree={base_octree} chunks={base_chunks}[/dim]"
        )

        for item in items:
            item_id = item["id"]
            try:
                img_path = (manifest_dir / item["image"]).resolve()
                out_path = (out_base / item["output"]).resolve()

                if not force and out_path.is_file():
                    print(json.dumps({"id": item_id, "status": "skipped", "output": item["output"]}), flush=True)
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)

                item_steps = item.get("steps", base_steps)
                item_octree = item.get("octree_resolution", base_octree)
                item_chunks = item.get("num_chunks", base_chunks)
                item_seed = item.get("seed", None)

                t0 = time.time()
                mesh = _batch_generator.generate_from_image(
                    str(img_path),
                    num_inference_steps=item_steps,
                    guidance_scale=guidance,
                    octree_resolution=item_octree,
                    num_chunks=item_chunks,
                    hy_seed=item_seed,
                    keep_loaded=True,
                )

                mesh = prepare_mesh_topology(mesh)
                faces = len(mesh.faces)
                save_mesh(mesh, str(out_path), format="glb", origin_mode=_defaults.get_export_origin())
                elapsed = time.time() - t0

                record = {
                    "id": item_id,
                    "status": "ok",
                    "output": item["output"],
                    "faces": faces,
                    "seconds": round(elapsed, 1),
                }
                print(json.dumps(record), flush=True)

            except Exception as exc:
                record = {
                    "id": item_id,
                    "status": "error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(json.dumps(record), flush=True)

    finally:
        _batch_cleanup()
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)


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
        "hy3dshape vendorizado; licença Tencent Hunyuan Community",
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
