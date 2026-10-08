#!/usr/bin/env python3
"""Pre-flight de pré-requisitos do AiGameKit (standalone, apenas stdlib).

Corre ANTES do bootstrap do clified (install.sh / install.ps1) e verifica as
dependências externas que o instalador não consegue auto-instalar. Quando falta
algo obrigatório, imprime um único bloco com todos os comandos prontos a copiar
(uma paragem, em vez de descobrir as falhas uma a uma a meio do install).

Bypass (agentes/CI): ``--force`` ou env ``AIGAMEKIT_PREFLIGHT=0``.

Os conjuntos de ferramentas por perfil espelham o ``case`` de ``install.sh`` e
o ``tools.yaml`` (estudo: docs/findings/TOOLKIT_CORE_PROFILE_STUDY.md) —
mantê-los sincronizados ao editar qualquer um deles.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"

REPO_DIR = Path(__file__).resolve().parent.parent

# --- Perfis (espelham install.sh) -------------------------------------------

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
ALL_EXTRA_TOOLS = ("part3d", "motion3d", "intrinsic")

PROFILES: dict[str, tuple[str, ...]] = {
    "core": CORE_TOOLS,
    "examples": CORE_TOOLS + EXAMPLES_EXTRA_TOOLS,
    "all": CORE_TOOLS + EXAMPLES_EXTRA_TOOLS + ALL_EXTRA_TOOLS,
}
PROFILE_DESCRIPTIONS = {
    "core": "zero-a-jogo mínimo (DAG GLB animado → browser)",
    "examples": "tudo que os jogos de exemplo usam (core + céu/áudio/texturas/terreno/rochas + Viber)",
    "all": "catálogo completo (examples + part3d, motion3d, intrinsic)",
}

# Tools cuja geração depende de GPU NVIDIA (aviso quando o driver não é detetado).
GPU_TOOLS = frozenset(
    {
        "text2d",
        "text3d",
        "paint3d",
        "rigging3d",
        "texture2d",
        "skymap2d",
        "text2sound",
        "terrain3d",
        "part3d",
        "motion3d",
        "intrinsic",
    }
)

# --- Limites -----------------------------------------------------------------

MIN_PYTHON = (3, 10)  # engine clified; o 3.13 das tools é provisionado pelo uv
MIN_NODE = (20, 12)  # rolldown do VibeGame (apt do Ubuntu 24.04 traz 18.x)
MIN_DISK_GB = 25  # venvs do perfil core ≈ 21 GB (sem contar pesos HF em ~/.cache)

NODE_FIX = (
    "curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs"
    if not IS_MACOS
    else "brew install node"
)
NODE_FIX_WINDOWS = "winget install OpenJS.NodeJS.LTS"
BUN_FIX = "curl -fsSL https://bun.sh/install | bash"
BUN_FIX_WINDOWS = 'powershell -c "irm bun.sh/install.ps1 | iex"'
CARGO_FIX = "curl -sSf https://sh.rustup.rs | sh"
CARGO_FIX_WINDOWS = "winget install Rustlang.Rustup"
UNZIP_FIX = "sudo apt install unzip"


@dataclass
class Check:
    """Resultado de um pré-requisito."""

    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    needed_for: str
    fix_linux: str = ""
    fix_windows: str = ""

    @property
    def symbol(self) -> str:
        return {"ok": "✓", "warn": "!", "fail": "✗"}.get(self.status, "?")


def _run(cmd: list[str], timeout: float = 10.0) -> str | None:
    """Corre um comando e devolve o stdout (strip) ou None se indisponível."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.stdout.strip() if proc.returncode == 0 else None


def _version_tuple(text: str) -> tuple[int, ...]:
    """ "v22.11.0" ou "22.11" → (22, 11, 0). Partes não numéricas são ignoradas."""
    digits: list[int] = []
    for part in text.lstrip("vV").split("."):
        if part.isdigit():
            digits.append(int(part))
        else:
            prefix = "".join(ch for ch in part if ch.isdigit())
            digits.append(int(prefix) if prefix else 0)
            break
    return tuple(digits) if digits else (0,)


def _which(name: str) -> str | None:
    return shutil.which(name)


def check_python() -> Check:
    ver = sys.version_info[:2]
    if ver < MIN_PYTHON:
        return Check(
            name=f"Python ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
            status="fail",
            detail=f"encontrado {ver[0]}.{ver[1]} — o instalador (clified) precisa de ≥ 3.10",
            needed_for="instalador (todas as ferramentas)",
            fix_linux="sudo apt install python3-full python3-pip python3-venv",
            fix_windows="winget install Python.Python.3.13",
        )
    note = ""
    if ver < (3, 13) and _which("uv") is None:
        note = " — as tools pedem 3.13; o uv será instalado e provisiona-o automaticamente"
    return Check(
        name=f"Python ≥ {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        status="ok",
        detail=f"{ver[0]}.{ver[1]}{note}",
        needed_for="instalador (todas as ferramentas)",
    )


