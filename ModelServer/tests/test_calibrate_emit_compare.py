"""Testes da emissão YAML/JSON e da comparação medido vs declarado."""

from __future__ import annotations

import json

import pytest
import yaml
from modelserver.calibrate.compare import (
    VERDICT_OK,
    VERDICT_OVER,
    VERDICT_UNDER,
    VERDICT_UNKNOWN,
    ComparisonRow,
    compare_to_declared,
    declared_parts_from_registry,
    summarize_verdicts,
    verdict_for,
)
from modelserver.calibrate.emit import (
    calibration_to_descriptor,
    calibration_to_report,
    calibration_to_yaml,
)
from modelserver.registry import Registry, load_descriptors
from modelserver.vram_planner import peak_vram_mib

from .test_calibrate_analysis import build_windows, derive


@pytest.fixture
def cal():
    return derive(build_windows(context=320, weights=1200, activation=900))


class TestDescriptor:
    def test_keeps_v1_keys_for_the_current_loader(self, cal):
        entry = calibration_to_descriptor(cal, adapter="modelserver.adapters.fake3d", priority=30)
        assert entry["name"] == "fake3d"
        assert entry["adapter"] == "modelserver.adapters.fake3d"
        assert entry["priority"] == 30
        assert entry["vram_mib"] >= cal.peak_mib

    def test_vram_mib_is_the_measured_peak_rounded_up(self, cal):
        entry = calibration_to_descriptor(cal)
        assert entry["vram_mib"] % 64 == 0
        assert entry["vram_mib"] >= cal.peak_mib

    def test_vram_block_carries_decomposition(self, cal):
        block = calibration_to_descriptor(cal)["vram"]
        assert block["weights_gib"] == cal.weights_gib
        assert block["activation_gib"] == cal.activation_gib
        assert block["context_gib"] == cal.context_gib
        assert block["safety_mib"] == cal.recommended_safety_mib

    def test_runtime_defaults_to_monorepo_tool(self, cal):
        assert calibration_to_descriptor(cal)["runtime"] == {"monorepo_tool": "fake3d"}

    def test_runtime_override_is_respected(self, cal):
        runtime = {"command": ["python", "-m", "x"], "env": {"HF_HOME": "/tmp"}}
        assert calibration_to_descriptor(cal, runtime=runtime)["runtime"] == runtime

    def test_peak_profile_records_quant_and_load_kwargs(self, cal):
        profile = calibration_to_descriptor(cal)["peak_profile"]
        assert profile["quant_mode"] == "sdnq-int4"
        assert profile["load_kwargs"] == {"sdnq_preset": "int4"}

    def test_staged_load_flag_surfaces_in_profile(self):
        staged = derive(build_windows(context=300, weights=400, activation=3000))
        assert calibration_to_descriptor(staged)["peak_profile"]["staged_load"] is True

    def test_load_bound_flag_surfaces_in_profile(self):
        load_bound = derive(build_windows(context=300, weights=1000, activation=200, load_extra=1500))
        assert calibration_to_descriptor(load_bound)["peak_profile"]["load_bound"] is True

    def test_load_and_shape_keys_are_sorted_and_deduped(self, cal):
        entry = calibration_to_descriptor(cal, load_keys=["b", "a", "a"], shape_keys=["z"])
        assert entry["load_keys"] == ["a", "b"]
        assert entry["shape_keys"] == ["z"]

    def test_measured_block_omits_zero_signals(self, cal):
        measured = calibration_to_descriptor(cal)["measured"]
        assert "fragmentation_mib" not in measured
        assert measured["confidence"] == cal.confidence
        assert measured["repeats"] == cal.repeats

    def test_measured_block_includes_nonzero_signals(self):
        leaky = derive(build_windows(fragmentation=128, leak=100))
        measured = calibration_to_descriptor(leaky)["measured"]
        assert measured["fragmentation_mib"] == 128
        assert measured["leak_mib_per_run"] > 0
        assert measured["warnings"]

    def test_footprint_key_is_preserved_when_given(self, cal):
        entry = calibration_to_descriptor(cal, footprint_key="hunyuan3d-omni")
        assert entry["footprint_key"] == "hunyuan3d-omni"


class TestYamlDocument:
    def test_document_has_version_and_backends(self, cal):
        doc = yaml.safe_load(calibration_to_yaml(cal))
        assert doc["version"] == 2
        assert len(doc["backends"]) == 1

    def test_header_is_a_comment_and_optional(self, cal):
        with_header = calibration_to_yaml(cal)
        without = calibration_to_yaml(cal, header=False)
        assert with_header.startswith("#")
        assert not without.startswith("#")
        assert yaml.safe_load(with_header) == yaml.safe_load(without)

    def test_emitted_yaml_loads_in_the_current_registry(self, cal, tmp_path):
        """Compat dura: o loader v1 tem de aceitar o ficheiro emitido."""
        path = tmp_path / "backends.yaml"
        meta = {"fake3d": {"adapter": "modelserver.adapters.text3d", "priority": 40}}
        path.write_text(calibration_to_yaml(cal, descriptors=meta), encoding="utf-8")
        descriptors = load_descriptors(str(path))
        assert "fake3d" in descriptors
        desc = descriptors["fake3d"]
        assert desc.priority == 40
        assert desc.tool == "fake3d"
        assert desc.vram_mib >= cal.peak_mib

    def test_registry_accepts_emitted_file(self, cal, tmp_path):
        path = tmp_path / "backends.yaml"
        path.write_text(calibration_to_yaml(cal), encoding="utf-8")
        registry = Registry(yaml_path=str(path))
        assert registry.names == ["fake3d"]

    def test_multiple_calibrations_emit_multiple_entries(self, cal):
        other = derive(build_windows(context=100, weights=500, activation=300), backend="outro", tool="outro")
        doc = yaml.safe_load(calibration_to_yaml([cal, other]))
        assert [b["name"] for b in doc["backends"]] == ["fake3d", "outro"]

    def test_unicode_survives_the_dump(self):
        noisy = derive(build_windows(leak=100))
        doc = yaml.safe_load(calibration_to_yaml(noisy))
        assert any("fuga" in w for w in doc["backends"][0]["measured"]["warnings"])


