"""Testes de integração do CLI gameassets (Click + ficheiros temporários)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from gameassets.cli import main as cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def test_main_help(runner: CliRunner) -> None:
    r = runner.invoke(cli, ["--help"])
    assert r.exit_code == 0
    assert "gameassets" in r.output.lower() or "Batch" in r.output


def test_info_runs(runner: CliRunner) -> None:
    r = runner.invoke(cli, ["info"])
    assert r.exit_code == 0
    assert "text2d" in r.output.lower() or "text3d" in r.output.lower()


def test_init_creates_files(runner: CliRunner, tmp_path: Path) -> None:
    r = runner.invoke(cli, ["init", "--path", str(tmp_path)])
    assert r.exit_code == 0
    assert (tmp_path / "game.yaml").is_file()
    assert (tmp_path / "manifest.yaml").is_file()


def test_init_refuses_overwrite_without_force(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "game.yaml").write_text("x: 1\n", encoding="utf-8")
    r = runner.invoke(cli, ["init", "--path", str(tmp_path)])
    assert r.exit_code != 0


def test_init_force_overwrites(runner: CliRunner, tmp_path: Path) -> None:
    (tmp_path / "game.yaml").write_text("x: 1\n", encoding="utf-8")
    r = runner.invoke(cli, ["init", "--path", str(tmp_path), "--force"])
    assert r.exit_code == 0
    text = (tmp_path / "game.yaml").read_text(encoding="utf-8")
    assert "title:" in text


def test_prompts_after_init(runner: CliRunner, tmp_path: Path) -> None:
    r = runner.invoke(cli, ["init", "--path", str(tmp_path)])
    assert r.exit_code == 0
    r2 = runner.invoke(
        cli,
        [
            "prompts",
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
        ],
    )
    assert r2.exit_code == 0
    assert "chest_01" in r2.output or "baú" in r2.output


def test_prompts_jsonl_output(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    out = tmp_path / "prompts.jsonl"
    r = runner.invoke(
        cli,
        [
            "prompts",
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
            "-o",
            str(out),
        ],
    )
    assert r.exit_code == 0
    lines = out.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    first = json.loads(lines[0])
    assert "id" in first and "prompt" in first


def test_batch_dry_run_json(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    true_bin = shutil.which("true")
    if not true_bin:
        pytest.skip("comando 'true' não encontrado no PATH")
    monkeypatch.setenv("TEXT2D_BIN", true_bin)
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    out = tmp_path / "plan.json"
    r = runner.invoke(
        cli,
        [
            "batch",
            "--dry-run",
            "--dry-run-json",
            str(out),
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
        ],
    )
    assert r.exit_code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data.get("version") == 1
    assert "steps" in data
    assert isinstance(data["steps"], list)
    assert any("argv" in s for s in data["steps"])


def test_handoff_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    pub = tmp_path / "public"
    pub.mkdir()
    r = runner.invoke(
        cli,
        [
            "handoff",
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
            "--public-dir",
            str(pub),
            "--dry-run",
        ],
    )
    assert r.exit_code == 0
    assert "public_dir" in r.output or "assets_base_url" in r.output


def test_batch_dry_run(runner: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Integração: batch --dry-run sem executar text2d (binário inócuo via env)."""
    true_bin = shutil.which("true")
    if not true_bin:
        pytest.skip("comando 'true' não encontrado no PATH")
    monkeypatch.setenv("TEXT2D_BIN", true_bin)
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    r = runner.invoke(
        cli,
        [
            "batch",
            "--dry-run",
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
        ],
    )
    assert r.exit_code == 0
    assert "dry-run" in r.output.lower()


def test_batch_dry_run_json_requires_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    out = tmp_path / "plan.json"
    r = runner.invoke(
        cli,
        [
            "batch",
            "--dry-run-json",
            str(out),
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
        ],
    )
    assert r.exit_code != 0


def test_dream_dry_run(runner: CliRunner, tmp_path: Path) -> None:
    """dream --dry-run gera ficheiros sem GPU (usa fallback plan)."""
    r = runner.invoke(
        cli,
        [
            "dream",
            "simple platformer with crystals",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
        ],
    )
    assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
    # Deve ter criado um subdiretório com ficheiros
    project_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
    assert len(project_dirs) >= 1
    project = project_dirs[0]
    batch_dir = project / "_batch"
    assert (batch_dir / "game.yaml").is_file()
    assert (batch_dir / "manifest.yaml").is_file()
    assert (batch_dir / "dream_plan.json").is_file()


def test_dream_dry_run_plan_json(runner: CliRunner, tmp_path: Path) -> None:
    """dream --dry-run --plan-json exporta o plano."""
    plan_out = tmp_path / "exported_plan.json"
    r = runner.invoke(
        cli,
        [
            "dream",
            "idle clicker game",
            "--dry-run",
            "--output-dir",
            str(tmp_path),
            "--plan-json",
            str(plan_out),
        ],
    )
    assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
    assert plan_out.is_file()
    data = json.loads(plan_out.read_text(encoding="utf-8"))
    assert "title" in data
    assert "assets" in data


def test_batch_skip_text2d_requires_3d_generation(runner: CliRunner, tmp_path: Path) -> None:
    runner.invoke(cli, ["init", "--path", str(tmp_path)])
    r = runner.invoke(
        cli,
        [
            "batch",
            "--skip-text2d",
            "--no-3d",
            "--profile",
            str(tmp_path / "game.yaml"),
            "--manifest",
            str(tmp_path / "manifest.yaml"),
        ],
    )
    assert r.exit_code != 0
    assert "skip-text2d" in r.output.lower() or "3d" in r.output.lower()
