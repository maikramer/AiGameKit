"""Tabelas Rich partilhadas para comandos ``info`` e ``doctor`` das CLIs.

Extrai o padrão duplicado em 7 comandos ``info`` e 3 ``doctor``:
  - Panel de header
  - Tabela de sistema (Python/PyTorch/CUDA + loop de GPUs)
  - HF_HOME display
  - Tabela de doctor com uso de VRAM % e threshold

Cada tool fornece apenas as rows extra específicas (model IDs, presets, etc.).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .gpu import format_bytes, get_system_info
from .hf import hf_home_display_rich


def render_info_table(
    console: Console,
    *,
    tool_name: str,
    subtitle: str = "ambiente de execução e cache Hugging Face",
    extra_rows: list[tuple[str, str]] | None = None,
    output_dir: str | None = None,
    hw_profile_summary: str | None = None,
    hw_auto_state: str = "",
) -> None:
    """Renderiza o comando ``info`` padrão.

    Args:
        console: Console Rich.
        tool_name: Nome da tool para o header (ex: ``text2icon``).
        subtitle: Subtítulo do panel.
        extra_rows: Rows extra no topo da tabela (ex: model IDs, presets) —
            lista de ``(label, value)``.
        output_dir: Diretório de saída padrão (ex: ``str(DEFAULT_ICON_DIR.resolve())``).
        hw_profile_summary: Summary do hardware profile auto-detectado.
        hw_auto_state: Estado do hw-auto (ex: ``" (desligado: TEXT2ICON_HW_AUTO=0)"``).
    """
    console.print(
        Panel.fit(
            f"[bold]{tool_name} info[/bold] — {subtitle}",
            border_style="blue",
        )
    )

    data = get_system_info()
    t = Table(title="[bold blue]Sistema", box=box.ROUNDED)
    t.add_column("Componente", style="cyan", no_wrap=True)
    t.add_column("Valor", style="green")

    # Rows extra da tool (model IDs, presets, etc.) — antes do sistema.
    if extra_rows:
        for label, value in extra_rows:
            t.add_row(label, value)

    # Sistema.
    t.add_row("Python", data.get("python_version", "N/A"))
    t.add_row("PyTorch", data.get("torch_version", "N/A"))
    t.add_row("CUDA", str(data.get("cuda_available", False)))
    if data.get("cuda_available"):
        t.add_row("CUDA (versão)", str(data.get("cuda_version", "N/A")))
        for i, gpu in enumerate(data.get("gpus", [])):
            t.add_row(f"GPU {i}", str(gpu.get("name", "")))
            t.add_row("  └ VRAM total", format_bytes(gpu.get("total_memory", 0)))
            t.add_row("  └ VRAM livre", format_bytes(gpu.get("free_memory", 0)))

    t.add_row("HF_HOME (cache Hub)", hf_home_display_rich())

    if output_dir:
        t.add_row("Saída padrão", output_dir)

    if hw_profile_summary is not None:
        t.add_row("Perfil hardware (auto)", f"{hw_profile_summary}{hw_auto_state}")

    console.print(t)


def render_doctor_table(
    console: Console,
    *,
    tool_name: str,
    extra_checks: list[tuple[str, bool, str]] | None = None,
    min_vram_gb: float = 6.0,
) -> bool:
    """Renderiza o comando ``doctor`` padrão (diagnóstico de ambiente).

    Verifica: CUDA disponível, VRAM suficiente, uso de VRAM por outros processos.
    Tools podem adicionar checks extra (ex: npx gltf-transform, sageattention).

    Args:
        console: Console Rich.
        tool_name: Nome da tool para o header.
        extra_checks: Checks tool-specific — lista de ``(name, passed, detail)``.
            ``passed=True`` mostra ✓ verde; ``False`` mostra ✗ vermelho.
        min_vram_gb: VRAM mínima recomendada (default 6.0).

    Returns:
        ``True`` se todos os checks passaram, ``False`` se algum falhou.
    """
    from .gpu import check_gpu_compatibility

    console.print(
        Panel.fit(
            f"[bold]{tool_name} doctor[/bold] — diagnóstico de ambiente",
            border_style="blue",
        )
    )

    checks: list[tuple[str, bool, str]] = []

    # CUDA + VRAM.
    ok, msg = check_gpu_compatibility(min_vram_gb=min_vram_gb)
    checks.append(("GPU compatível", ok, msg))

    # Checks extra da tool.
    if extra_checks:
        checks.extend(extra_checks)

    t = Table(title="[bold blue]Diagnóstico", box=box.ROUNDED)
    t.add_column("Check", style="cyan", no_wrap=True)
    t.add_column("Estado")
    t.add_column("Detalhe", style="dim")

    all_ok = True
    for name, passed, detail in checks:
        status = "[green]✓ OK[/green]" if passed else "[red]✗ FALHA[/red]"
        if not passed:
            all_ok = False
        t.add_row(name, status, detail)

    console.print(t)
    return all_ok


def render_presets_table(
    console: Console,
    *,
    title: str,
    presets: Mapping[str, dict[str, Any]],
    columns: list[
        tuple[
            str,
            str,
            Literal["default", "left", "center", "right", "full"] | None,
            Callable[[dict[str, Any]], str],
        ]
    ],
) -> None:
    """Renderiza o comando ``presets`` (tabela Nome + colunas por preset).

    Padrão das tools 2D (Texture2D/Skymap2D byte-idênticos; Text2Sound com
    colunas próprias) — as tools fornecem apenas título e colunas.

    Args:
        console: Console Rich.
        title: Título da tabela (ex: ``Presets de Texturas``).
        presets: Dict de presets da tool.
        columns: ``(label, style, justify, formatter)`` — o formatter recebe o
            dict do preset e devolve a string da célula. ``justify`` vazio = esquerda.
    """
    t = Table(title=f"[bold blue]{title}", box=box.ROUNDED)
    t.add_column("Nome", style="cyan", no_wrap=True)
    for label, style, justify, _ in columns:
        t.add_column(label, style=style, justify=justify or "left")
    for name, preset in presets.items():
        t.add_row(name, *(fmt(preset) for _, _, _, fmt in columns))
    console.print(t)
