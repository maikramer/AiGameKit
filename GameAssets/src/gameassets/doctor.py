"""Doctor de primeiro uso — um único comando que valida a instalação toda.

Cobre o que os três doctors parciais (``text3d doctor``, ``clified doctor``,
``vramd doctor``) não cobriam juntos: ferramentas do perfil, vramd/GPU,
compressão GLB, Node/Bun, LLM do ``dream`` e disco. Saída humana (rich) ou
``--json`` para agentes; exit 1 quando algo obrigatório falha.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .runner import resolve_binary

# Perfis (espelham install.sh / scripts/preflight.py — mantê-los sincronizados).
CORE_TOOLS = (
    "vramd",
    "text2d",
    "text3d",
    "paint3d",
    "rigging3d",
    "animator3d",
    "gameassets",
    "materialize",
    "aigamekitlab",
    "vibegame",
)
EXAMPLES_EXTRA_TOOLS = ("texture2d", "skymap2d", "text2sound", "terrain3d", "rocks3d", "viber")

# tool → nome do CLI (aigamekitlab difere da chave do tools.yaml).
TOOL_CLIS = {
    "vramd": "vramd",
    "text2d": "text2d",
    "text3d": "text3d",
    "paint3d": "paint3d",
    "rigging3d": "rigging3d",
    "animator3d": "animator3d",
    "gameassets": "gameassets",
    "materialize": "materialize",
    "aigamekitlab": "aigamekit-lab",
    "vibegame": "vibegame",
    "texture2d": "texture2d",
    "skymap2d": "skymap2d",
    "text2sound": "text2sound",
    "terrain3d": "terrain3d",
    "rocks3d": "rocks3d",
    "viber": "viber",
}

# Tools que o ``gameassets dream`` usa por defeito mas que só existem no perfil
# examples — o dream salta os passos correspondentes quando faltam.
DREAM_OPTIONAL_TOOLS = ("skymap2d", "terrain3d", "text2sound")

MIN_DISK_GB = 25  # pesos dos modelos ficam em ~/.cache/huggingface
TEXT3D_DOCTOR_TIMEOUT = 180  # importa bpy (~10-30 s)


@dataclass
class DoctorCheck:
    """Resultado de uma verificação do doctor."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    fix: str = ""  # ação sugerida quando status != ok

    @property
    def symbol(self) -> str:
        return {"ok": "✓", "warn": "!", "fail": "✗"}.get(self.status, "?")


def tool_bin(tool: str) -> str | None:
    cli = TOOL_CLIS[tool]
    try:
        return str(resolve_binary(cli.upper().replace("-", "_") + "_BIN", cli))
    except FileNotFoundError:
        return None


def _run_version(cmd: list[str], timeout: float = 10.0) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def check_tools() -> list[DoctorCheck]:
    """Ferramentas do perfil core + extras opcionais do dream."""
    missing_core = [t for t in CORE_TOOLS if t != "gameassets" and tool_bin(t) is None]
    if missing_core:
        check = DoctorCheck(
            name=f"Ferramentas do perfil core ({len(CORE_TOOLS)})",
            status="fail",
            detail=f"em falta: {', '.join(missing_core)}",
            fix="./install.sh   (na raiz do monorepo — instala o perfil core)",
        )
    else:
        check = DoctorCheck(
            name=f"Ferramentas do perfil core ({len(CORE_TOOLS)})",
            status="ok",
            detail="todas resolvíveis",
        )

    missing_optional = [t for t in DREAM_OPTIONAL_TOOLS if tool_bin(t) is None]
    if missing_optional:
        names = ", ".join(missing_optional)
        optional = DoctorCheck(
            name="Extras do dream (sky/terreno/áudio)",
            status="warn",
            detail=f"em falta: {names} — o dream salta estes passos",
            fix=f"./install.sh examples   (ou individual: ./install.sh {' '.join(missing_optional)})",
        )
    else:
        optional = DoctorCheck(
            name="Extras do dream (sky/terreno/áudio)",
            status="ok",
            detail="skymap2d, terrain3d e text2sound instaladas",
        )
    return [check, optional]


def check_vramd_gpu() -> list[DoctorCheck]:
    from aigamekit_shared.gpu import query_gpu_snapshot
    from aigamekit_shared.vramd_client import is_vramd_running

    vramd_check = DoctorCheck(
        name="vramd (supervisor de VRAM)",
        status="ok",
        detail="a correr" if is_vramd_running() else "não ativo — auto-arranca no primeiro job GPU",
    )
    snap = query_gpu_snapshot(0)
    if snap is None:
        gpu_check = DoctorCheck(
            name="GPU NVIDIA",
            status="warn",
            detail="não detetada (NVML/nvidia-smi) — as ferramentas GPU não geram localmente",
        )
    else:
        gpu_check = DoctorCheck(
            name="GPU NVIDIA",
            status="ok",
            detail=f"{snap.name}: {snap.free_mib} MiB livres / {snap.total_mib} MiB [via {snap.source}]",
        )
    return [vramd_check, gpu_check]