class TestReport:
    def test_report_is_json_serializable(self, cal):
        payload = json.dumps(calibration_to_report(cal))
        assert "vram_mib" in payload

    def test_report_sections(self, cal):
        report = calibration_to_report(cal)
        assert set(report) >= {"backend", "vram_mib", "health", "timing", "quality", "hardware", "phases"}
        assert report["vram_mib"]["weights"] == cal.weights_mib
        assert report["health"]["staged_load_suspected"] is False

    def test_phases_can_be_omitted(self, cal):
        assert "phases" not in calibration_to_report(cal, include_phases=False)


class TestVerdicts:
    def test_declared_none_is_unknown(self):
        assert verdict_for(None, 1000) == VERDICT_UNKNOWN

    def test_within_tolerance_is_ok(self):
        assert verdict_for(1050, 1000) == VERDICT_OK
        assert verdict_for(950, 1000) == VERDICT_OK

    def test_below_tolerance_is_under(self):
        assert verdict_for(800, 1000) == VERDICT_UNDER

    def test_above_tolerance_is_over(self):
        assert verdict_for(1300, 1000) == VERDICT_OVER

    def test_zero_measured_with_declaration_is_unknown(self):
        assert verdict_for(500, 0) == VERDICT_UNKNOWN

    def test_zero_measured_and_zero_declared_is_ok(self):
        assert verdict_for(0, 0) == VERDICT_OK

    def test_custom_tolerance(self):
        assert verdict_for(1050, 1000, tolerance=0.01) == VERDICT_OVER


class TestComparisonRows:
    def test_delta_and_ratio(self):
        row = ComparisonRow("b", "m", declared_mib=1200, measured_mib=1000, verdict=VERDICT_OVER)
        assert row.delta_mib == 200
        assert row.ratio == 1.2

    def test_delta_none_when_not_declared(self):
        row = ComparisonRow("b", "m", declared_mib=None, measured_mib=1000, verdict=VERDICT_UNKNOWN)
        assert row.delta_mib is None
        assert row.ratio is None

    def test_as_dict_round_trips(self):
        row = ComparisonRow("b", "m", declared_mib=1200, measured_mib=1000, verdict=VERDICT_OVER, note="x")
        payload = row.as_dict()
        assert payload["delta_mib"] == 200
        assert payload["note"] == "x"

    def test_compare_produces_three_rows_without_static_vram(self, cal):
        rows = compare_to_declared(cal, declared_weights_mib=1200, declared_activation_mib=900)
        assert [r.metric for r in rows] == ["weights_mib", "activation_mib", "admit_peak_mib"]

    def test_compare_adds_static_vram_row(self, cal):
        rows = compare_to_declared(cal, declared_weights_mib=1200, declared_activation_mib=900, declared_vram_mib=10000)
        assert rows[-1].metric == "vram_mib"
        assert rows[-1].verdict == VERDICT_OVER

    def test_admit_peak_uses_the_same_formula_as_the_planner(self, cal):
        rows = compare_to_declared(cal, declared_weights_mib=1200, declared_activation_mib=900)
        admit = next(r for r in rows if r.metric == "admit_peak_mib")
        assert admit.declared_mib == peak_vram_mib(1200, 900)

    def test_under_declaration_is_flagged(self, cal):
        rows = compare_to_declared(cal, declared_weights_mib=100, declared_activation_mib=100)
        assert any(r.verdict == VERDICT_UNDER for r in rows)

    def test_staged_load_changes_the_activation_note(self):
        staged = derive(build_windows(context=300, weights=400, activation=3000))
        rows = compare_to_declared(staged, declared_weights_mib=400, declared_activation_mib=3000)
        note = next(r.note for r in rows if r.metric == "activation_mib")
        assert "staged" in note

    def test_summarize_verdicts_counts(self, cal):
        rows = compare_to_declared(cal, declared_weights_mib=1200, declared_activation_mib=900)
        counts = summarize_verdicts(rows)
        assert sum(counts.values()) == len(rows)


class TestDeclaredFromRegistry:
    def test_unknown_backend_returns_nones(self):
        assert declared_parts_from_registry("nao-existe") == (None, None, None)

    def test_known_backend_returns_parts(self):
        weights, activation, vram = declared_parts_from_registry("text3d")
        assert weights and weights > 0
        assert activation and activation > 0
        assert vram == 10000

    def test_quant_reduces_declared_weights(self):
        base, _, _ = declared_parts_from_registry("text3d", quant_mode="none")
        quant, _, _ = declared_parts_from_registry("text3d", quant_mode="sdnq-int4")
        assert quant < base
