"""
Otimizador SDNQ avançado para GameDevLab.

Consolida técnicas de otimização:
- SDNQ 4-bit e 8-bit quantização
- TinyVAE (TAESD) para redução de VRAM
- Attention slicing e VAE tiling
- torch.compile para aceleração
- Fallback automático em OOM

Quantization presets are sourced from ``gamedev_shared.sdnq``.
"""

from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from gamedev_shared.sdnq import create_config as create_sdnq_config_shared

console = Console()


@dataclass
class SDNQOptimizationConfig:
    """Configuração otimizada para SDNQ."""

    name: str
    weights_dtype: str  # "uint8", "int8", "int4", "fp8"
    group_size: int = 0  # 0 = auto
    use_svd: bool = False
    svd_rank: int = 32
    svd_steps: int = 8
    use_quantized_matmul: bool = True
    dequantize_fp32: bool = True

    # Otimizações adicionais
    use_tiny_vae: bool = False
    enable_vae_slicing: bool = True
    enable_vae_tiling: bool = True
    vae_tile_size: int = 256
    enable_attention_slicing: bool = True
    enable_torch_compile: bool = False
    torch_compile_mode: str = "reduce-overhead"

    # Parâmetros de resolução (para ajuste dinâmico)
    max_num_view: int = 6
    view_resolution: int = 512
    texture_size: int = 2048

    # Fallback em caso de OOM
    fallback_to: str | None = None  # Nome de outra config para fallback

    def __post_init__(self):
        """Valida configuração."""
        if self.weights_dtype not in ("uint8", "int8", "int4", "fp8", "fp16"):
            raise ValueError(f"weights_dtype inválido: {self.weights_dtype}")


