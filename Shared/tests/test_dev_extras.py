"""Dev-extras installer hook — every package must get pytest in its own venv."""

from __future__ import annotations

from pathlib import Path
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


class TestToolsYamlWiring:
    def test_every_python_tool_has_an_install_hook(self) -> None:
        """A Python tool without a hook silently ends up with a venv lacking pytest."""
        import yaml

        root = Path(__file__).resolve().parents[2]
        tools = yaml.safe_load((root / "tools.yaml").read_text(encoding="utf-8"))["tools"]
        missing = [
            name
            for name, spec in tools.items()
            if spec.get("kind") == "python" and not (spec.get("post_install") or spec.get("custom_install"))
        ]
        assert missing == []
