"""Pós-instalação Paint3D: peso Real-ESRGAN + pré-quantização UNet (qint8)."""

from __future__ import annotations

import os
import subprocess
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from ..logging import Logger

if TYPE_CHECKING:
    from clified.installer.python_installer import PythonProjectInstaller

_REALESRGAN_URL = "https://github.com/xinntao/Real-ESRGAN/releases/download/v0.1.0/RealESRGAN_x4plus.pth"


def _ensure_realesrgan(monorepo_root: Path, logger: Logger) -> bool:
    """Garante ``RealESRGAN_x4plus.pth`` no directório de checkpoints vendored."""
    ckpt_dir = monorepo_root / "Paint3D" / "src" / "paint3d" / "hy3dpaint" / "ckpt"
    ckpt = ckpt_dir / "RealESRGAN_x4plus.pth"

    ckpt_dir.mkdir(parents=True, exist_ok=True)
    if not ckpt.is_file():
        logger.step(f"A descarregar Real-ESRGAN → {ckpt.name} ...")
        try:
            urllib.request.urlretrieve(_REALESRGAN_URL, ckpt)
            logger.success("RealESRGAN_x4plus.pth instalado")
        except OSError as e:
            logger.error(f"Falha ao descarregar Real-ESRGAN: {e}")
            logger.info(f"Descarrega manualmente:\n  {_REALESRGAN_URL}\n  → {ckpt}")
            return False
    else:
        logger.success("RealESRGAN_x4plus.pth já existe")
    return True


def _ensure_paint3d_unet_quantized(installer: PythonProjectInstaller) -> None:
    """Gera artefactos UNet qint8 no cache HF (opcional, após deps instaladas)."""
    if installer.skip_models:
        return
    env_skip = os.environ.get("PAINT3D_SKIP_UNET_QUANTIZE", "").strip().lower()
    if env_skip in ("1", "true", "yes", "on"):
        installer.logger.info("PAINT3D_SKIP_UNET_QUANTIZE ativo — a saltar pré-quantização UNet.")
        return
    installer.logger.step(
        "Paint3D: pré-quantização UNet (qint8, optimum-quanto) — 1ª execução pode demorar vários minutos..."
    )
    try:
        r = subprocess.run(
            [str(installer.venv_python), "-m", "paint3d.quantize_unet"],
            cwd=str(installer.project_root),
            timeout=7200,
            check=False,
        )
    except subprocess.TimeoutExpired:
        installer.logger.warn(
            "Pré-quantização UNet excedeu tempo limite. Corre manualmente: python -m paint3d.quantize_unet"
        )
        return
    except OSError as e:
        installer.logger.warn(f"Não foi possível executar pré-quantização UNet: {e}")
        return
    if r.returncode != 0:
        installer.logger.warn(
            "Pré-quantização UNet terminou com erro; o pipeline ainda funciona com UNet FP16 "
            "(ou corre manualmente no venv: python -m paint3d.quantize_unet)."
        )
    else:
        installer.logger.success("UNet qint8 gerado — GPUs < 10 GB VRAM usam-no automaticamente.")


def run_paint3d_post_install(installer: PythonProjectInstaller) -> bool:
    """Real-ESRGAN + pré-quantização UNet (fluxo completo após ``pip install``)."""
    monorepo = installer.project_root.parent
    if not _ensure_realesrgan(monorepo, installer.logger):
        return False
    _ensure_paint3d_unet_quantized(installer)
    return True
