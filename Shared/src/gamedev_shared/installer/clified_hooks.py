"""Hooks de instalação GameDev para tools.yaml do Clified."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clified.installer.python_installer import PythonProjectInstaller


def text2sound_custom_install(installer: PythonProjectInstaller) -> bool:
    from .text2sound_extras import text2sound_install_in_venv

    text2sound_install_in_venv(installer)
    return True


def text3d_post_install(installer: PythonProjectInstaller) -> bool:
    from .text3d_extras import Text3DPostInstall

    Text3DPostInstall(installer).run()
    return True


def rigging3d_post_install(installer: PythonProjectInstaller) -> bool:
    from .rigging_inference import install_rigging_inference_extras

    return install_rigging_inference_extras(
        venv_python=installer.venv_python,
        project_root=installer.project_root,
        logger=installer.logger,
    )


def part3d_post_install(installer: PythonProjectInstaller) -> bool:
    from .part3d_extras import (
        _ensure_part3d_dit_quantized,
        ensure_part3d_torch_geometric_extras,
        show_part3d_install_summary,
    )

    if not ensure_part3d_torch_geometric_extras(
        installer.venv_python, installer.logger
    ):
        return False
    _ensure_part3d_dit_quantized(installer)
    show_part3d_install_summary(installer)
    return True


def paint3d_post_install(installer: PythonProjectInstaller) -> bool:
    from clified.hooks.pytorch import install_nvdiffrast

    if not install_nvdiffrast(installer):
        return False
    from .paint3d_extras import run_paint3d_post_install

    return run_paint3d_post_install(installer)
