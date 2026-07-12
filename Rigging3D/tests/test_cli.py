"""Testes do CLI (sem executar inferência real)."""

from __future__ import annotations

import json
import struct
from pathlib import Path

from click.testing import CliRunner
from rigging3d import __version__
from rigging3d.cli import cli

# ── Help / version ─────────────────────────────────────────────────────


class TestHelp:
    def test_root_help(self) -> None:
        result = CliRunner().invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "rigging3d" in result.output.lower()

    def test_version(self) -> None:
        result = CliRunner().invoke(cli, ["--version"])
        assert result.exit_code == 0
        assert __version__ in result.output

    def test_pipeline_help(self) -> None:
        result = CliRunner().invoke(cli, ["pipeline", "--help"])
        assert result.exit_code == 0
        assert "--input" in result.output
        assert "--output" in result.output
        assert "--num-beams" in result.output

    def test_transfer_weights_help(self) -> None:
        result = CliRunner().invoke(cli, ["transfer-weights", "--help"])
        assert result.exit_code == 0
        assert "--source" in result.output
        assert "--target" in result.output

    def test_fix_bone_orientation_help(self) -> None:
        result = CliRunner().invoke(cli, ["fix-bone-orientation", "--help"])
        assert result.exit_code == 0
        assert "--min-gap" in result.output


# ── pipeline ────────────────────────────────────────────────────────────


class TestPipeline:
    def test_requires_input(self) -> None:
        result = CliRunner().invoke(cli, ["pipeline"], catch_exceptions=False)
        assert result.exit_code != 0

    def test_requires_existing_input(self, tmp_path: Path) -> None:
        result = CliRunner().invoke(
            cli,
            ["pipeline", "-i", str(tmp_path / "missing.glb"), "-o", str(tmp_path / "out.glb")],
            catch_exceptions=False,
        )
        assert result.exit_code != 0

    def test_rejects_bad_quality(self, tmp_path: Path) -> None:
        mesh = tmp_path / "in.glb"
        mesh.write_bytes(b"x")
        result = CliRunner().invoke(
            cli,
            ["pipeline", "-i", str(mesh), "-o", str(tmp_path / "out.glb"), "--quality", "ultra-mega"],
            catch_exceptions=False,
        )
        assert result.exit_code != 0


# ── transfer-weights ─────────────────────────────────────────────────────


class TestTransferWeights:
    def test_requires_source_and_target(self) -> None:
        result = CliRunner().invoke(cli, ["transfer-weights"], catch_exceptions=False)
        assert result.exit_code != 0

    def test_output_count_must_match_targets(self, tmp_path: Path) -> None:
        source = tmp_path / "source.glb"
        target = tmp_path / "target.glb"
        source.write_bytes(b"x")
        target.write_bytes(b"x")
        result = CliRunner().invoke(
            cli,
            [
                "transfer-weights",
                "-s",
                str(source),
                "-t",
                str(target),
                "-o",
                str(tmp_path / "a.glb"),
                "-o",
                str(tmp_path / "b.glb"),
            ],
        )
        assert result.exit_code != 0


# ── _rename_generic_bones (classificação topológica) ────────────────────

_HUMANOID_PARENTS = [
    None,
    0,
    1,
    2,
    3,
    4,
    3,
    6,
    7,
    8,
    9,
    10,
    11,
    3,
    13,
    14,
    15,
    16,
    17,
    18,
    0,
    20,
    21,
    22,
    0,
    24,
    25,
    26,
]


