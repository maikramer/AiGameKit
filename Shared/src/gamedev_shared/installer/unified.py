"""Instalador unificado do monorepo GameDev.

Fornece ``install_tool`` e ``UnifiedInstaller`` — interface única para
instalar qualquer ferramenta (Python, Rust ou Bun/TypeScript) registada no registry.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from ..logging import Logger
from .bun_installer import BunProjectInstaller
from .python_installer import PythonProjectInstaller
from .registry import TOOLS, ToolKind, ToolSpec, find_monorepo_root, get_tool, list_available_tools
from .rust_installer import RustProjectInstaller


class _ToolPythonInstaller(PythonProjectInstaller):
    """Adaptador: instancia PythonProjectInstaller a partir de um ToolSpec."""

    def __init__(
        self,
        spec: ToolSpec,
        monorepo: Path,
        *,
        install_prefix: Path | None = None,
        python_cmd: str = "python3",
        use_venv: bool = False,
        skip_deps: bool = False,
        skip_models: bool = False,
        force: bool = False,
        skip_env_config: bool = False,
        text2d_venv_only: bool = False,
    ) -> None:
        super().__init__(
            project_name=spec.name,
            cli_name=spec.cli_name,
            project_root=spec.project_root(monorepo),
            install_prefix=install_prefix,
            python_cmd=python_cmd,
            use_venv=use_venv,
            skip_deps=skip_deps,
            skip_models=skip_models,
            force=force,
            skip_pytorch=not spec.needs_pytorch,
            min_python=spec.min_python,
        )
        self.spec = spec
        self.skip_env_config = skip_env_config
        self.text2d_venv_only = text2d_venv_only
        self._monorepo_root = monorepo

    def check_python(self, min_version: tuple[int, int] = (3, 10)) -> bool:
        if self._use_uv:
            self.logger.info(
                f"uv disponível — Python {self.spec.min_python[0]}.{self.spec.min_python[1]}+ "
                "será provisionado automaticamente ao criar o venv."
            )
            return True
        return super().check_python(min_version=self.spec.min_python)

    def install_in_venv(self) -> None:
        if self.spec.cli_name == "text2sound":
            from .text2sound_extras import text2sound_install_in_venv

            text2sound_install_in_venv(self)
            return
        if self.spec.cli_name == "text3d":
            text2d = self.project_root.parent / "Text2D"
            if not text2d.is_dir():
                self.logger.warn("Monorepo: espera-se Text2D ao lado de Text3D (ex.: GameDev/Text2D + GameDev/Text3D).")
        super().install_in_venv()

    def run(self) -> bool:
        if self.spec.cli_name == "text3d":
            text2d_dir = self._monorepo_root / "Text2D"
            if not text2d_dir.is_dir():
                self.logger.error(
                    "Text3D precisa da pasta Text2D no monorepo (ex.: GameDev/Text2D). Clone o repositório completo."
                )
                return False
            if not self.text2d_venv_only:
                self.logger.warn(
                    "Text2D é indispensável para text-to-3D: o Text3D importa o pacote `text2d` e gera a imagem 2D."
                )
                self.logger.step("Instalando Text2D primeiro (equivalente a ./install.sh text2d)...")
                if not install_tool(
                    "text2d",
                    monorepo=self._monorepo_root,
                    install_prefix=self.install_prefix,
                    python_cmd=self.python_cmd,
                    use_venv=self.use_venv,
                    skip_deps=self.skip_deps,
                    skip_models=self.skip_models,
                    force=self.force,
                    text2d_venv_only=False,
                ):
                    self.logger.error("A instalação do Text2D falhou; não é possível concluir o Text3D.")
                    return False
            else:
                self.logger.info(
                    "Opção --text2d-venv-only: a saltar a instalação dedicada do Text2D. "
                    "O pacote text2d será instalado apenas no venv do Text3D (requirements.txt). "
                    "Garante `text2d` no PATH (ex.: corre ./install.sh text2d) se precisares do CLI global."
                )

        if not super().run():
            return False

        if self.spec.cli_name == "rigging3d":
            from .rigging_inference import install_rigging_inference_extras

            if not install_rigging_inference_extras(
                venv_python=self.venv_python,
                project_root=self.project_root,
                logger=self.logger,
            ):
                return False

        if self.spec.cli_name == "text3d":
            from .text3d_extras import Text3DPostInstall

            Text3DPostInstall(self, skip_env_config=self.skip_env_config).run()
            return True

        if self.spec.cli_name == "part3d":
            from .part3d_extras import run_part3d_post_install

            return run_part3d_post_install(self)

        if self.spec.cli_name == "paint3d":
            if not _install_nvdiffrast(self.venv_python, self.project_root, self.logger):
                return False
            from .paint3d_extras import run_paint3d_post_install

            if not run_paint3d_post_install(self):
                return False

        aliases = list(self.spec.extra_aliases) if self.spec.extra_aliases else None
        self.create_cli_wrappers(extra_aliases=aliases)
        self.create_activate_wrapper()

        cmds = [
            f"{self.cli_name} --help",
            f"{self.cli_name} --version" if self.spec.kind == ToolKind.PYTHON else "",
        ]
        if self.spec.cli_name == "rigging3d":
            cmds = [
                f"{self.cli_name} --help",
                f"{self.cli_name} pipeline -i mesh.glb -o rigged.glb",
                f"{self.cli_name} --version",
            ]

        self.show_summary(commands=[c for c in cmds if c])
        return True


class _ToolRustInstaller(RustProjectInstaller):
    """Adaptador: instancia RustProjectInstaller a partir de um ToolSpec."""

    def __init__(
        self,
        spec: ToolSpec,
        monorepo: Path,
        *,
        install_prefix: Path | None = None,
    ) -> None:
        super().__init__(
            project_name=spec.name,
            cli_name=spec.cli_name,
            project_root=spec.project_root(monorepo),
            cargo_bin_name=spec.cargo_bin_name,
            install_prefix=install_prefix,
        )
        self.spec = spec


def _install_nvdiffrast(venv_python: Path, project_root: Path, logger: Logger) -> bool:
    """Instala nvdiffrast com --no-build-isolation (requer PyTorch pré-instalado no venv)."""
    import subprocess

    from .base import has_uv, uv_cmd

    logger.step("Instalando nvdiffrast (--no-build-isolation)...")
    try:
        if has_uv():
            cmd = [
                uv_cmd(),
                "pip",
                "install",
                "--python",
                str(venv_python),
                "git+https://github.com/NVlabs/nvdiffrast.git",
                "--no-build-isolation",
            ]
        else:
            cmd = [
                str(venv_python),
                "-m",
                "pip",
                "install",
                "git+https://github.com/NVlabs/nvdiffrast.git",
                "--no-build-isolation",
            ]
        subprocess.run(cmd, check=True, cwd=str(project_root))
        logger.success("nvdiffrast instalado")
        return True
    except subprocess.CalledProcessError as e:
        logger.error(f"Falha ao instalar nvdiffrast: {e}")
        logger.info(
            "Instala manualmente: .venv/bin/pip install "
            "git+https://github.com/NVlabs/nvdiffrast.git --no-build-isolation"
        )
        return False


def install_tool(
    name: str,
    *,
    monorepo: Path | None = None,
    action: str = "install",
    install_prefix: Path | None = None,
    python_cmd: str = "python3",
    use_venv: bool = False,
    skip_deps: bool = False,
    skip_models: bool = False,
    force: bool = False,
    skip_env_config: bool = False,
    text2d_venv_only: bool = False,
) -> bool:
    """Instala/desinstala/reinstala uma ferramenta pelo nome.

    Returns:
        True se a acção teve sucesso.
    """
    if monorepo is None:
        monorepo = find_monorepo_root()

    spec = get_tool(name)

    if not spec.exists(monorepo):
        Logger().error(f"Diretório não encontrado: {spec.project_root(monorepo)}")
        return False

    if spec.kind == ToolKind.PYTHON:
        inst: PythonProjectInstaller | RustProjectInstaller | BunProjectInstaller = _ToolPythonInstaller(
            spec,
            monorepo,
            install_prefix=install_prefix,
            python_cmd=python_cmd,
            use_venv=use_venv,
            skip_deps=skip_deps,
            skip_models=skip_models,
            force=force,
            skip_env_config=skip_env_config,
            text2d_venv_only=text2d_venv_only,
        )
    elif spec.kind == ToolKind.RUST:
        inst = _ToolRustInstaller(
            spec,
            monorepo,
            install_prefix=install_prefix,
        )
    elif spec.kind == ToolKind.BUN:
        inst = BunProjectInstaller(
            project_name=spec.name,
            cli_name=spec.cli_name,
            project_root=spec.project_root(monorepo),
            install_prefix=install_prefix,
        )
    else:
        Logger().error(f"Tipo de ferramenta não suportado: {spec.kind}")
        return False

    if action == "install":
        return inst.run()
    elif action == "uninstall":
        return inst.run_uninstall()
    elif action == "reinstall":
        return inst.run_reinstall()
    else:
        Logger().error(f"Acção desconhecida: {action}")
        return False


def install_all(
    *,
    monorepo: Path | None = None,
    install_prefix: Path | None = None,
    python_cmd: str = "python3",
    use_venv: bool = False,
    skip_deps: bool = False,
    skip_models: bool = False,
    force: bool = False,
    skip_env_config: bool = False,
    text2d_venv_only: bool = False,
) -> bool:
    """Instala todas as ferramentas disponíveis no monorepo."""
    if monorepo is None:
        monorepo = find_monorepo_root()

    logger = Logger()
    tools = list_available_tools(monorepo)
    if not tools:
        logger.error("Nenhuma ferramenta encontrada no monorepo.")
        return False

    logger.header(f"Instalando {len(tools)} ferramentas")
    results: dict[str, bool] = {}

    for spec in tools:
        logger.header(f"→ {spec.name}")
        try:
            ok = install_tool(
                spec.cli_name,
                monorepo=monorepo,
                install_prefix=install_prefix,
                python_cmd=python_cmd,
                use_venv=use_venv,
                skip_deps=skip_deps,
                skip_models=skip_models,
                force=force,
                skip_env_config=skip_env_config,
                text2d_venv_only=text2d_venv_only,
            )
        except Exception as exc:
            logger.error(f"{spec.name}: excepção não tratada — {exc}")
            ok = False
        results[spec.name] = ok

    logger.header("Resumo")
    for name, ok in results.items():
        status = "OK" if ok else "FALHOU"
        if ok:
            logger.success(f"{name}: {status}")
        else:
            logger.error(f"{name}: {status}")

    return all(results.values())


def main(argv: list[str] | None = None) -> int:
    """Ponto de entrada CLI: ``gamedev-install``."""
    monorepo = find_monorepo_root()
    available = list_available_tools(monorepo)
    tool_names = sorted(TOOLS.keys())

    parser = argparse.ArgumentParser(
        prog="gamedev-install",
        description=(
            "Instalador unificado do monorepo GameDev. "
            "Ferramentas Python: instalação em modo editável (pip install -e) por defeito. "
            "VibeGame: requer Bun e Node no PATH (bun install + build + wrapper vibegame)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_build_epilog(available),
    )

    parser.add_argument(
        "tool",
        nargs="?",
        default=None,
        help="Ferramenta a instalar (ou 'all' para todas). Opções: " + ", ".join([*tool_names, "all"]),
    )
    parser.add_argument(
        "--action",
        choices=["install", "uninstall", "reinstall"],
        default="install",
        help="Acção (default: install)",
    )
    parser.add_argument("--list", action="store_true", help="Listar ferramentas disponíveis")
    parser.add_argument(
        "--prefix",
        default=None,
        help="Prefixo de instalação (default: ~/.local)",
    )
    parser.add_argument(
        "--use-venv",
        action="store_true",
        help="(Legado / no-op.) O instalador cria ``projecto/.venv`` se não existir e instala sempre aí.",
    )
    parser.add_argument("--skip-deps", action="store_true", help="Não instalar deps de sistema")
    parser.add_argument("--skip-models", action="store_true", help="Não configurar modelos")
    parser.add_argument(
        "--skip-env-config",
        action="store_true",
        help="Text3D: não escrever ~/.config/text3d/env.sh (ou env.bat)",
    )
    parser.add_argument(
        "--text2d-venv-only",
        action="store_true",
        help=(
            "Só com text3d (ou all): não corre a instalação dedicada do Text2D antes; "
            "instala o pacote text2d apenas no venv do Text3D (modo editável via requirements)."
        ),
    )
    parser.add_argument("--force", action="store_true", help="Forçar reinstalação")
    parser.add_argument(
        "--python",
        default="python" if platform.system() == "Windows" else "python3",
        help="Comando Python (default: python no Windows, python3 nos outros)",
    )

    args = parser.parse_args(argv)
    logger = Logger()

    if args.list:
        _print_tool_list(available, logger)
        return 0

    if args.tool is None:
        parser.print_help()
        return 0

    prefix = Path(args.prefix) if args.prefix else None

    if args.tool.lower() == "all":
        ok = install_all(
            monorepo=monorepo,
            install_prefix=prefix,
            python_cmd=args.python,
            use_venv=args.use_venv,
            skip_deps=args.skip_deps,
            skip_models=args.skip_models,
            force=args.force,
            skip_env_config=args.skip_env_config,
            text2d_venv_only=args.text2d_venv_only,
        )
    else:
        ok = install_tool(
            args.tool,
            monorepo=monorepo,
            action=args.action,
            install_prefix=prefix,
            python_cmd=args.python,
            use_venv=args.use_venv,
            skip_deps=args.skip_deps,
            skip_models=args.skip_models,
            force=args.force,
            skip_env_config=args.skip_env_config,
            text2d_venv_only=args.text2d_venv_only,
        )

    return 0 if ok else 1


def _build_epilog(available: list[ToolSpec]) -> str:
    lines = [
        "Ferramentas disponíveis:",
        "",
    ]
    for spec in available:
        kind = (
            "Python"
            if spec.kind == ToolKind.PYTHON
            else "Rust"
            if spec.kind == ToolKind.RUST
            else "Bun"
            if spec.kind == ToolKind.BUN
            else spec.kind.value
        )
        lines.append(f"  {spec.cli_name:<14s}  [{kind}]  {spec.description}")
    lines.extend(
        [
            "",
            "Exemplos:",
            "  gamedev-install materialize               # Instalar Materialize (Rust)",
            "  gamedev-install vibegame                  # VibeGame: bun install + build + ~/.local/bin/vibegame",
            "  gamedev-install text2d                    # Text2D: venv + wrappers",
            "  gamedev-install text3d                    # text2d + Text3D (import + venv)",
            "  gamedev-install text3d --text2d-venv-only # Only text2d editable in venv",
            "  gamedev-install gameassets --skip-deps      # Install GameAssets (no sys deps)",
            "  gamedev-install materialize --action uninstall",
            "  gamedev-install all                        # Instalar tudo",
            "  gamedev-install --list                     # Listar ferramentas",
            "  gamedev-install part3d                     # Part3D: instala torch-scatter/cluster após PyTorch",
        ]
    )
    return "\n".join(lines)


def _print_tool_list(available: list[ToolSpec], logger: Logger) -> None:
    rows = []
    for spec in available:
        kind = (
            "Python"
            if spec.kind == ToolKind.PYTHON
            else "Rust"
            if spec.kind == ToolKind.RUST
            else "Bun"
            if spec.kind == ToolKind.BUN
            else spec.kind.value
        )
        rows.append((f"{spec.cli_name} [{kind}]", spec.description))
    logger.table(rows, title="Ferramentas GameDev disponíveis")


if __name__ == "__main__":
    sys.exit(main())