# Configurações predefinidas otimizadas
OPTIMIZED_SDNQ_CONFIGS: list[SDNQOptimizationConfig] = [
    # Configuração ESTÁVEL - Paint3D com pré-quantização nativa qint8 (não usa SDNQ)
    # Esta é a configuração padrão que funciona de forma estável
    SDNQOptimizationConfig(
        name="paint3d-qint8-stable",
        weights_dtype="fp16",  # FP16 pois o Paint3D aplica qint8 internamente
        group_size=0,
        use_svd=False,
        use_tiny_vae=False,  # TinyVAE incompatível com HunyuanPaintPBR
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=256,
        enable_attention_slicing=True,
        enable_torch_compile=False,  # Compilação desligada para estabilidade
        max_num_view=4,  # Views reduzidas para economia de VRAM
        view_resolution=256,  # Resolução conservadora
        texture_size=1024,
        fallback_to=None,
    ),
    # Configuração estável alternativa com mais qualidade
    SDNQOptimizationConfig(
        name="paint3d-qint8-balanced",
        weights_dtype="fp16",
        group_size=0,
        use_svd=False,
        use_tiny_vae=False,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=256,
        enable_attention_slicing=True,
        enable_torch_compile=False,
        max_num_view=6,
        view_resolution=384,
        texture_size=2048,
        fallback_to="paint3d-qint8-stable",
    ),
    # Configurações SDNQ 8-bit (experimental - requer correção de bugs)
    SDNQOptimizationConfig(
        name="sdnq-uint8-full",
        weights_dtype="uint8",
        group_size=0,
        use_svd=False,
        use_tiny_vae=False,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=256,
        enable_attention_slicing=True,
        enable_torch_compile=True,
        max_num_view=6,
        view_resolution=512,
        texture_size=2048,
        fallback_to="sdnq-uint8-tiny",
    ),
    SDNQOptimizationConfig(
        name="sdnq-uint8-tiny",
        weights_dtype="uint8",
        group_size=0,
        use_svd=False,
        use_tiny_vae=True,  # TinyVAE ativo
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=128,  # Tiles menores
        enable_attention_slicing=True,
        enable_torch_compile=True,
        max_num_view=4,  # Menos views
        view_resolution=384,
        texture_size=1024,
        fallback_to="sdnq-uint8-minimal",
    ),
    SDNQOptimizationConfig(
        name="sdnq-uint8-minimal",
        weights_dtype="uint8",
        group_size=32,  # Grupos menores para melhor precisão
        use_svd=False,
        use_tiny_vae=True,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=64,
        enable_attention_slicing=True,
        enable_torch_compile=False,  # Desligado para economia
        max_num_view=2,
        view_resolution=256,
        texture_size=512,
        fallback_to=None,
    ),
    # Configurações 4-bit (máxima compressão)
    SDNQOptimizationConfig(
        name="sdnq-int4-full",
        weights_dtype="int4",
        group_size=32,  # Grupos pequenos para melhor precisão com 4-bit
        use_svd=True,  # SVD ajuda com 4-bit
        svd_rank=32,
        svd_steps=8,
        use_tiny_vae=False,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=256,
        enable_attention_slicing=True,
        enable_torch_compile=True,
        max_num_view=6,
        view_resolution=512,
        texture_size=2048,
        fallback_to="sdnq-int4-tiny",
    ),
    SDNQOptimizationConfig(
        name="sdnq-int4-tiny",
        weights_dtype="int4",
        group_size=32,
        use_svd=True,
        svd_rank=24,  # Rank menor para economia
        svd_steps=6,
        use_tiny_vae=True,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=128,
        enable_attention_slicing=True,
        enable_torch_compile=True,
        max_num_view=4,
        view_resolution=384,
        texture_size=1024,
        fallback_to="sdnq-int4-minimal",
    ),
    SDNQOptimizationConfig(
        name="sdnq-int4-minimal",
        weights_dtype="int4",
        group_size=64,  # Grupos maiores = mais compressão
        use_svd=True,
        svd_rank=16,
        svd_steps=4,
        use_tiny_vae=True,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=64,
        enable_attention_slicing=True,
        enable_torch_compile=False,
        max_num_view=2,
        view_resolution=256,
        texture_size=512,
        fallback_to=None,
    ),
    # Configuração FP8 (para GPUs RTX 40 series)
    SDNQOptimizationConfig(
        name="sdnq-fp8",
        weights_dtype="fp8",
        group_size=0,
        use_svd=False,
        use_tiny_vae=False,
        enable_vae_slicing=True,
        enable_vae_tiling=True,
        vae_tile_size=256,
        enable_attention_slicing=True,
        enable_torch_compile=True,
        max_num_view=6,
        view_resolution=512,
        texture_size=2048,
        fallback_to="sdnq-uint8-full",
    ),
]


def get_config_by_name(name: str) -> SDNQOptimizationConfig | None:
    """Retorna configuração pelo nome."""
    for cfg in OPTIMIZED_SDNQ_CONFIGS:
        if cfg.name == name:
            return cfg
    return None


def create_sdnq_config_from_optimization(opt_config: SDNQOptimizationConfig) -> Any:
    """Cria SDNQConfig a partir da configuração otimizada (via shared module)."""
    try:
        preset_name = f"sdnq-{opt_config.weights_dtype}"
        return create_sdnq_config_shared(
            preset_name,
            use_quantized_matmul=opt_config.use_quantized_matmul,
            dequantize_fp32=opt_config.dequantize_fp32,
        )
    except (ImportError, KeyError):
        console.print("[red]SDNQ não instalado ou preset desconhecido[/red]")
        return None


def _check_cuda() -> bool:
    """Verifica se CUDA está disponível."""
    try:
        import torch

        return torch.cuda.is_available()
    except ImportError:
        return False