def check_node(tool_set: frozenset[str]) -> Check:
    if "vibegame" not in tool_set and not ({"text3d", "gameassets"} & tool_set):
        return Check(name="Node.js", status="ok", detail="não necessário para este conjunto", needed_for="—")
    version = _run(["node", "--version"])
    needed = "vibegame (build) e compressão KTX2/meshopt (npx)"
    if version is None:
        status = "fail" if "vibegame" in tool_set else "warn"
        missing_detail = (
            "não encontrado" if "vibegame" in tool_set else "não encontrado — KTX2/meshopt via npx ficam offline"
        )
        return Check(
            name=f"Node.js ≥ {MIN_NODE[0]}.{MIN_NODE[1]}",
            status=status,
            detail=missing_detail,
            needed_for=needed,
            fix_linux=NODE_FIX,
            fix_windows=NODE_FIX_WINDOWS,
        )
    if _version_tuple(version) < MIN_NODE:
        return Check(
            name=f"Node.js ≥ {MIN_NODE[0]}.{MIN_NODE[1]}",
            status="fail" if "vibegame" in tool_set else "warn",
            detail=f"encontrado {version} — demasiado velho para o rolldown do VibeGame",
            needed_for=needed,
            fix_linux=NODE_FIX,
            fix_windows=NODE_FIX_WINDOWS,
        )
    return Check(
        name=f"Node.js ≥ {MIN_NODE[0]}.{MIN_NODE[1]}",
        status="ok",
        detail=version,
        needed_for=needed,
    )


def check_bun(tool_set: frozenset[str]) -> Check:
    if "vibegame" not in tool_set:
        return Check(name="Bun", status="ok", detail="não necessário para este conjunto", needed_for="—")
    version = _run(["bun", "--version"])
    if version is None:
        fixes: dict[str, str] = {"fix_linux": BUN_FIX, "fix_windows": BUN_FIX_WINDOWS}
        return Check(
            name="Bun",
            status="fail",
            detail="não encontrado (necessário para vibegame)",
            needed_for="vibegame",
            **fixes,
        )
    return Check(name="Bun", status="ok", detail=version, needed_for="vibegame")


def check_unzip(tool_set: frozenset[str]) -> Check:
    """O instalador oficial do Bun (Linux) precisa de unzip — só checar se o Bun falta."""
    if IS_WINDOWS or IS_MACOS or "vibegame" not in tool_set or _which("bun") is not None:
        return Check(name="unzip", status="ok", detail="não necessário", needed_for="—")
    if _which("unzip") is None:
        return Check(
            name="unzip",
            status="fail",
            detail="não encontrado — o instalador do Bun precisa dele",
            needed_for="instalar Bun (vibegame)",
            fix_linux=UNZIP_FIX,
        )
    return Check(name="unzip", status="ok", detail="presente", needed_for="instalar Bun (vibegame)")


def check_cargo(tool_set: frozenset[str]) -> Check:
    rust_tools = {"materialize", "viber"} & tool_set
    if not rust_tools:
        return Check(name="Rust/cargo", status="ok", detail="não necessário para este conjunto", needed_for="—")
    needed = ", ".join(sorted(rust_tools))
    version = _run(["cargo", "--version"])
    if version is None:
        return Check(
            name="Rust/cargo",
            status="fail",
            detail=f"não encontrado (necessário para {needed})",
            needed_for=needed,
            fix_linux=CARGO_FIX,
            fix_windows=CARGO_FIX_WINDOWS,
        )
    detail = version
    if not IS_WINDOWS and _which("cc") is None:
        return Check(
            name="Rust/cargo",
            status="warn",
            detail=f"{version} — mas falta um compilador C (build-essential)",
            needed_for=needed,
            fix_linux="sudo apt install build-essential",
        )
    return Check(name="Rust/cargo", status="ok", detail=detail, needed_for=needed)


def check_gpu(tool_set: frozenset[str]) -> Check:
    if not (GPU_TOOLS & tool_set):
        return Check(name="GPU NVIDIA", status="ok", detail="não necessário para este conjunto", needed_for="—")
    smi = _run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"])
    if smi is None:
        return Check(
            name="GPU NVIDIA",
            status="warn",
            detail="não detetada — as ferramentas GPU ficam lentas/CPU (o install continua)",
            needed_for="text2d/text3d/paint3d/…",
        )
    return Check(name="GPU NVIDIA", status="ok", detail=smi.splitlines()[0], needed_for="ferramentas GPU")


def _disk_free_gb(path: Path) -> float:
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return float("inf")
    return usage.free / (1024**3)


