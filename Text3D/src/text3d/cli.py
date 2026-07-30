#!/usr/bin/env python3
"""
Text3D - CLI Principal

Text-to-3D: Text2D (texto → imagem) + Hunyuan3D-Omni (imagem → mesh, controlos opcionais).
"""

import atexit
import contextlib
import json
import math
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.rule import Rule
from rich.table import Table

from gamedev_shared.cli_helpers import (
    add_ums_options,
    prepare_gpu_exclusive,
    try_ums_delegation,
    with_ums_load_opts,
    with_ums_peak_opts,
)
from gamedev_shared.hf import hf_home_display_rich
from gamedev_shared.progress import STATUS_ERROR, STATUS_OK, STATUS_SKIPPED, TOOL_TEXT3D, emit_progress, emit_result
from gamedev_shared.quality import VALID_QUALITIES
from gamedev_shared.skill_install import install_my_skill

from . import defaults as _defaults
from .cli_rich import click
from .generator import HunyuanTextTo3DGenerator
from .omni_presets import list_pose_presets as _list_pose_presets
from .utils.env import ensure_pytorch_cuda_alloc_conf
from .utils.memory import (
    format_bytes,
)
from .utils.mesh_align_hunyuan import align_glb_plus_z_safe
from .utils.mesh_lod import generate_lod_glb_triplet
from .utils.mesh_remesh_textured import remesh_geometry_only_glb, remesh_textured_glb

console = Console()


def _parse_mc_level_flag(value: Any) -> float | str:
    """``--mc-level``: ``auto`` (default) ou número; erro claro caso contrário."""
    s = str(value).strip().lower()
    if s == "auto":
        return "auto"
    try:
        return float(value)
    except (TypeError, ValueError):
        raise click.ClickException(f"--mc-level inválido: {value!r} (número ou 'auto')") from None


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


