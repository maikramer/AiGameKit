"""Dev-extras installer hook — every package must get pytest in its own venv."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from aigamekit_shared.installer.dev_extras import (
    install_dev_extras_in_venv,
    read_dev_requirements,
)


class _Logger:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warns: list[str] = []

    def info(self, msg: str, **_: Any) -> None:
        self.infos.append(msg)

    def warn(self, msg: str, **_: Any) -> None:
        self.warns.append(msg)


def _write_pyproject(root: Path, body: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(body, encoding="utf-8")


class TestReadDevRequirements:
    def test_reads_dev_extra(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["pytest>=7", "ruff>=0.11"]\n',
        )
        assert read_dev_requirements(tmp_path) == ["pytest>=7", "ruff>=0.11"]

    def test_skips_local_and_url_specs(self, tmp_path: Path) -> None:
        """``pkg @ file:../Shared`` cannot be resolved without the absolutising step."""
        _write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.optional-dependencies]\n'
            'dev = ["pytest", "sibling @ file:../Shared", "clip @ git+https://example/c.git"]\n',
        )
        assert read_dev_requirements(tmp_path) == ["pytest"]

    def test_missing_pyproject(self, tmp_path: Path) -> None:
        assert read_dev_requirements(tmp_path) == []

    def test_missing_dev_extra(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, '[project]\nname = "x"\n')
        assert read_dev_requirements(tmp_path) == []

    def test_invalid_toml_is_not_fatal(self, tmp_path: Path) -> None:
        _write_pyproject(tmp_path, "[project\nname =")
        assert read_dev_requirements(tmp_path) == []


class TestInstallDevExtrasInVenv:
    def test_noop_without_dev_extra(self, tmp_path: Path) -> None:
        logger = _Logger()
        with patch("subprocess.run") as run:
            assert install_dev_extras_in_venv(venv_python="/tmp/py", project_root=tmp_path, logger=logger)
        run.assert_not_called()

    def test_installs_specs_with_uv(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["pytest"]\n',
        )
        logger = _Logger()
        with (
            patch("aigamekit_shared.installer.dev_extras.has_uv", return_value=True),
            patch("aigamekit_shared.installer.dev_extras.uv_cmd", return_value="uv"),
            patch("subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            assert install_dev_extras_in_venv(venv_python="/venv/bin/python", project_root=tmp_path, logger=logger)
        argv = run.call_args[0][0]
        assert argv[:5] == ["uv", "pip", "install", "--python", "/venv/bin/python"]
        assert argv[-1] == "pytest"

    def test_falls_back_to_pip(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["pytest"]\n',
        )
        logger = _Logger()
        with (
            patch("aigamekit_shared.installer.dev_extras.has_uv", return_value=False),
            patch("subprocess.run") as run,
        ):
            run.return_value.returncode = 0
            assert install_dev_extras_in_venv(venv_python="/venv/bin/python", project_root=tmp_path, logger=logger)
        assert run.call_args[0][0] == ["/venv/bin/python", "-m", "pip", "install", "pytest"]

    def test_failure_warns_without_raising(self, tmp_path: Path) -> None:
        _write_pyproject(
            tmp_path,
            '[project]\nname = "x"\n[project.optional-dependencies]\ndev = ["pytest"]\n',
        )
        logger = _Logger()
        with (
            patch("aigamekit_shared.installer.dev_extras.has_uv", return_value=False),
            patch("subprocess.run") as run,
        ):
            run.return_value.returncode = 1
            run.return_value.stderr = "boom"
            run.return_value.stdout = ""
            assert not install_dev_extras_in_venv(venv_python="/venv/bin/python", project_root=tmp_path, logger=logger)
        assert logger.warns and "boom" in logger.warns[0]


class TestHi3DExtrasRobustness:
    """Sondas do install Hi3D não podem rebentar nem pendurar o install.sh."""

    def test_module_probe_missing_python_is_false(self) -> None:
        from aigamekit_shared.installer import hi3d_extras

        assert hi3d_extras._module_ok("/nonexistent/hi3d-python", "torch") is False

    def test_run_timeout_warns(self, tmp_path: Path) -> None:
        import subprocess

        from aigamekit_shared.installer import hi3d_extras

        logger = _Logger()

        def _timeout(*_a: Any, **_k: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="pip", timeout=1)

        with patch("subprocess.run", side_effect=_timeout):
            assert hi3d_extras._run(["pip"], logger=logger, cwd=tmp_path, timeout=1) is False
        assert any("excedeu" in warn for warn in logger.warns)

    def test_run_missing_binary_warns(self, tmp_path: Path) -> None:
        from aigamekit_shared.installer import hi3d_extras

        logger = _Logger()
        with patch("subprocess.run", side_effect=FileNotFoundError("nope")):
            assert hi3d_extras._run(["nope"], logger=logger, cwd=tmp_path) is False
        assert any("falhou" in warn for warn in logger.warns)

    def test_torch_probe_checks_version_and_cuda(self) -> None:
        """Um torch CPU/outra CUDA não pode passar a idempotência."""
        from aigamekit_shared.installer import hi3d_extras

        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(returncode=1, stdout="", stderr=""),
        ) as run:
            assert hi3d_extras._torch_stack_ok("/venv/bin/python") is False
        code = run.call_args[0][0][-1]
        assert "torch.version.cuda" in code
        assert hi3d_extras.TORCH_VERSION in code

    def test_nvidia_probe_without_smi_is_false(self) -> None:
        from aigamekit_shared.installer import hi3d_extras

        with patch("aigamekit_shared.gpu._nvidia_smi_path", return_value=None):
            assert hi3d_extras._nvidia_driver_ok() is False

    def test_stack_check_includes_diffusers(self, tmp_path: Path) -> None:
        """diffusers é aplicado pelo stack (hooks de GO) — tem de ser verificado."""
        from aigamekit_shared.installer import hi3d_extras

        logger = _Logger()
        seen: list[str] = []

        def _module_ok(_venv: str, module: str, _attr: str = "__version__") -> bool:
            seen.append(module)
            return True

        with (
            patch.object(hi3d_extras, "_torch_stack_ok", return_value=True),
            patch.object(hi3d_extras, "_run", return_value=True),
            patch.object(hi3d_extras, "_module_ok", side_effect=_module_ok),
        ):
            assert hi3d_extras.install_hi3d_stack("/venv/bin/python", tmp_path, logger) is True
        assert "diffusers" in seen


class TestToolsYamlWiring:
    def test_every_python_tool_has_an_install_hook(self) -> None:
        """A Python tool without a hook silently ends up with a venv lacking pytest."""
        import yaml

        root = Path(__file__).resolve().parents[2]
        tools = yaml.safe_load((root / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        # ``vramd`` é um wrapper de um pacote PyPI sem testes no monorepo — não
        # precisa de hook (o hook existe para instalar o extra [dev]/pytest).
        missing = [
            name
            for name, spec in tools.items()
            if spec.get("kind") == "python"
            and name != "vramd"
            and not (spec.get("post_install") or spec.get("custom_install"))
        ]
        assert missing == []