def get_available_configs_for_gpu(vram_gb: float | None = None) -> list[SDNQOptimizationConfig]:
    """Retorna configs disponíveis baseado na VRAM da GPU."""
    if vram_gb is None:
        try:
            import torch

            vram_gb = torch.cuda.get_device_properties(0).total_memory / 1024**3 if torch.cuda.is_available() else 0
        except ImportError:
            vram_gb = 0

    configs = []
    for cfg in OPTIMIZED_SDNQ_CONFIGS:
        # Estimar VRAM necessária
        _estimate_vram_for_config(cfg)

        # Configurações 4-bit são mais acessíveis
        if cfg.weights_dtype == "int4" or (cfg.weights_dtype == "uint8" and vram_gb >= 6):
            configs.append(cfg)
        elif cfg.weights_dtype == "fp8" and vram_gb >= 8:
            # FP8 precisa de GPU mais recente
            props = torch.cuda.get_device_properties(0)
            if props.major >= 8 and props.minor >= 9:
                configs.append(cfg)

    return configs


def _estimate_vram_for_config(cfg: SDNQOptimizationConfig) -> float:
    """Estima VRAM necessária em GB."""
    base_model_gb = 4.0  # Modelo base em FP16

    # Fator de compressão baseado no dtype
    compression_factors = {
        "fp16": 1.0,
        "fp8": 0.5,
        "uint8": 0.5,
        "int8": 0.5,
        "int4": 0.25,
    }

    factor = compression_factors.get(cfg.weights_dtype, 1.0)
    model_gb = base_model_gb * factor

    # Overhead para ativações
    activation_overhead = 1.5

    # TinyVAE reduz VRAM do VAE
    vae_gb = 0.1 if cfg.use_tiny_vae else 0.5

    # Resolução afeta ativações
    resolution_factor = (cfg.view_resolution / 512) ** 2
    view_factor = cfg.max_num_view / 6

    total_gb = model_gb * activation_overhead + vae_gb
    total_gb *= 1 + (resolution_factor * view_factor - 1) * 0.3

    return total_gb


@dataclass
class OptimizationResult:
    """Resultado de uma tentativa de otimização."""

    config_name: str
    success: bool
    vram_peak_mb: float | None
    vram_allocated_mb: float | None
    execution_time_sec: float
    error_message: str | None = None
    fallback_used: str | None = None
    oom_triggered: bool = False


def apply_paint3d_with_sdnq_opt(
    mesh_path: Path,
    image_path: Path,
    output_path: Path,
    opt_config: SDNQOptimizationConfig,
    verbose: bool = True,
) -> OptimizationResult:
    """Aplica Paint3D com configuração SDNQ otimizada."""
    start_time = time.time()
    result = OptimizationResult(
        config_name=opt_config.name,
        success=False,
        vram_peak_mb=None,
        vram_allocated_mb=None,
        execution_time_sec=0.0,
    )

    try:
        from gamedev_lab.paths import gamedev_repo_root
        from gamedev_shared.vram_monitor import VRAMMonitor

        root = gamedev_repo_root()
        paint_src = root / "Paint3D" / "src"
        shared_src = root / "Shared" / "src"

        for p in (paint_src, shared_src):
            sp = str(p)
            if sp not in sys.path:
                sys.path.insert(0, sp)

        import torch

        from gamedev_shared.bpy_mesh import load_glb
        from paint3d.painter import apply_hunyuan_paint

        # Limpar VRAM
        torch.cuda.empty_cache()
        torch.cuda.synchronize()

        # Carregar mesh
        mesh_objects = load_glb(mesh_path)

        # Configurar modo de quantização
        # Configurações paint3d-qint8-* usam quantização nativa do Paint3D (não SDNQ)
        if opt_config.name.startswith("paint3d-qint8"):
            quant_mode = None  # Paint3D usará seu próprio qint8 pré-quantizado
            quant_display = "qint8-nativo ( Paint3D )"
        else:
            quant_mode = f"sdnq-{opt_config.weights_dtype}"
            quant_display = quant_mode

        # Monitorar VRAM
        monitor = VRAMMonitor(interval_sec=0.2)
        monitor.start()

        try:
            if verbose:
                console.print(f"[yellow]Testando config: {opt_config.name}[/yellow]")
                console.print(f"  Quantização: {quant_display}")
                console.print(f"  TinyVAE: {opt_config.use_tiny_vae}")
                console.print(f"  Views: {opt_config.max_num_view} @ {opt_config.view_resolution}px")

            # Aplicar paint
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
                verbose=verbose,
            )

            # Salvar resultado
            from paint3d.utils.mesh_io import save_glb

            save_glb(textured_mesh, output_path)

            # Parar monitoramento
            stats = monitor.stop()

            result.success = True
            result.vram_peak_mb = stats.peak_allocated_mb
            result.vram_allocated_mb = stats.avg_allocated_mb
            result.execution_time_sec = time.time() - start_time

            if verbose:
                console.print(f"[green]✓ Sucesso: {opt_config.name}[/green]")
                console.print(f"  VRAM pico: {stats.peak_allocated_mb:.0f} MB")
                console.print(f"  Tempo: {result.execution_time_sec:.1f}s")

        except RuntimeError as e:
            monitor.stop()
            if "out of memory" in str(e).lower():
                result.oom_triggered = True
                result.error_message = "OOM (Out of Memory)"
                torch.cuda.empty_cache()

                # Tentar fallback
                if opt_config.fallback_to:
                    if verbose:
                        console.print(f"[yellow]→ Fallback para: {opt_config.fallback_to}[/yellow]")
                    fallback_cfg = get_config_by_name(opt_config.fallback_to)
                    if fallback_cfg:
                        return apply_paint3d_with_sdnq_opt(mesh_path, image_path, output_path, fallback_cfg, verbose)
            else:
                result.error_message = str(e)

        except Exception as e:
            monitor.stop()
            result.error_message = str(e)

    except ImportError as e:
        result.error_message = f"Dependência em falta: {e}"

    result.execution_time_sec = time.time() - start_time
    return result


