"""Post-install do Motion3D: compila o runtime C e pré-descarrega o GGUF.

O Motion3D agora é CPU-only: o "stack" é um runtime C11 vendorizado (compilado
para ``~/.cache/aigamekit/sprongmotion/``) e um GGUF de 33 MB. Nenhum dos dois
passos bloqueia a instalação — o CLI compila/descarrega no primeiro uso e
``motion3d doctor`` mostra o estado real.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

# Opt-out do pré-download do modelo (instalações offline/CI). O runtime continua
# a ser compilado: sem compilador, o passo falha com aviso e nada mais.
SKIP_MODEL_ENV = "AIGAMEKIT_NO_MODEL_DOWNLOAD"


def _run(project_root: Path, argv: list[str], *, timeout: int = 900) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=str(project_root), capture_output=True, text=True, timeout=timeout)


def build_sprongmotion_runtime(
    *,
    venv_python: str,
    project_root: Path,
    logger: Any,
) -> bool:
    """Compile the vendored C runtime into the cache (graceful on failure)."""
    python = str(venv_python)
    logger.info("Motion3D: a compilar o runtime C (SprongMotion)...")
    try:
        proc = _run(project_root, [python, "-m", "motion3d.sprong.runtime"], timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warn(f"Motion3D: não foi possível compilar o runtime ({exc}); corre `motion3d doctor --build`.")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        logger.warn(
            "Motion3D: runtime C não compilado "
            f"({detail or 'sem stderr'}). Instala um toolchain C11 ou aponta SPRONG_CC; "
            "`motion3d doctor --build` volta a tentar."
        )
        return False
    logger.info(f"Motion3D: runtime pronto ({(proc.stdout or '').strip().splitlines()[-1] if proc.stdout else 'ok'})")
    return True


def download_model(*, venv_python: str, project_root: Path, logger: Any) -> bool:
    """Pre-fetch the 33 MB GGUF (skipped by ``AIGAMEKIT_NO_MODEL_DOWNLOAD=1``)."""
    if os.environ.get(SKIP_MODEL_ENV, "").strip() not in ("", "0", "false", "False"):
        logger.info(f"Motion3D: pré-download do modelo saltado ({SKIP_MODEL_ENV}).")
        return True
    python = str(venv_python)
    logger.info("Motion3D: a obter o modelo (SegerEnd/SprongMotion-14M, ~33 MB)...")
    try:
        proc = _run(project_root, [python, "-m", "motion3d", "download-model"], timeout=1200)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warn(f"Motion3D: modelo não descarregado ({exc}); `motion3d download-model` volta a tentar.")
        return False
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        logger.warn(
            f"Motion3D: modelo não descarregado ({detail or 'sem stderr'}); `motion3d download-model` volta a tentar."
        )
        return False
    return True


def motion3d_install_in_venv(
    *,
    venv_python: str | Path,
    project_root: str | Path,
    logger: Any,
) -> bool:
    """Build the runtime and pre-fetch the model; never raises, never blocks."""
    root = Path(project_root).resolve()
    runtime_ok = build_sprongmotion_runtime(venv_python=str(venv_python), project_root=root, logger=logger)
    model_ok = download_model(venv_python=str(venv_python), project_root=root, logger=logger)
    return runtime_ok and model_ok
