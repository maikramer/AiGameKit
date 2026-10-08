"""Testes do ``gameassets doctor`` — checks puros com dependências externas mockadas."""

from __future__ import annotations

import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from gameassets import doctor
from gameassets.cli import main as cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _tool_bin_map(mapping: dict[str, str | None]):
    """Factory de fake para ``_tool_bin`` (tool → caminho ou None)."""

    def fake(tool: str) -> str | None:
        return mapping.get(tool, f"/fake/bin/{tool}")

    return fake


class TestCheckTools:
    def test_all_installed_ok(self) -> None:
        with patch.object(doctor, "tool_bin", _tool_bin_map({})):
            checks = doctor.check_tools()
        assert checks[0].status == "ok"
        assert checks[1].status == "ok"

    def test_missing_core_fails_with_fix(self) -> None:
        with patch.object(doctor, "tool_bin", _tool_bin_map({"text3d": None, "paint3d": None})):
            checks = doctor.check_tools()
        assert checks[0].status == "fail"
        assert "text3d" in checks[0].detail and "paint3d" in checks[0].detail
        assert "install.sh" in checks[0].fix

    def test_missing_dream_optional_warns(self) -> None:
        with patch.object(doctor, "tool_bin", _tool_bin_map({"skymap2d": None})):
            checks = doctor.check_tools()
        assert checks[1].status == "warn"
        assert "skymap2d" in checks[1].detail
        assert "install.sh" in checks[1].fix

    def test_gameassets_itself_is_not_required(self) -> None:
        with patch.object(doctor, "tool_bin", _tool_bin_map({"gameassets": None})):
            checks = doctor.check_tools()
        assert checks[0].status == "ok"


class TestCheckVramdGpu:
    def test_vramd_down_and_no_gpu(self) -> None:
        with (
            patch("aigamekit_shared.vramd_client.is_vramd_running", return_value=False),
            patch("aigamekit_shared.gpu.query_gpu_snapshot", return_value=None),
        ):
            checks = doctor.check_vramd_gpu()
        assert checks[0].status == "ok"  # auto-arranca — não é erro
        assert "auto-arranca" in checks[0].detail
        assert checks[1].status == "warn"

    def test_gpu_ok(self) -> None:
        snap = SimpleNamespace(name="RTX 4050", free_mib=5825, total_mib=6141, source="nvml")
        with (
            patch("aigamekit_shared.vramd_client.is_vramd_running", return_value=True),
            patch("aigamekit_shared.gpu.query_gpu_snapshot", return_value=snap),
        ):
            checks = doctor.check_vramd_gpu()
        assert checks[0].detail == "a correr"
        assert checks[1].status == "ok"
        assert "RTX 4050" in checks[1].detail


class TestCheckCompression:
    def test_text3d_missing_warns(self) -> None:
        with patch.object(doctor, "tool_bin", _tool_bin_map({"text3d": None})):
            checks = doctor.check_compression()
        assert checks[0].status == "warn"
        assert "install.sh text3d" in checks[0].fix

    def test_doctor_passes(self) -> None:
        proc = subprocess.CompletedProcess([], 0, stdout="all good", stderr="")
        with (
            patch.object(doctor, "tool_bin", _tool_bin_map({})),
            patch.object(doctor.subprocess, "run", return_value=proc),
        ):
            checks = doctor.check_compression()
        assert checks[0].status == "ok"

    def test_doctor_failing_degrades_to_warn_with_tail(self) -> None:
        proc = subprocess.CompletedProcess([], 1, stdout="ktx: MISSING\nmeshopt: ok", stderr="")
        with (
            patch.object(doctor, "tool_bin", _tool_bin_map({})),
            patch.object(doctor.subprocess, "run", return_value=proc),
        ):
            checks = doctor.check_compression()
        assert checks[0].status == "warn"
        assert "ktx" in checks[0].detail

    def test_timeout_warns(self) -> None:
        with (
            patch.object(doctor, "tool_bin", _tool_bin_map({})),
            patch.object(doctor.subprocess, "run", side_effect=subprocess.TimeoutExpired("x", 1)),
        ):
            checks = doctor.check_compression()
        assert checks[0].status == "warn"


