"""Testes CLI para o grupo `gameassets dream` (create default, refine, explain)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gameassets.cli import main as cli

runner = CliRunner()


@pytest.fixture(autouse=True)
def _isolated_dream_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AIGAMEKIT_DREAM_CACHE", str(tmp_path / "dream-cache"))


def _plan_dict(**overrides: object) -> dict:
    base: dict = {
        "title": "Test Game",
        "genre": "platformer",
        "tone": "bright",
        "style_preset": "lowpoly",
        "sky_prompt": "blue sky 360",
        "assets": [
            {"id": "hero", "idea": "hero char", "kind": "character", "generate_3d": True, "generate_rig": True},
            {"id": "crate", "idea": "crate", "kind": "prop", "generate_3d": True},
        ],
        "scene": {
            "sky_color": "#87CEEB",
            "ground_size": 50,
            "spawn_y": 5,
            "placements": [
                {"asset_id": "hero", "pos": "0 1 0", "scale": "1 1 1"},
                {"asset_id": "crate", "pos": "4 0 4", "scale": "1 1 1"},
            ],
        },
        "seed": 11,
        "source": "llm:openai",
    }
    base.update(overrides)
    return base


class TestDreamGroupRouting:
    def test_help_lists_subcommands(self) -> None:
        r = runner.invoke(cli, ["dream", "--help"])
        assert r.exit_code == 0
        for cmd in ("create", "refine", "explain"):
            assert cmd in r.output

    def test_no_args_shows_help(self) -> None:
        r = runner.invoke(cli, ["dream"])
        assert r.exit_code == 0
        assert "create" in r.output

    def test_description_routes_to_create(self, tmp_path: Path) -> None:
        r = runner.invoke(
            cli,
            ["dream", "simple platformer with crystals", "--dry-run", "--output-dir", str(tmp_path)],
        )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
        project_dirs = [d for d in tmp_path.iterdir() if d.is_dir()]
        assert project_dirs
        assert (project_dirs[0] / "_batch" / "dream_plan.json").is_file()

    def test_explicit_create_subcommand(self, tmp_path: Path) -> None:
        r = runner.invoke(
            cli,
            ["dream", "create", "a cozy farm game", "--dry-run", "--output-dir", str(tmp_path)],
        )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
        assert "dry-run" in r.output

    def test_options_before_description_still_route(self, tmp_path: Path) -> None:
        r = runner.invoke(
            cli,
            ["dream", "--dry-run", "--output-dir", str(tmp_path), "a racing game"],
        )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"

    def test_seed_flag_flows_to_game_yaml(self, tmp_path: Path) -> None:
        import yaml

        r = runner.invoke(
            cli,
            ["dream", "a farm game", "--dry-run", "--output-dir", str(tmp_path), "--seed", "31337"],
        )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
        game_yaml = next((tmp_path).rglob("game.yaml"))
        doc = yaml.safe_load(game_yaml.read_text(encoding="utf-8"))
        assert doc["seed_base"] == 31337

    def test_fallback_warning_visible(self, tmp_path: Path) -> None:
        r = runner.invoke(
            cli,
            ["dream", "a lonely game", "--dry-run", "--output-dir", str(tmp_path)],
        )
        assert r.exit_code == 0
        assert "fallback" in r.output


class TestDreamExplain:
    def _write_plan(self, tmp_path: Path, payload: dict) -> Path:
        p = tmp_path / "dream_plan.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_valid_plan_exits_zero(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path, _plan_dict())
        r = runner.invoke(cli, ["dream", "explain", str(p)])
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
        assert "plano OK" in r.output
        assert "hero" in r.output

    def test_json_mode(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path, _plan_dict())
        r = runner.invoke(cli, ["dream", "explain", str(p), "--json"])
        assert r.exit_code == 0
        payload = json.loads(r.output)
        assert payload["plan"]["title"] == "Test Game"
        assert payload["stage_chains"]["hero"][0] == "3d"
        assert "artifacts" in payload

    def test_broken_plan_exits_one(self, tmp_path: Path) -> None:
        broken = _plan_dict()
        broken["scene"]["placements"] = [{"asset_id": "ghost", "pos": "0 0 0"}]
        p = self._write_plan(tmp_path, broken)
        r = runner.invoke(cli, ["dream", "explain", str(p)])
        assert r.exit_code == 1
        assert "DREAM_PLACEMENT_DANGLING" in r.output

    def test_missing_file_exits_one(self, tmp_path: Path) -> None:
        r = runner.invoke(cli, ["dream", "explain", str(tmp_path / "nope.json")])
        assert r.exit_code == 1
        assert "não encontrado" in r.output


class TestDreamRefine:
    def _write_plan(self, tmp_path: Path) -> Path:
        p = tmp_path / "dream_plan.json"
        p.write_text(json.dumps(_plan_dict()), encoding="utf-8")
        return p

    def test_refine_success_writes_and_backs_up(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path)
        refined = _plan_dict(title="Test Game v2")
        refined["assets"].append({"id": "dragon", "idea": "dragon", "kind": "character", "generate_3d": True})
        with patch("gameassets.dream.planner._call_openai", return_value=json.dumps(refined)):
            r = runner.invoke(
                cli,
                ["dream", "refine", str(p), "add a dragon", "--llm-provider", "openai"],
            )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
        assert (tmp_path / "dream_plan.json.bak").is_file()
        saved = json.loads(p.read_text(encoding="utf-8"))
        assert saved["title"] == "Test Game v2"
        assert any(a["id"] == "dragon" for a in saved["assets"])
        assert saved["source"] == "refine:openai"

    def test_refine_regenerates_emitted_files(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path)
        (tmp_path / "game.yaml").write_text("old: true\n", encoding="utf-8")
        refined = _plan_dict()
        with patch("gameassets.dream.planner._call_openai", return_value=json.dumps(refined)):
            r = runner.invoke(cli, ["dream", "refine", str(p), "touch"])
        assert r.exit_code == 0
        import yaml

        doc = yaml.safe_load((tmp_path / "game.yaml").read_text(encoding="utf-8"))
        assert doc["title"] == "Test Game"
        assert (tmp_path / "manifest.yaml").is_file()
        assert (tmp_path / "world.xml").is_file()

    def test_refine_no_emit_keeps_files(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path)
        (tmp_path / "game.yaml").write_text("old: true\n", encoding="utf-8")
        with patch("gameassets.dream.planner._call_openai", return_value=json.dumps(_plan_dict())):
            r = runner.invoke(cli, ["dream", "refine", str(p), "touch", "--no-emit"])
        assert r.exit_code == 0
        assert (tmp_path / "game.yaml").read_text(encoding="utf-8") == "old: true\n"

    def test_refine_failure_exits_one_plan_untouched(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path)
        original = p.read_text(encoding="utf-8")
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("LLM down")):
            r = runner.invoke(cli, ["dream", "refine", str(p), "add a dragon"])
        assert r.exit_code == 1
        assert "refine falhou" in r.output
        assert p.read_text(encoding="utf-8") == original
        assert not (tmp_path / "dream_plan.json.bak").exists()

    def test_refine_seed_flag_pinned(self, tmp_path: Path) -> None:
        p = self._write_plan(tmp_path)
        with patch("gameassets.dream.planner._call_openai", return_value=json.dumps(_plan_dict())):
            r = runner.invoke(cli, ["dream", "refine", str(p), "x", "--seed", "777"])
        assert r.exit_code == 0
        saved = json.loads(p.read_text(encoding="utf-8"))
        assert saved["seed"] == 777

    def test_refine_missing_plan_exits_one(self, tmp_path: Path) -> None:
        r = runner.invoke(cli, ["dream", "refine", str(tmp_path / "nope.json"), "x"])
        assert r.exit_code == 1


class TestDreamCreateCacheFlag:
    def test_replan_flag_accepted(self, tmp_path: Path) -> None:
        r = runner.invoke(
            cli,
            [
                "dream",
                "a unique replan game",
                "--dry-run",
                "--output-dir",
                str(tmp_path),
                "--replan",
            ],
        )
        assert r.exit_code == 0, f"exit={r.exit_code}\n{r.output}"
