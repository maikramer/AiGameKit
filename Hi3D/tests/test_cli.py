"""CLI ``hi3d`` (help, doctor, guards, worker) sem GPU."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from hi3d.cli import EXIT_UNAVAILABLE, cli


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestHelp:
    def test_root_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "refine" in result.output
        assert "doctor" in result.output

    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_refine_help(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["refine", "--help"])
        assert result.exit_code == 0
        assert "--resolution" in result.output
        assert "--target-faces" in result.output
        assert "--offload" in result.output

    def test_doctor_help(self, runner: CliRunner) -> None:
        assert runner.invoke(cli, ["doctor", "--help"]).exit_code == 0

    def test_download_help(self, runner: CliRunner) -> None:
        assert runner.invoke(cli, ["download-model", "--help"]).exit_code == 0

    def test_serve_requires_flag(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["serve"])
        assert result.exit_code == 2
        assert "--ums-worker" in result.output


class TestRefineGuards:
    def test_missing_coarse_mesh_is_click_error(self, runner: CliRunner, tmp_path: Path) -> None:
        img = tmp_path / "i.png"
        img.write_bytes(b"png")
        result = runner.invoke(
            cli, ["refine", str(tmp_path / "nope.glb"), "-i", str(img), "-o", str(tmp_path / "o.glb")]
        )
        assert result.exit_code == 2

    def test_missing_image_is_click_error(self, runner: CliRunner, tmp_path: Path) -> None:
        mesh = tmp_path / "c.glb"
        mesh.write_bytes(b"x")
        result = runner.invoke(cli, ["refine", str(mesh), "-i", str(tmp_path / "nope.png"), "-o", "o.glb"])
        assert result.exit_code == 2

    def test_unavailable_environment_exits_3(self, runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        from hi3d import capability

        monkeypatch.setattr(capability, "query_gpus", lambda: [])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: None)
        mesh = tmp_path / "c.glb"
        mesh.write_bytes(b"x")
        img = tmp_path / "i.png"
        img.write_bytes(b"x")
        result = runner.invoke(cli, ["refine", str(mesh), "-i", str(img), "-o", str(tmp_path / "o.glb")])
        assert result.exit_code == EXIT_UNAVAILABLE

    def test_unavailable_json_marks_not_available(self, runner: CliRunner, tmp_path: Path, monkeypatch) -> None:
        from hi3d import capability

        monkeypatch.setattr(capability, "query_gpus", lambda: [])
        monkeypatch.setattr(capability, "pick_gpu", lambda gpus: None)
        mesh = tmp_path / "c.glb"
        mesh.write_bytes(b"x")
        img = tmp_path / "i.png"
        img.write_bytes(b"x")
        result = runner.invoke(cli, ["refine", str(mesh), "-i", str(img), "-o", str(tmp_path / "o.glb"), "--json"])
        assert result.exit_code == EXIT_UNAVAILABLE
        payload = json.loads(result.output)
        assert payload["available"] is False
        assert "nvidia-smi" in payload["reason"]


class _NullConsole:
    def print(self, *_a, **_k) -> None:
        return None


class TestDoctor:
    def test_json_payload_shape(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["doctor", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.output)
        for key in ("ok", "python", "python_ok", "modules", "gpu", "model", "plan"):
            assert key in payload

    def test_python_flag_matches_interpreter(self, runner: CliRunner) -> None:
        payload = json.loads(runner.invoke(cli, ["doctor", "--json"]).output)
        assert payload["python_ok"] is (payload["python"].startswith("3.10"))

    def test_model_dir_is_reported(self, runner: CliRunner) -> None:
        payload = json.loads(runner.invoke(cli, ["doctor", "--json"]).output)
        assert payload["model"]["dir"].endswith("hi3d-v1.0")
        assert isinstance(payload["model"]["missing"], list)

    def test_human_output_contains_header(self, runner: CliRunner) -> None:
        result = runner.invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "Hi3D" in result.output


class TestWorkerEntry:
    def test_serve_calls_run_worker(self, runner: CliRunner, monkeypatch) -> None:
        import hi3d.ums as ums

        monkeypatch.setattr(ums, "run_worker", lambda: 0)
        result = runner.invoke(cli, ["serve", "--ums-worker"])
        assert result.exit_code == 0
