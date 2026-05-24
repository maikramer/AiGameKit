"""Instaladores e hooks do monorepo GameDev (via Clified)."""

from .base import (
    BaseInstaller,
    default_python_command,
    install_all_constraint_argv,
    path_env_contains_dir,
)
from .clified_hooks import (
    paint3d_post_install,
    part3d_post_install,
    rigging3d_post_install,
    text2sound_custom_install,
    text3d_post_install,
)
from .monorepo import find_monorepo_root, try_find_monorepo_root
from .unified import install_all, install_tool, list_available_tools, main

__all__ = [
    "BaseInstaller",
    "default_python_command",
    "find_monorepo_root",
    "install_all",
    "install_all_constraint_argv",
    "install_tool",
    "list_available_tools",
    "main",
    "paint3d_post_install",
    "part3d_post_install",
    "path_env_contains_dir",
    "rigging3d_post_install",
    "text2sound_custom_install",
    "text3d_post_install",
    "try_find_monorepo_root",
]
