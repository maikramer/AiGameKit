"""Instalação do stack Hi3D (sparc3d) no venv 3.10 da tool.

O upstream fixa uma única stack medida — Python 3.10.12 / torch 2.5.0+cu124 /
CUDA 12.4 / flash-attn 2.7.4.post1 / spconv-cu120 2.3.6 — e não publica wheel no
PyPI. O `uv pip install -e .` do clified só instala o package `hi3d` (sem
dependências declaradas, de propósito: ver `Hi3D/pyproject.toml`); este módulo
faz o resto.

Tudo é não-interativo: falhas são reportadas no logger e devolvem ``False``
(o `./install.sh` continua e o refine fica em skip gracioso no pipeline).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from ..logging import Logger
from .base import has_uv, uv_cmd

#: Stack verificado pelo upstream (docker/constraints.txt do Hi3Dv1.0).
TORCH_VERSION = "2.5.0"
TORCHVISION_VERSION = "0.20.0"
TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu124"

FLASH_ATTN_VERSION = "2.7.4.post1"
FLASH_ATTN_WHEEL = (
    "https://github.com/Dao-AILab/flash-attention/releases/download/"
    "v2.7.4.post1/"
    "flash_attn-2.7.4.post1+cu12torch2.5cxx11abiFALSE-cp310-cp310-linux_x86_64.whl"
)

SPARC3D_GIT = "git+https://github.com/hi3d-official/Hi3Dv1.0.git"

#: Timeouts dos subprocessos. Sondagens (imports/metadata) não podem pendurar o
#: install; os pip installs incluem builds de ~30 min (flash-attn), daí o teto
#: folgado. Sem timeout um nvidia-smi/pip pendurado bloqueava o install.sh.
PROBE_TIMEOUT_SEC = 60.0
INSTALL_TIMEOUT_SEC = 3600.0

#: Dependências do sparc3d com as versões que o upstream mediu. `einops` e
#: `setuptools` não estão declarados lá mas são exigidos em runtime (CLIP e o
#: loader do Triton, respetivamente) — descobertos numa instalação limpa.
STACK_PACKAGES = (
    "numpy==1.26.4",
    "transformers==4.46.3",
    "accelerate>=0.31.0,<2",
    # Group offload do transformer (diffusers.hooks.apply_group_offloading).
    # 0.38 é a versão dos outros tools; os hooks são a API canónica.
    "diffusers==0.38.0",
    "spconv-cu120==2.3.6",
    "trimesh>=4.4.0,<5",
    "Pillow>=10.3.0",
    "click>=8.1.0",
    "rich>=13.0.0",
    "fast-simplification>=0.1.7",
    "safetensors>=0.4.3",
    "huggingface-hub>=0.28.0,<1",
    "pyfqmr>=0.4.0",
    "einops>=0.8.0",
    "setuptools>=69,<81",
)


def _pip_prefix(venv_python: str) -> list[str]:
    if has_uv():
        return [uv_cmd(), "pip", "install", "--python", venv_python]
    return [venv_python, "-m", "pip", "install"]


def _run(argv: list[str], *, logger: Logger, cwd: Path, timeout: float = INSTALL_TIMEOUT_SEC) -> bool:
    logger.info("$ " + " ".join(argv))
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        logger.warn(f"comando excedeu {timeout:.0f}s: {' '.join(argv[:3])}…")
        return False
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warn(f"comando falhou: {exc}")
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        logger.warn("saída:\n  " + "\n  ".join(tail))
        return False
    return True


def _probe(argv: list[str], *, timeout: float = PROBE_TIMEOUT_SEC) -> subprocess.CompletedProcess[str] | None:
    """Corre uma sonda curta; ``None`` em erro/timeout (nunca levanta)."""
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _module_ok(venv_python: str, module: str, attr: str = "__version__") -> bool:
    code = f"import {module} as m; print(getattr(m, '{attr}', 'ok'))"
    proc = _probe([venv_python, "-c", code])
    return proc is not None and proc.returncode == 0


def _torch_stack_ok(venv_python: str) -> bool:
    """Torch instalado É o pin do upstream (versão + CUDA 12.4)?

    Só ``import torch`` não chega para idempotência: um torch CPU/cu121 de
    outra tool no mesmo venv passa o import mas o flash-attn cu12/torch2.5
    falha no ``import`` em runtime. Nesse caso reinstalamos o wheel medido.
    """
    code = (
        "import sys, torch;"
        "v = torch.__version__.split('+')[0];"
        "c = torch.version.cuda or '';"
        f"sys.exit(0 if (v == '{TORCH_VERSION}' and c.startswith('12.4')) else 1)"
    )
    proc = _probe([venv_python, "-c", code])
    return proc is not None and proc.returncode == 0


def _nvidia_driver_ok() -> bool:
    # Usa o resolvedor do gpu.py (inclui System32/NVSMI no Windows; antes um
    # nvidia-smi fora do PATH dava falso negativo).
    from ..gpu import _nvidia_smi_path

    smi = _nvidia_smi_path()
    if not smi:
        return False
    proc = _probe([smi, "--query-gpu=name", "--format=csv,noheader"])
    return proc is not None and proc.returncode == 0 and bool((proc.stdout or "").strip())


def install_hi3d_stack(venv_python: str, project_root: Path, logger: Logger) -> bool:
    """Instala torch cu124, kernels, sparc3d e o package local.

    Returns:
        ``True`` quando os quatro módulos do upstream importam no venv.
    """
    if sys.version_info[:2] != (3, 10):
        # Aviso informativo: o hook corre no interpretador do installer, não no venv.
        logger.info("Hi3D: venv alvo deve ser Python 3.10 (sparc3d exige >=3.10,<3.11)")

    pip = _pip_prefix(venv_python)

    if _torch_stack_ok(venv_python):
        logger.info("Hi3D: torch cu124 já instalado")
    else:
        if not _nvidia_driver_ok():
            logger.warn("Hi3D: nenhuma GPU NVIDIA visível — a instalar wheels CUDA mesmo assim (CPU não é suportado)")
        ok = _run(
            [
                *pip,
                f"torch=={TORCH_VERSION}+cu124",
                f"torchvision=={TORCHVISION_VERSION}+cu124",
                "--index-url",
                TORCH_CUDA_INDEX,
            ],
            logger=logger,
            cwd=project_root,
        )
        if not ok:
            logger.warn("Hi3D: falha ao instalar torch cu124")
            return False

    if not _run([*pip, *STACK_PACKAGES], logger=logger, cwd=project_root):
        logger.warn("Hi3D: falha ao instalar dependências do sparc3d")
        return False

    if not _module_ok(venv_python, "flash_attn"):
        installed = _run([*pip, "--no-deps", FLASH_ATTN_WHEEL], logger=logger, cwd=project_root)
        if not installed:
            logger.warn(
                "Hi3D: wheel pré-compilado do flash-attn indisponível; a tentar build (exige nvcc 12.4 e ~30 min)"
            )
            installed = _run(
                [*pip, f"flash-attn=={FLASH_ATTN_VERSION}", "--no-build-isolation"],
                logger=logger,
                cwd=project_root,
            )
        if not installed:
            logger.warn("Hi3D: flash-attn não instalado — o refine fica indisponível")
            return False

    if not _module_ok(venv_python, "sparc3d") and not _run(
        [*pip, "--no-deps", f"sparc3d @ {SPARC3D_GIT}"], logger=logger, cwd=project_root
    ):
        logger.warn("Hi3D: falha ao instalar sparc3d a partir do git")
        return False

    missing = [m for m in ("torch", "flash_attn", "spconv", "sparc3d", "diffusers") if not _module_ok(venv_python, m)]
    if missing:
        logger.warn("Hi3D: módulos em falta após instalação: " + ", ".join(missing))
        return False

    logger.info(
        "Hi3D: stack OK. Pesos (~7.7 GB) não são descarregados pelo install — corre "
        "`hi3d download-model` quando quiseres usar o refine."
    )
    return True


def hi3d_install_in_venv(venv_python: str, project_root: Path, logger: Logger) -> bool:
    """Instala o package local (editável) e o stack do upstream.

    Returns:
        ``True`` quando o package e os quatro módulos do upstream importam.
    """
    os.environ.setdefault("UV_LINK_MODE", "copy")
    pip = _pip_prefix(venv_python)
    ok_local = _run(
        [*pip, "--no-deps", "-e", str(project_root)],
        logger=logger,
        cwd=project_root,
    )
    stack_ok = install_hi3d_stack(venv_python, project_root, logger)
    return bool(ok_local and stack_ok)
