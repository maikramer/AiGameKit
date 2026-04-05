"""BunProjectInstaller — VibeGame (bun install + build + wrapper ``vibegame``)."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from .base import BaseInstaller


class BunProjectInstaller(BaseInstaller):
    """Instala projecto TypeScript/Bun: ``bun install``, ``bun run build``, wrapper Node para o CLI."""

    def __init__(
        self,
        *,
        project_name: str,
        cli_name: str,
        project_root: Path,
        install_prefix: Path | None = None,
    ) -> None:
        super().__init__(
            project_name=project_name,
            cli_name=cli_name,
            project_root=project_root,
            install_prefix=install_prefix,
        )
        self.cli_script = self.project_root / "scripts" / "vibegame-cli.mjs"

    def check_bun(self) -> bool:
        self.logger.step("Verificando Bun...")
        bun = shutil.which("bun")
        if not bun:
            self.logger.error(
                "Bun não encontrado no PATH. Instale: https://bun.sh (ex.: curl -fsSL https://bun.sh/install | bash)"
            )
            return False
        try:
            out = subprocess.run(
                [bun, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=10,
            )
            self.logger.success(f"Bun: {(out.stdout or out.stderr or '').strip()}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.logger.error(f"Bun não executável: {e}")
            return False

    def check_node(self) -> bool:
        """O wrapper CLI usa ``node`` (script .mjs)."""
        self.logger.step("Verificando Node.js...")
        node = shutil.which("node")
        if not node:
            self.logger.error(
                "Node.js não encontrado no PATH. O comando `vibegame` precisa de `node` (https://nodejs.org/)."
            )
            return False
        try:
            out = subprocess.run(
                [node, "--version"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            self.logger.success(f"Node: {(out.stdout or out.stderr or '').strip()}")
            return True
        except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired) as e:
            self.logger.error(f"Node não executável: {e}")
            return False

    def install_dependencies(self) -> bool:
        self.logger.header("bun install (frozen lockfile)")
        bun = shutil.which("bun")
        assert bun is not None
        try:
            subprocess.run(
                [bun, "install", "--frozen-lockfile"],
                cwd=self.project_root,
                check=True,
                timeout=600,
            )
            self.logger.success("Dependências instaladas")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"bun install falhou: {e}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error("bun install: timeout")
            return False

    def build_project(self) -> bool:
        self.logger.header("bun run build")
        bun = shutil.which("bun")
        assert bun is not None
        try:
            subprocess.run(
                [bun, "run", "build"],
                cwd=self.project_root,
                check=True,
                timeout=900,
            )
            self.logger.success("Build concluído")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"bun run build falhou: {e}")
            return False
        except subprocess.TimeoutExpired:
            self.logger.error("bun run build: timeout")
            return False

    def install_wrapper(self) -> bool:
        if not self.cli_script.is_file():
            self.logger.error(f"Script CLI em falta: {self.cli_script}")
            return False
        node = shutil.which("node")
        assert node is not None
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        script_abs = str(self.cli_script.resolve())

        if self.is_windows:
            wrapper = self.bin_dir / f"{self.cli_name}.cmd"
            with open(wrapper, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\r\n")
                f.write(f"REM {self.project_name} — gerado por gamedev-install\r\n")
                f.write(f'"{node}" "{script_abs}" %*\r\n')
            self.logger.success(str(wrapper))
            return True

        wrapper = self.bin_dir / self.cli_name
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\n")
            f.write(f"# {self.project_name} — gerado por gamedev-install\n")
            f.write(f'exec "{node}" "{script_abs}" "$@"\n')
        wrapper.chmod(0o755)
        self.logger.success(str(wrapper))
        return True

    def test_installation(self) -> bool:
        self.logger.header("Testando instalação")
        w = self.bin_dir / (f"{self.cli_name}.cmd" if self.is_windows else self.cli_name)
        if not w.is_file():
            return False
        try:
            result = subprocess.run(
                [str(w), "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                self.logger.success(f"Versão: {(result.stdout or '').strip()}")
                return True
            self.logger.warn(f"vibegame --version retornou {result.returncode}")
            return True
        except Exception as e:
            self.logger.warn(f"Não foi possível testar: {e}")
            return True

    def uninstall(self) -> bool:
        self.logger.header(f"Desinstalando {self.project_name}")
        try:
            w = self.bin_dir / (f"{self.cli_name}.cmd" if self.is_windows else self.cli_name)
            if w.exists():
                w.unlink()
                self.logger.success(f"Removido: {w}")
            self.logger.success(f"{self.project_name} desinstalado.")
            return True
        except OSError as e:
            self.logger.error(f"Erro ao desinstalar: {e}")
            return False

    def run(self) -> bool:
        self.logger.header(f"{self.project_name} Installer (Bun)")
        self.logger.table(
            [
                ("Repositório", str(self.project_root)),
                ("Binários", str(self.bin_dir)),
            ],
            title=self.project_name,
        )

        if not self.check_bun():
            return False
        if not self.check_node():
            return False
        if not self.install_dependencies():
            return False
        if not self.build_project():
            return False
        if not self.install_wrapper():
            return False

        self.check_path()
        self.test_installation()

        self.show_summary(
            commands=[
                "vibegame create my-game",
                "vibegame --version",
                "vibegame help",
            ],
        )
        return True

    def run_uninstall(self) -> bool:
        return self.uninstall()

    def run_reinstall(self) -> bool:
        self.uninstall()
        return self.run()