def _make_step_callback(item_id: str, total_steps: int):
    """Return a diffusion-step callback that emits per-step progress."""

    def _callback(step_idx: int, _t, _outputs) -> None:
        pct = round(min(100.0, step_idx / total_steps * 100), 1)
        emit_progress(item_id, TOOL_TEXT3D, phase="inference", percent=pct)

    return _callback


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
    Text3D — mesh 3D a partir de texto (geometria: Text2D → Hunyuan3D-Omni).

    Textura e PBR: usa o CLI **paint3d** ou um batch **gameassets** com perfil text3d.texture.

    \b
        text3d generate "um robô futurista" -o robo.glb
        text3d generate "carro" --preset hq -o carro.glb
        text3d generate -i ref.png -o mesh.glb
        text3d generate -i ref.png --control-type bbox --bbox 0.8,0.64,1 -o mesh.glb
        text3d doctor
        text3d lod modelo.glb -o ./out --basename prop
        text3d simplify modelo.glb -o simplificado.glb --target-faces 24000
        text3d remesh modelo.glb -o remeshed.glb --target-faces 24000
        text3d remesh-textured pintado.glb -o remeshed.glb --target-faces 6000
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
    "--seed-fingerprint",
    type=int,
    default=None,
    hidden=True,
    help="Seed de re-roll explícito (GameAssets manifest seed:) — entra no sidecar Omni, distinto de --seed (RNG).",
)
@click.option(
    "--steps",
    "-s",
    default=_defaults.DEFAULT_HY_STEPS,
    show_default=True,
    type=int,
    help=f"Passos Hunyuan3D (perfil balanced: {_defaults.MEMORY_EFFICIENT_STEPS})",
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
    help=(f"Octree Hunyuan (VRAM no decode). Perfil balanced: {_defaults.MEMORY_EFFICIENT_OCTREE}"),
)
@click.option(
    "--num-chunks",
    default=_defaults.DEFAULT_NUM_CHUNKS,
    show_default=True,
    type=int,
    help=(f"Chunks extração de superfície. Perfil balanced: {_defaults.MEMORY_EFFICIENT_NUM_CHUNKS}"),
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
    default="auto",
    show_default=True,
    help=(
        "Nível marching cubes Hunyuan: 'auto' = ligeiro negativo proporcional a "
        "1/octree (fecha pinholes MC); número para valor fixo (0 = clássico)."
    ),
)
@click.option(
    "--bounds-mode",
    "bounds_mode",
    type=click.Choice(["auto", "cube"]),
    default="auto",
    show_default=True,
    help=(
        "Bounds do grid de decode: auto = segue o aspecto da bbox Omni (voxels "
        "mais finos no eixo fino — anti-buracos em assets achatados); cube = cubo clássico."
    ),
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
    default="",
    hidden=True,
    help="[Deprecated] Omni usa repo flat — ignorado.",
)
@click.option(
    "--control-type",
    "control_type",
    type=click.Choice(["none", "bbox", "pose", "point", "voxel"]),
    default="none",
    show_default=True,
    help="Controlo geométrico Omni (none = bbox neutro image-led).",
)
@click.option(
    "--bbox",
    "bbox_str",
    default=None,
    help="Bbox Omni: L,H,W (3 floats 0-1) ou xmin,ymin,zmin,xmax,ymax,zmax.",
)
@click.option(
    "--size",
    "size_str",
    default=None,
    help="Alias de --bbox com 3 floats L,H,W (implica control-type=bbox se omitido).",
)
@click.option(
    "--size-m",
    "size_m_str",
    default=None,
    help=(
        "Tamanho mundo L,H,W em metros. Define aspect Omni (como --size) e activa "
        "autotune de octree/steps/chunks (soft; flags explícitas ganham)."
    ),
)
@click.option(
    "--height-m",
    "height_m",
    type=float,
    default=None,
    help=(
        "Altura alvo em metros (authoring). Expande para --size-m; com --footprint-m "
        "em modo bbox vira molde Omni (o modelo enche o aspect) — não é só escala."
    ),
)
@click.option(
    "--footprint-m",
    "footprint_m",
    type=float,
    default=None,
    help="Footprint L=W em metros com --height-m (coluna/prop). Molde bbox em modo bbox.",
)
@click.option(
    "--bbox-preset",
    "bbox_preset",
    default=None,
    help=(
        "Preset de aspect Omni: cube|humanoid|humanoid-child|quadruped|sword|shield|"
        "crate|door|barrel|tree|column|cactus|chest|furniture|building|chapel."
    ),
)
@click.option(
    "--pose-file",
    "pose_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Bone points (txt) para --control-type=pose.",
)
@click.option(
    "--pose-preset",
    "pose_preset",
    default=None,
    type=click.Choice(_list_pose_presets()),
    help="Pose embutida (adulto, anão/chibi ou A-pose). Implica --control-type=pose.",
)
@click.option(
    "--point-cloud",
    "point_cloud",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Mesh/PLY de referência para --control-type=point (âncora de forma).",
)
@click.option(
    "--voxel-mesh",
    "voxel_mesh",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Mesh/PLY para --control-type=voxel (âncora de volume/blockout).",
)
@click.option(
    "--sdnq-preset",
    default=None,
    type=click.Choice(["sdnq-uint8", "sdnq-int8", "sdnq-int4", "sdnq-fp8", "none"]),
    help=(
        "Preset SDNQ para quantização do DiT. Defeito: none (full precision), "
        "ou sdnq-int4 via hw-auto em GPUs pequenas."
    ),
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: ajusta steps/octree/chunks, SDNQ, multi-GPU e "
        "volume decoder à VRAM disponível. Só preenche o que não foi definido "
        "explicitamente (flags, --quality, --preset ganham). Env: TEXT3D_HW_AUTO=0."
    ),
)
@click.option(
    "--volume-decoder",
    "volume_decoder",
    type=click.Choice(["vanilla", "hierarchical", "flashvdm"]),
    default="vanilla",
    show_default=True,
    help=(
        "Decoder volumétrico do VAE: vanilla (denso, original), hierarchical "
        "(consulta só voxels perto da superfície; grande speedup, ~lossless), "
        "flashvdm (hierárquico + top-k KV; o mais rápido, perda ligeira)."
    ),
)
@click.option(
    "--mc-algo",
    "mc_algo",
    type=click.Choice(["mc", "dmc"]),
    default=None,
    help="Extracção de superfície: mc (skimage, CPU) ou dmc (GPU, requer pacote diso).",
)
@click.option(
    "--compile",
    "compile_models",
    is_flag=True,
    default=False,
    help="torch.compile no DiT+VAE (warmup lento na 1ª inferência; compensa em batch).",
)
@click.option(
    "--compile-mode",
    "compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help=("Modo Inductor. reduce-overhead/max-autotune = CUDA graphs (só full-GPU). Com offload cai para default."),
)
@click.option(
    "--sage-attn",
    "sage_attention",
    is_flag=True,
    default=False,
    help="SageAttention (attention INT8, Ampere+; requer pacote sageattention).",
)
@click.option(
    "--sdnq-matmul",
    "sdnq_matmul",
    is_flag=True,
    default=False,
    help="Matmul quantizado SDNQ (INT8) — mais rápido em GPUs recentes; usar com --sdnq-preset.",
)
@click.option(
    "--group-offload/--no-group-offload",
    "allow_group_offload",
    default=True,
    show_default=True,
    help=(
        "Group offload + CUDA streams no DiT/cond_encoder (attrs Omni). "
        "Planner escolhe quando full-GPU não cabe; --no-group-offload força "
        "só full-GPU / model_cpu."
    ),
)
@click.option(
    "--fp8-layerwise/--no-fp8-layerwise",
    "fp8_layerwise",
    default=False,
    show_default=True,
    help="Layerwise casting fp8 storage / bf16 compute no DiT+conditioner (diffusers hooks).",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="Memory format NHWC no VAE/DiT (ganho maior no VAE conv).",
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
    "--gpu-ids",
    "gpu_ids",
    type=str,
    default=None,
    help=(
        "IDs de GPU para multi-GPU (separados por vírgula, ex.: 0,1). "
        "Usa accelerate para dividir pesos do Hunyuan3D entre GPUs."
    ),
)
@click.option(
    "--skip-remesh",
    "skip_remesh",
    is_flag=True,
    default=False,
    hidden=True,
    help="(obsoleto, ignorado) Mantido por backward-compat.",
)
@click.option(
    "--no-topology-fix",
    "no_topology_fix",
    is_flag=True,
    default=False,
    help=(
        "Stage 1 cru: salta o passo de reparo topológico (weld/manifold/normals). "
        "Recomendado para a pipeline LOD0-master, que aplica topology-fix em comando "
        "separado depois (text3d topology-fix)."
    ),
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for automatic tuning (e.g., humanoid, weapon, prop).",
)
@add_ums_options
@click.pass_context
def generate(
    ctx,
    prompt,
    from_image,
    output,
    output_format,
    cpu,
    image_width,
    image_height,
    t2d_steps,
    t2d_guidance,
    text2d_model_id,
    t2d_full_gpu,
    seed,
    seed_fingerprint,
    steps,
    guidance,
    octree_resolution,
    num_chunks,
    preset,
    mc_level,
    bounds_mode,
    no_remove_bg,
    hw_auto,
    volume_decoder,
    mc_algo,
    compile_models,
    compile_mode,
    sage_attention,
    sdnq_matmul,
    allow_group_offload,
    fp8_layerwise,
    channels_last,
    generate_verbose,
    allow_shared_gpu,
    gpu_kill_others,
    model_subfolder,
    control_type,
    bbox_str,
    size_str,
    size_m_str,
    height_m,
    footprint_m,
    bbox_preset,
    pose_file,
    pose_preset,
    point_cloud,
    voxel_mesh,
    sdnq_preset,
    export_origin,
    export_rotation_x_deg,
    save_reference_image,
    no_prompt_optimize,
    prof_profile,
    gpu_ids,
    skip_remesh,
    no_topology_fix,
    quality,
    category,
    ums_priority,
    no_ums,
    ums_stream,
):
    """Gera 3D: PROMPT (Text2D → Omni) ou --from-image (só Hunyuan3D-Omni)."""
    _ = model_subfolder  # deprecated / ignored (Omni flat repo)
    mc_level = _parse_mc_level_flag(mc_level)
    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    verbose = bool(ctx.obj.get("VERBOSE")) or generate_verbose

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_preset = ctx.get_parameter_source("preset") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance") not in (_src.DEFAULT,)
    _user_set_octree = ctx.get_parameter_source("octree_resolution") not in (_src.DEFAULT,)
    _user_set_chunks = ctx.get_parameter_source("num_chunks") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("text3d", quality=quality, category=category)
    if not _user_set_preset and "preset" in _qresolved.params:
        preset = _qresolved.params["preset"]
    if not _user_set_guidance and "guidance" in _qresolved.params:
        guidance = _qresolved.params["guidance"]
    if not _user_set_steps and "steps" in _qresolved.params:
        steps = _qresolved.params["steps"]
    if not _user_set_octree and "octree" in _qresolved.params:
        octree_resolution = _qresolved.params["octree"]
    if not _user_set_chunks and "chunks" in _qresolved.params:
        num_chunks = _qresolved.params["chunks"]

    parsed_gpu_ids: list[int] | None = None
    if gpu_ids is not None:
        parsed_gpu_ids = [int(x) for x in gpu_ids.split(",") if x.strip()]

    if preset is not None:
        pv = _defaults.PRESET_HUNYUAN[preset]
        # Preset (explícito ou soft-fill do quality tier) é base — flags CLI
        # explícitas de steps/octree/chunks ganham sempre (antes o preset
        # soft-filled por --quality atropelava flags do utilizador).
        if not _user_set_steps:
            steps = pv["steps"]
        if not _user_set_octree:
            octree_resolution = pv["octree"]
        if not _user_set_chunks:
            num_chunks = pv["chunks"]

    # Hardware auto-detection: soft resolution — explicit flags, --quality e
    # --preset ganham sempre; preenche só o que veio dos defaults do click.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    _user_set_quality = ctx.get_parameter_source("quality") not in (_src.DEFAULT,)
    _user_set_vdecoder = ctx.get_parameter_source("volume_decoder") not in (_src.DEFAULT,)
    _user_set_img_w = ctx.get_parameter_source("image_width") not in (_src.DEFAULT,)
    _user_set_img_h = ctx.get_parameter_source("image_height") not in (_src.DEFAULT,)
    hwp = None
    if hw_auto and hw_auto_enabled() and not cpu:
        hwp = detect_hardware_profile()
        _params_untouched = not (
            _user_set_preset or _user_set_quality or _user_set_steps or _user_set_octree or _user_set_chunks
        )
        if _params_untouched:
            steps = hwp.steps
            octree_resolution = hwp.octree
            num_chunks = hwp.chunks
        if sdnq_preset is None and hwp.sdnq_preset:
            sdnq_preset = hwp.sdnq_preset
        if parsed_gpu_ids is None and hwp.gpu_ids:
            parsed_gpu_ids = hwp.gpu_ids
        if not _user_set_vdecoder:
            volume_decoder = hwp.volume_decoder
        if not _user_set_img_w and hwp.image_width is not None:
            image_width = hwp.image_width
        if not _user_set_img_h and hwp.image_height is not None:
            image_height = hwp.image_height

    offload = hwp.offload if hwp is not None else False

    if sdnq_preset is None:
        sdnq_preset = "none"

    if not from_image and not (prompt and str(prompt).strip()):
        raise click.UsageError("Indica um PROMPT em texto ou --from-image /path/to/png")

    # GPU prep (ensure_vram / kill) só no path in-process — UMS é a autoridade da fila.
    allow_shared = bool(allow_shared_gpu) or _env_allow_shared_gpu()
    gpu_kill = _gpu_kill_others_effective(bool(gpu_kill_others))

    from .bbox_tune import apply_bbox_tune, size_m_from_mapping
    from .omni_presets import merge_omni_controls, parse_bbox_csv

    size_vals: list[float] | None = None
    size_m_vals: list[float] | None = None
    bbox_raw: list[float] | None = None
    try:
        if bbox_str:
            bbox_raw = parse_bbox_csv(bbox_str)
        if size_str:
            size_vals = parse_bbox_csv(size_str)
            if len(size_vals) != 3:
                raise click.ClickException("--size espera exactamente 3 floats L,H,W")
        if size_m_str:
            size_m_vals = size_m_from_mapping(size_m_str)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    try:
        _omni = merge_omni_controls(
            control_type=control_type,
            bbox=bbox_raw,
            bbox_preset=bbox_preset,
            size=size_vals,
            size_m=size_m_vals,
            height_m=height_m,
            footprint_m=footprint_m,
            pose_file=pose_file,
            pose_preset=pose_preset,
            point_cloud=point_cloud,
            voxel_mesh=voxel_mesh,
            category=category,
        )
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
    if size_m_vals is None and _omni.get("size_m") is not None:
        size_m_vals = list(_omni["size_m"])

    control_type = _omni["control_type"]
    bbox_vals = _omni["bbox"]
    pose_file = _omni["pose_file"]
    point_cloud = _omni["point_cloud"]
    voxel_mesh = _omni["voxel_mesh"]

    # Soft autotune octree/steps/chunks pelo tamanho mundo (size_m / category).
    # Flags CLI explícitas e item overrides ganham; preset/hw-auto são a base.
    _bbox_tune = None
    if not (_user_set_steps and _user_set_octree and _user_set_chunks):
        steps, octree_resolution, num_chunks, _bbox_tune = apply_bbox_tune(
            steps=steps,
            octree=octree_resolution,
            chunks=num_chunks,
            size_m=size_m_vals,
            category=category,
            bbox_preset=bbox_preset,
            total_vram_gib=hwp.total_vram_gib if hwp is not None else None,
            volume_decoder=volume_decoder,
            tune_steps=not _user_set_steps,
            tune_octree=not _user_set_octree,
            tune_chunks=not _user_set_chunks,
            group_offload=bool(allow_group_offload),
            quality=quality,
        )

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
    if _bbox_tune is not None and _bbox_tune.applied:
        _bt = f"char={_bbox_tune.char_m:.2f}m ({_bbox_tune.source}) voxel≈{_bbox_tune.voxel_m * 100:.1f}cm"
        if _bbox_tune.morph_close is not None:
            _bt += f" morph≈{_bbox_tune.morph_close:.3f}m"
        info_table.add_row("[bold]Scale tune[/bold]", _bt)
    if mc_level == "auto":
        info_table.add_row("[bold]mc_level[/bold]", "auto (negativo ∝ 1/octree, fecha pinholes)")
    elif float(mc_level) != 0.0:
        info_table.add_row("[bold]mc_level[/bold]", str(mc_level))
    if bounds_mode != "cube":
        info_table.add_row("[bold]bounds[/bold]", "auto (grid segue aspecto da bbox Omni)")
    if not from_image:
        opt_label = "desligada" if no_prompt_optimize else "ativa (anti-placa)"
        info_table.add_row("[bold]Otimização prompt[/bold]", opt_label)
    info_table.add_row("[bold]BG removal[/bold]", "desactivada" if no_remove_bg else "BiRefNet")
    info_table.add_row("[bold]Formato[/bold]", output_format.upper())
    info_table.add_row(
        "[bold]Export[/bold]",
        f"origem={export_origin}"
        + (f", rotação X={export_rotation_x_deg}°" if export_rotation_x_deg is not None else ""),
    )
    if hwp is not None:
        info_table.add_row("[bold]Hardware (auto)[/bold]", hwp.summary())
    _accel_parts = []
    if volume_decoder != "vanilla":
        _accel_parts.append(f"volume_decoder={volume_decoder}")
    if mc_algo:
        _accel_parts.append(f"mc_algo={mc_algo}")
    if compile_models:
        _accel_parts.append(f"torch.compile({compile_mode})")
    if sage_attention:
        _accel_parts.append("sage-attn")
    if sdnq_matmul:
        _accel_parts.append("sdnq-matmul")
    if allow_group_offload:
        _accel_parts.append("group-offload")
    if fp8_layerwise:
        _accel_parts.append("fp8-layerwise")
    if channels_last:
        _accel_parts.append("channels_last")
    if _accel_parts:
        info_table.add_row("[bold]Aceleração[/bold]", ", ".join(_accel_parts))
    if parsed_gpu_ids:
        info_table.add_row("[bold]Multi-GPU[/bold]", f"IDs: {parsed_gpu_ids} (accelerate dispatch)")

    if control_type and control_type != "none":
        ctrl_label = control_type
        if pose_preset:
            ctrl_label += f" ({pose_preset})"
        if bbox_preset:
            ctrl_label += f" ({bbox_preset})"
        if size_m_vals:
            ctrl_label += f" size_m={size_m_vals}"
        info_table.add_row("[bold]Controlo Omni[/bold]", ctrl_label)

    console.print(Panel(info_table, title="[bold green]Configuração", border_style="green"))

    prof_log_p = env_profile_log_path()
    prof_log = Path(prof_log_p) if prof_log_p else None
    prof_params = {
        "preset": preset,
        "steps": steps,
        "guidance": guidance,
        "octree_resolution": octree_resolution,
        "num_chunks": num_chunks,
        "model_id": _defaults.DEFAULT_HF_ID,
        "control_type": control_type,
        "pose_preset": pose_preset,
        "bbox_preset": bbox_preset,
        "from_image": bool(from_image),
    }

    try:
        with ProfilerSession(
            "text3d",
            log_path=prof_log,
            cli_profile=prof_profile,
            model_id=_defaults.DEFAULT_HF_ID,
            params=prof_params,
        ) as _prof:
            if export_rotation_x_deg is not None:
                _defaults.set_export_rotation_x_rad_override(math.radians(float(export_rotation_x_deg)))
            try:
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

                _ums_request: dict[str, Any] = {
                    "output": str(output.resolve()) if hasattr(output, "resolve") else str(output),
                    "steps": steps,
                    "guidance": guidance,
                    "octree_resolution": octree_resolution,
                    "num_chunks": num_chunks,
                    "seed": seed,
                    "mc_level": mc_level,
                    "bounds_mode": bounds_mode,
                    "auto_num_chunks": not _user_set_chunks,
                    "remove_bg": not no_remove_bg,
                    "optimize_prompt": not no_prompt_optimize,
                    "origin_mode": export_origin,
                    "control_type": control_type,
                    "pose_preset": pose_preset,
                    "bbox_preset": bbox_preset,
                    # Scale / tune — adapter precisa para metros reais + bbox_tune.
                    "category": category,
                    "quality": quality,
                    "bbox_tune": True,
                    # Topology / accel — parity in-process (adapter respeita).
                    "topology_fix": not no_topology_fix,
                    "volume_decoder": volume_decoder,
                    "mc_algo": mc_algo,
                    "torch_compile": compile_models,
                    "torch_compile_mode": compile_mode,
                    "channels_last": channels_last,
                    "allow_group_offload": allow_group_offload,
                    "fp8_layerwise": fp8_layerwise,
                    "sdnq_quantized_matmul": sdnq_matmul,
                    "sage_attention": sage_attention,
                    "offload": offload,
                    "verbose": verbose,
                }
                if size_m_vals is not None:
                    _ums_request["size_m"] = size_m_vals
                if bbox_vals is not None:
                    _ums_request["bbox"] = bbox_vals
                if pose_file:
                    _ums_request["pose_file"] = str(pose_file)
                if point_cloud:
                    _ums_request["point_cloud"] = str(point_cloud)
                if voxel_mesh:
                    _ums_request["voxel_mesh"] = str(voxel_mesh)
                if from_image:
                    _ums_request["from_image"] = from_image
                else:
                    _ums_request["prompt"] = prompt
                    _ums_request["t2d_width"] = image_width
                    _ums_request["t2d_height"] = image_height
                    _ums_request["t2d_steps"] = t2d_steps
                    _ums_request["t2d_guidance"] = t2d_guidance
                    if text2d_model_id:
                        _ums_request["text2d_model_id"] = text2d_model_id
                    _ums_request["t2d_full_gpu"] = t2d_full_gpu

                # UMS primeiro — nunca ensure_vram/kill antes de enfileirar.
                if try_ums_delegation(
                    "text3d",
                    with_ums_peak_opts(
                        with_ums_load_opts(
                            _ums_request,
                            gpu_ids=parsed_gpu_ids,
                            volume_decoder=volume_decoder,
                            allow_group_offload=allow_group_offload,
                            channels_last=channels_last,
                            offload=offload,
                        ),
                        backend="text3d",
                        memory_efficient=bool(offload or allow_group_offload)
                        or (sdnq_preset not in (None, "none", "")),
                        sdnq_preset=None if sdnq_preset in (None, "none", "") else sdnq_preset,
                    ),
                    t_start=start_time,
                    noun="Mesh",
                    console=console,
                    enabled=not no_ums,
                    priority=ums_priority,
                    stream=ums_stream,
                    timeout_sec=1800.0,
                ):
                    sys.exit(0)

                # Path in-process: coordenação VRAM / kill só aqui.
                if not cpu:
                    prepare_gpu_exclusive(
                        needed_mib=6000,
                        allow_shared=allow_shared,
                        kill_others=gpu_kill,
                        allow_shared_env="TEXT3D_ALLOW_SHARED_GPU",
                        kill_others_env="TEXT3D_GPU_KILL_OTHERS",
                        backend="text3d",
                        quant_mode=None if sdnq_preset in (None, "none") else sdnq_preset,
                        console=console,
                    )

                from text3d.ums_load import map_ums_load_kwargs

                with console.status("[bold yellow]A preparar gerador...", spinner="dots"):
                    _load_kw = map_ums_load_kwargs(
                        {
                            "verbose": verbose,
                            "sdnq_preset": "" if sdnq_preset == "none" else sdnq_preset,
                            "gpu_ids": parsed_gpu_ids,
                            "volume_decoder": volume_decoder,
                            "mc_algo": mc_algo,
                            "torch_compile": compile_models,
                            "torch_compile_mode": compile_mode,
                            "sage_attention": sage_attention,
                            "sdnq_quantized_matmul": sdnq_matmul,
                            "offload": offload,
                            "allow_group_offload": allow_group_offload,
                            "fp8_layerwise": fp8_layerwise,
                            "channels_last": channels_last,
                            "memory_efficient": bool(offload or allow_group_offload),
                        },
                    )
                    if cpu:
                        _load_kw["device"] = "cpu"
                    generator = HunyuanTextTo3DGenerator(**_load_kw)

                item_id = output.stem if output else "text3d_single"
                _ctrl = dict(
                    control_type=control_type,
                    bbox=bbox_vals,
                    pose_file=pose_file,
                    point_cloud=point_cloud,
                    voxel_mesh=voxel_mesh,
                )

                emit_progress(item_id, TOOL_TEXT3D, phase="loading_model", percent=0)

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    if from_image:
                        task = progress.add_task("[cyan]Hunyuan3D-Omni (imagem → mesh)...", total=None)
                        result = generator.generate_from_image(
                            from_image,
                            num_inference_steps=steps,
                            guidance_scale=guidance,
                            octree_resolution=octree_resolution,
                            num_chunks=num_chunks,
                            hy_seed=seed,
                            mc_level=mc_level,
                            bounds_mode=bounds_mode,
                            auto_num_chunks=not _user_set_chunks,
                            remove_bg=not no_remove_bg,
                            step_callback=_make_step_callback(item_id, steps),
                            **_ctrl,
                        )
                    else:
                        task = progress.add_task("[cyan]Text2D → Hunyuan3D-Omni...", total=None)
                        emit_progress(item_id, TOOL_TEXT3D, phase="inference", percent=0)
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
                            bounds_mode=bounds_mode,
                            auto_num_chunks=not _user_set_chunks,
                            t2d_full_gpu=t2d_full_gpu,
                            optimize_prompt=not no_prompt_optimize,
                            remove_bg=not no_remove_bg,
                            **_ctrl,
                        )
                        emit_progress(item_id, TOOL_TEXT3D, phase="inference", percent=100)

                        if save_reference_image:
                            out_png = output.parent / f"{output.stem}_text2d.png"
                            out_png.parent.mkdir(parents=True, exist_ok=True)
                            ref_img.save(str(out_png), format="PNG")
                            console.print(f"[dim]Imagem Text2D (rede Hunyuan): [cyan]{out_png.resolve()}[/cyan][/dim]")

                    progress.update(task, description="[green]Concluído")

                if result is not None and size_m_vals and hasattr(result, "apply_scale"):
                    from .bbox_tune import scale_factor_to_meters

                    _sf = scale_factor_to_meters(float(max(result.extents)), size_m_vals)
                    if _sf is not None:
                        result.apply_scale(_sf)

                if result is not None and not no_topology_fix:
                    from text3d.utils.mesh_lod import prepare_mesh_topology

                    emit_progress(item_id, TOOL_TEXT3D, phase="mesh_repair", percent=0)
                    result = prepare_mesh_topology(result)
                    emit_progress(item_id, TOOL_TEXT3D, phase="mesh_repair", percent=100)
                elif no_topology_fix:
                    emit_progress(item_id, TOOL_TEXT3D, phase="mesh_repair", percent=100)

                if save_reference_image and from_image:
                    import shutil

                    src = Path(from_image)
                    out_copy = output.parent / f"{output.stem}_input{src.suffix.lower() or '.png'}"
                    out_copy.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(from_image, out_copy)
                    console.print(f"[dim]Imagem de entrada copiada: [cyan]{out_copy.resolve()}[/cyan][/dim]")

                emit_progress(item_id, TOOL_TEXT3D, phase="export", percent=0)
                from .utils.export import save_mesh

                mesh_path = save_mesh(result, output, format=output_format, origin_mode=export_origin)
                emit_progress(item_id, TOOL_TEXT3D, phase="export", percent=100)
                mp = Path(mesh_path).resolve()
                elapsed = time.time() - start_time
                try:
                    from datetime import UTC, datetime

                    from text3d.bbox_tune import morph_close_voxels_for

                    from .omni_presets import (
                        build_generate_debug,
                        mesh_stats_for_debug,
                        write_omni_fingerprint,
                    )

                    _decode_stats = dict(getattr(generator, "last_decode_stats", None) or {})
                    _morph_n = morph_close_voxels_for(category)
                    _dbg = build_generate_debug(
                        seconds=elapsed,
                        started_at=datetime.fromtimestamp(start_time, tz=UTC).isoformat(),
                        finished_at=datetime.now(UTC).isoformat(),
                        bbox_tune=_bbox_tune,
                        morph_close_voxels=_morph_n,
                        morph_source="bbox_tune" if _bbox_tune is not None else None,
                        decode={
                            "octree_resolution": octree_resolution,
                            "steps": steps,
                            "guidance": guidance,
                            "num_chunks": num_chunks,
                            "auto_num_chunks": not _user_set_chunks,
                            "mc_level": mc_level,
                            "bounds_mode": bounds_mode,
                            "volume_decoder": volume_decoder,
                            "mc_algo": mc_algo,
                            "last_decode_stats": _decode_stats,
                        },
                        generation={
                            "quality": quality,
                            "category": category,
                            "preset": preset,
                            "seed_rng": seed,
                            "seed_fingerprint": seed_fingerprint,
                            "topology_fix_inline": not no_topology_fix,
                            "origin_mode": export_origin,
                            "remove_bg": not no_remove_bg,
                            "optimize_prompt": not no_prompt_optimize,
                            "from_image": bool(from_image),
                            "prompt": prompt if not from_image else None,
                            "t2d_width": image_width,
                            "t2d_height": image_height,
                            "t2d_steps": t2d_steps,
                            "t2d_guidance": t2d_guidance,
                            "text2d_model_id": text2d_model_id,
                            "t2d_full_gpu": t2d_full_gpu,
                        },
                        omni_resolved=_omni,
                        hardware={
                            "summary": hwp.summary() if hwp is not None else None,
                            "total_vram_gib": getattr(hwp, "total_vram_gib", None) if hwp else None,
                            "gpu_ids": parsed_gpu_ids,
                            "sdnq_preset": sdnq_preset,
                        },
                        accel={
                            "volume_decoder": volume_decoder,
                            "mc_algo": mc_algo,
                            "torch_compile": compile_models,
                            "torch_compile_mode": compile_mode,
                            "channels_last": channels_last,
                            "allow_group_offload": allow_group_offload,
                            "fp8_layerwise": fp8_layerwise,
                            "sdnq_quantized_matmul": sdnq_matmul,
                            "sage_attention": sage_attention,
                            "offload": offload,
                        },
                        mesh=mesh_stats_for_debug(result, path=mp),
                        extra={"path": "cli_generate"},
                    )
                    write_omni_fingerprint(
                        mp,
                        {
                            "control_type": control_type,
                            "bbox": bbox_vals,
                            "bbox_preset": bbox_preset,
                            "pose_preset": pose_preset,
                            "pose_file": str(pose_file) if pose_file else None,
                            "point_cloud": str(point_cloud) if point_cloud else None,
                            "voxel_mesh": str(voxel_mesh) if voxel_mesh else None,
                            "bounds_mode": bounds_mode,
                            "mc_level": mc_level,
                            "size_m": size_m_vals,
                            "seed": seed_fingerprint,
                            "octree_resolution": octree_resolution,
                        },
                        debug=_dbg,
                    )
                except OSError:
                    pass
                try:
                    sz = format_bytes(mp.stat().st_size)
                except OSError:
                    sz = "?"
                console.print(Rule("[bold green]Resultado", style="green"))
                console.print(f"[bold green]✓[/bold green] Mesh: [cyan]{mp}[/cyan] [dim]({sz})[/dim]")

                console.print(f"\n[dim]Tempo total: {elapsed:.1f}s[/dim]")
                console.print("[bold green]Sucesso.[/bold green]")

                faces = len(result.faces) if result is not None else 0
                emit_result(
                    item_id,
                    TOOL_TEXT3D,
                    STATUS_OK,
                    phase="shape",
                    output=str(mp),
                    faces=faces,
                    seconds=round(elapsed, 1),
                )

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

    from .hardware import detect_hardware_profile, hw_auto_enabled

    _hwp = detect_hardware_profile()
    _hw_state = "" if hw_auto_enabled() else " [yellow](desligado: TEXT3D_HW_AUTO=0)[/yellow]"
    table.add_row("Perfil hardware (auto)", f"{_hwp.summary()}{_hw_state}")

    console.print(table)

    extra = Table(title="[bold blue]Ferramentas externas (bake-master)", box=box.ROUNDED)
    extra.add_column("Item", style="cyan", no_wrap=True)
    extra.add_column("Estado", style="green")

    import shutil as _sh
    import subprocess

    try:
        import bpy as _bpy

        from gamedev_shared.bpy_mesh import meshopt_runtime_available

        extra.add_row("bpy", f"OK ({_bpy.app.version_string})")
        if meshopt_runtime_available():
            extra.add_row(
                "meshopt (bpy)",
                "OK — export_meshopt_compression_enable + libmeshoptimizer",
            )
        else:
            extra.add_row(
                "meshopt (bpy)",
                "[yellow]RNA OK mas libmeshoptimizer.so ausente — "
                "instale libmeshoptimizer-dev (Debian/Ubuntu); fallback gltf-transform[/yellow]",
            )
    except Exception as exc:
        extra.add_row("bpy / meshopt", f"[yellow]erro: {exc}[/yellow]")

    npx_path = _sh.which("npx")
    if npx_path:
        extra.add_row("npx", f"OK ({npx_path})")
        try:
            r = subprocess.run(
                ["npx", "--yes", "@gltf-transform/cli", "--version"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if r.returncode == 0:
                ver = (r.stdout or "").strip().splitlines()[0] if r.stdout else "?"
                extra.add_row("@gltf-transform/cli", f"OK ({ver}) — KTX2/UASTC + fallback meshopt")
            else:
                extra.add_row(
                    "@gltf-transform/cli",
                    "[yellow]falhou — bake-master sem KTX2; meshopt via bpy se disponível[/yellow]",
                )
        except Exception as exc:
            extra.add_row("@gltf-transform/cli", f"[yellow]erro: {exc}[/yellow]")
    else:
        extra.add_row(
            "npx",
            "[yellow]não encontrado — KTX2/UASTC precisa Node.js; meshopt pode usar bpy 5.2+ nativo[/yellow]",
        )

    # KTX-Software ``ktx`` — requisito real do passo uastc (além do npx).
    try:
        from text3d.utils.gltf_finish import _has_ktx

        if _has_ktx():
            ktx_path = _sh.which("ktx") or "?"
            extra.add_row("ktx (KTX-Software)", f"OK ({ktx_path}) — UASTC/KTX2")
        else:
            extra.add_row(
                "ktx (KTX-Software)",
                "[yellow]ausente — UASTC/KTX2 falha sem isto; "
                "https://github.com/KhronosGroup/KTX-Software/releases "
                "ou reinstall text3d extras[/yellow]",
            )
    except Exception as exc:
        extra.add_row("ktx (KTX-Software)", f"[yellow]erro: {exc}[/yellow]")

    console.print(extra)

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
                    "--num-chunks ou usa --preset fast.[/yellow]",
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


@cli.command("bake-master")
@click.argument("painted_glb", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    required=True,
    help="GLB de saída (LOD0 master).",
)
@click.option(
    "--target-faces",
    type=int,
    required=True,
    help=("Faces alvo após decimação (categoria). Use 0 para LOD0 = painted (sem decimar — pipeline master)."),
)
@click.option(
    "--high-poly",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="GLB high-poly limpo (id_clean.glb) usado como fonte para bake de normal map.",
)
@click.option(
    "--bake-normals/--no-bake-normals",
    default=False,
    show_default=True,
    help=(
        "Bake de normal map high-poly → low-poly via Cycles. Caro (segundos a "
        "minutos), só vale a pena quando o LOD0 é muito mais leve que o clean. "
        "Requer ``--high-poly``."
    ),
)
@click.option(
    "--normal-resolution",
    type=int,
    default=1024,
    show_default=True,
    help="Resolução do normal map (pixels) quando --bake-normals.",
)
@click.option(
    "--ktx2/--no-ktx2",
    default=True,
    show_default=True,
    help="Aplica compressão KTX2/UASTC via @gltf-transform/cli (npx).",
)
@click.option(
    "--meshopt/--no-meshopt",
    default=True,
    show_default=True,
    help=(
        "Aplica EXT_meshopt_compression (bpy 5.2+ + libmeshoptimizer preferido; "
        "fallback @gltf-transform/cli quando input já tem KTX2)."
    ),
)
@click.option(
    "--texture-size",
    type=int,
    default=2048,
    show_default=True,
    help="Resolução da textura base (passada ao remesh).",
)
def bake_master_cmd(
    painted_glb: Path,
    output: Path,
    target_faces: int,
    high_poly: Path | None,
    bake_normals: bool,
    normal_resolution: int,
    ktx2: bool,
    meshopt: bool,
    texture_size: int,
) -> None:
    """Stage 4 — produz LOD0 master (decimação + tangents + KTX2 + meshopt).

    Input: GLB painted (high-poly texturizado, vindo do paint3d).
    Output: GLB LOD0 com TANGENT, KTX2 e meshopt aplicados quando disponíveis.

    Pós-processamento KTX2/meshopt requer ``npx`` no PATH e baixa
    ``@gltf-transform/cli`` na primeira execução. Falha graciosamente sem
    bloquear a pipeline (apenas warning).
    """
    from .utils.bake_master import bake_master

    res = bake_master(
        painted_glb,
        output,
        target_faces=target_faces,
        high_poly_clean=high_poly,
        bake_normals=bake_normals,
        normal_map_resolution=normal_resolution,
        apply_ktx2=ktx2,
        apply_meshopt=meshopt,
        texture_size=texture_size,
    )

    try:
        sz = format_bytes(Path(res.output_path).stat().st_size)
    except OSError:
        sz = "?"
    flags = []
    if res.tangents_added:
        flags.append("tangents")
    if res.normal_map_path is not None:
        flags.append("normal-map")
    if res.ktx2_applied:
        flags.append("ktx2")
    if res.meshopt_applied:
        flags.append("meshopt")
    flags_str = "+".join(flags) if flags else "vanilla"
    console.print(
        f"[bold green]✓[/bold green] bake-master → [cyan]{res.output_path}[/cyan] "
        f"[dim]({sz}, {res.decimated_faces} tris, {flags_str})[/dim]"
    )


@cli.command("topology-fix")
@click.argument("input_mesh", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="GLB de saída (defeito: sobrepõe input).",
)
@click.option(
    "--fill-holes-sides",
    type=int,
    default=None,
    help=(
        "Tamanho máximo (arestas) de buracos a preencher. Omitido: perfil topology_clean (64). 0 desativa fill_holes."
    ),
)
@click.option(
    "--watertight/--no-watertight",
    default=None,
    help=(
        "Override do perfil: fecho seletivo (skip_flap_erode + limite diâmetro loop). Omitido: topology_clean (ligado)."
    ),
)
@click.option(
    "--morph-close",
    type=float,
    default=None,
    help=(
        "Fecho morfológico volumétrico (metros): dilate→erode via voxel remesh. "
        "Funde double shells finas. Omitido=auto (Nxvoxel MC via "
        "--morph-close-voxels/--category). 0=desliga. Valores altos derretem detalhe."
    ),
)
@click.option(
    "--morph-close-voxels",
    "morph_close_voxels",
    type=float,
    default=None,
    help=(
        "N de «voxel merge» no auto morph-close (default 0.125; terrain/rock=0.375). "
        "Ignorado se --morph-close (metros) for explícito."
    ),
)
@click.option(
    "--size-m",
    "size_m_str",
    default=None,
    help="L,H,W metros — escala / hints (morph-close só se --morph-close >0).",
)
@click.option(
    "--category",
    default=None,
    help="Prior de tamanho típico se faltar --size-m (humanoid≈1.7, building≈6).",
)
@click.option(
    "--bbox-preset",
    default=None,
    help="Prior de tamanho via preset Omni (só char_m; mesma fórmula).",
)
@click.option(
    "--octree",
    "octree_for_morph",
    type=int,
    default=None,
    help="Octree usado no shape (refina auto morph ≈ Nxchar_m/octree).",
)
@click.option(
    "--export-origin",
    type=click.Choice(["feet", "center", "none"]),
    default=None,
    help=(
        "Aplica reposicionamento da origem ao final do reparo. Se omitido, mantém "
        "a origem do input (recomendado quando o input já vem orientado)."
    ),
)
@click.option(
    "--export-rotation-x-deg",
    type=float,
    default=None,
    help="Rotação X em graus aplicada antes do reposicionamento (raro; default 0).",
)
@click.option(
    "--remove-internal-shells/--keep-internal-shells",
    default=None,
    help="Strip cascas internas. Omitido: auto ON para building/chapel.",
)
@click.option(
    "--engine",
    type=click.Choice(["auto", "arrays", "bpy"]),
    default="arrays",
    show_default=True,
    help=(
        "Motor de reparo: arrays = fase vetorizada numpy/scipy nos filtros "
        "(default; fallback bpy se houver UVs/weights/armature); bpy = "
        "caminho legado bmesh completo; auto = aliases arrays (compat)."
    ),
)
def topology_fix_cmd(
    input_mesh: Path,
    output: Path | None,
    fill_holes_sides: int | None,
    watertight: bool | None,
    morph_close: float | None,
    morph_close_voxels: float | None,
    size_m_str: str | None,
    category: str | None,
    bbox_preset: str | None,
    octree_for_morph: int | None,
    export_origin: str | None,
    export_rotation_x_deg: float | None,
    remove_internal_shells: bool | None,
    engine: str,
) -> None:
    """Repara topologia de um GLB cru (Stage 2 da pipeline).

    Operações: reweld → weld → dissolve/loose → long edges → slivers → debris →
    fill_holes → watertight seletivo → (building) strip cascas → normais →
    shade-smooth.

    Substitui a etapa que estava embebida em ``text3d generate``.
    Recomendado correr em ``id_shape.glb`` para produzir ``id_clean.glb``.
    """
    from text3d.bbox_tune import resolve_morph_close, size_m_from_mapping
    from text3d.utils.mesh_lod import _is_hollow_shell_category, prepare_mesh_topology

    out_path = Path(output) if output else input_mesh
    out_path.parent.mkdir(parents=True, exist_ok=True)

    size_m_vals = size_m_from_mapping(size_m_str) if size_m_str else None
    # Hint de categoria também via bbox_preset Omni (chapel → building).
    cat_eff = category
    if cat_eff is None and bbox_preset:
        from text3d.bbox_tune import _PRESET_APPROACH_KEY

        cat_eff = _PRESET_APPROACH_KEY.get(str(bbox_preset).strip().lower())
    hollow = _is_hollow_shell_category(cat_eff)
    if remove_internal_shells is None and hollow:
        remove_internal_shells = True

    # Octree do sidecar de generate (se --octree omitido) → morph alinhado ao tune.
    if octree_for_morph is None:
        from .omni_presets import read_omni_fingerprint

        _fp = read_omni_fingerprint(input_mesh)
        if isinstance(_fp, dict) and _fp.get("octree_resolution") is not None:
            with contextlib.suppress(TypeError, ValueError):
                octree_for_morph = int(_fp["octree_resolution"])

    t_topo = time.perf_counter()
    morph_eff = resolve_morph_close(
        explicit=morph_close,
        size_m=size_m_vals,
        category=category,
        bbox_preset=bbox_preset,
        octree=octree_for_morph,
        morph_close_voxels=morph_close_voxels,
    )
    from text3d.bbox_tune import morph_close_voxels_for

    n_vox = morph_close_voxels_for(category, explicit=morph_close_voxels)
    if morph_eff is not None and morph_close is None:
        console.print(f"[dim]auto morph-close={morph_eff:.4f}m (voxel-merge N={n_vox:g}, escala física)[/dim]")

    if export_rotation_x_deg is not None:
        _defaults.set_export_rotation_x_rad_override(math.radians(float(export_rotation_x_deg)))
    try:
        prepare_mesh_topology(
            input_mesh,
            out_path,
            fill_holes_sides=fill_holes_sides,
            watertight=watertight,
            morph_close=morph_eff,
            size_m=size_m_vals,
            remove_internal_shells=remove_internal_shells,
            category=cat_eff or category,
            engine=engine,
        )
        if export_origin is not None and export_origin != "none":
            from .utils.export import convert_mesh

            # Smooth + NORMAL: omitir normais fazia clean flat; split duro
            # evita-se com smooth_shade_scene em _export_glb_bpy.
            convert_mesh(
                out_path,
                out_path,
                rotate=False,
                origin_mode=export_origin,
                export_normals=True,
            )
    finally:
        if export_rotation_x_deg is not None:
            _defaults.set_export_rotation_x_rad_override(None)

    verts = 0
    tris = 0
    # Recusar clean vazio (resume tratava 228 B como "clean existe" e skipava).
    try:
        from gamedev_shared.glb_verify import extract_glb_meta

        meta = extract_glb_meta(out_path)
        verts = int(meta.get("vertex_count_total") or 0)
        tris = int(meta.get("triangle_count_total") or 0)
        # <64 tris = colapso (weld density / debris) — não é clean útil.
        if meta.get("_error") or verts <= 0 or tris < 64:
            console.print(
                f"[bold red]✗[/bold red] topology-fix produziu mesh vazia/colapsada "
                f"(V={verts} T={tris}): [cyan]{out_path}[/cyan]"
            )
            with contextlib.suppress(OSError):
                out_path.unlink()
            sys.exit(1)
    except SystemExit:
        raise
    except Exception as exc:
        console.print(f"[yellow]aviso: não consegui validar clean: {exc}[/yellow]")

    topo_secs = time.perf_counter() - t_topo
    with contextlib.suppress(OSError):
        from .omni_presets import patch_omni_debug

        # Grava no sidecar do shape de input (e no clean se path diferente).
        _topo_dbg = {
            "topology_fix": {
                "seconds": round(topo_secs, 3),
                "morph_close_m": morph_eff,
                "morph_close_voxels": n_vox,
                "morph_explicit_m": morph_close,
                "octree": octree_for_morph,
                "engine": engine,
                "category": cat_eff or category,
                "bbox_preset": bbox_preset,
                "size_m": size_m_vals,
                "fill_holes_sides": fill_holes_sides,
                "watertight": watertight,
                "remove_internal_shells": remove_internal_shells,
                "export_origin": export_origin,
                "input": str(input_mesh),
                "output": str(out_path),
                "verts": verts,
                "tris": tris,
            }
        }
        patch_omni_debug(input_mesh, _topo_dbg)
        if Path(out_path).resolve() != Path(input_mesh).resolve():
            patch_omni_debug(out_path, _topo_dbg)

    try:
        sz = format_bytes(out_path.stat().st_size)
    except OSError:
        sz = "?"
    console.print(
        f"[bold green]✓[/bold green] topology-fix → [cyan]{out_path}[/cyan] [dim]({sz}, {topo_secs:.1f}s)[/dim]"
    )


@cli.command("gpu-processes")
def gpu_processes_cmd() -> None:
    """Lista GPUs e processos compute (NVML → nvidia-smi) — útil quando VRAM exclusiva falha."""
    from gamedev_shared.gpu import list_gpu_snapshots, list_nvidia_compute_apps, nvml_available

    snaps = list_gpu_snapshots()
    apps = list_nvidia_compute_apps()
    if not snaps and not apps and not nvml_available():
        console.print("[yellow]NVML/nvidia-smi indisponível — sem driver NVIDIA ou libs em falta.[/yellow]")
        sys.exit(1)

    console.print(
        Panel.fit(
            "[bold]Uso da GPU[/bold] — snapshots + processos compute (via Shared NVML)",
            border_style="cyan",
        )
    )
    if snaps:
        gtab = Table(title="GPUs", box=box.ROUNDED)
        gtab.add_column("ID", style="cyan")
        gtab.add_column("Nome")
        gtab.add_column("Livre MiB", justify="right")
        gtab.add_column("Usado MiB", justify="right")
        gtab.add_column("Total MiB", justify="right")
        gtab.add_column("Fonte", style="dim")
        for s in snaps:
            gtab.add_row(
                str(s.index),
                s.name,
                str(s.free_mib),
                str(s.used_mib),
                str(s.total_mib),
                s.source,
            )
        console.print(gtab)
    else:
        console.print("[dim]Nenhuma GPU listada.[/dim]")

    ptab = Table(title="Processos compute", box=box.ROUNDED)
    ptab.add_column("PID", style="cyan")
    ptab.add_column("Nome")
    ptab.add_column("VRAM MiB", justify="right")
    if apps:
        for pid, name, mib in apps:
            ptab.add_row(str(pid), name, "?" if mib is None else str(mib))
    else:
        ptab.add_row("—", "(nenhum)", "—")
    console.print(ptab)

    console.print()
    console.print(
        Panel(
            "[bold]Parar um processo[/bold]\n"
            "• Na tabela [bold]Processos compute[/bold], anota o [bold]PID[/bold] da linha que consome VRAM.\n"
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
@click.option(
    "--painted-mesh",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="GLB texturizado para LOD com texturas. LOD0=painted, LOD1 textura/2, LOD2 textura/4.",
)
@click.option(
    "--texture-size",
    type=int,
    default=2048,
    show_default=True,
    help=(
        "Lado do atlas lod0 (snap 64px). lod1=/2, lod2=/4. Nunca upscale acima "
        "da textura source. Path geométrico (rigged) também downscale."
    ),
)
@click.option(
    "--target-faces",
    type=int,
    default=None,
    help="Face target para LOD0. Com --painted-mesh: LOD1≈/2, LOD2≈/3. Sem painted: decima lod0.",
)
@click.option(
    "--finish/--no-finish",
    default=True,
    show_default=True,
    help=(
        "Round 2: aplica gltf_transform_finish (dedup+prune+uastc+meshopt+tangents) "
        "aos LOD1/LOD2. Use --no-finish para gerar LODs crus para debug."
    ),
)
@click.option(
    "--finish-lod0",
    is_flag=True,
    default=False,
    help="Aplica finalização também ao LOD0 (use só quando lod_cmd corre sem bake-master).",
)
@click.option(
    "--meshopt/--no-meshopt",
    default=True,
    show_default=True,
    help="Aplica EXT_meshopt_compression + KHR_mesh_quantization aos LODs via "
    "@gltf-transform/cli (compressão ~85%% em geometria). O bug histórico "
    "(POSITION SHORT sem extensão) foi resolvido na CLI 4.x. Atenção: POSITION "
    "fica quantizado (SHORT) — incompatível com colliders trimesh reutilizados "
    "do mesh visual; gerar colliders dedicados (text3d collision) nesses casos.",
)
@click.option(
    "--skin-source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=(
        "GLB rigged (weights+skeleton). Após LOD texturizado, rebind via "
        "gamedev_shared.skin_transfer (KDTree + armature + anims)."
    ),
)
@click.option(
    "--animation-source",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help=("GLB com clips de animação quando --skin-source não tem actions (ex.: rigged_hi + id_animated.glb)."),
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
    painted_mesh: Path | None,
    texture_size: int,
    target_faces: int | None,
    finish: bool,
    finish_lod0: bool,
    meshopt: bool,
    skin_source: Path | None,
    animation_source: Path | None,
) -> None:
    """Gera três GLB com níveis de detalhe (LOD0=cheio, LOD1/LOD2 decimados).

    Com ``--painted-mesh``: ``remesh_textured_glb`` (perfil ``pre_decimate_uv``
    + Decimate COLLAPSE + piso heurístico de faces). Sem painted: decimate
    geométrico + downscale de textura por nível. ``--meshfix`` só fecha buracos
    pequenos no caminho geométrico (não é o topology-fix completo).
    """
    stem = basename_opt if basename_opt else input_mesh.stem
    try:
        if painted_mesh:
            from text3d.utils.mesh_lod import generate_lod_textured_glb_triplet

            paths = generate_lod_textured_glb_triplet(
                painted_mesh,
                output_dir,
                stem,
                lod1_ratio=lod1_ratio,
                lod2_ratio=lod2_ratio,
                min_faces_lod1=min_faces_lod1,
                min_faces_lod2=min_faces_lod2,
                texture_size_lod0=texture_size,
                target_faces=target_faces,
                apply_finish=finish,
                finish_lod0=finish_lod0,
                apply_meshopt=meshopt,
                skin_source=skin_source,
                animation_source=animation_source,
            )
        else:
            paths = generate_lod_glb_triplet(
                input_mesh,
                output_dir,
                stem,
                lod1_ratio=lod1_ratio,
                lod2_ratio=lod2_ratio,
                min_faces_lod1=min_faces_lod1,
                min_faces_lod2=min_faces_lod2,
                meshfix=meshfix,
                texture_size_lod0=texture_size,
                target_faces=target_faces,
            )
            # Non-painted path has no finish step — apply meshopt post-hoc to
            # each LOD output so compressed LODs are the default.
            if meshopt:
                from text3d.utils.gltf_finish import gltf_transform_finish

                compressed = []
                for p in paths:
                    res = gltf_transform_finish(p, p, apply_meshopt=True, apply_uastc=False)
                    compressed.append(res.output_path)
                paths = compressed
    except RuntimeError as e:
        raise click.ClickException(str(e)) from e
    except ValueError as e:
        raise click.ClickException(str(e)) from e

    missing = [p for p in paths if not Path(p).is_file() or Path(p).stat().st_size < 64]
    if missing:
        raise click.ClickException(
            "LOD ladder incompleta (ficheiro ausente/vazio): " + ", ".join(Path(p).name for p in missing)
        )

    console.print(
        Panel(
            "\n".join(f"• [cyan]{p}[/cyan]" for p in paths),
            title="[bold green]LOD gerado[/bold green]",
            border_style="green",
        )
    )


@cli.command("simplify")
@click.argument("input_mesh", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB de saída (Decimate COLLAPSE + reparo Shared).",
)
@click.option(
    "--target-faces",
    type=int,
    required=True,
    help="Número alvo de faces após Decimate.",
)
@click.option(
    "--no-repair",
    "no_repair",
    is_flag=True,
    default=False,
    help="Desliga perfis pre_decimate_uv / post_decimate.",
)
def simplify_cmd(
    input_mesh: Path,
    output: Path,
    target_faces: int,
    no_repair: bool,
) -> None:
    """Simplifica GLB via Decimate COLLAPSE (sistema unificado Shared).

    Mesmo pipeline que LOD / remesh-textured: merge → ``pre_decimate_uv`` →
    Decimate → ``post_decimate``. Preferir isto a ``remesh`` (voxel) antes
    do Paint3D — voxel destrói paredes finas/janelas.

    \b
        text3d simplify clean.glb -o to_paint.glb --target-faces 80000
    """
    from gamedev_shared.mesh_simplify import simplify_glb

    if target_faces < 4:
        raise click.ClickException("--target-faces deve ser >= 4")
    try:
        with console.status(
            f"[bold yellow]Simplifying para ~{target_faces} faces (Decimate)...",
            spinner="dots",
        ):
            simplify_glb(input_mesh, output, target_faces=target_faces, repair=not no_repair)
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    out_p = output.resolve()
    try:
        sz = format_bytes(out_p.stat().st_size)
    except OSError:
        sz = "?"
    console.print(Rule("[bold green]simplify", style="green"))
    console.print(f"[bold green]✓[/bold green] [cyan]{out_p}[/cyan] [dim]({sz})[/dim]")


@cli.command("remesh")
@click.argument("input_mesh", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB de saída (voxel remesh, sem textura).",
)
@click.option(
    "--target-faces",
    type=int,
    required=True,
    help="Número alvo de faces após voxel remesh.",
)
def remesh_cmd(
    input_mesh: Path,
    output: Path,
    target_faces: int,
) -> None:
    """Voxel remesh de GLB (só geometria). Preferir ``simplify`` na maioria dos casos.

    Re-malha com bpy voxel remesh + perfil ``post_voxel``. Útil para
    regularizar topologia; destrutivo em paredes finas — para orçamento
    pré-paint usar ``text3d simplify``.

    \b
        text3d remesh modelo.glb -o remeshed.glb --target-faces 24000
    """
    if target_faces < 4:
        raise click.ClickException("--target-faces deve ser >= 4")
    try:
        with console.status(
            f"[bold yellow]Remeshing para ~{target_faces} faces (voxel)...",
            spinner="dots",
        ):
            remesh_geometry_only_glb(input_mesh, output, target_faces=target_faces)
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    out_p = output.resolve()
    try:
        sz = format_bytes(out_p.stat().st_size)
    except OSError:
        sz = "?"
    console.print(Rule("[bold green]remesh", style="green"))
    console.print(f"[bold green]✓[/bold green] [cyan]{out_p}[/cyan] [dim]({sz})[/dim]")


@cli.command("remesh-textured")
@click.argument("input_mesh", type=click.Path(exists=True, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB de saída (remeshed com textura reprojetada).",
)
@click.option(
    "--target-faces",
    type=int,
    required=True,
    help="Número alvo de faces após remesh isotrópico.",
)
@click.option(
    "--texture-size",
    type=int,
    default=2048,
    show_default=True,
    help="Resolução da textura de saída (pixels).",
)
def remesh_textured_cmd(
    input_mesh: Path,
    output: Path,
    target_faces: int,
    texture_size: int,
) -> None:
    """Remesh isotrópico de GLB texturado com reprojeção de textura.

    Re-malha para ``--target-faces`` triângulos regulares (pymeshlab isotropic)
    e re-projeta a textura original no novo layout UV (xatlas + closest-point
    sampling + rasterização).

    \b
        text3d remesh-textured pintado.glb -o remeshed.glb --target-faces 6000
        text3d remesh-textured model.glb -o out.glb --target-faces 10000 --texture-size 4096
    """
    if target_faces < 4:
        raise click.ClickException("--target-faces deve ser >= 4")
    try:
        with console.status(
            f"[bold yellow]Remeshing para ~{target_faces} faces + reprojeção de textura...",
            spinner="dots",
        ):
            remesh_textured_glb(
                input_mesh,
                output,
                target_faces=target_faces,
                texture_size=texture_size,
            )
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    out_p = output.resolve()
    try:
        sz = format_bytes(out_p.stat().st_size)
    except OSError:
        sz = "?"
    console.print(Rule("[bold green]remesh-textured", style="green"))
    console.print(f"[bold green]✓[/bold green] [cyan]{out_p}[/cyan] [dim]({sz})[/dim]")


@cli.command("split-at-height")
@click.argument("input_mesh", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=True,
    help="GLB multi-mesh de saída (objectos Stump + Top).",
)
@click.option(
    "--cut-height",
    type=float,
    default=None,
    help="Altura do corte em metros acima da base (bbox.min.y). Default: min(0.8, altura/4).",
)
@click.option(
    "--cut-ratio",
    type=float,
    default=None,
    help="Fracao da altura AABB (0-1) como alternativa a --cut-height.",
)
@click.option(
    "--cap/--no-cap",
    default=False,
    show_default=True,
    help="Fechar o corte (legado/experimental — default off: só bisect).",
)
@click.option(
    "--bevel-offset",
    type=float,
    default=None,
    help="Largura do chanfro no rebordo (metros). Default: auto (~8%% do raio).",
)
@click.option(
    "--bevel-segments",
    type=int,
    default=0,
    show_default=True,
    help="Segmentos do bevel no rebordo (0=off, recomendado p/ fecho seamless).",
)
@click.option(
    "--bevel-profile",
    type=float,
    default=0.7,
    show_default=True,
    help="Profile do bevel (0.5=recto, ~0.7=arredondado).",
)
@click.option("--stump-name", type=str, default="Stump", show_default=True, help="Nome do mesh stump.")
@click.option("--top-name", type=str, default="Top", show_default=True, help="Nome do mesh top/canopy.")
@click.option(
    "--split-files",
    is_flag=True,
    default=False,
    help="Também escreve {stem}_stump.glb e {stem}_top.glb junto do output.",
)
def split_at_height_cmd(
    input_mesh: Path,
    output: Path,
    cut_height: float | None,
    cut_ratio: float | None,
    cap: bool,
    bevel_offset: float | None,
    bevel_segments: int,
    bevel_profile: float,
    stump_name: str,
    top_name: str,
    split_files: bool,
) -> None:
    """Parte um GLB num plano horizontal em stump + top.

    Útil para árvores destruíveis: stump fica no chão, top anima a queda.
    O corte é fechado com disco + chanfro suave em ambas as metades.
    Por defeito exporta um único GLB com dois meshes nomeados; ``--split-files``
    acrescenta GLBs separados.

    \b
        text3d split-at-height tree_lod0.glb -o tree_split.glb
        text3d split-at-height tree.glb -o tree.glb --cut-ratio 0.25 --split-files
    """
    from .utils.mesh_split import split_at_height_glb

    if cut_height is not None and cut_ratio is not None:
        raise click.ClickException("Use --cut-height ou --cut-ratio, não ambos")
    if bevel_segments < 0:
        raise click.ClickException("--bevel-segments deve ser >= 0")
    if not 0.0 <= bevel_profile <= 1.0:
        raise click.ClickException("--bevel-profile deve estar em [0, 1]")
    try:
        with console.status("[bold yellow]A partir malha por altura…", spinner="dots"):
            result = split_at_height_glb(
                input_mesh,
                output,
                cut_height=cut_height,
                cut_ratio=cut_ratio,
                cap=cap,
                bevel_offset=bevel_offset,
                bevel_segments=bevel_segments,
                bevel_profile=bevel_profile,
                stump_name=stump_name,
                top_name=top_name,
                split_files=split_files,
            )
    except (RuntimeError, TypeError, ValueError) as e:
        raise click.ClickException(str(e)) from e

    console.print(Rule("[bold green]split-at-height", style="green"))
    console.print(
        f"[bold green]✓[/bold green] [cyan]{result.output.resolve()}[/cyan] "
        f"[dim](cut_y={result.cut_y:.3f}, stump={result.stump_faces}f, top={result.top_faces}f)[/dim]"
    )
    if result.stump_path is not None:
        console.print(f"  stump → [cyan]{result.stump_path.resolve()}[/cyan]")
    if result.top_path is not None:
        console.print(f"  top   → [cyan]{result.top_path.resolve()}[/cyan]")


@cli.command("finish")
@click.argument("input_glb", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False, path_type=Path),
    default=None,
    help="GLB de saída (default: in-place sobre o input).",
)
@click.option("--ktx2/--no-ktx2", default=True, show_default=True, help="Comprime texturas para KTX2/UASTC.")
@click.option(
    "--meshopt/--no-meshopt",
    default=True,
    show_default=True,
    help="Aplica EXT_meshopt_compression (bpy nativo ou gltf-transform).",
)
@click.option("--tangents/--no-tangents", default=True, show_default=True, help="Recalcula tangents MikkTSpace.")
@click.option("--dedup/--no-dedup", default=True, show_default=True, help="gltf-transform dedup.")
@click.option("--prune/--no-prune", default=True, show_default=True, help="gltf-transform prune.")
def finish_cmd(
    input_glb: Path,
    output: Path | None,
    ktx2: bool,
    meshopt: bool,
    tangents: bool,
    dedup: bool,
    prune: bool,
) -> None:
    """Finaliza GLB: tangents → dedup → prune → KTX2/UASTC → meshopt.

    Caminho feliz para re-comprimir assets já gerados sem regenerar a pipeline.

    \b
    text3d finish hero_lod0.glb
    text3d finish hero_lod0.glb -o hero_lod0_opt.glb --no-tangents
    """
    from .utils.gltf_finish import gltf_transform_finish

    out = output if output is not None else input_glb
    res = gltf_transform_finish(
        input_glb,
        out,
        apply_tangents=tangents,
        apply_dedup=dedup,
        apply_prune=prune,
        apply_uastc=ktx2,
        apply_meshopt=meshopt,
    )
    flags = []
    if res.tangents_added:
        flags.append("tangents")
    if res.dedup_applied:
        flags.append("dedup")
    if res.prune_applied:
        flags.append("prune")
    if res.ktx2_applied:
        flags.append("ktx2")
    if res.meshopt_applied:
        flags.append(f"meshopt:{res.meshopt_backend or '?'}")
    try:
        sz = format_bytes(Path(res.output_path).stat().st_size)
    except OSError:
        sz = "?"
    detail = "+".join(flags) if flags else "sem passos aplicados"
    if res.skipped_reason:
        console.print(f"[yellow]finish[/yellow] skipped: {res.skipped_reason}")
        sys.exit(1)
    console.print(
        Rule(
            f"[bold green]finish[/bold green] → {res.output_path} [dim]({sz}; {detail})[/dim]",
            style="green",
        )
    )
    if ktx2 and not res.ktx2_applied:
        console.print("[yellow]aviso:[/yellow] KTX2 não aplicado — `text3d doctor` (npx @gltf-transform/cli)")
    if meshopt and not res.meshopt_applied:
        console.print(
            "[yellow]aviso:[/yellow] meshopt não aplicado — "
            "`text3d doctor` (libmeshoptimizer-dev e/ou npx @gltf-transform/cli)"
        )


@cli.command("collision")
@click.argument("input_mesh", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True, help="Output collision GLB")
@click.option("--max-faces", type=int, default=300, show_default=True, help="Target face count for collision mesh")
@click.option(
    "--mode",
    type=click.Choice(["hull", "envelope", "mesh"], case_sensitive=False),
    default=None,
    help=(
        "hull=convex+decimate (default); envelope=inflate+voxel remesh+decimate; "
        "mesh=inflate+decimate source (mais preciso p/ arcos). Omitido → --convex-hull."
    ),
)
@click.option(
    "--voxel-size",
    type=float,
    default=None,
    help="Envelope only: voxel remesh size in metres (default ≈ char_m/48).",
)
@click.option(
    "--inflate",
    type=float,
    default=None,
    help="Outward offset in metres before remesh/decimate (envelope/mesh default ≈ max(char·0.008, 0.04)).",
)
@click.option(
    "--convex-hull/--no-convex-hull",
    default=True,
    help="Legacy: True→hull, False→mesh when --mode omitted (default: yes)",
)
def collision_cmd(
    input_mesh: Path,
    output: Path,
    max_faces: int,
    mode: str | None,
    voxel_size: float | None,
    inflate: float | None,
    convex_hull: bool,
) -> None:
    """Generate a simplified collision mesh from any GLB/OBJ/PLY.

    Produces a low-poly mesh suitable for physics collision in Unity/Godot/Unreal.
    Default: convex hull + quadric decimation to 300 faces.
    For arches/gates: ``--mode mesh`` (surface-accurate) or ``--mode envelope``.

    \b
    text3d collision modelo.glb -o collision.glb
    text3d collision arco.glb -o coll.glb --mode mesh --max-faces 256 --inflate 0.08
    text3d collision modelo.glb -o coll.glb --max-faces 500 --no-convex-hull
    """
    from .utils.collision import generate_collision_mesh

    out = generate_collision_mesh(
        input_mesh,
        output,
        max_faces=max_faces,
        mode=mode,
        voxel_size=voxel_size,
        inflate=inflate,
        convex_hull=None if mode is not None else convex_hull,
    )
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
@click.option("--guidance", type=float, default=_defaults.DEFAULT_HY_GUIDANCE)
@click.option("--octree-resolution", type=int, default=None)
@click.option("--num-chunks", type=int, default=None)
@click.option("--mc-level", default="auto", show_default=True, help="Nível marching cubes (número ou 'auto').")
@click.option(
    "--bounds-mode",
    "bounds_mode",
    type=click.Choice(["auto", "cube"]),
    default="auto",
    show_default=True,
    help="Bounds do grid MC: auto=aspecto bbox Omni; cube=±1.01 clássico.",
)
@click.option("--sdnq-preset", type=str, default=None)
@click.option("--model-subfolder", default="", hidden=True, help="[Deprecated] ignorado (Omni flat).")
@click.option(
    "--export-origin",
    "export_origin",
    type=click.Choice(["feet", "center", "none"]),
    default=_defaults.DEFAULT_EXPORT_ORIGIN,
    help="Origem ao gravar: feet=pés no chão, center=centro da caixa, none=não mover.",
)
@click.option("--allow-shared-gpu", is_flag=True)
@click.option("--gpu-kill-others/--no-gpu-kill-others", default=False)
@click.option("--force", is_flag=True, help="Regenerar mesmo se o output já existe.")
@click.option("--gpu-ids", type=str, default=None)
@click.option(
    "--volume-decoder",
    "volume_decoder",
    type=click.Choice(["vanilla", "hierarchical", "flashvdm"]),
    default="vanilla",
    show_default=True,
    help="Decoder volumétrico: vanilla | hierarchical (~lossless, rápido) | flashvdm (mais rápido).",
)
@click.option(
    "--mc-algo",
    "mc_algo",
    type=click.Choice(["mc", "dmc"]),
    default=None,
    help="Extracção de superfície: mc (skimage, CPU) ou dmc (GPU, requer diso).",
)
@click.option(
    "--compile",
    "compile_models",
    is_flag=True,
    default=False,
    help="torch.compile no DiT+VAE — warmup na 1ª inferência amortizado pelo lote.",
)
@click.option(
    "--compile-mode",
    "compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default="default",
    show_default=True,
    help="Modo Inductor (cudagraphs só full-GPU).",
)
@click.option(
    "--sage-attn",
    "sage_attention",
    is_flag=True,
    default=False,
    help="SageAttention (attention INT8, Ampere+; requer sageattention).",
)
@click.option(
    "--sdnq-matmul",
    "sdnq_matmul",
    is_flag=True,
    default=False,
    help="Matmul quantizado SDNQ (INT8); usar com --sdnq-preset.",
)
@click.option(
    "--group-offload/--no-group-offload",
    "allow_group_offload",
    default=True,
    show_default=True,
    help="Group offload + CUDA streams (DiT/cond_encoder Omni).",
)
@click.option(
    "--fp8-layerwise/--no-fp8-layerwise",
    "fp8_layerwise",
    default=False,
    show_default=True,
    help="Layerwise casting fp8 no DiT+conditioner.",
)
@click.option(
    "--channels-last/--no-channels-last",
    "channels_last",
    default=False,
    show_default=True,
    help="Memory format NHWC no VAE/DiT.",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    "hw_auto",
    default=True,
    show_default=True,
    help=(
        "Auto-detecção de hardware: preenche steps/octree/chunks, SDNQ, multi-GPU e "
        "volume decoder pelo perfil da(s) GPU(s) quando não definidos. Env: TEXT3D_HW_AUTO=0."
    ),
)
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier (fast / low / medium / high / highest).",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for automatic tuning (e.g., humanoid, weapon, prop).",
)
@click.option(
    "--no-topology-fix/--topology-fix",
    "no_topology_fix",
    default=True,
    show_default=True,
    help=(
        "Por defeito salta o reparo (shape cru) — a master pipeline limpa no "
        "Stage 2 ``text3d topology-fix``. Use ``--topology-fix`` para reparar "
        "in-process (custo duplo se também correr topology-fix depois)."
    ),
)
@click.option("-v", "--verbose", "batch_verbose", is_flag=True)
@add_ums_options
@click.pass_context
def generate_batch(
    ctx,
    manifest: str,
    output_dir: str,
    preset: str | None,
    steps: int | None,
    guidance: float,
    octree_resolution: int | None,
    num_chunks: int | None,
    mc_level: str,
    bounds_mode: str,
    sdnq_preset: str | None,
    model_subfolder: str,
    export_origin: str,
    allow_shared_gpu: bool,
    gpu_kill_others: bool,
    gpu_ids: str | None,
    force: bool,
    volume_decoder: str,
    mc_algo: str | None,
    compile_models: bool,
    compile_mode: str,
    sage_attention: bool,
    sdnq_matmul: bool,
    allow_group_offload: bool,
    fp8_layerwise: bool,
    channels_last: bool,
    hw_auto: bool,
    quality: str,
    category: str | None,
    no_topology_fix: bool,
    batch_verbose: bool,
    ums_priority: str | None,
    no_ums: bool,
    ums_stream: bool,
) -> None:
    """Processa lote image-to-3D a partir de manifest JSON (JSONL em stdout).

    Por defeito cada item passa pelo UMS (paridade Paint3D/Text2D batch).
    Load in-process só se sobrar trabalho após UMS.
    """
    mc_level = _parse_mc_level_flag(mc_level)
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

    # QualityEngine: soft resolution — fills defaults when user didn't specify.
    _src = click.core.ParameterSource
    _user_set_preset = ctx.get_parameter_source("preset") not in (_src.DEFAULT,)
    _user_set_steps = ctx.get_parameter_source("steps") not in (_src.DEFAULT,)
    _user_set_guidance = ctx.get_parameter_source("guidance") not in (_src.DEFAULT,)
    _user_set_octree = ctx.get_parameter_source("octree_resolution") not in (_src.DEFAULT,)
    _user_set_chunks = ctx.get_parameter_source("num_chunks") not in (_src.DEFAULT,)
    _user_set_quality = ctx.get_parameter_source("quality") not in (_src.DEFAULT,)

    from gamedev_shared.quality import QualityEngine

    _qengine = QualityEngine()
    _qresolved = _qengine.resolve("text3d", quality=quality, category=category)
    if not _user_set_preset and "preset" in _qresolved.params:
        preset = _qresolved.params["preset"]
    if not _user_set_guidance and "guidance" in _qresolved.params:
        guidance = _qresolved.params["guidance"]
    if not _user_set_steps and "steps" in _qresolved.params:
        steps = _qresolved.params["steps"]
    if not _user_set_octree and "octree" in _qresolved.params:
        octree_resolution = _qresolved.params["octree"]
    if not _user_set_chunks and "chunks" in _qresolved.params:
        num_chunks = _qresolved.params["chunks"]

    base_steps = steps
    base_octree = octree_resolution
    base_chunks = num_chunks
    if preset is not None:
        pv = _defaults.PRESET_HUNYUAN[preset]
        base_steps = base_steps if base_steps is not None else pv["steps"]
        base_octree = base_octree if base_octree is not None else pv["octree"]
        base_chunks = base_chunks if base_chunks is not None else pv["chunks"]

    parsed_gpu_ids: list[int] | None = None
    if gpu_ids is not None:
        parsed_gpu_ids = [int(x) for x in gpu_ids.split(",") if x.strip()]

    # Hardware auto-detection (soft): só preenche valores não definidos.
    from .hardware import detect_hardware_profile, hw_auto_enabled

    hwp = None
    if hw_auto and hw_auto_enabled():
        hwp = detect_hardware_profile()
        _params_untouched = not (
            _user_set_preset or _user_set_quality or _user_set_steps or _user_set_octree or _user_set_chunks
        )
        if _params_untouched:
            base_steps = hwp.steps
            base_octree = hwp.octree
            base_chunks = hwp.chunks
        if sdnq_preset is None and hwp.sdnq_preset:
            sdnq_preset = hwp.sdnq_preset
        if parsed_gpu_ids is None and hwp.gpu_ids:
            parsed_gpu_ids = hwp.gpu_ids
        if volume_decoder == "vanilla":  # default click — "vanilla" explícito também conta
            volume_decoder = hwp.volume_decoder
        _err.print(f"[dim]Hardware (auto): {hwp.summary()}[/dim]")

    if base_steps is None:
        base_steps = _defaults.DEFAULT_HY_STEPS
    if base_octree is None:
        base_octree = _defaults.DEFAULT_OCTREE_RESOLUTION
    if base_chunks is None:
        base_chunks = _defaults.DEFAULT_NUM_CHUNKS

    # UMS por item primeiro; GPU prep + load só no fallback in-process.
    allow_shared = bool(allow_shared_gpu) or _env_allow_shared_gpu()
    gpu_kill = _gpu_kill_others_effective(bool(gpu_kill_others))
    resolved_sdnq = sdnq_preset if sdnq_preset else ""
    batch_offload = hwp.offload if hwp is not None else False
    _ = model_subfolder  # deprecated

    old_sigterm = signal.signal(signal.SIGTERM, _batch_signal_handler)
    old_sigint = signal.signal(signal.SIGINT, _batch_signal_handler)

    global _batch_generator

    def _ensure_inprocess_generator() -> None:
        global _batch_generator
        if _batch_generator is not None:
            return
        prepare_gpu_exclusive(
            needed_mib=6000,
            allow_shared=allow_shared,
            kill_others=gpu_kill,
            allow_shared_env="TEXT3D_ALLOW_SHARED_GPU",
            kill_others_env="TEXT3D_GPU_KILL_OTHERS",
            backend="text3d",
            quant_mode=None if sdnq_preset in (None, "none", "") else sdnq_preset,
            console=_err,
        )
        from text3d.ums_load import map_ums_load_kwargs

        with _err.status("[bold yellow]A preparar gerador batch (fallback in-process)...", spinner="dots"):
            _batch_kw = map_ums_load_kwargs(
                {
                    "verbose": batch_verbose,
                    "sdnq_preset": resolved_sdnq,
                    "gpu_ids": parsed_gpu_ids,
                    "volume_decoder": volume_decoder,
                    "mc_algo": mc_algo,
                    "torch_compile": compile_models,
                    "torch_compile_mode": compile_mode,
                    "sage_attention": sage_attention,
                    "sdnq_quantized_matmul": sdnq_matmul,
                    "offload": batch_offload,
                    "allow_group_offload": allow_group_offload,
                    "fp8_layerwise": fp8_layerwise,
                    "channels_last": channels_last,
                    "memory_efficient": batch_offload,
                },
            )
            _batch_generator = HunyuanTextTo3DGenerator(**_batch_kw)

    try:
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
                    emit_result(item_id, TOOL_TEXT3D, STATUS_SKIPPED, output=item["output"])
                    continue

                out_path.parent.mkdir(parents=True, exist_ok=True)

                item_steps = item.get("steps", base_steps)
                item_octree = item.get("octree_resolution", item.get("octree", base_octree))
                item_chunks = item.get("num_chunks", item.get("chunks", base_chunks))
                item_seed = item.get("seed", None)
                item_mc_level = item.get("mc_level", mc_level)

                from .bbox_tune import apply_bbox_tune, size_m_from_mapping
                from .omni_presets import (
                    bbox_tune_to_debug,
                    build_generate_debug,
                    merge_omni_controls,
                    mesh_stats_for_debug,
                    write_omni_fingerprint,
                )

                try:
                    _item_size_m = size_m_from_mapping(item.get("size_m"))
                    _omni = merge_omni_controls(
                        control_type=item.get("control_type"),
                        bbox=item.get("bbox"),
                        bbox_preset=item.get("bbox_preset"),
                        size=item.get("size"),
                        size_m=_item_size_m,
                        pose_file=item.get("pose_file"),
                        pose_preset=item.get("pose_preset"),
                        point_cloud=item.get("point_cloud"),
                        voxel_mesh=item.get("voxel_mesh"),
                        category=item.get("category"),
                    )
                except (KeyError, FileNotFoundError, ValueError) as exc:
                    raise click.ClickException(f"item {item_id}: {exc}") from exc

                # Soft: CLI --steps/--octree/--chunks explícitos ganham; item optimize
                # (steps no JSON) serve de base e ainda recebe escala size_m.
                # Opt-out: ``"bbox_tune": false`` no item do manifest.
                _bt = None
                if item.get("bbox_tune", True) is not False:
                    item_steps, item_octree, item_chunks, _bt = apply_bbox_tune(
                        steps=int(item_steps),
                        octree=int(item_octree),
                        chunks=int(item_chunks),
                        size_m=_item_size_m,
                        category=item.get("category"),
                        bbox_preset=item.get("bbox_preset"),
                        total_vram_gib=hwp.total_vram_gib if hwp is not None else None,
                        volume_decoder=volume_decoder,
                        tune_steps=not _user_set_steps,
                        tune_octree=not _user_set_octree,
                        tune_chunks=not _user_set_chunks,
                        group_offload=bool(allow_group_offload),
                        quality=item.get("quality", quality),
                    )
                    if _bt.applied:
                        _morph = f" morph≈{_bt.morph_close:.3f}m" if _bt.morph_close is not None else ""
                        _err.print(
                            f"[dim]scale-tune {item_id}: char={_bt.char_m:.2f}m "
                            f"voxel≈{_bt.voxel_m * 100:.1f}cm "
                            f"→ steps={item_steps} octree={item_octree} chunks={item_chunks}"
                            f"{_morph}[/dim]"
                        )

                _ctrl = {
                    "control_type": _omni["control_type"],
                    "bbox": _omni["bbox"],
                    "pose_file": _omni["pose_file"],
                    "point_cloud": _omni["point_cloud"],
                    "voxel_mesh": _omni["voxel_mesh"],
                }
                _ctrl = {k: v for k, v in _ctrl.items() if v is not None}

                t0 = time.time()
                from .ums_payload import build_generate_request

                _ums_item = build_generate_request(
                    from_image=str(img_path),
                    output=str(out_path),
                    steps=item_steps,
                    guidance=guidance,
                    octree_resolution=item_octree,
                    num_chunks=item_chunks,
                    seed=item_seed,
                    mc_level=item_mc_level,
                    bounds_mode=bounds_mode,
                    origin_mode=export_origin,
                    topology_fix=not no_topology_fix,
                    volume_decoder=volume_decoder,
                    mc_algo=mc_algo,
                    torch_compile=compile_models,
                    torch_compile_mode=compile_mode,
                    channels_last=channels_last,
                    allow_group_offload=allow_group_offload,
                    fp8_layerwise=fp8_layerwise,
                    sdnq_quantized_matmul=sdnq_matmul,
                    sage_attention=sage_attention,
                    offload=batch_offload,
                    verbose=batch_verbose,
                    category=item.get("category", category),
                    quality=item.get("quality", quality),
                    bbox_tune=False,  # já afinado acima
                    control_type=_omni.get("control_type"),
                    pose_preset=_omni.get("pose_preset"),
                    bbox_preset=_omni.get("bbox_preset"),
                    size_m=_item_size_m,
                    bbox=_omni.get("bbox"),
                    pose_file=str(_omni["pose_file"]) if _omni.get("pose_file") else None,
                    point_cloud=str(_omni["point_cloud"]) if _omni.get("point_cloud") else None,
                    voxel_mesh=str(_omni["voxel_mesh"]) if _omni.get("voxel_mesh") else None,
                    gpu_ids=parsed_gpu_ids,
                    sdnq_preset=sdnq_preset,
                    memory_efficient=bool(batch_offload or allow_group_offload)
                    or (sdnq_preset not in (None, "none", "")),
                    extra={
                        "bbox_tune_snapshot": bbox_tune_to_debug(_bt),
                        "seed_fingerprint": item.get("seed_fingerprint"),
                    },
                )

                if try_ums_delegation(
                    "text3d",
                    _ums_item,
                    t_start=t0,
                    noun="Mesh",
                    console=_err,
                    enabled=not no_ums,
                    priority=ums_priority or "batch",
                    stream=ums_stream,
                    timeout_sec=1800.0,
                ):
                    elapsed = time.time() - t0
                    emit_result(
                        item_id,
                        TOOL_TEXT3D,
                        STATUS_OK,
                        phase="shape",
                        output=item["output"],
                        seconds=round(elapsed, 1),
                    )
                    continue

                _ensure_inprocess_generator()
                assert _batch_generator is not None
                emit_progress(item_id, TOOL_TEXT3D, phase="loading_model", percent=0)
                mesh = _batch_generator.generate_from_image(
                    str(img_path),
                    num_inference_steps=item_steps,
                    guidance_scale=guidance,
                    octree_resolution=item_octree,
                    num_chunks=item_chunks,
                    hy_seed=item_seed,
                    mc_level=item_mc_level,
                    bounds_mode=bounds_mode,
                    keep_loaded=True,
                    step_callback=_make_step_callback(item_id, item_steps),
                    **_ctrl,
                )

                # Escala Omni (~2u) → metros reais antes do reparo/export:
                # morph/weld em metros e mundo do jogo recebem tamanho físico.
                if _item_size_m:
                    from .bbox_tune import scale_factor_to_meters

                    _sf = scale_factor_to_meters(float(max(mesh.extents)), _item_size_m)
                    if _sf is not None:
                        mesh.apply_scale(_sf)

                emit_progress(item_id, TOOL_TEXT3D, phase="mesh_repair", percent=0)
                if not no_topology_fix:
                    mesh = prepare_mesh_topology(mesh)
                emit_progress(item_id, TOOL_TEXT3D, phase="mesh_repair", percent=100)
                faces = len(mesh.faces)

                emit_progress(item_id, TOOL_TEXT3D, phase="export", percent=0)
                save_mesh(mesh, str(out_path), format="glb", origin_mode=export_origin)
                elapsed = time.time() - t0
                with contextlib.suppress(OSError):
                    from datetime import UTC, datetime

                    from text3d.bbox_tune import morph_close_voxels_for

                    _cat = item.get("category", category)
                    _dbg = build_generate_debug(
                        seconds=elapsed,
                        started_at=datetime.fromtimestamp(t0, tz=UTC).isoformat(),
                        finished_at=datetime.now(UTC).isoformat(),
                        bbox_tune=_bt,
                        morph_close_voxels=morph_close_voxels_for(_cat),
                        morph_source="bbox_tune" if _bt is not None else None,
                        decode={
                            "octree_resolution": item_octree,
                            "steps": item_steps,
                            "guidance": guidance,
                            "num_chunks": item_chunks,
                            "mc_level": item_mc_level,
                            "bounds_mode": bounds_mode,
                            "volume_decoder": volume_decoder,
                            "mc_algo": mc_algo,
                            "last_decode_stats": dict(getattr(_batch_generator, "last_decode_stats", None) or {}),
                        },
                        generation={
                            "quality": item.get("quality", quality),
                            "category": _cat,
                            "seed_rng": item_seed,
                            "seed_fingerprint": item.get("seed_fingerprint"),
                            "topology_fix_inline": not no_topology_fix,
                            "origin_mode": export_origin,
                            "from_image": True,
                        },
                        omni_resolved=_omni,
                        hardware={
                            "summary": hwp.summary() if hwp is not None else None,
                            "total_vram_gib": getattr(hwp, "total_vram_gib", None) if hwp else None,
                            "gpu_ids": parsed_gpu_ids,
                            "sdnq_preset": sdnq_preset,
                        },
                        accel={
                            "volume_decoder": volume_decoder,
                            "mc_algo": mc_algo,
                            "torch_compile": compile_models,
                            "channels_last": channels_last,
                            "allow_group_offload": allow_group_offload,
                        },
                        mesh=mesh_stats_for_debug(mesh, path=out_path),
                        extra={"path": "cli_generate_batch"},
                    )
                    write_omni_fingerprint(
                        out_path,
                        {
                            **_omni,
                            "bounds_mode": bounds_mode,
                            "mc_level": item_mc_level,
                            "size_m": _item_size_m,
                            "seed": item.get("seed_fingerprint"),
                            "octree_resolution": item_octree,
                        },
                        debug=_dbg,
                    )
                emit_progress(item_id, TOOL_TEXT3D, phase="export", percent=100)

                emit_result(
                    item_id,
                    TOOL_TEXT3D,
                    STATUS_OK,
                    phase="shape",
                    output=item["output"],
                    faces=faces,
                    seconds=round(elapsed, 1),
                )

            except Exception as exc:
                emit_result(
                    item_id,
                    TOOL_TEXT3D,
                    STATUS_ERROR,
                    error=f"{type(exc).__name__}: {exc}",
                )

    finally:
        _batch_cleanup()
        signal.signal(signal.SIGTERM, old_sigterm)
        signal.signal(signal.SIGINT, old_sigint)


@cli.command("bench-decode")
@click.option(
    "--image",
    "image_path",
    type=click.Path(exists=True, dir_okay=False),
    required=True,
    help="Imagem de referência (mesma para toda a matriz — casos comparáveis).",
)
@click.option("--octrees", default="256,320,384,448,512", show_default=True, help="CSV de octree resolutions.")
@click.option(
    "--decoders",
    default="vanilla,flashvdm",
    show_default=True,
    help="CSV de volume decoders (vanilla|hierarchical|flashvdm).",
)
@click.option("--mc-levels", default="auto", show_default=True, help="CSV de mc_levels (floats ou 'auto').")
@click.option("--bounds-modes", default="cube,auto", show_default=True, help="CSV de bounds modes (cube|auto).")
@click.option("--bbox", "bbox_str", default=None, help="Bbox Omni L,H,W para os casos (opcional).")
@click.option("--bbox-preset", "bbox_preset", default=None, help="Preset de bbox Omni (ex.: building, sword).")
@click.option("--steps", default=30, show_default=True, type=int, help="Steps Hunyuan (fixos na matriz).")
@click.option("--guidance", default=5.0, show_default=True, type=float)
@click.option("--seed", default=1234, show_default=True, type=int)
@click.option("--num-chunks", default=None, type=int, help="Fixar chunks (default: auto dinâmico).")
@click.option(
    "--sdnq-preset",
    default=None,
    type=click.Choice(["sdnq-uint8", "sdnq-int8", "sdnq-int4", "sdnq-fp8", "none"]),
    help="Quantização DiT no bench (default: hw-auto).",
)
@click.option("-o", "--output", "out_dir", type=click.Path(file_okay=False), default="bench_decode_out")
def bench_decode(
    image_path,
    octrees,
    decoders,
    mc_levels,
    bounds_modes,
    bbox_str,
    bbox_preset,
    steps,
    guidance,
    seed,
    num_chunks,
    sdnq_preset,
    out_dir,
):
    """Bench de calibração do decode: octree x decoder x mc_level x bounds.

    Mede lixo interno, boundary edges, VRAM e tempo por caso; o relatório
    (`bench_report.json`) recomenda o tecto informativo de octree.
    """
    from .bench import build_matrix, parse_mc_levels, run_bench_decode
    from .hardware import detect_hardware_profile, hw_auto_enabled
    from .omni_presets import parse_bbox_csv, resolve_bbox_preset

    try:
        octree_list = [int(x) for x in str(octrees).split(",") if x.strip()]
        decoder_list = [x.strip() for x in str(decoders).split(",") if x.strip()]
        mc_list = parse_mc_levels(mc_levels)
        bounds_list = [x.strip() for x in str(bounds_modes).split(",") if x.strip()]
        bbox_vals = None
        if bbox_str:
            bbox_vals = parse_bbox_csv(bbox_str)
        elif bbox_preset:
            bbox_vals = resolve_bbox_preset(bbox_preset)
        cases = build_matrix(octree_list, decoder_list, mc_list, bounds_list)
    except (ValueError, KeyError) as exc:
        raise click.ClickException(str(exc)) from exc

    if sdnq_preset is None and hw_auto_enabled():
        hwp = detect_hardware_profile()
        sdnq_preset = hwp.sdnq_preset or "none"

    console.print(
        Panel(
            f"[bold]{len(cases)}[/bold] casos | imagem=[cyan]{image_path}[/cyan] | seed={seed} | "
            f"bbox={'preset:' + bbox_preset if bbox_preset else (bbox_vals or 'none')}",
            title="[bold green]Bench decode",
            border_style="green",
        )
    )
    report = run_bench_decode(
        image_path,
        cases,
        out_dir,
        steps=steps,
        guidance=guidance,
        seed=seed,
        num_chunks=num_chunks,
        sdnq_preset="" if sdnq_preset in (None, "none") else sdnq_preset,
        control_type="bbox" if bbox_vals else None,
        bbox=bbox_vals,
    )

    table = Table(title="[bold blue]Bench decode", box=box.SIMPLE)
    for col in ("caso", "s", "VRAM GiB", "faces", "boundary", "internos", "vol int"):
        table.add_column(col)
    for row in report["results"]:
        m = row.get("metrics") or {}
        table.add_row(
            row["case_id"],
            str(row["seconds"]),
            str(row["peak_vram_gib"]),
            str(m.get("faces", "—")),
            str(m.get("boundary_edges", "—")),
            str(m.get("internal_components", "—")),
            f"{m.get('internal_volume_ratio', 0.0):.4f}" if m else "—",
        )
    console.print(table)
    summary = report["summary"]
    console.print(
        Panel(
            f"Tecto de octree recomendado: [bold]{summary['recommended_latent_ceiling']}[/bold]\n"
            f"Pior lixo interno: {summary['worst_internal_case']}\n"
            f"Pior boundary: {summary['worst_boundary_case']}",
            title="Resumo",
            border_style="cyan",
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
        "Hunyuan3D-Omni",
        "Image-to-3D + controlos (bbox/pose/point/voxel); SDNQ INT4 em GPUs pequenas",
        "tencent/Hunyuan3D-Omni; hy3dshape vendorizado; licença Tencent Hunyuan Community",
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


@cli.command("serve")
@click.option(
    "--ums-worker",
    is_flag=True,
    help=(
        "Modo worker subprocesso do UMS: lê comandos JSONL do stdin (load / "
        "generate / unload / shutdown) e emite eventos no stdout. Usado pelo "
        "SubprocessWorkerPool do ModelServer — text3d corre no seu próprio "
        "venv e o supervisor (ModelServer/.venv) coordena via JSONL."
    ),
)
def serve(ums_worker: bool) -> None:
    """Modo worker subprocesso do UMS (subprocess-per-backend).

    Sem ``--ums-worker`` não faz nada (futuro: modo server legacy).
    Com ``--ums-worker`` arranca o loop canónico
    :func:`gamedev_shared.worker_serve.run_worker_loop` com o adapter text3d
    local (:mod:`text3d.worker_serve_adapter`).
    """
    if not ums_worker:
        console.print("[yellow]text3d serve sem --ums-worker não faz nada.[/yellow]")
        console.print("[dim]O UMS arranca este subcomando internamente.[/dim]")
        return

    from gamedev_shared.worker_serve import run_worker_loop
    from text3d.worker_serve_adapter import Adapter

    run_worker_loop(Adapter, backend_name="text3d")


def main():
    try:
        cli()
    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelado.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    main()