def check_compression() -> list[DoctorCheck]:
    """Compressão GLB (KTX2/meshopt/npx) — agrega o ``text3d doctor``."""
    if tool_bin("text3d") is None:
        return [
            DoctorCheck(
                name="Compressão GLB (KTX2/meshopt)",
                status="warn",
                detail="text3d ausente — sem verificação",
                fix="./install.sh text3d",
            )
        ]
    text3d_bin = tool_bin("text3d")
    try:
        proc = subprocess.run(
            [text3d_bin, "doctor"],
            capture_output=True,
            text=True,
            timeout=TEXT3D_DOCTOR_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return [
            DoctorCheck(
                name="Compressão GLB (KTX2/meshopt)",
                status="warn",
                detail="não foi possível correr 'text3d doctor' (timeout/exec)",
            )
        ]
    tail = "\n".join((proc.stdout or "").strip().splitlines()[-12:])
    if proc.returncode == 0:
        return [DoctorCheck(name="Compressão GLB (KTX2/meshopt)", status="ok", detail="text3d doctor passou")]
    return [
        DoctorCheck(
            name="Compressão GLB (KTX2/meshopt)",
            status="warn",
            detail=f"text3d doctor relatou problemas:\n{tail}",
            fix="text3d doctor   (corre direto para detalhe; docs/GLB_FINISH_COMPRESSION.md)",
        )
    ]


def check_node_bun() -> list[DoctorCheck]:
    checks = []
    node = _run_version(["node", "--version"])
    checks.append(
        DoctorCheck(
            name="Node.js",
            status="ok" if node else "warn",
            detail=node or "não encontrado — KTX2/meshopt via npx ficam offline",
            fix="docs/INSTALLING.md (nodesource/winget)",
        )
    )
    bun = _run_version(["bun", "--version"])
    checks.append(
        DoctorCheck(
            name="Bun",
            status="ok" if bun else "warn",
            detail=bun or "não encontrado — projetos do dream não arranjam (bun install/dev)",
            fix="curl -fsSL https://bun.sh/install | bash",
        )
    )
    return checks


def ollama_reachable() -> bool:
    host = os.environ.get("OLLAMA_HOST") or "http://localhost:11434"
    if "://" not in host:
        host = f"http://{host}"
    try:
        urllib.request.urlopen(host, timeout=1.5)
    except (OSError, urllib.error.URLError, ValueError):
        return False
    return True


def check_dream_llm() -> list[DoctorCheck]:
    """Provider LLM do ``gameassets dream`` (sem chave degrada para fallback)."""
    if os.environ.get("OPENAI_API_KEY"):
        provider = DoctorCheck(name="LLM do dream", status="ok", detail="provider openai (OPENAI_API_KEY)")
    elif ollama_reachable():
        provider = DoctorCheck(
            name="LLM do dream",
            status="ok",
            detail="provider ollama local (responde em OLLAMA_HOST)",
        )
    else:
        provider = DoctorCheck(
            name="LLM do dream",
            status="warn",
            detail="nenhum provider — o dream gera planos fallback por keywords",
            fix="export OPENAI_API_KEY=…   ou instala ollama (--llm-provider ollama)",
        )
    hf = DoctorCheck(
        name="Hugging Face token",
        status="ok" if os.environ.get("HF_TOKEN") else "warn",
        detail=(
            "HF_TOKEN definido"
            if os.environ.get("HF_TOKEN")
            else "sem HF_TOKEN — modelos gated (text2sound/skymap2d) falham no download"
        ),
        fix="huggingface-cli login   (só se o plano usar modelos gated)",
    )
    return [provider, hf]


def check_disk() -> list[DoctorCheck]:
    try:
        free_gb = shutil.disk_usage(Path.home()).free / (1024**3)
    except OSError:
        return [DoctorCheck(name="Disco livre", status="ok", detail="indeterminado")]
    detail = f"{free_gb:.0f} GB livres em ~ (pesos de modelos: ~/.cache/huggingface)"
    status = "ok" if free_gb >= MIN_DISK_GB else "warn"
    return [DoctorCheck(name=f"Disco livre ≥ {MIN_DISK_GB} GB", status=status, detail=detail)]


def run_doctor() -> list[DoctorCheck]:
    """Corre todas as verificações (ordem estável de apresentação)."""
    checks: list[DoctorCheck] = []
    checks += check_tools()
    checks += check_vramd_gpu()
    checks += check_compression()
    checks += check_node_bun()
    checks += check_dream_llm()
    checks += check_disk()
    return checks


def doctor_payload(checks: list[DoctorCheck]) -> dict[str, Any]:
    ok = all(c.status != "fail" for c in checks)
    return {"ok": ok, "checks": [asdict(c) for c in checks]}


def render_doctor(checks: list[DoctorCheck]) -> None:
    """Imprime a tabela de verificações (rich) e o veredicto final."""
    from rich import box
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    console = Console()
    table = Table(title="[bold]gameassets doctor[/bold] — primeiro uso", box=box.ROUNDED)
    table.add_column("", width=2)
    table.add_column("Verificação", style="cyan", no_wrap=True)
    table.add_column("Detalhe")
    for check in checks:
        style = {"ok": "green", "warn": "yellow", "fail": "red"}[check.status]
        table.add_row(f"[{style}]{check.symbol}[/{style}]", check.name, check.detail)

    console.print(table)

    failing = [c for c in checks if c.status == "fail"]
    warnings = [c for c in checks if c.status == "warn"]
    if failing:
        console.print(Panel("[bold red]Corrige os itens ✗ e volta a correr:[/bold red]", border_style="red"))
        for check in failing:
            console.print(f"  ✗ {check.name}: {check.fix or '(ver detalhe acima)'}")
    else:
        if warnings:
            console.print(f"[yellow]{len(warnings)} aviso(s)[/yellow] — o fluxo principal funciona sem estes.")
        console.print(
            Panel(
                "[bold green]READY[/bold green]  gameassets dream "
                '"A dark fantasy RPG with skeletons and treasure chests" --dry-run',
                border_style="green",
            )
        )
