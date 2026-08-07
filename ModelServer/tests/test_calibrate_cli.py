"""Testes do comando ``ums calibrate`` (sem GPU: o runner é substituído)."""

from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner
from modelserver import cli as cli_mod
from modelserver.calibrate.analysis import Calibration

from .test_calibrate_analysis import build_windows, derive


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def fake_calibration():
    return derive(build_windows(context=320, weights=1200, activation=900), backend="text3d", tool="text3d")


@pytest.fixture
def patched(monkeypatch, fake_calibration):
    """Substitui pool e runner; o comando corre de ponta a ponta sem GPU."""
    state: dict = {"spec": None, "preflight": [], "shutdown": False}

    class FakePool:
        def shutdown_all(self):
            state["shutdown"] = True

    class FakeRunner:
        def __init__(self, pool, **kwargs):
            state["pool"] = pool

        def preflight(self, **kwargs):
            return list(state["preflight"])

        def run(self, spec):
            state["spec"] = spec
            if isinstance(state.get("raise"), Exception):
                raise state["raise"]
            return state.get("result", fake_calibration)

    monkeypatch.setattr("modelserver.subprocess_pool.SubprocessWorkerPool", FakePool)
    monkeypatch.setattr("modelserver.calibrate.CalibrationRunner", FakeRunner)
    monkeypatch.setattr("aigamekit_shared.model_server.zero_ums_vram", lambda **_: None, raising=False)
    return state


class TestParsers:
    def test_coerce_scalar_types(self):
        assert cli_mod._coerce_scalar("4") == 4
        assert cli_mod._coerce_scalar("0.5") == 0.5
        assert cli_mod._coerce_scalar("true") is True
        assert cli_mod._coerce_scalar("off") is False
        assert cli_mod._coerce_scalar("none") is None
        assert cli_mod._coerce_scalar("int4") == "int4"

    def test_parse_kv_builds_dict(self):
        assert cli_mod._parse_kv(("a=1", "b=x")) == {"a": 1, "b": "x"}

    def test_parse_kv_rejects_missing_equals(self):
        with pytest.raises(Exception, match="K=V"):
            cli_mod._parse_kv(("solto",))


class TestCalibrateCommand:
    def test_unknown_backend_exits_two(self, runner, patched):
        result = runner.invoke(cli_mod.cli, ["calibrate", "nao-existe"])
        assert result.exit_code == 2

    def test_happy_path_prints_decomposition(self, runner, patched):
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare"])
        assert result.exit_code == 0
        assert "pesos" in result.output
        assert "2420" in result.output

    def test_load_kwargs_and_quant_reach_the_spec(self, runner, patched):
        result = runner.invoke(
            cli_mod.cli,
            ["calibrate", "text3d", "--no-compare", "--quant", "sdnq-int4", "--load-kwarg", "max_num_view=4"],
        )
        assert result.exit_code == 0
        spec = patched["spec"]
        assert spec.load_kwargs["sdnq_preset"] == "sdnq-int4"
        assert spec.load_kwargs["max_num_view"] == 4
        assert spec.quant_mode == "sdnq-int4"

    def test_request_json_file_is_used(self, runner, patched, tmp_path):
        path = tmp_path / "job.json"
        path.write_text(json.dumps({"mesh_path": "/x.glb", "output": "/y.glb"}), encoding="utf-8")
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--request-json", str(path)])
        assert result.exit_code == 0
        assert patched["spec"].request["mesh_path"] == "/x.glb"

    def test_default_request_uses_prompt(self, runner, patched):
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--prompt", "uma pedra"])
        assert result.exit_code == 0
        assert patched["spec"].request["prompt"] == "uma pedra"

    def test_repeats_and_cycles_flow_through(self, runner, patched):
        runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--repeats", "5", "--cycles", "2"])
        assert patched["spec"].repeats == 5
        assert patched["spec"].cycles == 2

    def test_preflight_blocks_without_force(self, runner, patched):
        patched["preflight"] = ["UMS tem jobs em curso"]
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d"])
        assert result.exit_code == 2
        assert "UMS tem jobs em curso" in result.output

    def test_force_overrides_preflight(self, runner, patched):
        patched["preflight"] = ["algum bloqueio"]
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--force"])
        assert result.exit_code == 0

    def test_runner_error_exits_one(self, runner, patched):
        patched["raise"] = RuntimeError("load falhou")
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d"])
        assert result.exit_code == 1
        assert "load falhou" in result.output

    def test_pool_is_shut_down_even_on_failure(self, runner, patched):
        patched["raise"] = RuntimeError("boom")
        runner.invoke(cli_mod.cli, ["calibrate", "text3d"])
        assert patched["shutdown"] is True

    def test_writes_yaml_that_loads(self, runner, patched, tmp_path):
        out = tmp_path / "backends.yaml"
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--out", str(out)])
        assert result.exit_code == 0
        doc = yaml.safe_load(out.read_text(encoding="utf-8"))
        entry = doc["backends"][0]
        assert entry["name"] == "text3d"
        assert entry["adapter"] == "modelserver.adapters.text3d"
        assert entry["priority"] == 40
        assert entry["footprint_key"] == "hunyuan3d-omni"

    def test_writes_json_report(self, runner, patched, tmp_path):
        out = tmp_path / "report.json"
        runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--report", str(out)])
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["vram_mib"]["weights"] == 1200

    def test_json_flag_dumps_report(self, runner, patched):
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--no-compare", "--json"])
        assert result.exit_code == 0
        assert "weights" in result.output

    def test_comparison_against_declared_runs_by_default(self, runner, patched, tmp_path):
        out = tmp_path / "report.json"
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d", "--report", str(out)])
        payload = json.loads(out.read_text(encoding="utf-8"))
        metrics = {row["metric"] for row in payload["comparison"]}
        assert {"weights_mib", "activation_mib", "admit_peak_mib", "vram_mib"} == metrics
        # text3d declara ~10 GiB; a calibração falsa mede ~2.4 GiB → sobredimensionado.
        assert "sobredimensionado" in result.output

    def test_under_provisioned_comparison_exits_one(self, runner, patched, monkeypatch):
        huge = derive(build_windows(context=500, weights=20000, activation=8000), backend="text3d", tool="text3d")
        assert isinstance(huge, Calibration)
        patched["result"] = huge
        result = runner.invoke(cli_mod.cli, ["calibrate", "text3d"])
        assert result.exit_code == 1
        assert "subdimensionada" in result.output