def check_disk(tool_set: frozenset[str]) -> Check:
    del tool_set  # o requisito de disco aplica-se a qualquer instalação
    repo_gb = _disk_free_gb(REPO_DIR)
    home_gb = _disk_free_gb(Path.home())
    detail = f"repo {repo_gb:.0f} GB livres"
    if home_gb != repo_gb:
        detail += f" · home {home_gb:.0f} GB"
    detail += " (venvs core ≈ 21 GB; pesos de modelos ficam em ~/.cache/huggingface)"
    if min(repo_gb, home_gb) < MIN_DISK_GB:
        return Check(name=f"Disco livre ≥ {MIN_DISK_GB} GB", status="warn", detail=detail, needed_for="venvs + modelos")
    return Check(name=f"Disco livre ≥ {MIN_DISK_GB} GB", status="ok", detail=detail, needed_for="venvs + modelos")


def check_7zip(tool_set: frozenset[str]) -> Check:
    if not IS_WINDOWS or "text3d" not in tool_set:
        return Check(name="7-Zip", status="ok", detail="não necessário", needed_for="—")
    if _which("7z") is None:
        return Check(
            name="7-Zip",
            status="warn",
            detail="não encontrado — KTX2 automático fica offline (instalar KTX-Software manualmente depois)",
            needed_for="KTX2 automático (text3d)",
            fix_windows="winget install 7zip.7zip",
        )
    return Check(name="7-Zip", status="ok", detail="presente", needed_for="KTX2 automático (text3d)")


def run_checks(tool_set: frozenset[str]) -> list[Check]:
    return [
        check_python(),
        check_node(tool_set),
        check_bun(tool_set),
        check_unzip(tool_set),
        check_cargo(tool_set),
        check_gpu(tool_set),
        check_7zip(tool_set),
        check_disk(tool_set),
    ]


# --- Apresentação -------------------------------------------------------------


def _ensure_utf8_stdout() -> None:
    with contextlib.suppress(AttributeError, OSError):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]


def print_checks(checks: list[Check]) -> None:
    for check in checks:
        print(f"  {check.symbol} {check.name:<28} {check.detail}")


def print_fix_block(checks: list[Check]) -> None:
    failing = [c for c in checks if c.status == "fail"]
    if not failing:
        return
    n = len(failing)
    plural = "s" if n > 1 else ""
    print()
    print(f"Faltam {n} pré-requisito{plural} obrigatório{plural}. Instala e volta a correr:")
    print()
    for check in failing:
        fix = check.fix_windows if IS_WINDOWS else check.fix_linux
        print(f"  # {check.name} — para {check.needed_for}")
        if fix:
            print(f"  {fix}")
        else:
            print("  (ver documentação em docs/INSTALLING.md)")
        print()
    print("Depois: ./install.sh   (Linux/macOS)   ·   .\\install.ps1   (Windows)")
    print("Bypass para agentes/CI: AIGAMEKIT_PREFLIGHT=0")


def list_profiles() -> None:
    print("Perfis de instalação (./install.sh <perfil> | .\\install.ps1 <perfil>):")
    print()
    for name in ("core", "examples", "all"):
        tools = PROFILES[name]
        print(f"  {name:<10} ({len(tools)} tools) — {PROFILE_DESCRIPTIONS[name]}")
        print(f"             {', '.join(tools)}")
    print()
    print("Ferramenta individual: ./install.sh <tool>   (chaves de tools.yaml)")
    print("Sem argumentos instala o perfil 'core'.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILES),
        default="core",
        help="conjunto de ferramentas a validar (default: core)",
    )
    parser.add_argument("--tools", default=None, help="lista de tools (vírgula) em vez de um perfil")
    parser.add_argument("--json", action="store_true", help="saída JSON para agentes")
    parser.add_argument(
        "--force",
        action="store_true",
        help="não falhar quando faltam pré-requisitos (como AIGAMEKIT_PREFLIGHT=0)",
    )
    parser.add_argument("--list-profiles", action="store_true", help="listar perfis e ferramentas")
    args = parser.parse_args(argv)

    if args.list_profiles:
        list_profiles()
        return 0

    if args.tools:
        tool_set = frozenset(t.strip() for t in args.tools.split(",") if t.strip())
    else:
        tool_set = frozenset(PROFILES[args.profile])

    bypass = args.force or os.environ.get("AIGAMEKIT_PREFLIGHT", "1") == "0"
    checks = run_checks(tool_set)
    ok = all(c.status != "fail" for c in checks)

    if args.json:
        payload = {
            "ok": ok,
            "bypassed": bypass and not ok,
            "tools": sorted(tool_set),
            "checks": [asdict(c) for c in checks],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if ok or bypass else 1

    _ensure_utf8_stdout()
    scope = ", ".join(sorted(tool_set)) if args.tools else f"perfil '{args.profile}'"
    print(f"Pré-requisitos ({scope}):")
    print_checks(checks)

    if not ok:
        print_fix_block(checks)
        if bypass:
            print("AVISO: a continuar apesar dos pré-requisitos em falta (--force/AIGAMEKIT_PREFLIGHT=0).")
            return 0
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
