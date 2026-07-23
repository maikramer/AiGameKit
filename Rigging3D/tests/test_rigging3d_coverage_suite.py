"""Suite de cobertura Rigging3D — hardware, helpers puros do CLI, checkpoints, --help."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rigging3d import skintokens_runner as runner
from rigging3d.cli import (
    _assign_fingers,
    _assign_head_accessories,
    _bone_descendants,
    _bone_linear_chain,
    _ctx_gpu_ids,
    _ctx_profiler,
    _is_humanoid_topology,
    cli,
)
from rigging3d.hardware import GIB, hw_auto_enabled, profile_from_specs


def _gib(n: float) -> int:
    return int(n * GIB)


def _topology_from_nodes(
    nodes: list[dict[str, Any]],
) -> tuple[dict[int, int], list[dict[str, object]], dict[int, int | None]]:
    """Constrói argumentos de ``_is_humanoid_topology`` a partir de nós GLTF-like."""
    parent_map: dict[int, int | None] = {i: None for i in range(len(nodes))}
    for pi, node in enumerate(nodes):
        for ci in node.get("children") or []:
            parent_map[int(ci)] = pi
    bone_nodes = {i: i for i in range(len(nodes))}
    return bone_nodes, nodes, parent_map


def _minimal_humanoid_nodes() -> list[dict[str, Any]]:
    # 0=root, 1=spine, 2=chest, 3=leg_l, 4=leg_r, 5=arm_l, 6=arm_r, 7=neck
    return [
        {"name": "bone_0", "children": [1, 3, 4]},
        {"name": "bone_1", "children": [2]},
        {"name": "bone_2", "children": [5, 6, 7]},
        {"name": "bone_3"},
        {"name": "bone_4"},
        {"name": "bone_5"},
        {"name": "bone_6"},
        {"name": "bone_7"},
    ]


class TestHwAutoEnabled:
    def test_default_on_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RIGGING3D_HW_AUTO", raising=False)
        assert hw_auto_enabled() is True

    def test_off_when_zero(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_HW_AUTO", "0")
        assert hw_auto_enabled() is False

    def test_on_when_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_HW_AUTO", "1")
        assert hw_auto_enabled() is True


class TestProfileFromSpecs:
    def test_empty_list_cpu(self) -> None:
        p = profile_from_specs([])
        assert p.device == "cpu"
        assert p.name == "cpu"
        assert p.gpu_ids is None
        assert p.free_gib == 0.0
        assert p.low_memory_warning is True

    def test_single_gpu_no_pin(self) -> None:
        p = profile_from_specs([(0, _gib(10.0), _gib(12))])
        assert p.device == "cuda"
        assert p.gpu_ids is None
        assert p.low_memory_warning is False
        assert "cuda-1x12g" in p.name

    def test_single_gpu_low_memory_warn(self) -> None:
        p = profile_from_specs([(0, _gib(2.0), _gib(3))])
        assert p.low_memory_warning is True

    def test_dual_gpu_pins_freest(self) -> None:
        p = profile_from_specs([(0, _gib(4), _gib(12)), (1, _gib(11), _gib(12))])
        assert p.gpu_ids == [1]
        assert p.free_gib == 11.0

    def test_dual_gpu_name_uses_largest_total(self) -> None:
        p = profile_from_specs([(0, _gib(8), _gib(8)), (1, _gib(7), _gib(24))])
        assert "cuda-2x24g" in p.name

    def test_triple_gpu_still_pins_one(self) -> None:
        specs = [(0, _gib(1), _gib(8)), (1, _gib(9), _gib(8)), (2, _gib(2), _gib(8))]
        p = profile_from_specs(specs)
        assert p.gpu_ids == [1]

    def test_summary_includes_gpu_when_pinned(self) -> None:
        p = profile_from_specs([(0, _gib(1), _gib(12)), (1, _gib(11), _gib(12))])
        s = p.summary()
        assert "gpu=1" in s
        assert "11.0GiB" in s

    def test_summary_warns_low_memory(self) -> None:
        p = profile_from_specs([(0, _gib(1), _gib(3))])
        assert "aviso" in p.summary()


class TestHumanoidTopology:
    def test_minimal_humanoid_passes(self) -> None:
        args = _topology_from_nodes(_minimal_humanoid_nodes())
        assert _is_humanoid_topology(*args) is True

    def test_too_few_bones_rejected(self) -> None:
        nodes = [{"name": "bone_0", "children": [1]}, {"name": "bone_1"}]
        assert _is_humanoid_topology(*_topology_from_nodes(nodes)) is False

    def test_missing_root_rejected(self) -> None:
        # Todos com parent bone — nenhuma raiz
        nodes = [{"name": "bone_0", "children": [1]}, {"name": "bone_1", "children": [0]}]
        bone_nodes = {0: 0, 1: 1}
        parent_map = {0: 1, 1: 0}
        assert _is_humanoid_topology(bone_nodes, nodes, parent_map) is False

    def test_insect_many_upper_chains_rejected(self) -> None:
        # chest com 5 braços/asas
        nodes = _minimal_humanoid_nodes()
        nodes[2] = {"name": "bone_2", "children": [5, 6, 7, 8, 9]}
        for i in range(5, 10):
            nodes.append({"name": f"bone_{i}"})
        args = _topology_from_nodes(nodes)
        assert _is_humanoid_topology(*args) is False

    def test_single_root_child_rejected(self) -> None:
        nodes = [{"name": "bone_0", "children": [1]}] + [{"name": f"bone_{i}"} for i in range(1, 10)]
        assert _is_humanoid_topology(*_topology_from_nodes(nodes)) is False


class TestBoneHelpers:
    def test_linear_chain_through_single_child_links(self) -> None:
        children = {0: [1], 1: [2], 2: []}
        assert _bone_linear_chain(children, 0) == [0, 1, 2]

    def test_linear_chain_stops_at_branch(self) -> None:
        children = {0: [1, 2], 1: [], 2: []}
        assert _bone_linear_chain(children, 0) == [0]

    def test_descendants_counts_subtree(self) -> None:
        children = {0: [1, 2], 1: [3], 2: [], 3: []}
        count_fn = _bone_descendants(children)
        assert count_fn(0) == 3
        assert count_fn(2) == 0

    def test_assign_fingers_stable_names(self) -> None:
        children = {9: [10, 12], 10: [11], 12: [13]}
        assignments: dict[int, str] = {9: "LeftHand"}
        _assign_fingers(9, "Left", children, assignments)
        assert assignments[10] == "LeftHandFinger1"
        assert assignments[11] == "LeftHandFinger1_1"
        assert assignments[12] == "LeftHandFinger2"

    def test_assign_fingers_skips_when_single_child(self) -> None:
        children = {9: [10], 10: []}
        assignments: dict[int, str] = {}
        _assign_fingers(9, "Right", children, assignments)
        assert assignments == {}

    def test_assign_head_accessories(self) -> None:
        children = {6: [8, 10], 8: [9], 10: [11]}
        assignments: dict[int, str] = {6: "Head"}
        _assign_head_accessories(6, children, assignments)
        assert assignments[8] == "HeadAccessory1"
        assert assignments[9] == "HeadAccessory1_1"
        assert assignments[10] == "HeadAccessory2"


class TestCheckpointsHome:
    def test_default_cache_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("RIGGING3D_SKINTOKENS_HOME", raising=False)
        assert runner._checkpoints_home() == Path.home() / ".cache" / "rigging3d" / "skintokens"

    def test_env_absolute_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("RIGGING3D_SKINTOKENS_HOME", str(tmp_path / "ckpt"))
        assert runner._checkpoints_home() == (tmp_path / "ckpt").resolve()

    def test_env_tilde_expanded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("RIGGING3D_SKINTOKENS_HOME", "~/rig_ckpt")
        home = runner._checkpoints_home()
        assert home == Path(os.path.expanduser("~/rig_ckpt")).resolve()


class TestCliHelpGroups:
    def test_root_help(self) -> None:
        r = CliRunner().invoke(cli, ["--help"])
        assert r.exit_code == 0
        assert "SkinTokens" in r.output or "rigging" in r.output.lower()

    def test_pipeline_help(self) -> None:
        r = CliRunner().invoke(cli, ["pipeline", "--help"])
        assert r.exit_code == 0
        assert "--input" in r.output

    def test_transfer_weights_help(self) -> None:
        r = CliRunner().invoke(cli, ["transfer-weights", "--help"])
        assert r.exit_code == 0
        assert "--source" in r.output

    def test_fix_bone_orientation_help(self) -> None:
        r = CliRunner().invoke(cli, ["fix-bone-orientation", "--help"])
        assert r.exit_code == 0

    def test_no_hw_auto_in_root_help(self) -> None:
        r = CliRunner().invoke(cli, ["--help"])
        assert "--no-hw-auto" in r.output


class TestCliContextHelpers:
    def test_ctx_profiler_without_parent(self) -> None:
        from click import Context

        ctx = Context(cli)
        assert _ctx_profiler(ctx) is False

    def test_ctx_gpu_ids_without_parent(self) -> None:
        from click import Context

        ctx = Context(cli)
        assert _ctx_gpu_ids(ctx) is None