class TestCheckNodeBun:
    def test_versions_ok(self) -> None:
        with patch.object(doctor, "_run_version", side_effect=lambda cmd: {"node": "v22.1.0", "bun": "1.2.3"}[cmd[0]]):
            checks = doctor.check_node_bun()
        assert all(c.status == "ok" for c in checks)

    def test_missing_both_warn(self) -> None:
        with patch.object(doctor, "_run_version", return_value=None):
            checks = doctor.check_node_bun()
        assert all(c.status == "warn" for c in checks)
        assert any("KTX2" in c.detail for c in checks)
        assert any("bun.sh" in c.fix for c in checks)


class TestCheckDreamLlm:
    def test_openai_key_wins(self) -> None:
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=False),
            patch.object(doctor, "ollama_reachable", return_value=False),
        ):
            checks = doctor.check_dream_llm()
        assert checks[0].status == "ok"
        assert "openai" in checks[0].detail

    def test_ollama_local(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with patch.object(doctor, "ollama_reachable", return_value=True):
                checks = doctor.check_dream_llm()
        assert checks[0].status == "ok"
        assert "ollama" in checks[0].detail

    def test_no_llm_falls_back_with_fix(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("OPENAI_API_KEY", None)
            with patch.object(doctor, "ollama_reachable", return_value=False):
                checks = doctor.check_dream_llm()
        assert checks[0].status == "warn"
        assert "OPENAI_API_KEY" in checks[0].fix

    def test_hf_token_absent_warns(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HF_TOKEN", None)
            with patch.object(doctor, "ollama_reachable", return_value=False):
                checks = doctor.check_dream_llm()
        assert checks[1].status == "warn"
        assert "gated" in checks[1].detail


class TestOllamaReachable:
    def test_unreachable(self) -> None:
        with patch.object(doctor.urllib.request, "urlopen", side_effect=OSError("down")):
            assert doctor.ollama_reachable() is False

    def test_reachable(self) -> None:
        with patch.object(doctor.urllib.request, "urlopen", return_value=object()):
            assert doctor.ollama_reachable() is True


class TestCheckDisk:
    def test_warns_when_low(self) -> None:
        usage = SimpleNamespace(free=10 * 1024**3)
        with patch.object(doctor.shutil, "disk_usage", return_value=usage):
            checks = doctor.check_disk()
        assert checks[0].status == "warn"

    def test_ok_when_plenty(self) -> None:
        usage = SimpleNamespace(free=200 * 1024**3)
        with patch.object(doctor.shutil, "disk_usage", return_value=usage):
            checks = doctor.check_disk()
        assert checks[0].status == "ok"


class TestPayloadAndRender:
    def test_payload_structure(self) -> None:
        checks = [doctor.DoctorCheck(name="x", status="ok", detail="d")]
        payload = doctor.doctor_payload(checks)
        assert payload["ok"] is True
        assert payload["checks"][0]["name"] == "x"

    def test_payload_not_ok_on_fail(self) -> None:
        checks = [doctor.DoctorCheck(name="x", status="fail", detail="d", fix="f")]
        assert doctor.doctor_payload(checks)["ok"] is False

    def test_render_ready(self, capsys) -> None:
        doctor.render_doctor([doctor.DoctorCheck(name="tudo", status="ok", detail="d")])
        out = capsys.readouterr().out
        assert "READY" in out

    def test_render_failing_prints_fix(self, capsys) -> None:
        doctor.render_doctor([doctor.DoctorCheck(name="x", status="fail", detail="d", fix="corrige")])
        out = capsys.readouterr().out
        assert "corrige" in out
        assert "READY" not in out


class TestDoctorCli:
    def test_json_output(self, runner: CliRunner) -> None:
        fake = [doctor.DoctorCheck(name="a", status="ok", detail="d")]
        with patch.object(doctor, "run_doctor", return_value=fake):
            r = runner.invoke(cli, ["doctor", "--json"])
        assert r.exit_code == 0
        parsed = json.loads(r.output)
        assert parsed["ok"] is True
        assert parsed["checks"][0]["name"] == "a"

    def test_fail_exits_1(self, runner: CliRunner) -> None:
        fake = [doctor.DoctorCheck(name="a", status="fail", detail="d", fix="f")]
        with patch.object(doctor, "run_doctor", return_value=fake):
            r = runner.invoke(cli, ["doctor"])
        assert r.exit_code == 1

    def test_help_lists_scopes(self, runner: CliRunner) -> None:
        r = runner.invoke(cli, ["doctor", "--help"])
        assert r.exit_code == 0
        assert "vramd" in r.output and "dream" in r.output
