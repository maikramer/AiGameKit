"""Hooks de instalação AiGameKit para tools.yaml do Clified."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from clified.installer.python_installer import PythonProjectInstaller


def dev_extras_post_install(installer: PythonProjectInstaller) -> bool:
    """Default Python post-install: put the ``[dev]`` extra in the tool's own venv.

    Every suite runs from ``<Tool>/.venv`` (``make test-<tool>``); without pytest
    there the Makefile silently falls back to the system interpreter.
    """
    from .dev_extras import install_dev_extras_hook

    return install_dev_extras_hook(installer)


def pip_check_post_install(installer: PythonProjectInstaller) -> bool:
    """``clified.hooks:pip_check`` plus the standard dev extras."""
    from clified.hooks import pip_check

    ok = bool(pip_check(installer))
    return dev_extras_post_install(installer) and ok


def text2sound_custom_install(installer: PythonProjectInstaller) -> bool:
    from .text2sound_extras import text2sound_install_in_venv

    text2sound_install_in_venv(installer)
    return dev_extras_post_install(installer)


def text3d_post_install(installer: PythonProjectInstaller) -> bool:
    from .text3d_extras import Text3DPostInstall

    Text3DPostInstall(installer).run()
    return dev_extras_post_install(installer)


def rigging3d_post_install(installer: PythonProjectInstaller) -> bool:
    from .rigging_inference import install_rigging_inference_extras

    ok = install_rigging_inference_extras(
        venv_python=installer.venv_python,
        project_root=installer.project_root,
        logger=installer.logger,
    )
    return dev_extras_post_install(installer) and ok


def paint3d_post_install(installer: PythonProjectInstaller) -> bool:
    from clified.hooks.pytorch import install_nvdiffrast

    if not install_nvdiffrast(installer):
        return False
    from .paint3d_extras import run_paint3d_post_install

    ok = run_paint3d_post_install(installer)
    return dev_extras_post_install(installer) and ok
