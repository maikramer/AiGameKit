"""Part3D CLI — decomposição de meshes 3D em partes semânticas."""

from __future__ import annotations

import sys
import time
from pathlib import Path

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
    help="Resolução do octree (default: autotune por geometria/VRAM)",
)
@click.option(
    "--steps",
    type=int,
    default=None,
    help="Passos DiT (default: autotune)",
)
@click.option(
    "--num-chunks",
    type=int,
    default=None,
    help="Chunks marching cubes (default: autotune)",
)
@click.option("--seed", type=int, default=42, show_default=True)
@click.option(
    "--no-auto-tune",
    is_flag=True,
    help="Desactivar ajuste automático (usa valores fixos de defaults.py)",
)
@click.option("--no-cpu-offload", is_flag=True, help="Desactivar CPU offloading (requer >10 GB VRAM)")
@click.option("--device", type=str, default=None, help="Forçar device (cuda/cpu)")
@click.option("--segment-only", is_flag=True, help="Apenas segmentar, sem gerar partes")
@click.option("-v", "--verbose", is_flag=True)
# --- Otimizações de VRAM ---
@click.option(
    "--quantization",
    "-q",
    type=click.Choice(["auto", "none", "int8", "int4"], case_sensitive=False),
    default=_d.DEFAULT_QUANTIZATION_MODE,
    show_default=True,
    help="Modo de quantização: auto (detecta VRAM), int8/int4 (bitsandbytes).",
)
@click.option(
    "--no-quantize-dit",
    is_flag=True,
    help="Desactivar quantização do DiT mesmo quando disponível.",
)
@click.option(
    "--torch-compile/--no-torch-compile",
    default=_d.DEFAULT_TORCH_COMPILE,
    show_default=True,
    help="Compilar DiT com torch.compile.",
)
@click.option(
    "--no-attention-slicing",
    is_flag=True,
    help="Desactivar attention slicing.",
)
@click.option(
    "--low-vram-mode",
    is_flag=True,
    help="Activar modo baixa VRAM: quantização automática + CPU offload.",
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
    help="IDs de GPU para multi-GPU (ex: '0,1'). Só afecta o DiT.",
)
def decompose(
    mesh_path: str,
    output_path: str | None,
    output_segmented: str | None,
    octree_resolution: int | None,
    steps: int | None,
    num_chunks: int | None,
    seed: int,
    no_auto_tune: bool,
    no_cpu_offload: bool,
    device: str | None,
    segment_only: bool,
    verbose: bool,
    quantization: str,
    no_quantize_dit: bool,
    torch_compile: bool,
    no_attention_slicing: bool,
    low_vram_mode: bool,
    profile: bool,
    gpu_ids: str | None,
) -> None:
    """Decompõe uma mesh 3D em partes semânticas.

    Usa P3-SAM para segmentação e X-Part para geração das partes.
    Optimizado para ~6 GB VRAM com CPU offloading sequencial.
    Suporta quantização 4-bit/8-bit e torch.compile para aceleração.
    """
    from gamedev_shared.env import ensure_pytorch_cuda_alloc_conf
    from gamedev_shared.quantization import (
        format_quantization_info,
        get_quantization_config,
    )

    # --low-vram-mode overrides: activar quantização + CPU offload
    if low_vram_mode:
        quantization = "auto"
        no_quantize_dit = False
        no_cpu_offload = False
        no_attention_slicing = False

    ensure_pytorch_cuda_alloc_conf()

    ensure_pytorch_cuda_alloc_conf()

    # Obter configuração de quantização
    quant_config = get_quantization_config(quantization)
    quant_str = format_quantization_info(quant_config)

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

    # Construir linha de otimizações
    opt_parts = [f"quantização={quant_str}"]
    if torch_compile:
        opt_parts.append("torch.compile")
    if not no_attention_slicing:
        opt_parts.append("attention slicing")
    opt_line = ", ".join(opt_parts)

    console.print(
        Panel.fit(
            f"[bold]Part3D[/] — Decomposição de [cyan]{Path(mesh_path).name}[/]\n"
            f"Saída: [green]{output_path}[/]\n"
            f"Modo: {mode}\n"
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
        if vram_gb < 6.0 and not no_cpu_offload:
            console.print("[yellow]Aviso: VRAM < 6 GB — resultado pode ser instável[/]")
    else:
        console.print("[yellow]Sem CUDA — execução em CPU (muito lento)[/]")

    import numpy as np
    import trimesh

    from gamedev_shared.profiler import ProfilerSession
    from gamedev_shared.profiler.env import env_profile_log_path

    from .pipeline import Part3DPipeline

    parsed_gpu_ids = [int(x.strip()) for x in gpu_ids.split(",")] if gpu_ids else None

    t_start = time.time()
    log_p = env_profile_log_path()
    prof_log = Path(log_p) if log_p else None

    def _gen_kwargs() -> dict:
        if no_auto_tune:
            return {
                "octree_resolution": octree_resolution
                if octree_resolution is not None
                else _d.DEFAULT_OCTREE_RESOLUTION,
                "num_inference_steps": steps if steps is not None else _d.DEFAULT_NUM_INFERENCE_STEPS,
                "num_chunks": num_chunks if num_chunks is not None else _d.DEFAULT_NUM_CHUNKS,
            }
        out: dict = {}
        if octree_resolution is not None:
            out["octree_resolution"] = octree_resolution
        if steps is not None:
            out["num_inference_steps"] = steps
        if num_chunks is not None:
            out["num_chunks"] = num_chunks
        return out

    with (
        ProfilerSession("part3d", log_path=prof_log, cli_profile=profile),
        Part3DPipeline(
            device=device,
            cpu_offload=not no_cpu_offload,
            verbose=verbose,
            autotune=not no_auto_tune,
            quantization_mode=quantization,
            quantize_dit=not no_quantize_dit,
            enable_torch_compile=torch_compile,
            enable_attention_slicing=not no_attention_slicing,
            low_vram=low_vram_mode,
            gpu_ids=parsed_gpu_ids,
        ) as pipe,
    ):
        if segment_only:
            mesh = trimesh.load(mesh_path, force="mesh", process=False)
            _aabb, face_ids, clean_mesh = pipe.segment(mesh, seed=seed)

            color_map = {}
            for uid in np.unique(face_ids):
                if uid < 0:
                    continue
                color_map[uid] = np.random.randint(0, 255, size=3)

            face_colors = np.array([color_map.get(fid, [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
            clean_mesh.visual.face_colors = face_colors
            clean_mesh.export(output_segmented)
            console.print(f"[green]Mesh segmentada gravada em:[/] {output_segmented}")

        else:
            parts_scene, face_ids, clean_mesh = pipe(mesh_path, seed=seed, **_gen_kwargs())

            # Verificar se a cena tem conteúdo antes de exportar
            if not parts_scene.geometry:
                console.print("[yellow]⚠ Aviso: Nenhuma parte detectada. A usar modo segment_only como fallback.[/]")
                # Fallback: gerar apenas mesh segmentada sem GLB multi-parte
                color_map = {}
                for uid in np.unique(face_ids):
                    if uid < 0:
                        continue
                    color_map[uid] = np.random.randint(0, 255, size=3)

                face_colors = np.array([color_map.get(fid, [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
                clean_mesh.visual.face_colors = face_colors
                clean_mesh.export(output_segmented)
                console.print(f"[green]Mesh segmentada gravada em:[/] {output_segmented}")

                # Criar GLB vazio de partes (placeholder) para não quebrar pipeline
                placeholder = trimesh.Scene()
                placeholder.export(output_path)
                console.print(f"[dim]Partes (placeholder vazio):[/] {output_path}")
            else:
                parts_scene.export(output_path)
                console.print(f"[green]Partes gravadas em:[/] {output_path}")

                # Gravar segmentação também
                color_map = {}
                for uid in np.unique(face_ids):
                    if uid < 0:
                        continue
                    color_map[uid] = np.random.randint(0, 255, size=3)

                face_colors = np.array([color_map.get(fid, [0, 0, 0]) for fid in face_ids], dtype=np.uint8)
                clean_mesh.visual.face_colors = face_colors
                clean_mesh.export(output_segmented)
                console.print(f"[green]Mesh segmentada gravada em:[/] {output_segmented}")

    elapsed = time.time() - t_start
    console.print(f"\n[bold green]Concluído em {elapsed:.1f}s[/]")


if __name__ == "__main__":
    main()
