"""Pré-quantização SDNQ (Paint3D UNet)."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from gamedev_lab.paths import gamedev_repo_root

console = Console()


def quantizar_paint3d_unet() -> bool:
    """Pré-quantiza o UNet do Paint3D para SDNQ uint8."""
    console.print(
        Panel.fit(
            "[bold blue]Paint3D UNet Quantization[/bold blue]\n"
            "Modelo: tencent/Hunyuan3D-2.1/paint\n"
            "Destino: uint8 (SDNQ)",
            title="Pré-quantização",
        )
    )

    try:
        import torch
        from diffusers import DiffusionPipeline
        from huggingface_hub import snapshot_download

        root = gamedev_repo_root()
        custom_pipeline = str(root / "Paint3D" / "src" / "paint3d" / "hy3dpaint")

        console.print("[yellow]1. Download e carregamento do pipeline...[/yellow]")
        snapshot = snapshot_download(
            repo_id="tencent/Hunyuan3D-2.1",
            allow_patterns=["hunyuan3d-paintpbr-v2-1/*"],
        )
        model_dir = Path(snapshot) / "hunyuan3d-paintpbr-v2-1"

        pipe = DiffusionPipeline.from_pretrained(
            str(model_dir),
            custom_pipeline=custom_pipeline,
            torch_dtype=torch.float16,
        )
        unet = pipe.unet
        unet.eval()

        total_params = sum(p.numel() for p in unet.parameters())
        console.print(f"   [green]✓[/green] UNet carregado: {total_params / 1e6:.0f}M params")

        console.print("[yellow]2. Aplicando SDNQ uint8 ao UNet...[/yellow]")
        from gamedev_shared.sdnq import quantize_model

        unet_quantizado = quantize_model(unet, preset="sdnq-uint8")
        unet_quantizado.eval()
        console.print("   [green]✓[/green] Quantização aplicada")

        console.print("[yellow]3. Salvando UNet quantizado...[/yellow]")
        output_dir = model_dir / "unet_sdnq_uint8"
        output_dir.mkdir(exist_ok=True)

        from safetensors.torch import save_file

        state_dict = unet_quantizado.state_dict()
        save_file(state_dict, str(output_dir / "diffusion_pytorch_model.safetensors"))
        config = {"_class_name": "UNet2DConditionModel", "quantization": "sdnq-uint8"}
        with open(output_dir / "config.json", "w") as f:
            json.dump(config, f, indent=2)

        console.print(f"   [green]✓[/green] Salvo em: {output_dir}")
        del pipe, unet, unet_quantizado, state_dict
        torch.cuda.empty_cache()

        console.print(
            Panel.fit(
                "[bold green]✓ Paint3D UNet quantizado com sucesso![/bold green]",
                title="Concluído",
            )
        )
        return True

    except Exception as e:
        console.print(f"[bold red]✗ Erro:[/bold red] {e}")
        import traceback

        console.print(traceback.format_exc())
        return False


def run_pre_quantize_cli(modelo: str, dry_run: bool) -> int:
    from gamedev_shared.sdnq import is_available as sdnq_available

    if not sdnq_available():
        console.print("[red]✗ SDNQ não instalado. Execute: pip install sdnq[/red]")
        return 1

    console.print("[green]✓ SDNQ disponível[/green]")

    if dry_run:
        console.print("[dim]Dry-run: apenas verificando...[/dim]")
        return 0

    resultados: dict[str, bool] = {}
    if modelo in ("paint3d", "todos"):
        resultados["paint3d"] = quantizar_paint3d_unet()

    console.print("\n" + "=" * 60)
    console.print("[bold]RESUMO[/bold]")
    console.print("=" * 60)
    for m, sucesso in resultados.items():
        status = "[green]✓ SUCESSO[/green]" if sucesso else "[red]✗ FALHA[/red]"
        console.print(f"{m}: {status}")

    return 0 if resultados and all(resultados.values()) else 1
