"""
Otimizador de pipeline completo GameDev - integra Part3D + Paint3D.

Itera entre as ferramentas para encontrar a melhor combinação de
configurações SDNQ que funcionam em conjunto sem OOM.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from gamedev_lab.paths import gamedev_repo_root
from gamedev_lab.sdnq_optimizer import (
    SDNQOptimizationConfig,
    get_available_configs_for_gpu,
    get_config_by_name,
)
from gamedev_shared.vram_monitor import VRAMMonitor

console = Console()


@dataclass
class PipelineStageResult:
    """Resultado de uma etapa do pipeline."""

    stage: str  # "part3d" ou "paint3d"
    config_name: str
    success: bool
    vram_peak_mb: float | None
    execution_time_sec: float
    input_file: Path | None = None
    output_file: Path | None = None
    error_message: str | None = None
    oom_triggered: bool = False


@dataclass
class PipelineResult:
    """Resultado completo do pipeline."""

    pipeline_name: str
    part3d_config: str
    paint3d_config: str
    stages: list[PipelineStageResult] = field(default_factory=list)
    total_time_sec: float = 0.0
    success: bool = False
    total_vram_peak_mb: float = 0.0


class GameDevPipelineOptimizer:
    """
    Otimizador de pipeline completo que integra Part3D e Paint3D.

    Testa diferentes combinações de configurações SDNQ para encontrar
    o melhor equilíbrio entre qualidade e uso de VRAM.
    """

    def __init__(self, project_dir: Path, verbose: bool = True):
        self.project_dir = project_dir.resolve()
        self.verbose = verbose
        self.repo_root = gamedev_repo_root()
        self.output_dir = self.project_dir / "pipeline_opt_results"
        self.output_dir.mkdir(exist_ok=True)

        # Cache de binários
        self._part3d_bin: str | None = None
        self._resolve_binaries()

    def _resolve_binaries(self) -> None:
        """Resolve caminhos dos binários."""
        from gamedev_shared.subprocess_utils import resolve_binary

        self._part3d_bin = resolve_binary("PART3D_BIN", "part3d")

    def run_part3d_decompose(
        self,
        input_mesh: Path,
        output_dir: Path,
        quant_config: str,
        steps: int = 50,
        octree: int = 256,
    ) -> PipelineStageResult:
        """Executa Part3D decompose com configuração de quantização."""
        start_time = time.time()
        result = PipelineStageResult(
            stage="part3d",
            config_name=quant_config,
            success=False,
            vram_peak_mb=None,
            execution_time_sec=0.0,
            input_file=input_mesh,
        )

        output_dir.mkdir(parents=True, exist_ok=True)
        output_parts = output_dir / f"{input_mesh.stem}_parts.glb"
        output_segmented = output_dir / f"{input_mesh.stem}_segmented.glb"

        cmd = [
            self._part3d_bin,
            "decompose",
            str(input_mesh),
            "-o",
            str(output_parts),
            "--output-segmented",
            str(output_segmented),
            "--steps",
            str(steps),
            "--octree-resolution",
            str(octree),
            "--quantization",
            quant_config,
            "--seed",
            "42",
            "--profile",
        ]

        if self.verbose:
            console.print(f"[dim]Part3D: {quant_config} (steps={steps}, octree={octree})[/dim]")

        try:
            import torch

            torch.cuda.empty_cache()
            torch.cuda.synchronize()

            monitor = VRAMMonitor(interval_sec=0.5)
            monitor.start()

            proc_result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600.0,
            )

            stats = monitor.stop()

            result.execution_time_sec = time.time() - start_time
            result.vram_peak_mb = stats.peak_allocated_mb if stats else None
            result.success = proc_result.returncode == 0
            result.output_file = output_parts if output_parts.exists() else None

            if not result.success:
                result.error_message = proc_result.stderr[-500:] if proc_result.stderr else "Unknown error"

            if "out of memory" in (proc_result.stderr or "").lower():
                result.oom_triggered = True
                result.error_message = "OOM (Out of Memory)"
                torch.cuda.empty_cache()

        except subprocess.TimeoutExpired:
            result.error_message = "Timeout (10min)"
        except Exception as e:
            result.error_message = str(e)

        result.execution_time_sec = time.time() - start_time
        return result

    def run_paint3d_texture(
        self,
        input_mesh: Path,
        image_path: Path,
        output_path: Path,
        opt_config: SDNQOptimizationConfig,
    ) -> PipelineStageResult:
        """Executa Paint3D texturização com configuração SDNQ otimizada."""
        start_time = time.time()
        result = PipelineStageResult(
            stage="paint3d",
            config_name=opt_config.name,
            success=False,
            vram_peak_mb=None,
            execution_time_sec=0.0,
            input_file=input_mesh,
        )

        try:
            paint_src = self.repo_root / "Paint3D" / "src"
            shared_src = self.repo_root / "Shared" / "src"

            for p in (paint_src, shared_src):
                sp = str(p)
                if sp not in sys.path:
                    sys.path.insert(0, sp)

            import torch

            from gamedev_shared.bpy_mesh import load_glb
            from paint3d.painter import apply_hunyuan_paint
            from paint3d.utils.mesh_io import save_glb

            mesh_objects = load_glb(input_mesh)

            # Limpar VRAM
            torch.cuda.empty_cache()
            torch.cuda.synchronize()

            # Monitorar
            monitor = VRAMMonitor(interval_sec=0.2)
            monitor.start()

            quant_mode = f"sdnq-{opt_config.weights_dtype}"

            textured_mesh = apply_hunyuan_paint(
                mesh=mesh_objects,
                image=image_path,
                quantization_mode=quant_mode,
                use_tiny_vae=opt_config.use_tiny_vae,
                enable_vae_slicing=opt_config.enable_vae_slicing,
                enable_vae_tiling=opt_config.enable_vae_tiling,
                vae_tile_size=opt_config.vae_tile_size,
                enable_attention_slicing=opt_config.enable_attention_slicing,
                enable_torch_compile=opt_config.enable_torch_compile,
                max_num_view=opt_config.max_num_view,
                view_resolution=opt_config.view_resolution,
                verbose=self.verbose,
            )

            # Salvar
            save_glb(textured_mesh, output_path)

            stats = monitor.stop()

            result.success = output_path.exists()
            result.vram_peak_mb = stats.peak_allocated_mb if stats else None
            result.output_file = output_path if output_path.exists() else None

        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                result.oom_triggered = True
                result.error_message = "OOM (Out of Memory)"
            else:
                result.error_message = str(e)
        except Exception as e:
            result.error_message = str(e)

        result.execution_time_sec = time.time() - start_time
        return result

    def run_pipeline_combination(
        self,
        input_mesh: Path,
        reference_image: Path,
        part3d_quant: str,
        paint3d_config: SDNQOptimizationConfig,
        steps: int = 50,
        octree: int = 256,
    ) -> PipelineResult:
        """Executa pipeline completo com uma combinação de configs."""
        start_time = time.time()
        pipeline_name = f"{part3d_quant}+{paint3d_config.name}"

        result = PipelineResult(
            pipeline_name=pipeline_name,
            part3d_config=part3d_quant,
            paint3d_config=paint3d_config.name,
        )

        if self.verbose:
            console.print(
                Panel.fit(
                    f"[bold cyan]Pipeline: {pipeline_name}[/bold cyan]\n"
                    f"Part3D: {part3d_quant}\n"
                    f"Paint3D: {paint3d_config.name} "
                    f"({paint3d_config.weights_dtype}, TinyVAE={paint3d_config.use_tiny_vae})",
                    title="Testando Combinação",
                )
            )

        # Stage 1: Part3D
        stage1_dir = self.output_dir / f"stage1_{part3d_quant.replace('/', '_')}"
        stage1 = self.run_part3d_decompose(
            input_mesh=input_mesh,
            output_dir=stage1_dir,
            quant_config=part3d_quant,
            steps=steps,
            octree=octree,
        )
        result.stages.append(stage1)

        if not stage1.success:
            result.total_time_sec = time.time() - start_time
            result.error_message = f"Part3D falhou: {stage1.error_message}"
            if self.verbose:
                console.print(f"[red]✗ Part3D falhou: {stage1.error_message}[/red]")
            return result

        # Stage 2: Paint3D
        if stage1.output_file:
            stage2_output = self.output_dir / f"final_{pipeline_name.replace('/', '_')}.glb"
            stage2 = self.run_paint3d_texture(
                input_mesh=stage1.output_file,
                image_path=reference_image,
                output_path=stage2_output,
                opt_config=paint3d_config,
            )
            result.stages.append(stage2)

            if not stage2.success:
                result.error_message = f"Paint3D falhou: {stage2.error_message}"
                if self.verbose:
                    console.print(f"[red]✗ Paint3D falhou: {stage2.error_message}[/red]")

        # Calcular estatísticas
        result.total_time_sec = time.time() - start_time
        result.success = all(s.success for s in result.stages)

        vram_values = [s.vram_peak_mb for s in result.stages if s.vram_peak_mb is not None]
        result.total_vram_peak_mb = max(vram_values) if vram_values else 0.0

        return result

    def find_optimal_pipeline(
        self,
        input_mesh: Path,
        reference_image: Path,
        target_vram_mb: float = 5500.0,
        steps: int = 50,
        octree: int = 256,
    ) -> dict[str, Any]:
        """
        Encontra a melhor combinação de configs para o pipeline completo.

        Testa múltiplas combinações Part3D + Paint3D e retorna a melhor
        que atende ao target de VRAM.
        """
        if self.verbose:
            console.print(
                Panel.fit(
                    f"[bold blue]Otimização de Pipeline GameDev[/bold blue]\n"
                    f"Input: {input_mesh.name}\n"
                    f"Imagem: {reference_image.name}\n"
                    f"Target VRAM: {target_vram_mb:.0f} MB\n"
                    f"Steps: {steps}, Octree: {octree}",
                    title="Pipeline Optimizer",
                )
            )

        # Configs Part3D a testar

        # Configs Paint3D otimizadas
        get_available_configs_for_gpu(target_vram_mb / 1024)

        # Priorizar configs que provavelmente funcionam
        # Primeiro testar configs Paint3D ESTÁVEIS (qint8 nativo), depois SDNQ
        priority_order = [
            # === CONFIGS ESTÁVEIS (Paint3D qint8 nativo) ===
            # Estas são as configs recomendadas para uso diário
            ("sdnq-uint8", "paint3d-qint8-balanced"),
            ("quanto-int8", "paint3d-qint8-balanced"),
            ("sdnq-uint8", "paint3d-qint8-stable"),
            ("quanto-int8", "paint3d-qint8-stable"),
            ("none", "paint3d-qint8-stable"),
            # === CONFIGS SDNQ (experimental) ===
            # Estas requerem correções de bugs para funcionar completamente
            ("sdnq-uint8", "sdnq-uint8-minimal"),
            ("quanto-int8", "sdnq-uint8-minimal"),
            ("sdnq-uint8", "sdnq-uint8-tiny"),
            ("sdnq-int8", "sdnq-int8-tiny"),
            ("sdnq-uint8", "sdnq-int4-tiny"),
            ("quanto-int8", "sdnq-int4-minimal"),
            ("sdnq-uint8", "sdnq-uint8-full"),
            ("sdnq-int8", "sdnq-uint8-full"),
        ]

        all_results: list[PipelineResult] = []

        # Testar configs prioritárias primeiro
        tested_combinations: set[str] = set()

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Testando combinações...", total=len(priority_order))

            for part3d_quant, paint3d_name in priority_order:
                combo_key = f"{part3d_quant}+{paint3d_name}"
                if combo_key in tested_combinations:
                    continue
                tested_combinations.add(combo_key)

                paint3d_cfg = get_config_by_name(paint3d_name)
                if not paint3d_cfg:
                    continue

                progress.update(task, description=f"[cyan]Testando {combo_key}...")

                result = self.run_pipeline_combination(
                    input_mesh=input_mesh,
                    reference_image=reference_image,
                    part3d_quant=part3d_quant,
                    paint3d_config=paint3d_cfg,
                    steps=steps,
                    octree=octree,
                )

                all_results.append(result)

                # Se encontrou uma que funciona bem, podemos parar cedo
                if (
                    result.success
                    and result.total_vram_peak_mb <= target_vram_mb
                    and ("full" in paint3d_name or "tiny" in paint3d_name)
                ):
                    if self.verbose:
                        console.print(f"[green]✓ Encontrada config ideal: {combo_key}[/green]")
                    break

                progress.advance(task)

        # Analisar resultados
        successful = [r for r in all_results if r.success]

        if not successful:
            if self.verbose:
                console.print("[red]✗ Nenhuma combinação funcionou![/red]")
            return {
                "success": False,
                "results": [self._serialize_result(r) for r in all_results],
                "recommendation": None,
            }

        # Encontrar melhor combinação
        def score_pipeline(r: PipelineResult) -> float:
            score = 0.0

            # Bonus ALTO para configs Paint3D estáveis (prioridade máxima)
            if r.paint3d_config.startswith("paint3d-qint8"):
                score += 100
                if "balanced" in r.paint3d_config:
                    score += 50

            # Bonus por usar SDNQ no Part3D (melhor qualidade)
            if "sdnq" in r.part3d_config:
                score += 20

            # Bonus por VRAM baixa
            if r.total_vram_peak_mb <= target_vram_mb:
                score += 30
                score += (target_vram_mb - r.total_vram_peak_mb) / 100  # Margem extra

            # Bonus por tempo rápido
            score -= r.total_time_sec / 10

            return score

        best = max(successful, key=score_pipeline)

        # Criar tabela de resultados
        if self.verbose:
            table = Table(title="Resultados do Pipeline")
            table.add_column("Pipeline", style="cyan")
            table.add_column("Part3D", style="magenta")
            table.add_column("Paint3D", style="blue")
            table.add_column("VRAM Pico", style="green")
            table.add_column("Tempo", style="yellow")
            table.add_column("Status", style="bold")

            for r in all_results:
                status = "✓" if r.success else "✗"
                style = "green" if r.success else "red"
                if r.pipeline_name == best.pipeline_name:
                    style = "bold green"

                table.add_row(
                    r.pipeline_name,
                    r.part3d_config,
                    r.paint3d_config,
                    f"{r.total_vram_peak_mb:.0f} MB",
                    f"{r.total_time_sec:.1f}s",
                    status,
                    style=style,
                )

            console.print(table)
            console.print(f"\n[bold green]Melhor pipeline: {best.pipeline_name}[/bold green]")
            console.print(f"  VRAM pico: {best.total_vram_peak_mb:.0f} MB")
            console.print(f"  Tempo total: {best.total_time_sec:.1f}s")

        # Salvar relatório
        report = {
            "success": True,
            "recommendation": {
                "pipeline_name": best.pipeline_name,
                "part3d_config": best.part3d_config,
                "paint3d_config": best.paint3d_config,
                "vram_peak_mb": best.total_vram_peak_mb,
                "total_time_sec": best.total_time_sec,
            },
            "all_results": [self._serialize_result(r) for r in all_results],
        }

        report_path = self.output_dir / "pipeline_optimization_report.json"
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

        if self.verbose:
            console.print(f"\n[dim]Relatório salvo em: {report_path}[/dim]")

        return report

    def _serialize_result(self, result: PipelineResult) -> dict[str, Any]:
        """Serializa resultado para JSON."""
        return {
            "pipeline_name": result.pipeline_name,
            "part3d_config": result.part3d_config,
            "paint3d_config": result.paint3d_config,
            "success": result.success,
            "total_time_sec": result.total_time_sec,
            "total_vram_peak_mb": result.total_vram_peak_mb,
            "stages": [
                {
                    "stage": s.stage,
                    "config_name": s.config_name,
                    "success": s.success,
                    "vram_peak_mb": s.vram_peak_mb,
                    "execution_time_sec": s.execution_time_sec,
                    "oom_triggered": s.oom_triggered,
                    "error_message": s.error_message,
                }
                for s in result.stages
            ],
        }


def run_pipeline_optimization_cli(
    mesh: Path,
    image: Path,
    output_dir: Path,
    target_vram_mb: float,
    steps: int = 50,
    octree: int = 256,
) -> int:
    """CLI para otimização de pipeline."""
    optimizer = GameDevPipelineOptimizer(
        project_dir=output_dir,
        verbose=True,
    )

    result = optimizer.find_optimal_pipeline(
        input_mesh=mesh,
        reference_image=image,
        target_vram_mb=target_vram_mb,
        steps=steps,
        octree=octree,
    )

    return 0 if result["success"] else 1
