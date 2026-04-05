"""Extras de inferência Rigging3D (UniRig): ``[inference]``, PyTorch CUDA, spconv, PyG."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from ..logging import Logger
from .base import has_uv, uv_cmd

_TORCH_INFO_SCRIPT = """
import torch, sys
v = torch.__version__.split('+')[0]
parts = v.split('.')
short = f'{parts[0]}.{parts[1]}.0'
c = torch.version.cuda or ''
if c:
    p = c.split('.')
    tag = f'cu{p[0]}{p[1]}'
else:
    tag = 'cpu'
print(f'{short} {tag}')
"""

_SPCONV_MAP = {
    "cu130": "spconv-cu121",
    "cu128": "spconv-cu121",
    "cu126": "spconv-cu121",
    "cu124": "spconv-cu121",
    "cu121": "spconv-cu121",
    "cu118": "spconv-cu118",
}

# PyTorch nightlies / PyPI: driver recente sem NVML ainda pode usar CUDA
_DEFAULT_CUDA_INDEX = "https://download.pytorch.org/whl/cu130"


def _pip_list(python: str) -> list[str]:
    if has_uv():
        return [uv_cmd(), "pip", "install", "--python", python]
    return [python, "-m", "pip", "install"]


def ensure_cuda_torch(
    pip_cmd: list[str],
    *,
    cwd: Path,
    logger: Logger,
) -> None:
    """Garante ``torch`` com CUDA quando há GPU mas o instalador base instalou CPU (p.ex. NVML falhou)."""
    python = pip_cmd[0]

    check = subprocess.run(
        [python, "-c", "import torch; print(torch.version.cuda or '')"],
        capture_output=True,
        text=True,
        cwd=str(cwd),
    )
    cuda_ver = (check.stdout or "").strip()
    if cuda_ver:
        logger.info(f"PyTorch já com CUDA runtime: {cuda_ver}")
        return

    has_nvidia_smi = shutil.which("nvidia-smi") is not None
    force = os.environ.get("RIGGING3D_FORCE_CUDA", "").strip() in ("1", "true", "yes")
    index = os.environ.get("RIGGING3D_PYTORCH_CUDA_INDEX", "").strip() or _DEFAULT_CUDA_INDEX

    if not has_nvidia_smi and not force:
        logger.warn(
            "PyTorch sem CUDA e nenhuma GPU NVIDIA detectada (nvidia-smi). "
            "Inferência UniRig precisa de GPU + torch CUDA. "
            "Define RIGGING3D_FORCE_CUDA=1 para forçar wheels CUDA."
        )
        return

    logger.warn(
        "PyTorch instalado sem CUDA; GPU presente ou RIGGING3D_FORCE_CUDA=1 — "
        f"a reinstalar torch/torchvision de {index}"
    )
    try:
        subprocess.run(
            [*pip_cmd, "torch", "torchvision", "--index-url", index],
            check=True,
            cwd=str(cwd),
        )
    except subprocess.CalledProcessError as e:
        logger.error(f"Falha ao instalar PyTorch CUDA: {e}")
        raise


def install_rigging_inference_extras(
    *,
    venv_python: str | Path,
    project_root: Path,
    logger: Logger,
) -> bool:
    """Instala ``pip install -e .[inference]``, PyG wheels, spconv/cumm.

    Returns:
        True se passos principais concluíram (scatter/spconv podem falhar em combinações exóticas).
    """
    python = str(venv_python)
    pip_cmd = _pip_list(python)
    root = project_root.resolve()

    logger.step("Instalando rigging3d[inference]...")
    subprocess.run(
        [*pip_cmd, "-e", f"{root}[inference]"],
        check=True,
        cwd=str(root),
    )

    ensure_cuda_torch(pip_cmd, cwd=root, logger=logger)

    logger.step("Detectando torch/CUDA para deps nativas...")
    try:
        info = subprocess.run(
            [python, "-c", _TORCH_INFO_SCRIPT],
            capture_output=True,
            text=True,
            check=True,
        )
        torch_short, cuda_tag = info.stdout.strip().split()
    except (subprocess.CalledProcessError, ValueError) as e:
        logger.warn(f"Não foi possível detectar torch/CUDA ({e}) — deps nativas em falta")
        return False

    logger.info(f"torch={torch_short}  cuda_tag={cuda_tag}")

    scatter_url = f"https://data.pyg.org/whl/torch-{torch_short}+{cuda_tag}.html"
    logger.info(f"Instalando torch-scatter, torch-cluster de {scatter_url} ...")
    sc = subprocess.run(
        [*pip_cmd, "torch-scatter", "torch-cluster", "-f", scatter_url],
        cwd=str(root),
        capture_output=True,
        text=True,
    )
    if sc.returncode != 0:
        logger.warn(f"torch-scatter/cluster: pip exit {sc.returncode}")
        if sc.stderr:
            logger.warn(sc.stderr[-2000:])
        logger.warn("Tentativa alternativa: pip sem -f (compilação local pode demorar)...")
        subprocess.run([*pip_cmd, "torch-scatter", "torch-cluster"], cwd=str(root))

    spconv_pkg = _SPCONV_MAP.get(cuda_tag)
    if spconv_pkg:
        cumm_pkg = spconv_pkg.replace("spconv", "cumm")
        logger.info(f"Instalando {cumm_pkg}, {spconv_pkg}...")
        subprocess.run([*pip_cmd, cumm_pkg, spconv_pkg], cwd=str(root), check=False)
    else:
        logger.warn(f"Sem pacote spconv mapeado para {cuda_tag} — instala manualmente (ver README)")

    _verify_inference_imports(python, logger)
    return True


def _verify_inference_imports(python: str, logger: Logger) -> None:
    """Confirma imports críticos e avisa se faltar."""
    checks = [
        ("torch", "import torch"),
        ("spconv", "import spconv"),
        ("torch_scatter", "import torch_scatter"),
        ("torch_cluster", "import torch_cluster"),
        ("bpy", "import bpy"),
    ]
    failed: list[str] = []
    for name, stmt in checks:
        r = subprocess.run(
            [python, "-c", stmt],
            capture_output=True,
            text=True,
        )
        if r.returncode != 0:
            failed.append(name)
    if failed:
        logger.warn(f"Imports em falta após instalação: {', '.join(failed)} — ver mensagens pip acima.")
    else:
        logger.success("Imports verificados: torch, spconv, torch_scatter, torch_cluster, bpy")
