"""CLI surface: commands, quality defaults and the generate happy path (mocked model)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.core import ParameterSource
from click.testing import CliRunner, Result
from motion3d import cli as cli_module
from motion3d.cli import QUALITY_TIERS, _option_from_cli, _quality_defaults, cli


def run(*args: str) -> Result:
    return CliRunner().invoke(cli, list(args))


class TestCommands:
    def test_version(self) -> None:
        result = run("--version")
        assert result.exit_code == 0
        assert "motion3d" in result.output.lower()

    @pytest.mark.parametrize(
        "command",
        ["generate", "export-glb", "apply-rigged", "pack-rigged", "doctor", "download-model"],
    )
    def test_command_is_registered(self, command: str) -> None:
        assert command in run("--help").output

    def test_serve_was_removed(self) -> None:
        assert "serve" not in run("--help").output

    def test_generate_help_lists_the_model_options(self) -> None:
        output = run("generate", "--help").output
        for flag in ("--attempts", "--seed", "--duration", "--steps", "--loop", "--foot-lock", "--bvh", "--quality"):
            assert flag in output

    def test_generate_help_has_no_gpu_flags(self) -> None:
        output = run("generate", "--help").output
        for flag in ("--vramd", "--gpu-ids", "--sdnq-preset", "--model lite"):
            assert flag not in output

    def test_pack_help_mentions_motion_specs(self) -> None:
        assert "name=path.npz" in run("pack-rigged", "--help").output

    def test_doctor_help(self) -> None:
        output = run("doctor", "--help").output
        assert "--json" in output and "--build" in output


class TestQualityDefaults:
    def test_medium_profile_carries_the_runtime_knobs(self) -> None:
        params = _quality_defaults("medium", None)
        assert params["duration"] == pytest.approx(5.0)
        assert params["steps"] == 10
        assert params["residual_layers"] == 5
        assert params["attempts"] == 3

    @pytest.mark.parametrize("tier", QUALITY_TIERS)
    def test_every_tier_resolves(self, tier: str) -> None:
        params = _quality_defaults(tier, None)
        assert {"duration", "steps", "residual_layers", "text_guidance", "attempts"} <= set(params)

    def test_fast_spends_less(self) -> None:
        fast = _quality_defaults("fast", None)
        highest = _quality_defaults("highest", None)
        assert fast["steps"] < highest["steps"]
        assert fast["attempts"] <= highest["attempts"]

    def test_unknown_tier_falls_back_to_empty(self) -> None:
        assert _quality_defaults("nonsense", None) == {}


class TestOptionResolution:
    def make_context(self, source: ParameterSource) -> object:
        import click

        ctx = click.Context(click.Command("generate"))
        ctx.set_parameter_source("steps", source)
        return ctx

    def test_explicit_cli_value_wins(self) -> None:
        ctx = self.make_context(ParameterSource.COMMANDLINE)
        assert _option_from_cli(ctx, "steps", 4, {"steps": 10}, cast=int) == 4

    def test_default_uses_the_quality_tier(self) -> None:
        ctx = self.make_context(ParameterSource.DEFAULT)
        assert _option_from_cli(ctx, "steps", 10, {"steps": 6}, cast=int) == 6

    def test_missing_quality_key_keeps_the_cli_default(self) -> None:
        ctx = self.make_context(ParameterSource.DEFAULT)
        assert _option_from_cli(ctx, "steps", 10, {}, cast=int) == 10

    def test_bad_quality_value_keeps_the_cli_default(self) -> None:
        ctx = self.make_context(ParameterSource.DEFAULT)
        assert _option_from_cli(ctx, "steps", 10, {"steps": "not-a-number"}, cast=int) == 10


class TestGenerate:
    def stub(self, monkeypatch: pytest.MonkeyPatch, calls: dict[str, object]) -> None:
        class Stub:
            last_quality = SimpleNamespace(as_dict=lambda: {"score": 0.25, "foot_slide": 0.01})
            last_seed = 3

            def generate(self, prompt: str, output: str | Path, **kwargs: object) -> Path:
                calls.update({"prompt": prompt, "output": output, **kwargs})
                path = Path(output)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"npz")
                return path

        monkeypatch.setattr(cli_module.MotionGenerator, "get_instance", lambda *a, **k: Stub())

    def test_writes_the_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, object] = {}
        self.stub(monkeypatch, calls)
        target = tmp_path / "walk.npz"
        result = run("generate", "a person walks forward", "-o", str(target))
        assert result.exit_code == 0, result.output
        assert target.is_file()
        assert calls["prompt"] == "a person walks forward"

    def test_passes_the_runtime_options(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, object] = {}
        self.stub(monkeypatch, calls)
        run(
            "generate",
            "a person runs forward",
            "-o",
            str(tmp_path / "run.npz"),
            "--seed",
            "9",
            "--attempts",
            "1",
            "--duration",
            "1.5",
            "--steps",
            "6",
            "--loop",
            "--no-foot-lock",
            "--in-place",
        )
        assert calls["seed"] == 9
        assert calls["attempts"] == 1
        assert calls["seconds"] == pytest.approx(1.5)
        options = calls["options"]
        assert options.steps == 6  # type: ignore[attr-defined]
        assert options.loop is True  # type: ignore[attr-defined]
        assert options.foot_lock is False  # type: ignore[attr-defined]
        assert options.in_place is True  # type: ignore[attr-defined]

    def test_json_output(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: dict[str, object] = {}
        self.stub(monkeypatch, calls)
        result = run("generate", "a person walks forward", "-o", str(tmp_path / "walk.npz"), "--json")
        assert result.exit_code == 0
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert payload["output"].endswith("walk.npz")
        assert payload["score"] == pytest.approx(0.25)

    def test_empty_prompt_exits_2(self, tmp_path: Path) -> None:
        result = run("generate", "   ", "-o", str(tmp_path / "x.npz"))
        assert result.exit_code == 2

    def test_generator_error_is_reported(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        class Boom:
            last_quality = None
            last_seed = 0

            def generate(self, *args: object, **kwargs: object) -> Path:
                raise ValueError("modelo corrompido")

        monkeypatch.setattr(cli_module.MotionGenerator, "get_instance", lambda *a, **k: Boom())
        result = run("generate", "a person walks forward", "-o", str(tmp_path / "x.npz"))
        assert result.exit_code == 1
        assert "modelo corrompido" in result.output


class TestDoctor:
    def test_json_report_has_the_expected_sections(self) -> None:
        result = run("doctor", "--json")
        assert result.exit_code == 0, result.output
        payload = json.loads(result.output.strip().splitlines()[-1])
        assert "runtime" in payload
        assert "model" in payload
        assert "imports" in payload
        assert payload["imports"]["numpy"] == "ok"
