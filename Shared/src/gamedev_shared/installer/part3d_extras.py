"""Extras PyG (torch-scatter, torch-cluster) — Part3D após venv.

O X-Part (conditioner Hunyuan3D-Part) importa ``torch_scatter`` e ``torch_cluster``.
Não vêm como wheels estáveis em todos os Python/CUDA; o instalador universal
garante que existem no venv **depois** do PyTorch, com ``--no-build-isolation``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..logging import Logger
from .base import has_uv, uv_cmd
from .python_installer import PythonProjectInstaller

# Nome pip → módulo Python
_PART3D_PYG_PACKAGES: tuple[tuple[str, str], ...] = (
    ("torch-scatter", "torch_scatter"),
    ("torch-cluster", "torch_cluster"),
)


def _import_ok(venv_python: Path, import_name: str) -> bool:
    try:
        subprocess.run(
            [str(venv_python), "-c", f"import {import_name}"],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def ensure_part3d_torch_geometric_extras(venv_python: Path, logger: Logger) -> bool:
    """
    Garante ``torch_scatter`` e ``torch_cluster`` no venv (obrigatório para o pipeline completo).

    Returns:
        True se ambos ficam importáveis após o passo.
    """
    logger.step("Extras PyG para X-Part (torch-scatter, torch-cluster)...")
    python = str(venv_python)

    for pip_name, mod_name in _PART3D_PYG_PACKAGES:
        if _import_ok(venv_python, mod_name):
            logger.success(f"  {pip_name} — já instalado (import {mod_name} OK).")
            continue

        logger.info(f"  A instalar {pip_name}… (pode compilar vários minutos; não cancele o pip.)")
        try:
            if has_uv():
                cmd = [uv_cmd(), "pip", "install", "--python", python, pip_name, "--no-build-isolation"]
            else:
                cmd = [python, "-m", "pip", "install", pip_name, "--no-build-isolation"]
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"  pip falhou ao instalar {pip_name}: {e}")
            logger.info(f"  Tente manualmente: {python} -m pip install {pip_name} --no-build-isolation")
            return False

        if not _import_ok(venv_python, mod_name):
            logger.error(
                f"  {pip_name} instalou mas import {mod_name} falhou. Verifique CUDA Toolkit / versão do PyTorch."
            )
            return False
        logger.success(f"  {pip_name} instalado e importável.")

    return True


def show_part3d_install_summary(installer: PythonProjectInstaller) -> None:
    log = installer.logger
    installer.show_summary(
        commands=[
            "part3d --help",
            "part3d decompose mesh.glb -o partes.glb -v",
            "part3d decompose mesh.glb --segment-only -v",
            "part3d --version",
        ],
        extras=[
            "[dim]PyG: torch-scatter + torch-cluster instalados pelo instalador (após PyTorch).[/dim]"
            if log.rich_available
            else "PyG: torch-scatter + torch-cluster instalados pelo instalador (após PyTorch).",
            "[dim]Pipeline: P3-SAM (segmentação) → X-Part (geração de partes)[/dim]"
            if log.rich_available
            else "Pipeline: P3-SAM (segmentação) → X-Part (geração de partes)",
            "[dim]Modelos: cache ~/.cache/huggingface (tencent/Hunyuan3D-Part)[/dim]"
            if log.rich_available
            else "Modelos: cache ~/.cache/huggingface (tencent/Hunyuan3D-Part)",
            "[dim]VRAM: ~5 GB pico com CPU offloading (FP16)[/dim]"
            if log.rich_available
            else "VRAM: ~5 GB pico com CPU offloading (FP16)",
            "[dim]DiT qint8: instalador gera model-dit-qint8.* (optimum-quanto); "
            "GPUs < 8 GB usam automaticamente. PART3D_SKIP_DIT_QUANTIZE=1 para saltar.[/dim]"
            if log.rich_available
            else (
                "DiT qint8: instalador gera model-dit-qint8.* (optimum-quanto); "
                "GPUs < 8 GB usam automaticamente. PART3D_SKIP_DIT_QUANTIZE=1 para saltar."
            ),
        ],
    )


def _ensure_part3d_dit_quantized(installer: PythonProjectInstaller) -> None:
    """Gera artefactos DiT qint8 no cache HF (opcional, após deps instaladas)."""
    if installer.skip_models:
        return
    env_skip = os.environ.get("PART3D_SKIP_DIT_QUANTIZE", "").strip().lower()
    if env_skip in ("1", "true", "yes", "on"):
        installer.logger.info("PART3D_SKIP_DIT_QUANTIZE ativo — a saltar pré-quantização DiT.")
        return
    installer.logger.step(
        "Part3D: pré-quantização DiT (qint8, optimum-quanto) — 1ª execução pode demorar vários minutos..."
    )
    try:
        r = subprocess.run(
            [str(installer.venv_python), "-m", "part3d.quantize_dit"],
            cwd=str(installer.project_root),
            timeout=7200,
            check=False,
        )
    except subprocess.TimeoutExpired:
        installer.logger.warn(
            "Pré-quantização DiT excedeu o tempo limite. Corre manualmente: python -m part3d.quantize_dit"
        )
        return
    except OSError as e:
        installer.logger.warn(f"Não foi possível executar pré-quantização DiT: {e}")
        return
    if r.returncode != 0:
        installer.logger.warn(
            "Pré-quantização DiT terminou com erro; o pipeline ainda funciona com DiT FP16 "
            "(ou corre manualmente no venv: python -m part3d.quantize_dit)."
        )
    else:
        installer.logger.success("DiT qint8 gerado — GPUs < 8 GB VRAM usam-no automaticamente.")


def run_part3d_post_install(installer: PythonProjectInstaller) -> bool:
    """Extras PyG + wrappers + resumo (fluxo completo após ``pip install -e``)."""
    if not ensure_part3d_torch_geometric_extras(installer.venv_python, installer.logger):
        return False
    _ensure_part3d_dit_quantized(installer)
    installer.create_cli_wrappers(extra_aliases=["part3d-decompose"])
    installer.create_activate_wrapper()
    show_part3d_install_summary(installer)
    return True