def find_best_sdnq_config_paint3d(
    mesh_path: Path,
    image_path: Path,
    output_dir: Path,
    target_vram_mb: float = 5500.0,
    verbose: bool = True,
) -> dict[str, Any]:
    """
    Encontra a melhor configuração SDNQ para Paint3D iterando pelas opções.

    Retorna:
        Dict com resultados e recomendação
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    if verbose:
        console.print(
            Panel.fit(
                f"[bold blue]Busca por Melhor Config SDNQ[/bold blue]\n"
                f"Target VRAM: {target_vram_mb:.0f} MB\n"
                f"Mesh: {mesh_path.name}\n"
                f"Imagem: {image_path.name}",
                title="Paint3D SDNQ Optimizer",
            )
        )

    # Obter configs disponíveis - priorizar configs estáveis primeiro
    configs = get_available_configs_for_gpu()

    # Reordenar: primeiro configs paint3d-qint8 estáveis, depois as SDNQ
    stable_configs = [c for c in configs if c.name.startswith("paint3d-qint8")]
    sdnq_configs = [c for c in configs if not c.name.startswith("paint3d-qint8")]
    configs = stable_configs + sdnq_configs

    if verbose:
        console.print(
            f"\n[dim]Configs a testar: {len(configs)} "
            f"(estáveis: {len(stable_configs)}, SDNQ: {len(sdnq_configs)})[/dim]"
        )

    results: list[OptimizationResult] = []

    for cfg in configs:
        # Pular configs que claramente excederiam VRAM
        estimated = _estimate_vram_for_config(cfg)
        if estimated * 1024 > target_vram_mb * 1.5:  # Margem de 50%
            if verbose:
                console.print(f"[dim]Pulando {cfg.name} (estimado: {estimated * 1024:.0f} MB)[/dim]")
            continue

        output_path = output_dir / f"result_{cfg.name}.glb"

        result = apply_paint3d_with_sdnq_opt(
            mesh_path=mesh_path,
            image_path=image_path,
            output_path=output_path,
            opt_config=cfg,
            verbose=verbose,
        )

        results.append(result)

        # Se sucesso e dentro do target, marcar como candidato
        if result.success and result.vram_peak_mb and result.vram_peak_mb <= target_vram_mb and verbose:
            console.print(f"[green]✓ Config {cfg.name} atende ao target![/green]")

    # Analisar resultados
    successful = [r for r in results if r.success]

    if not successful:
        if verbose:
            console.print("[red]✗ Nenhuma configuração funcionou![/red]")
        return {
            "success": False,
            "results": [r.__dict__ for r in results],
            "recommendation": None,
        }

    # Ordenar por qualidade (preferir estável > 8-bit > 4-bit, mais views > menos views)
    def quality_score(r: OptimizationResult) -> float:
        cfg = get_config_by_name(r.config_name)
        if not cfg:
            return 0.0

        score = 0.0

        # Bonus alto para configurações estáveis (paint3d-qint8-*)
        if cfg.name.startswith("paint3d-qint8"):
            score += 200  # Prioridade máxima para configs estáveis
            if "balanced" in cfg.name:
                score += 20  # Bonus para versão balanced

        # Bonus por qualidade de quantização (apenas para configs SDNQ)
        if cfg.weights_dtype == "fp8":
            score += 100
        elif cfg.weights_dtype == "uint8":
            score += 80
        elif cfg.weights_dtype == "int8":
            score += 70
        elif cfg.weights_dtype == "int4":
            score += 50

        # Bonus por resolução
        score += cfg.view_resolution / 10
        score += cfg.max_num_view * 5

        # Bonus por TinyVAE (mais eficiente) - NOTA: TinyVAE atualmente incompatível
        if cfg.use_tiny_vae:
            score += 5  # Reduzido devido à incompatibilidade

        # Penalidade por VRAM excessiva
        if r.vram_peak_mb:
            score -= max(0, (r.vram_peak_mb - target_vram_mb) / 100)

        return score

    # Encontrar o melhor que atende ao target
    qualifying = [r for r in successful if r.vram_peak_mb and r.vram_peak_mb <= target_vram_mb]

    if qualifying:
        best = max(qualifying, key=quality_score)
    else:
        # Nenhum atende, pegar o que mais se aproxima
        best = min(successful, key=lambda r: abs((r.vram_peak_mb or 99999) - target_vram_mb))

    # Criar relatório
    table = Table(title="Resultados SDNQ Paint3D")
    table.add_column("Config", style="cyan")
    table.add_column("Dtype", style="magenta")
    table.add_column("VRAM Pico", style="green")
    table.add_column("Tempo", style="yellow")
    table.add_column("Status", style="bold")

    for r in results:
        cfg = get_config_by_name(r.config_name)
        dtype = cfg.weights_dtype if cfg else "?"
        vram = f"{r.vram_peak_mb:.0f} MB" if r.vram_peak_mb else "N/A"
        tempo = f"{r.execution_time_sec:.1f}s"
        status = "✓" if r.success else "✗"
        if r.oom_triggered:
            status = "💥 OOM"
        style = "green" if r.success else "red"
        if r.config_name == best.config_name:
            style = "bold green"
        table.add_row(r.config_name, dtype, vram, tempo, status, style=style)

    if verbose:
        console.print(table)
        console.print(f"\n[bold green]Recomendação: {best.config_name}[/bold green]")
        console.print(f"  VRAM pico: {best.vram_peak_mb:.0f} MB")
        console.print(f"  Tempo: {best.execution_time_sec:.1f}s")

    return {
        "success": True,
        "results": [r.__dict__ for r in results],
        "recommendation": best.config_name,
        "best_config": best.__dict__,
    }


def run_sdnq_sweep_cli(
    mesh: Path,
    image: Path,
    output_dir: Path,
    target_vram_mb: float,
) -> int:
    """CLI para executar sweep de configurações SDNQ."""
    result = find_best_sdnq_config_paint3d(
        mesh_path=mesh,
        image_path=image,
        output_dir=output_dir,
        target_vram_mb=target_vram_mb,
        verbose=True,
    )

    # Salvar relatório JSON
    report_path = output_dir / "sdnq_sweep_report.json"
    report_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
    console.print(f"\n[dim]Relatório salvo em: {report_path}[/dim]")

    return 0 if result["success"] else 1
