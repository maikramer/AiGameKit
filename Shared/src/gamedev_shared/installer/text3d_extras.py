"""Passos pós-venv do Text3D partilhados pelo instalador unificado e por ``Text3D/scripts/installer.py``."""

from __future__ import annotations

from pathlib import Path

from .python_installer import PythonProjectInstaller


class Text3DPostInstall:
    """nvdiffrast, config, wrappers e diretórios — após ``PythonProjectInstaller.run()`` base."""

    def __init__(
        self,
        installer: PythonProjectInstaller,
        *,
        skip_env_config: bool = False,
    ) -> None:
        self._i = installer
        self.skip_env_config = skip_env_config

    def run(self) -> None:
        self.setup_models()
        self.create_text3d_wrappers()
        self.setup_directories()
        if not self.skip_env_config:
            self.write_env_file()
        self._show_text3d_summary()

    def setup_models(self) -> None:
        log = self._i.logger
        log.step("Configurando modelos...")

        hf_cache = Path.home() / ".cache" / "huggingface"
        models_dir = self._i.project_root / "models"

        if not self._i.skip_models:
            log.info("Text2D (FLUX/SDNQ) e Hunyuan3D-2.1 vêm do Hugging Face na primeira execução.")
            log.info("Quantização SDNQ int4 do DiT é automática em runtime (sem passo extra).")
            log.info(f"Cache típico: {hf_cache}")
            log.info("Opcional: huggingface-cli login (modelos gated / quotas)")

        if models_dir.exists() and any(models_dir.iterdir()):
            log.info(f"Pasta local opcional (assets): {models_dir}")

        config_dir = Path.home() / ".config" / "text3d"
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.env"
        if not config_file.exists():
            with open(config_file, "w", encoding="utf-8") as f:
                f.write("# Text3D — gerado pelo instalador GameDev\n")
                f.write(f"TEXT3D_OUTPUT_DIR={Path.home() / '.text3d/outputs'}\n")
                if models_dir.exists():
                    f.write(f"TEXT3D_MODELS_DIR={models_dir}\n")
            log.info(f"Config criada: {config_file}")
        else:
            log.info(f"Config existente (mantida): {config_file}")

    def write_env_file(self) -> None:
        log = self._i.logger
        config_dir = Path.home() / ".config" / "text3d"
        config_dir.mkdir(parents=True, exist_ok=True)
        if self._i.is_windows:
            env_bat = config_dir / "env.bat"
            content_bat = (
                "@echo off\r\n"
                "REM Text3D — gerado pelo instalador GameDev\r\n"
                "REM Chama antes de text3d: call %USERPROFILE%\\.config\\text3d\\env.bat\r\n"
                "if not defined PYTORCH_CUDA_ALLOC_CONF set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True\r\n"
            )
            with open(env_bat, "w", encoding="utf-8", newline="\r\n") as f:
                f.write(content_bat)
            log.info(f"Ambiente opcional (cmd): {env_bat}")
        else:
            env_sh = config_dir / "env.sh"
            content = (
                "# Text3D — gerado pelo instalador GameDev\n"
                "# source ~/.config/text3d/env.sh\n"
                'export PYTORCH_CUDA_ALLOC_CONF="${PYTORCH_CUDA_ALLOC_CONF:-expandable_segments:True}"\n'
                "\n"
                "# Descomenta se precisares de CUDA_HOME explícito (PyTorch / drivers):\n"
                "# export CUDA_HOME=/usr/local/cuda-11.8\n"
            )
            with open(env_sh, "w", encoding="utf-8") as f:
                f.write(content)
            log.info(f"Ambiente opcional: {env_sh}")

    def create_text3d_wrappers(self) -> None:
        python_path = str(self._i.venv_python) if self._i.venv_exists else self._i.python_cmd
        self._i.bin_dir.mkdir(parents=True, exist_ok=True)

        if self._i.is_windows:
            w_cmd = self._i.bin_dir / "text3d.cmd"
            with open(w_cmd, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\r\n")
                f.write("REM Text3D — gerado por installer (Python do venv do projecto)\r\n")
                f.write(
                    "if not defined PYTORCH_CUDA_ALLOC_CONF set PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True\r\n"
                )
                f.write(f'"{python_path}" -m text3d %*\r\n')
            self._i.logger.success(str(w_cmd))

            w_gen = self._i.bin_dir / "text3d-generate.cmd"
            with open(w_gen, "w", encoding="utf-8", newline="\r\n") as f:
                f.write("@echo off\r\n")
                f.write(f'"{python_path}" -m text3d generate %*\r\n')
            self._i.logger.success(str(w_gen))
            self._i.create_activate_wrapper()
            return

        env_sh = Path.home() / ".config" / "text3d" / "env.sh"
        wrapper = self._i.bin_dir / "text3d"
        with open(wrapper, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write("# Text3D wrapper — gerado por installer\n")
            f.write(f'if [[ -f "{env_sh}" ]]; then\n')
            f.write("  # shellcheck source=/dev/null\n")
            f.write(f'  . "{env_sh}"\n')
            f.write("fi\n")
            f.write(f'exec "{python_path}" -m text3d "$@"\n')
        wrapper.chmod(0o755)
        self._i.logger.success(str(wrapper))

        wrapper_gen = self._i.bin_dir / "text3d-generate"
        with open(wrapper_gen, "w", encoding="utf-8") as f:
            f.write("#!/bin/bash\n")
            f.write(f'exec "{self._i.bin_dir}/text3d" generate "$@"\n')
        wrapper_gen.chmod(0o755)

        self._i.create_activate_wrapper()

    def setup_directories(self) -> None:
        output_dir = Path.home() / ".text3d" / "outputs"
        (output_dir / "meshes").mkdir(parents=True, exist_ok=True)
        (output_dir / "gifs").mkdir(parents=True, exist_ok=True)
        (output_dir / "images").mkdir(parents=True, exist_ok=True)
        self._i.logger.info(f"Diretórios de saída: {output_dir}")

    def _show_text3d_summary(self) -> None:
        log = self._i.logger
        self._i.show_summary(
            commands=[
                "text3d doctor",
                "text3d --help",
                "text3d generate 'um robô' -o robô.glb",
                "text3d generate 'carro' --preset hq -o carro.glb",
                "paint3d texture mesh.glb -i ref.png -o pintado.glb",
            ],
            extras=[
                "[dim]Text3D: só geometria (shape → repair → remesh). Textura: paint3d ou gameassets. "
                "Por defeito o instalador corre também Text2D (CLI em ~/.local/bin); o venv Text3D inclui "
                "`text2d` editável para import Python.[/dim]"
                if log.rich_available
                else (
                    "Text3D: só geometria. Textura: paint3d ou gameassets. "
                    "Instalador: Text2D dedicado + pacote text2d no venv Text3D."
                ),
                "[dim]Modelos: cache ~/.cache/huggingface[/dim]"
                if log.rich_available
                else "Modelos: cache ~/.cache/huggingface",
            ],
        )
