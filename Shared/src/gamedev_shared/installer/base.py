"""BaseInstaller — lógica partilhada entre instaladores Python e Rust."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from ..logging import Logger


def path_env_contains_dir(path_env: str, bin_dir: Path, *, is_windows: bool) -> bool:
    """Indica se ``PATH`` (string ``;`` ou ``:`` separada) inclui ``bin_dir`` (comparação normalizada)."""
    sep = ";" if is_windows else ":"
    resolved = bin_dir.resolve()
    if is_windows:
        target = os.path.normcase(os.path.normpath(str(resolved)))
        for p in path_env.split(sep):
            if not p.strip():
                continue
            if os.path.normcase(os.path.normpath(p)) == target:
                return True
    else:
        target = os.path.normpath(str(resolved))
        for p in path_env.split(sep):
            if not p.strip():
                continue
            if os.path.normpath(p) == target:
                return True
    return False


def default_python_command() -> str:
    """Comando Python por defeito: ``PYTHON_CMD`` se definido; senão ``python`` no Windows e ``python3`` noutros."""
    env = os.environ.get("PYTHON_CMD", "").strip()
    if env:
        return env
    if platform.system().lower() == "windows":
        return "python"
    return "python3"


def has_uv() -> bool:
    """Devolve ``True`` se ``uv`` estiver disponível no PATH."""
    return shutil.which("uv") is not None


def uv_cmd() -> str:
    """Devolve o caminho absoluto do ``uv`` ou ``'uv'`` se não encontrado."""
    found = shutil.which("uv")
    return found if found else "uv"


class BaseInstaller:
    """Classe base para instaladores do monorepo GameDev.

    Fornece:
    - Detecção de plataforma
    - Verificação de Python
    - Detecção de sistema de pacotes Linux
    - Instalação de PyTorch (CUDA/CPU)
    - Criação de wrappers bash
    - Sumário de instalação
    """

    def __init__(
        self,
        *,
        project_name: str,
        cli_name: str,
        project_root: Path,
        install_prefix: Path | None = None,
        python_cmd: str = "python3",
    ) -> None:
        self.project_name = project_name
        self.cli_name = cli_name
        self.project_root = project_root.resolve()
        self.install_prefix = install_prefix or Path(os.environ.get("INSTALL_PREFIX", str(Path.home() / ".local")))
        self.python_cmd = os.environ.get("PYTHON_CMD", python_cmd)

        self.plat = platform.system().lower()
        self.is_windows = self.plat == "windows"
        self.is_macos = self.plat == "darwin"
        self.is_linux = self.plat == "linux"

        self.bin_dir = self._default_bin_dir()
        self.logger = Logger()

    def _default_bin_dir(self) -> Path:
        if self.is_windows:
            return Path(os.environ.get("USERPROFILE") or "C:\\") / "bin"
        return self.install_prefix / "bin"

    # ------------------------------------------------------------------
    # Verificação de Python
    # ------------------------------------------------------------------

    def check_python(self, min_version: tuple[int, int] = (3, 10)) -> bool:
        self.logger.step("Verificando Python...")
        try:
            result = subprocess.run(
                [self.python_cmd, "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
            version_str = (result.stdout or result.stderr or "").strip()
            self.logger.info(f"Python detectado: {version_str}")

            major, minor = min_version
            ok = subprocess.run(
                [
                    self.python_cmd,
                    "-c",
                    f"import sys; print('OK' if sys.version_info >= ({major}, {minor}) else 'FAIL')",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            if "OK" not in ok.stdout:
                self.logger.error(f"Python {major}.{minor}+ necessário")
                return False
            return True
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            self.logger.error(f"Python não encontrado: {e}")
            return False

    # ------------------------------------------------------------------
    # Dependências do sistema
    # ------------------------------------------------------------------

    def install_system_deps(self) -> None:
        self.logger.step("Dependências do sistema...")
        if shutil.which("apt-get"):
            self.logger.info("Detectado: Debian/Ubuntu")
            self.logger.warn("Opcional: sudo apt-get install python3-dev python3-venv git")
        elif shutil.which("dnf"):
            self.logger.info("Detectado: Fedora")
            self.logger.warn("Opcional: sudo dnf install python3-devel python3-pip git")
        elif shutil.which("pacman"):
            self.logger.info("Detectado: Arch Linux")
            self.logger.warn("Opcional: sudo pacman -S python python-pip git base-devel")
        elif self.is_windows:
            self.logger.info("Detectado: Windows")
            self.logger.warn(
                "Python: python.org ou ``winget install Python.Python.3.12``. "
                "CUDA: drivers NVIDIA + CUDA Toolkit (nvcc) para compilar extensões. "
                "Git for Windows (clones sparse opcionais, ex. Paint3D / rasterizador)."
            )
        else:
            self.logger.warn("SO não reconhecido — instala Python 3.10+, pip e git manualmente.")

    # ------------------------------------------------------------------
    # PyTorch
    # ------------------------------------------------------------------

    def _python_minor(self) -> int:
        try:
            out = subprocess.run(
                [self.python_cmd, "-c", "import sys; print(sys.version_info[1])"],
                capture_output=True,
                text=True,
                check=True,
            )
            return int((out.stdout or "").strip())
        except (ValueError, subprocess.CalledProcessError):
            return 10

    def install_pytorch(
        self,
        pip_cmd: list[str] | None = None,
        *,
        cwd: str | Path | None = None,
    ) -> None:
        """Instala PyTorch com CUDA (se disponível) ou CPU.

        ``cwd`` deve ser a raiz do projecto quando ``requirements.txt`` usa caminhos
        relativos (``file:../Shared``), para o pip resolver bem a partir do CWD.
        """
        if pip_cmd is None:
            pip_cmd = [self.python_cmd, "-m", "pip", "install"]

        _kw: dict = {}
        if cwd is not None:
            _kw["cwd"] = str(cwd)

        has_cuda = shutil.which("nvidia-smi") is not None
        py_minor = self._python_minor()

        if has_cuda:
            try:
                result = subprocess.run(["nvidia-smi"], capture_output=True, text=True)
                if "CUDA Version" in result.stdout:
                    for line in result.stdout.split("\n"):
                        if "CUDA Version" in line:
                            cuda_version = line.split("CUDA Version:")[1].split()[0]
                            self.logger.info(f"CUDA detectado: {cuda_version}")
                            cuda_major = int(cuda_version.split(".")[0])
                            cuda_minor = int(cuda_version.split(".")[1]) if "." in cuda_version else 0
                            if py_minor >= 13 or cuda_major >= 13:
                                self.logger.info(f"PyTorch via PyPI (CUDA {cuda_version})...")
                                subprocess.run([*pip_cmd, "torch", "torchvision"], check=True, **_kw)
                                return
                            if cuda_major == 12 and cuda_minor >= 6:
                                idx = "https://download.pytorch.org/whl/cu126"
                            elif cuda_version.startswith("12"):
                                idx = "https://download.pytorch.org/whl/cu121"
                            else:
                                idx = "https://download.pytorch.org/whl/cu118"
                            self.logger.info(f"PyTorch ({idx.split('/')[-1]})...")
                            subprocess.run(
                                [*pip_cmd, "torch", "torchvision", "--index-url", idx],
                                check=True,
                                **_kw,
                            )
                            return
                else:
                    # Driver/NVML em falta mas nvidia-smi existe — mesmo assim instalar CUDA wheels
                    self.logger.warn(
                        "nvidia-smi não mostrou 'CUDA Version' (p.ex. NVML/driver mismatch). "
                        "A instalar PyTorch com CUDA a partir do índice PyPI (cu130)..."
                    )
                    subprocess.run([*pip_cmd, "torch", "torchvision"], check=True, **_kw)
                    return
            except Exception:
                pass

        self.logger.warn("PyTorch CPU...")
        subprocess.run(
            [*pip_cmd, "torch", "torchvision", "--index-url", "https://download.pytorch.org/whl/cpu"],
            check=True,
            **_kw,
        )

    # ------------------------------------------------------------------
    # Wrappers
    # ------------------------------------------------------------------

    def create_wrapper(
        self,
        bin_name: str,
        *,
        python_path: str | None = None,
        module_name: str | None = None,
        target_binary: Path | None = None,
    ) -> Path:
        """Cria wrapper em ``bin_dir`` (``.cmd`` no Windows, bash nos outros).

        Modo Python (module_name)::
            "<python>" -m <module_name> [args]

        Modo binário (target_binary)::
            "<target_binary>" [args]
        """
        self.bin_dir.mkdir(parents=True, exist_ok=True)
        if self.is_windows:
            wrapper = self.bin_dir / f"{bin_name}.cmd"
            with open(wrapper, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\r\n")
                f.write(f"REM {self.project_name} — gerado por installer\r\n")
                if target_binary:
                    f.write(f'"{target_binary}" %*\r\n')
                else:
                    py = python_path or self.python_cmd
                    mod = module_name or self.cli_name
                    f.write(f'"{py}" -m {mod} %*\r\n')
            self.logger.success(str(wrapper))
            return wrapper

        wrapper = self.bin_dir / bin_name
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write(f"# {self.project_name} — gerado por installer\n")
            if target_binary:
                f.write(f'exec "{target_binary}" "$@"\n')
            else:
                py = python_path or self.python_cmd
                mod = module_name or self.cli_name
                f.write(f'exec "{py}" -m {mod} "$@"\n')
        wrapper.chmod(0o755)
        self.logger.success(str(wrapper))
        return wrapper

    # ------------------------------------------------------------------
    # PATH
    # ------------------------------------------------------------------

    def _ensure_windows_user_path(self) -> bool:
        """Adiciona ``bin_dir`` ao PATH permanente do utilizador (HKCU\\Environment)."""
        import ctypes
        import winreg

        bin_str = str(self.bin_dir.resolve())
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment", 0, winreg.KEY_READ | winreg.KEY_WRITE)
        try:
            try:
                path, typ = winreg.QueryValueEx(key, "Path")
            except FileNotFoundError:
                path = ""
                typ = winreg.REG_EXPAND_SZ

            parts = [p.strip() for p in path.split(";") if p.strip()]
            norm = lambda s: os.path.normcase(os.path.normpath(s))
            target = norm(bin_str)
            for p in parts:
                if norm(p) == target:
                    return True

            new_path = path.rstrip().rstrip(";")
            if new_path:
                new_path = new_path + ";" + bin_str
            else:
                new_path = bin_str
            winreg.SetValueEx(key, "Path", 0, typ, new_path)
        except OSError as e:
            self.logger.warn(f"Não foi possível actualizar o PATH do utilizador (registo): {e}")
            return False
        finally:
            winreg.CloseKey(key)

        try:
            HWND_BROADCAST = 0xFFFF
            WM_SETTINGCHANGE = 0x001A
            SMTO_ABORTIFHUNG = 0x0002
            result = ctypes.c_ulong()
            ctypes.windll.user32.SendMessageTimeoutW(
                HWND_BROADCAST,
                WM_SETTINGCHANGE,
                0,
                ctypes.c_wchar_p("Environment"),
                SMTO_ABORTIFHUNG,
                5000,
                ctypes.byref(result),
            )
        except Exception:
            pass

        return True

    def _ensure_unix_user_path(self) -> bool:
        """Acrescenta ``export PATH=...`` a ``~/.profile`` se ainda não estiver lá."""
        profile = Path.home() / ".profile"
        bin_str = str(self.bin_dir.resolve())
        marker = "# gamedev-install PATH"
        line = f'export PATH="{bin_str}:$PATH"'
        try:
            if profile.is_file():
                text = profile.read_text(encoding="utf-8")
                if marker in text and bin_str in text:
                    return True
            block = f"\n{marker}\n{line}\n"
            with open(profile, "a", encoding="utf-8") as f:
                f.write(block)
        except OSError as e:
            self.logger.warn(f"Não foi possível actualizar {profile}: {e}")
            return False

        return True

    def check_path(self) -> bool:
        """Garante que ``bin_dir`` está no PATH da sessão e, se possível, no PATH permanente do utilizador."""
        bin_str = str(self.bin_dir.resolve())
        sep = ";" if self.is_windows else ":"
        path_env = os.environ.get("PATH", "")

        if path_env_contains_dir(path_env, self.bin_dir, is_windows=self.is_windows):
            self.logger.success(f"{bin_str} está no PATH")
            return True

        persisted = False
        if self.is_windows:
            persisted = self._ensure_windows_user_path()
        else:
            persisted = self._ensure_unix_user_path()

        os.environ["PATH"] = bin_str + sep + path_env

        if persisted:
            self.logger.success(f"{bin_str} adicionado ao PATH permanente do utilizador; já activo nesta sessão.")
            if not self.is_windows:
                self.logger.info("Novo terminal: abre uma sessão nova ou executa: source ~/.profile")
        else:
            self.logger.warn(f"{bin_str} foi adicionado só ao PATH desta sessão")
            if not self.is_windows:
                self.logger.info(f'Adicione manualmente: export PATH="{bin_str}:$PATH"')
            else:
                self.logger.info(f"Adicione manualmente ao PATH do utilizador: {bin_str}")

        return persisted

    # ------------------------------------------------------------------
    # Sumário
    # ------------------------------------------------------------------

    def show_summary(self, commands: list[str], *, extras: list[str] | None = None) -> None:
        """Mostra resumo de instalação com Rich/ANSI."""
        lines = ["[bold]Comandos[/bold]" if self.logger.rich_available else "Comandos:"]
        for cmd in commands:
            if self.logger.rich_available:
                lines.append(f"  [cyan]{cmd}[/cyan]")
            else:
                lines.append(f"  {cmd}")

        if extras:
            lines.append("")
            lines.extend(extras)

        lines.append("")
        found = shutil.which(self.cli_name)
        if found:
            if self.logger.rich_available:
                lines.append(f"{self.cli_name} no PATH: [bold green]{found}[/bold green]")
            else:
                lines.append(f"✓ {self.cli_name} no PATH: {found}")
        else:
            if self.is_windows:
                hint = f'[yellow]Adiciona ao PATH (PowerShell):[/yellow] $env:Path += ";{self.bin_dir}"'
                if self.logger.rich_available:
                    lines.append(hint)
                else:
                    lines.append(f"Adicione ao PATH: {self.bin_dir}")
            elif self.logger.rich_available:
                lines.append(f'[yellow]Adiciona ao PATH:[/yellow] export PATH="{self.bin_dir}:$PATH"')
            else:
                lines.append(f'⚠ Adicione ao PATH: export PATH="{self.bin_dir}:$PATH"')

        self.logger.panel(
            "\n".join(lines),
            title=f"{self.project_name} — instalação concluída",
            border="green",
        )
