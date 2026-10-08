"""Testes dos pré-checks do dream (LLM/tools opcionais) — avisos antes de trabalhar."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gameassets.cli import main as cli
from gameassets.dream.preflight import dream_preflight_issues

runner = CliRunner()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    monkeypatch.setenv("AIGAMEKIT_DREAM_CACHE", str(tmp_path / "dream-cache"))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("HF_TOKEN", raising=False)


def _all_tools_present(tool: str) -> str:
    return f"/fake/bin/{tool}"


class TestLlmIssues:
    def test_openai_missing_key_warns(self) -> None:
        issues = dream_preflight_issues(provider="openai", dry_run=True)
        assert len(issues) == 1
        assert "OPENAI_API_KEY" in issues[0] and "fallback" in issues[0]

    def test_openai_with_env_key_ok(self) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}, clear=False):
            assert dream_preflight_issues(provider="openai", dry_run=True) == []

    def test_openai_with_api_key_param_ok(self) -> None:
        assert dream_preflight_issues(provider="openai", api_key="sk-x", dry_run=True) == []

    def test_ollama_down_warns(self) -> None:
        with patch("gameassets.dream.preflight.ollama_reachable", return_value=False):
            issues = dream_preflight_issues(provider="ollama", dry_run=True)
        assert len(issues) == 1
        assert "ollama" in issues[0]

    def test_ollama_up_ok(self) -> None:
        with patch("gameassets.dream.preflight.ollama_reachable", return_value=True):
            assert dream_preflight_issues(provider="ollama", dry_run=True) == []

    def test_huggingface_without_token_warns(self) -> None:
        issues = dream_preflight_issues(provider="huggingface", dry_run=True)
        assert len(issues) == 1
        assert "HF_TOKEN" in issues[0]

    def test_stdin_has_no_llm_check(self) -> None:
        assert dream_preflight_issues(provider="stdin", dry_run=True) == []


class TestToolIssues:
    def test_all_tools_present_no_issues(self) -> None:
        with (
            patch("gameassets.dream.preflight.tool_bin", _all_tools_present),
            patch("gameassets.dream.preflight.ollama_reachable", return_value=True),
        ):
            assert dream_preflight_issues(provider="openai", api_key="k") == []

    def test_missing_sky_tool_warns_with_flag_hint(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None if tool == "skymap2d" else "/bin/x"):
            issues = dream_preflight_issues(provider="openai", api_key="k")
        assert any("skymap2d" in i and "--no-sky" in i for i in issues)

    def test_no_sky_flag_silences_sky_warning(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None if tool == "skymap2d" else "/bin/x"):
            issues = dream_preflight_issues(provider="openai", api_key="k", with_sky=False)
        assert not any("skymap2d" in i for i in issues)

    def test_missing_audio_tool_warns(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None if tool == "text2sound" else "/bin/x"):
            issues = dream_preflight_issues(provider="openai", api_key="k")
        assert any("text2sound" in i and "--no-audio" in i for i in issues)

    def test_terrain_auto_warns_when_missing(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None if tool == "terrain3d" else "/bin/x"):
            issues = dream_preflight_issues(provider="openai", api_key="k")
        assert any("terrain3d" in i and "--no-terrain" in i for i in issues)

    def test_terrain_explicit_false_silences(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None if tool == "terrain3d" else "/bin/x"):
            issues = dream_preflight_issues(provider="openai", api_key="k", terrain=False)
        assert not any("terrain3d" in i for i in issues)

    def test_dry_run_skips_tool_checks_but_keeps_llm(self) -> None:
        with patch("gameassets.dream.preflight.tool_bin", lambda tool: None):
            issues = dream_preflight_issues(provider="openai", dry_run=True)
        # O check LLM mantém-se (sem chave → 1 aviso); os tool checks saltam no dry-run.
        assert len(issues) == 1
        assert "OPENAI_API_KEY" in issues[0]


class TestCliIntegration:
    def test_create_prints_preflight_llm_warning(self, tmp_path) -> None:
        with patch("gameassets.dream.planner._call_openai", side_effect=RuntimeError("LLM down")):
            r = runner.invoke(cli, ["dream", "um jogo de teste", "--dry-run", "--output-dir", str(tmp_path)])
        assert r.exit_code == 0
        assert "Pré-checks do dream" in r.output
        assert "OPENAI_API_KEY" in r.output

    def test_create_silent_when_all_good(self, tmp_path) -> None:
        import json

        plan = {
            "title": "T",
            "genre": "platformer",
            "tone": "bright",
            "style_preset": "lowpoly",
            "sky_prompt": "sky",
            "assets": [],
            "scene": {"sky_color": "#87CEEB", "ground_size": 50, "spawn_y": 5, "placements": []},
            "seed": 1,
            "source": "llm:openai",
        }
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-x"}, clear=False),
            patch("gameassets.dream.planner._call_openai", return_value=json.dumps(plan)),
        ):
            r = runner.invoke(cli, ["dream", "um jogo de teste", "--dry-run", "--output-dir", str(tmp_path)])
        assert r.exit_code == 0
        assert "Pré-checks do dream" not in r.output
