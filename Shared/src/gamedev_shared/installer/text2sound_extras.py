"""Instalação Text2Sound: ``stable-audio-tools`` sem resolver pandas 2.0.2 nem flash-attn."""

from __future__ import annotations

import subprocess

from .python_installer import PythonProjectInstaller

# Wheel PyPI sem flash-attn; METADATA fixa pandas==2.0.2 (sem cp313) — instalamos
# --no-deps e depois requirements-stable-audio-deps.txt (sem pandas).
_STABLE_AUDIO_TOOLS = "stable-audio-tools==0.0.18"


def text2sound_install_in_venv(inst: PythonProjectInstaller) -> None:
    """Replica ``install_in_venv`` com passo extra para stable-audio-tools."""
    from .python_installer import _PIP_BOOTSTRAP

    inst.logger.step("Instalando no venv do projecto...")
    python = str(inst.venv_python)
    pip_cmd = inst._pip_install_cmd()
    _root = str(inst.project_root)

    if not inst._use_uv:
        subprocess.run(
            [python, "-m", "pip", "install", "--upgrade", *_PIP_BOOTSTRAP],
            check=True,
            cwd=_root,
        )

    shared_root = (inst.project_root.parent / "Shared").resolve()
    if (shared_root / "pyproject.toml").is_file():
        inst.logger.info(f"Sincronizando gamedev-shared: {shared_root}")
        subprocess.run([*pip_cmd, "-e", str(shared_root)], check=True, cwd=_root)

    if not inst.skip_pytorch:
        inst.install_pytorch(pip_cmd, cwd=inst.project_root)

    if inst.requirements_file.is_file():
        inst.logger.info(f"Instalando dependências: {inst.requirements_file}")
        subprocess.run([*pip_cmd, "-r", str(inst.requirements_file)], check=True, cwd=_root)
    else:
        inst.logger.warn(f"Ficheiro em falta: {inst.requirements_file}")

    sat_deps = inst.project_root / "config" / "requirements-stable-audio-deps.txt"
    inst.logger.info(f"{_STABLE_AUDIO_TOOLS} (--no-deps), depois dependências listadas...")
    subprocess.run([*pip_cmd, _STABLE_AUDIO_TOOLS, "--no-deps"], check=True, cwd=_root)
    if not sat_deps.is_file():
        msg = f"Ficheiro em falta: {sat_deps}"
        inst.logger.error(msg)
        raise RuntimeError(msg)
    subprocess.run([*pip_cmd, "-r", str(sat_deps)], check=True, cwd=_root)

    inst.logger.info("Instalando pacote em modo editável...")
    subprocess.run(
        [str(inst.venv_python), "-m", "pip", "install", "-e", str(inst.project_root), "--no-deps"],
        check=True,
        cwd=_root,
    )
    inst.logger.success("Instalado no venv")
