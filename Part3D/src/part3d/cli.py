"""Part3D CLI — decomposição de meshes 3D em partes semânticas."""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from gamedev_shared.quality import VALID_QUALITIES

from . import defaults as _d
from .cli_rich import click


@click.group()
@click.version_option(package_name="part3d")
def main() -> None:
    """Part3D — Decomposição semântica de meshes 3D via Hunyuan3D-Part."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(line_buffering=True)


@main.command()
@click.argument("mesh_path", type=click.Path(exists=True))
@click.option("-o", "--output", "output_path", type=click.Path(), default=None, help="Caminho de saída (.glb)")
@click.option("--output-segmented", type=click.Path(), default=None, help="Exportar mesh segmentada (cores por parte)")
@click.option(
    "--octree-resolution",
    type=int,
    default=None,
    help="Resolução do octree (default: quality tier / autotune)",
)
@click.option(
    "--steps",
    type=int,
    default=None,
    help="Passos DiT (default: quality tier / autotune)",
)
@click.option(
    "--num-chunks",
    type=int,
    default=None,
    help="Chunks marching cubes (default: quality tier / autotune)",
)
@click.option("--seed", type=int, default=None, show_default=True, help="Seed reprodutível (None = aleatório)")
@click.option(
    "--quality",
    type=click.Choice(list(VALID_QUALITIES)),
    default="medium",
    show_default=True,
    help="Quality tier — controls DiT steps, octree resolution, chunk count.",
)
@click.option(
    "--category",
    type=str,
    default=None,
    help="Asset category for category-specific overrides (e.g. humanoid, weapon, prop).",
)
@click.option(
    "--no-auto-tune",
    is_flag=True,
    help="Desactivar ajuste automático (usa valores fixos de defaults.py)",
)
@click.option("--no-cpu-offload", is_flag=True, help="Desactivar CPU offloading (requer >10 GB VRAM)")
@click.option("--device", type=str, default=None, help="Forçar device (cuda/cpu)")
@click.option("--segment-only", is_flag=True, help="Apenas segmentar, sem gerar partes")
@click.option("-v", "--verbose", is_flag=True)
@click.option(
    "--quantization",
    "-q",
    type=click.Choice(["auto", "none", "int8", "int4"], case_sensitive=False),
    default=_d.DEFAULT_QUANTIZATION_MODE,
    show_default=True,
    help="Quantização DiT: auto (SDNQ em GPUs <8 GB), int8/int4 (SDNQ explícito), none.",
)
@click.option(
    "--no-quantize-dit",
    is_flag=True,
    help="Desactivar quantização do DiT mesmo quando disponível.",
)
@click.option(
    "--torch-compile/--no-torch-compile",
    "--compile/--no-compile",
    "torch_compile",
    default=_d.DEFAULT_TORCH_COMPILE,
    show_default=True,
    help="Compilar DiT/VAE/Conditioner com torch.compile.",
)
@click.option(
    "--compile-mode",
    "torch_compile_mode",
    type=click.Choice(["default", "reduce-overhead", "max-autotune"]),
    default=_d.DEFAULT_TORCH_COMPILE_MODE,
    show_default=True,
    help="Modo Inductor. reduce-overhead/max-autotune = CUDA graphs (só sem CPU offload).",
)
@click.option(
    "--no-attention-slicing",
    is_flag=True,
    help="Desactivar attention slicing.",
)
@click.option(
    "--profile",
    is_flag=True,
    help="Medir tempos, CPU, RAM e VRAM.",
)
@click.option(
    "--gpu-ids",
    type=str,
    default=None,
    help="IDs de GPU para multi-GPU (ex: '0,1'). DiT na primeira, auxiliares na segunda.",
)
@click.option(
    "--hw-auto/--no-hw-auto",
    default=_d.DEFAULT_HW_AUTO,
    show_default=True,
    help="Hardware auto-detection: memory-efficient (SDNQ + offload) em GPUs <8 GB. "
    "Flags explícitas ganham. Env kill-switch: PART3D_HW_AUTO=0",
)
@click.option("--allow-shared-gpu", is_flag=True, help="Permite GPU partilhada com outros processos.")
@click.option(
    "--gpu-kill-others/--no-gpu-kill-others",
    default=False,
    help="Terminar processos GPU alheios antes do load. Default: off.",
)
@click.option(
    "--volume-decoder",
    type=click.Choice(["auto", "hierarchical", "flashvdm", "vanilla", "fast"], case_sensitive=False),
    default=None,
    help="ShapeVAE decode: auto (hierarchical em high+; flashvdm se VRAM baixa), "
    "hierarchical (~lossless sparse), flashvdm (rápido), vanilla (dense), fast (legacy).",
)
@click.option(
    "--mc-algo",
    type=click.Choice(["mc", "dmc"], case_sensitive=False),
    default=None,
    help="Surface extract: mc (skimage) ou dmc (DiffDMC/diso se instalado).",
)
@click.option(
    "--channels-last/--no-channels-last",
    default=_d.DEFAULT_CHANNELS_LAST,
    show_default=True,
    help="memory_format=channels_last em DiT/VAE/Conditioner.",
)
@click.option("--point-num", type=int, default=None, help="P3-SAM point samples (quality/autotune).")
@click.option("--prompt-num", type=int, default=None, help="P3-SAM prompt samples (quality/autotune).")
@click.option(
    "--kernel-modern",
    is_flag=True,
    help="Preset bleeding-edge: volume-decoder=auto, channels-last, torch.compile, mc=dmc se diso.",
)
@click.option(
    "--export-face-parts/--no-export-face-parts",
    default=True,
    show_default=True,
    help="Após segmentação, exportar GLB com submeshes por face_id (topologia original, sem X-Part).",
)
@click.option(
    "--postprocess/--no-postprocess",
    default=_d.DEFAULT_POSTPROCESS,
    show_default=True,
    help="P3-SAM do_post_process: funde partes pequenas em vizinhos. "
    "Desligar (--no-postprocess) preserva porta/moldura separados.",
)
@click.option(
    "--threshold",
    type=float,
    default=None,
    help=f"Threshold do postprocess (default {_d.DEFAULT_POSTPROCESS_THRESHOLD}). Mais alto = menos merge (ex. 0.99).",
)
@click.option(
    "--fine-parts",
    is_flag=True,
    help="Preset anti-fuse: --no-postprocess + point/prompt no teto VRAM-safe.",
)
@click.option(
    "--refine-labels/--no-refine-labels",
    default=_d.DEFAULT_REFINE_LABELS,
    show_default=True,
    help="Refinamento crease-aware pós-P3-SAM: fronteiras encaixam em arestas vivas, ilhas absorvidas.",
)
@click.option(
    "--merge-bbox-iou",
    type=float,
    default=None,
    help=f"IoU de bbox para fundir clusters P3-SAM (default {_d.SPACE_BBOX_MERGE_IOU}; "
    "upstream usa 0.5, que agrega porta+vizinhança; mais alto = menos fusão).",
)
@click.option(
    "--cap-part-holes/--no-cap-part-holes",
    default=_d.DEFAULT_CAP_PART_HOLES,
    show_default=True,
    help="Fechar buracos de fronteira nas face-parts (bpy fill_holes) — remover uma parte não deixa geometria aberta.",
)
@click.pass_context
def decompose(
    ctx: Any,
    mesh_path: str,
    output_path: str | None,
    output_segmented: str | None,
    octree_resolution: int | None,
    steps: int | None,
    num_chunks: int | None,
    seed: int | None,
    quality: str,
    category: str | None,
    no_auto_tune: bool,
    no_cpu_offload: bool,
    device: str | None,
    segment_only: bool,
    verbose: bool,
    quantization: str,
    no_quantize_dit: bool,
    torch_compile: bool,
    torch_compile_mode: str,
    no_attention_slicing: bool,
    profile: bool,
    gpu_ids: str | None,
    hw_auto: bool,
    allow_shared_gpu: bool,
    gpu_kill_others: bool,
    volume_decoder: str | None,
    mc_algo: str | None,
    channels_last: bool,
    point_num: int | None,
    prompt_num: int | None,
    kernel_modern: bool,
    export_face_parts: bool,
    postprocess: bool,
    threshold: float | None,
    fine_parts: bool,
    refine_labels: bool,
    merge_bbox_iou: float | None,
    cap_part_holes: bool,
) -> None:
    """Decompõe uma mesh 3D em partes semânticas.

    Usa P3-SAM para segmentação e X-Part para geração das partes.
    Optimizado para ~6 GB VRAM com CPU offloading sequencial + SDNQ.
    """
    from click.core import ParameterSource

    from gamedev_shared.cli_helpers import (
        apply_quality_defaults,
        prepare_gpu_exclusive,
        try_ums_delegation,
    )
    from gamedev_shared.env import ensure_pytorch_cuda_alloc_conf
    from gamedev_shared.quantization import format_quantization_info, get_quantization_config
    from gamedev_shared.seed_utils import resolve_effective_seed

    from .hardware import detect_hardware_profile, hw_auto_enabled
    from .utils.sdnq_resolve import resolve_sdnq_preset

    ctx = click.get_current_context()
    _src = ParameterSource
    seed = resolve_effective_seed(seed)
    # QualityEngine soft fill (YAML part3d: steps / octree / chunks / kernel).
    qfill = apply_quality_defaults(
        ctx,
        "part3d",
        quality,
        {
            "steps": "steps",
            "octree_resolution": "octree",
            "num_chunks": "chunks",
            "volume_decoder": "volume_decoder",
            "mc_algo": "mc_algo",
            "point_num": "point_num",
            "prompt_num": "prompt_num",
            "threshold": "postprocess_threshold",
        },
        category=category,
    )
    if "steps" in qfill:
        steps = qfill["steps"]
    if "octree" in qfill:
        octree_resolution = qfill["octree"]
    if "chunks" in qfill:
        num_chunks = qfill["chunks"]
    if volume_decoder is None and "volume_decoder" in qfill:
        volume_decoder = str(qfill["volume_decoder"])
    if mc_algo is None and "mc_algo" in qfill:
        mc_algo = str(qfill["mc_algo"])
    if point_num is None and "point_num" in qfill:
        point_num = int(qfill["point_num"])
    if prompt_num is None and "prompt_num" in qfill:
        prompt_num = int(qfill["prompt_num"])
    if threshold is None and "postprocess_threshold" in qfill:
        threshold = float(qfill["postprocess_threshold"])

    if fine_parts:
        postprocess = False
        if point_num is None:
            point_num = 56000
        if prompt_num is None:
            prompt_num = 128

    if threshold is None:
        threshold = _d.DEFAULT_POSTPROCESS_THRESHOLD

    if kernel_modern:
        from .utils.kernel_accel import resolve_mc_algo

        if volume_decoder is None:
            volume_decoder = "auto"
        if ctx.get_parameter_source("torch_compile") in (_src.DEFAULT,):
            torch_compile = True
        if ctx.get_parameter_source("channels_last") in (_src.DEFAULT,):
            channels_last = True
        if mc_algo is None:
            mc_algo = resolve_mc_algo("dmc", device="cuda")

    if volume_decoder is None:
        volume_decoder = _d.DEFAULT_VOLUME_DECODER
    if mc_algo is None:
        mc_algo = _d.DEFAULT_MC_ALGO

    # Hardware auto (soft): só preenche knobs que o user não explicitou.
    mem_eff = False
    sdnq_preset: str | None = None
    hwp = None
    user_set_quant = ctx.get_parameter_source("quantization") not in (_src.DEFAULT,)
    user_set_no_q = ctx.get_parameter_source("no_quantize_dit") not in (_src.DEFAULT,)
    user_set_no_offload = ctx.get_parameter_source("no_cpu_offload") not in (_src.DEFAULT,)
    user_set_no_attn = ctx.get_parameter_source("no_attention_slicing") not in (_src.DEFAULT,)
    user_set_gpu_ids = ctx.get_parameter_source("gpu_ids") not in (_src.DEFAULT,)

    if hw_auto and hw_auto_enabled():
        hwp = detect_hardware_profile()
        mem_eff = hwp.memory_efficient
        if not user_set_no_offload and hwp.cpu_offload:
            no_cpu_offload = False
        if mem_eff:
            if not user_set_quant:
                quantization = "auto"
            if not user_set_no_q:
                no_quantize_dit = False
            if not user_set_no_attn:
                no_attention_slicing = False
            sdnq_preset = hwp.sdnq_preset
        if not user_set_gpu_ids and hwp.gpu_ids and gpu_ids is None:
            gpu_ids = ",".join(str(i) for i in hwp.gpu_ids)

    # Soft VRAM guard: hierarchical + octree alto em ≤8 GB → flashvdm + clamp.
    if mem_eff:
        from .utils.kernel_accel import resolve_volume_decoder

        resolved_vd = resolve_volume_decoder(volume_decoder, quality=quality, memory_efficient=True)
        if volume_decoder in ("hierarchical", "auto") and resolved_vd == "flashvdm":
            volume_decoder = "flashvdm"
        if octree_resolution is not None and octree_resolution > 320:
            octree_resolution = 320
        # P3-SAM OOM em 6GB com point_num≥80k / prompt≥200 (highest YAML).
        if point_num is not None and point_num > 56000:
            point_num = 56000
        if prompt_num is not None and prompt_num > 128:
            prompt_num = 128

    ensure_pytorch_cuda_alloc_conf()

    quant_config = get_quantization_config(quantization)
    quant_str = format_quantization_info(quant_config)
    effective_preset = sdnq_preset or resolve_sdnq_preset(
        quantization,
        memory_efficient=mem_eff,
        quantize_dit=not no_quantize_dit,
    )

    from rich.console import Console
    from rich.panel import Panel

    console = Console()

    mesh_name = Path(mesh_path).stem
    if output_path is None:
        output_path = str(Path(mesh_path).parent / f"{mesh_name}_parts.glb")
    if output_segmented is None:
        output_segmented = str(Path(mesh_path).parent / f"{mesh_name}_segmented.glb")

    mode = "fixo (defaults)" if no_auto_tune else "autotune (geometria + VRAM)"
    oc_disp = octree_resolution if octree_resolution is not None else "auto"
    st_disp = steps if steps is not None else "auto"

    opt_parts = [f"quantização={quant_str}"]
    if effective_preset:
        opt_parts.append(f"sdnq={effective_preset}")
    if torch_compile:
        opt_parts.append("torch.compile")
    if not no_attention_slicing:
        opt_parts.append("attention slicing")
    if channels_last:
        opt_parts.append("channels_last")
    opt_parts.append(f"volume={volume_decoder}")
    opt_parts.append(f"mc={mc_algo}")
    if kernel_modern:
        opt_parts.append("kernel-modern")
    opt_line = ", ".join(opt_parts)

    console.print(
        Panel.fit(
            f"[bold]Part3D[/] — Decomposição de [cyan]{Path(mesh_path).name}[/]\n"
            f"Saída: [green]{output_path}[/]\n"
            f"Quality: {quality} | Modo: {mode}\n"
            f"Octree: {oc_disp} | Steps: {st_disp} | Seed: {seed}\n"
            f"CPU Offload: {'[green]SIM[/]' if not no_cpu_offload else '[red]NÃO[/]'}\n"
            f"Optimizações: {opt_line}",
            title="Hunyuan3D-Part",
        )
    )

    import torch

    if torch.cuda.is_available():
        gpu_info = torch.cuda.get_device_properties(0)
        vram_gb = gpu_info.total_memory / (1024**3)
        console.print(f"GPU: {gpu_info.name} ({vram_gb:.1f} GB VRAM)")
        if hwp is not None:
            console.print(f"[dim]Hardware (auto): {hwp.summary()}[/]")
    else:
        console.print("[yellow]Sem CUDA — execução em CPU (muito lento)[/]")

    import numpy as np

    from gamedev_shared.bpy_mesh import (
        load_mesh_as_trimesh,
        save_colored_mesh,
        save_empty_glb,
        save_scene_geometries,
    )
    from gamedev_shared.cli_helpers import make_profiler

    from .pipeline import Part3DPipeline

    parsed_gpu_ids = [int(x.strip()) for x in gpu_ids.split(",")] if gpu_ids else None

    t_start = time.time()

    _ums_request: dict[str, Any] = {
        "mesh_path": str(mesh_path),
        "output": str(output_path),
        "output_segmented": str(output_segmented),
        "seed": seed,
        "segment_only": segment_only,
    }
    if octree_resolution is not None:
        _ums_request["octree_resolution"] = octree_resolution
    if steps is not None:
        _ums_request["num_inference_steps"] = steps
    if num_chunks is not None:
        _ums_request["num_chunks"] = num_chunks

    if try_ums_delegation("part3d", _ums_request, t_start=t_start, noun="Partes", console=console):
        return

    if torch.cuda.is_available():
        prepare_gpu_exclusive(
            needed_mib=5000,
            allow_shared=allow_shared_gpu,
            kill_others=gpu_kill_others,
            allow_shared_env="PART3D_ALLOW_SHARED_GPU",
            kill_others_env="PART3D_GPU_KILL_OTHERS",
            console=console,
        )

    prof, _prof_log = make_profiler("part3d", cli_flag=profile, model_id=_d.DEFAULT_HF_REPO)

    def _gen_kwargs() -> dict[str, Any]:
        if no_auto_tune:
            out: dict[str, Any] = {
                "octree_resolution": octree_resolution
                if octree_resolution is not None
                else _d.DEFAULT_OCTREE_RESOLUTION,
                "num_inference_steps": steps if steps is not None else _d.DEFAULT_NUM_INFERENCE_STEPS,
                "num_chunks": num_chunks if num_chunks is not None else _d.DEFAULT_NUM_CHUNKS,
                "mc_algo": mc_algo,
            }
        else:
            out = {}
            if octree_resolution is not None:
                out["octree_resolution"] = octree_resolution
            if steps is not None:
                out["num_inference_steps"] = steps
            if num_chunks is not None:
                out["num_chunks"] = num_chunks
            out["mc_algo"] = mc_algo
        if point_num is not None:
            out["point_num"] = point_num
        if prompt_num is not None:
            out["prompt_num"] = prompt_num
        out["postprocess"] = postprocess
        out["threshold"] = threshold
        return out

    with (
        prof,
        Part3DPipeline(
            device=device,
            cpu_offload=not no_cpu_offload,
            verbose=verbose,
            autotune=not no_auto_tune,
            quantization_mode=quantization,
            quantize_dit=not no_quantize_dit,
            enable_torch_compile=torch_compile,
            torch_compile_mode=torch_compile_mode,
            enable_attention_slicing=not no_attention_slicing,
            memory_efficient=mem_eff,
            gpu_ids=parsed_gpu_ids,
            sdnq_preset=effective_preset,
            volume_decoder=volume_decoder,
            mc_algo=mc_algo,
            channels_last=channels_last,
            quality=quality,
            refine_labels=refine_labels,
            bbox_merge_iou=_d.SPACE_BBOX_MERGE_IOU if merge_bbox_iou is None else merge_bbox_iou,
        ) as pipe,
    ):
        from .utils.face_split import face_part_stats, split_mesh_by_face_ids

        def _export_segmented_and_face_parts(face_ids: Any, clean_mesh: Any) -> None:
            color_map = {}
            for uid in np.unique(face_ids):
                if uid < 0:
                    continue
                color_map[uid] = np.random.randint(0, 255, size=3)
            face_colors = np.array([color_map.get(fid, [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            save_colored_mesh(clean_mesh, face_colors, output_segmented)
            console.print(f"[green]Mesh segmentada gravada em:[/] {output_segmented}")
            face_ids_out = str(Path(output_segmented).with_name(Path(output_segmented).stem + "_face_ids.npy"))
            np.save(face_ids_out, np.asarray(face_ids))
            console.print(f"[dim]face_ids: {face_ids_out}[/]")
            if not export_face_parts:
                return
            face_out = str(Path(output_path).with_name(Path(output_path).stem + "_face_parts.glb"))
            try:
                face_scene = split_mesh_by_face_ids(clean_mesh, face_ids, cap_holes=cap_part_holes)
                if face_scene.geometry:
                    save_scene_geometries(face_scene, face_out)
                    stats = face_part_stats(face_scene)
                    console.print(
                        f"[green]Face-parts (topologia original):[/] {face_out} ({len(face_scene.geometry)} meshes)"
                    )
                    for row in stats[:8]:
                        console.print(
                            f"  [dim]{row['name']}: {row['faces']} faces, "
                            f"thin={row['thin_extent']:.3f} axis={row['thin_axis']} "
                            f"aspect={row['aspect_max_min']:.1f}[/]"
                        )
                else:
                    console.print("[yellow]Face-parts: nenhuma região ≥ min_faces[/]")
            except Exception as e:
                console.print(f"[yellow]Face-parts falhou: {e}[/]")

        if segment_only:
            mesh = load_mesh_as_trimesh(mesh_path)
            _aabb, face_ids, clean_mesh = pipe.segment(
                mesh,
                seed=seed,
                point_num=point_num,
                prompt_num=prompt_num,
                postprocess=postprocess,
                threshold=threshold,
            )
            _export_segmented_and_face_parts(face_ids, clean_mesh)

        else:
            parts_scene, face_ids, clean_mesh = pipe(mesh_path, seed=seed, **_gen_kwargs())

            if not parts_scene.geometry:
                console.print("[yellow]⚠ Aviso: Nenhuma parte detectada. A usar modo segment_only como fallback.[/]")
                _export_segmented_and_face_parts(face_ids, clean_mesh)
                save_empty_glb(output_path)
                console.print(f"[dim]Partes (placeholder vazio):[/] {output_path}")
            else:
                save_scene_geometries(parts_scene, output_path)
                console.print(f"[green]Partes gravadas em:[/] {output_path}")
                _export_segmented_and_face_parts(face_ids, clean_mesh)

    elapsed = time.time() - t_start
    console.print(f"\n[bold green]Concluído em {elapsed:.1f}s[/]")


if __name__ == "__main__":
    main()