def _build_glb_with_bones(tmp_path: Path, parents: list[int | None], prefix: str = "bone_") -> Path:
    """Create a minimal GLB file with bone nodes following *parents*."""
    n = len(parents)
    # Build node hierarchy
    nodes: list[dict[str, object]] = []
    children_of: dict[int, list[int]] = {}
    for i in range(n):
        children_of.setdefault(parents[i], []).append(i) if parents[i] is not None else None
    for i in range(n):
        node: dict[str, object] = {"name": f"{prefix}{i}"}
        kids = children_of.get(i)
        if kids:
            node["children"] = kids
        nodes.append(node)
    # Add mesh + armature wrapper nodes
    mesh_node = {"name": "geometry_0", "mesh": 0, "skin": 0}
    armature_node = {"name": "Armature", "children": [n, len(nodes)]}  # mesh_idx, bone_root
    world_node = {"name": "world", "children": [len(nodes) + 1]}
    # Find root bone index
    root_bi = parents.index(None)
    armature_node["children"] = [len(nodes), root_bi]
    nodes.append(mesh_node)
    nodes.append(armature_node)
    nodes.append(world_node)

    glb_json = {
        "asset": {"version": "2.0", "generator": "test"},
        "scene": len(nodes) - 1,
        "scenes": [{"nodes": [len(nodes) - 1]}],
        "nodes": nodes,
        "meshes": [{"primitives": []}],
        "skins": [{"joints": list(range(n))}],
    }
    json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad
    # Minimal BIN chunk
    bin_data = b"\x00" * 4
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    glb_path = tmp_path / "test.glb"
    with open(glb_path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_data), 0x004E4942))
        f.write(bin_data)
    return glb_path


def _read_glb_node_names(glb_path: Path) -> list[str]:
    """Read node names from GLB."""
    with open(glb_path, "rb") as f:
        f.read(12)
        c_len, _ = struct.unpack("<II", f.read(8))
        data = json.loads(f.read(c_len))
    return [n.get("name", "") for n in data.get("nodes", [])]


class TestRenameGenericBones:
    """Tests for ``_rename_generic_bones`` tree-based classification."""

    def test_humanoid_28_bones(self, tmp_path: Path) -> None:
        from rigging3d.cli import _rename_generic_bones

        glb = _build_glb_with_bones(tmp_path, _HUMANOID_PARENTS)
        root = tmp_path  # root unused by new implementation
        count = _rename_generic_bones(glb, root)
        names = _read_glb_node_names(glb)

        # All 28 bones should be renamed (body + finger chains)
        assert count == 28

        # Verify key bone names — naming canónico Quaternius/UE5.
        bone_names = {n for n in names if not n.startswith("_")}
        expected = {
            "pelvis",
            "spine_01",
            "spine_02",
            "spine_03",
            "neck_01",
            "Head",
            "clavicle_l",
            "upperarm_l",
            "lowerarm_l",
            "hand_l",
            "clavicle_r",
            "upperarm_r",
            "lowerarm_r",
            "hand_r",
            "thigh_l",
            "calf_l",
            "foot_l",
            "ball_l",
            "thigh_r",
            "calf_r",
            "foot_r",
            "ball_r",
            # Finger bones (3 per hand, beyond the 4-arm template)
            "LeftHandFinger1",
            "LeftHandFinger2",
            "LeftHandFinger3",
            "RightHandFinger1",
            "RightHandFinger2",
            "RightHandFinger3",
        }
        assert expected <= bone_names

        # No generic bone_* names should remain
        generic = {n for n in names if n.startswith("bone_")}
        assert len(generic) == 0

    def test_no_bones_no_change(self, tmp_path: Path) -> None:
        from rigging3d.cli import _rename_generic_bones

        # GLB with no bone_ nodes
        nodes = [{"name": "mesh"}, {"name": "root", "children": [0]}]
        glb_json = {
            "asset": {"version": "2.0"},
            "scene": 1,
            "scenes": [{"nodes": [1]}],
            "nodes": nodes,
        }
        json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
        pad = (4 - len(json_bytes) % 4) % 4
        json_bytes += b" " * pad
        bin_data = b"\x00" * 4
        total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
        glb = tmp_path / "empty.glb"
        with open(glb, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
            f.write(json_bytes)
            f.write(struct.pack("<II", len(bin_data), 0x004E4942))
            f.write(bin_data)

        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_already_named_no_change(self, tmp_path: Path) -> None:
        """Bones with non-generic names should not be touched."""
        from rigging3d.cli import _rename_generic_bones

        # Same topology but bones already have semantic names
        parents = [None, 0, 1, 2, 3, 4, 3, 6, 7, 8, 9, 10, 11, 3, 13, 14, 15, 16, 17, 18, 0, 20, 21, 22, 0, 24, 25, 26]
        glb = _build_glb_with_bones(tmp_path, parents, prefix="J_Bip_")
        assert _rename_generic_bones(glb, tmp_path) == 0
