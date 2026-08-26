"""Instalação Text2Sound: ``stable-audio-tools`` sem resolver pandas 2.0.2 nem flash-attn."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from .base import install_all_constraint_argv, uv_cmd

if TYPE_CHECKING:
    from clified.installer.python_installer import PythonProjectInstaller

_PIP_BOOTSTRAP = ("pip", "setuptools>=68,<82", "wheel")

# Stable Audio 3 Small (t5gemma conditioner + sampler pingpong) exige o
# stable-audio-tools do GitHub — o 0.0.19 do PyPI não tem o T5GemmaConditioner.
# Pin por commit para instalação reprodutível.
_STABLE_AUDIO_TOOLS = "stable-audio-tools @ git+https://github.com/Stability-AI/stable-audio-tools.git@3241adba4fc2a85cf5b29d9eb68d42f40a28e820"


def text2sound_install_in_venv(inst: PythonProjectInstaller) -> None:
    """Replica ``install_in_venv`` com passo extra para stable-audio-tools."""
    inst.logger.step("Instalando no venv do projecto...")
    python = str(inst.venv_python)
    pip_cmd = inst._pip_install_cmd()
    constr = install_all_constraint_argv()
    _root = str(inst.project_root)

    if inst._use_uv:
        subprocess.run(
            [
                uv_cmd(),
                "pip",
                "install",
                "--python",
                python,
                "pip",
                "setuptools>=68,<82",
                "wheel",
            ],
            check=True,
            cwd=_root,
        )
    else:
        subprocess.run(
            [python, "-m", "pip", "install", "--upgrade", *_PIP_BOOTSTRAP],
            check=True,
            cwd=_root,
        )

    shared_root = (inst.project_root.parent / "Shared").resolve()
    if (shared_root / "pyproject.toml").is_file():
        inst.logger.info(f"Sincronizando aigamekit-shared: {shared_root}")
        subprocess.run([*pip_cmd, *constr, "-e", str(shared_root)], check=True, cwd=_root)

    if not inst.skip_pytorch:
        inst.install_pytorch(pip_cmd, cwd=inst.project_root)

    req_file = getattr(inst, "requirements_file", None)
    if req_file is not None and req_file.is_file():
        inst.logger.info(f"Instalando dependências: {req_file}")
        subprocess.run([*pip_cmd, *constr, "-r", str(req_file)], check=True, cwd=_root)
    elif req_file is not None:
        inst.logger.warn(f"Ficheiro em falta: {req_file}")

    sat_deps = inst.project_root / "config" / "requirements-stable-audio-deps.txt"
    inst.logger.info("stable-audio-tools GitHub (--no-deps), depois dependências listadas...")
    # pip (não uv): o main do GitHub pin Requires-Python <3.11 (stack de treino
    # deles) — em inferência Py3.13 funciona; uv não tem --ignore-requires-python.
    subprocess.run(
        [
            str(inst.venv_python),
            "-m",
            "pip",
            "install",
            *constr,
            "--no-deps",
            "--ignore-requires-python",
            _STABLE_AUDIO_TOOLS,
        ],
        check=True,
        cwd=_root,
    )
    if not sat_deps.is_file():
        msg = f"Ficheiro em falta: {sat_deps}"
        inst.logger.error(msg)
        raise RuntimeError(msg)
    subprocess.run([*pip_cmd, *constr, "-r", str(sat_deps)], check=True, cwd=_root)

    inst.logger.info("Instalando pacote em modo editável...")
    # O uv rejeita deps ``file:`` relativas (ex. ``aigamekit-shared @ file:../Shared``)
    # sem o mesmo patch que o clified aplica no install normal.
    from clified.installer.python_installer import _patched_relative_deps

    if inst._use_uv:
        with _patched_relative_deps(inst.project_root, inst.logger):
            subprocess.run(
                [*pip_cmd, *constr, "-e", str(inst.project_root), "--no-deps"],
                check=True,
                cwd=_root,
            )
    else:
        subprocess.run(
            [
                str(inst.venv_python),
                "-m",
                "pip",
                "install",
                *constr,
                "-e",
                str(inst.project_root),
                "--no-deps",
            ],
            check=True,
            cwd=_root,
        )
    inst.logger.success("Instalado no venv")
