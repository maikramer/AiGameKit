"""Tests for pure helpers in ``rigging3d.cli``.

Backend-agnostic pieces that survive across geração backends (UniRig→SkinTokens):
bone-rename logic (``_rename_generic_bones``) — see ``test_cli.py`` for more cases.
"""

from __future__ import annotations

import json
import struct
from pathlib import Path

from rigging3d.cli import _rename_generic_bones


def _write_glb(tmp_path: Path, name: str, nodes: list[dict[str, object]]) -> Path:
    """Write a minimal GLB containing only the JSON node table."""
    glb_json = {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": nodes,
    }
    json_bytes = json.dumps(glb_json, separators=(",", ":")).encode("utf-8")
    pad = (4 - len(json_bytes) % 4) % 4
    json_bytes += b" " * pad
    bin_data = b"\x00" * 4
    total = 12 + 8 + len(json_bytes) + 8 + len(bin_data)
    glb = tmp_path / name
    with open(glb, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(json_bytes), 0x4E4F534A))
        f.write(json_bytes)
        f.write(struct.pack("<II", len(bin_data), 0x004E4942))
        f.write(bin_data)
    return glb


class TestRenameGenericBonesExtra:
    """Extra cases for ``_rename_generic_bones`` beyond those in ``test_cli.py``."""

    def test_mixamo_prefixed_names_untouched(self, tmp_path: Path) -> None:
        nodes = [
            {"name": "mixamorig:Hips", "children": [1]},
            {"name": "mixamorig:Spine"},
        ]
        glb = _write_glb(tmp_path, "mixamo.glb", nodes)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_semantic_names_untouched(self, tmp_path: Path) -> None:
        nodes = [
            {"name": "root", "children": [1, 2]},
            {"name": "J_Bip_C_Hips"},
            {"name": "J_Bip_L_UpperArm"},
        ]
        glb = _write_glb(tmp_path, "semantic.glb", nodes)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_tiny_file_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "tiny.glb"
        glb.write_bytes(b"\x00\x00")
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_bad_magic_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "bad.glb"
        glb.write_bytes(b"\x00" * 64)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_malformed_json_chunk_returns_zero(self, tmp_path: Path) -> None:
        glb = tmp_path / "malformed.glb"
        payload = b"{not valid json"
        pad = (4 - len(payload) % 4) % 4
        payload += b" " * pad
        bin_data = b"\x00" * 4
        total = 12 + 8 + len(payload) + 8 + len(bin_data)
        with open(glb, "wb") as f:
            f.write(struct.pack("<III", 0x46546C67, 2, total))
            f.write(struct.pack("<II", len(payload), 0x4E4F534A))
            f.write(payload)
            f.write(struct.pack("<II", len(bin_data), 0x004E4942))
            f.write(bin_data)
        assert _rename_generic_bones(glb, tmp_path) == 0

    def test_non_humanoid_chain_classified_as_creature(self, tmp_path: Path) -> None:
        """A 2-bone chain fails the humanoid gate → creature rename (Root + Spine).

        Since Fase 1.3, creatures are renamed by structural role (Root/Spine/
        Wings/Legs) instead of being left as bone_*.
        """
        nodes = [
            {"name": "bone_0", "children": [1]},
            {"name": "bone_1"},
        ]
        glb = _write_glb(tmp_path, "gen.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        assert count == 2
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = {n["name"] for n in data["nodes"]}
        assert "Root" in names
        assert "Spine" in names
        # No generic bone_* names remain.
        assert not any(n.startswith("bone_") for n in names)

    def test_minimal_humanoid_topology_renamed(self, tmp_path: Path) -> None:
        """A minimal humanoid skeleton (8 bones) passes the gate and is renamed.

        Topology: root → (spine→chest→(arm_l, arm_r, neck)), (leg_l), (leg_r).
        Matches the gate in ``_is_humanoid_topology``: 2 legs at root, spine≥2,
        upper chest branches into 2-3 chains.
        """
        # indices: 0=root, 1=spine, 2=chest, 3=leg_l, 4=leg_r, 5=arm_l, 6=arm_r, 7=neck
        nodes = [
            {"name": "bone_0", "children": [1, 3, 4]},
            {"name": "bone_1", "children": [2]},
            {"name": "bone_2", "children": [5, 6, 7]},
            {"name": "bone_3"},
            {"name": "bone_4"},
            {"name": "bone_5"},
            {"name": "bone_6"},
            {"name": "bone_7"},
        ]
        glb = _write_glb(tmp_path, "humanoid.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        assert count >= 5  # at least root + spine + 2 legs + some upper chains
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = {n["name"] for n in data["nodes"]}
        assert "Hips" in names  # root becomes Hips
        assert not any(n.startswith("bone_") for n in names)

    def test_branching_fingers_renamed(self, tmp_path: Path) -> None:
        """Real branching fingers (5 chains × 3 bones under each hand).

        Reproduces the witch_boss case: LeftHand branches into 5 finger chains
        of 3 phalanges each. Without the _assign_fingers recursion, these 30
        bones stayed as bone_*. They should now be LeftHandFinger1..5 (+_1/_2).
        """
        # Minimal humanoid with one arm whose hand branches into 2 fingers.
        # 0=root 1=spine 2=chest 3=legL 4=legR 5=neck 6=armL_shoulder
        # 7=armL_upper 8=armL_fore 9=armL_hand 10,11=finger1(2 bones) 12,13=finger2
        nodes = [
            {"name": "bone_0", "children": [1, 3, 4]},
            {"name": "bone_1", "children": [2]},
            {"name": "bone_2", "children": [5, 6]},
            {"name": "bone_3"},
            {"name": "bone_4"},
            {"name": "bone_5"},  # neck
            {"name": "bone_6", "children": [7]},  # shoulder
            {"name": "bone_7", "children": [8]},  # arm
            {"name": "bone_8", "children": [9]},  # forearm
            {"name": "bone_9", "children": [10, 12]},  # hand → 2 fingers
            {"name": "bone_10", "children": [11]},  # finger1 phalanx1
            {"name": "bone_11"},  # finger1 phalanx2
            {"name": "bone_12", "children": [13]},  # finger2 phalanx1
            {"name": "bone_13"},  # finger2 phalanx2
        ]
        glb = _write_glb(tmp_path, "fingers.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        assert count >= 10
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = {n["name"] for n in data["nodes"]}
        assert "LeftHandFinger1" in names
        assert "LeftHandFinger1_1" in names
        assert "LeftHandFinger2" in names
        assert not any(n.startswith("bone_") for n in names)

    def test_head_accessories_renamed(self, tmp_path: Path) -> None:
        """Children of Head (hat/hair) are renamed HeadAccessoryN.

        Reproduces the witch_boss hat: 2 chains of 2 bones each under Head.
        """
        # 0=root 1=spine 2=chest 3=legL 4=legR 5=neck→6=Head 7,8=hat1 9,10=hat2
        nodes = [
            {"name": "bone_0", "children": [1, 3, 4]},
            {"name": "bone_1", "children": [2]},
            {"name": "bone_2", "children": [5, 6, 7]},
            {"name": "bone_3"},
            {"name": "bone_4"},
            {"name": "bone_5", "children": [6, 8, 10]},  # this is the "neck" chain head
            {"name": "bone_6"},  # continues neck (Head)
            {"name": "bone_7"},  # other arm (placeholder)
            {"name": "bone_8", "children": [9]},  # accessory1 base
            {"name": "bone_9"},  # accessory1 tip
            {"name": "bone_10", "children": [11]},  # accessory2 base
            {"name": "bone_11"},  # accessory2 tip
        ]
        glb = _write_glb(tmp_path, "hat.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = {n["name"] for n in data["nodes"]}
        # At least one HeadAccessory should appear.
        assert any(n.startswith("HeadAccessory") for n in names)

    def test_pelvis_intermediate_pattern(self, tmp_path: Path) -> None:
        """legs==1 at root that branches into 2 → Pelvis intermediate bone.

        Some SkinTokens rigs insert a pelvis bone between root and the 2 legs.
        The gate accepts this; the rename names the intermediate 'Pelvis'.
        """
        # 0=root 1=spine→2=chest→(3=armL,4=armR,5=neck) 6=pelvis→(7=legL,8=legR)
        nodes = [
            {"name": "bone_0", "children": [1, 6]},
            {"name": "bone_1", "children": [2]},
            {"name": "bone_2", "children": [3, 4, 5]},
            {"name": "bone_3", "children": [9]},
            {"name": "bone_4", "children": [10]},
            {"name": "bone_5"},
            {"name": "bone_6", "children": [7, 8]},  # pelvis → 2 legs
            {"name": "bone_7", "children": [11]},
            {"name": "bone_8", "children": [12]},
            {"name": "bone_9"},  # armL hand
            {"name": "bone_10"},  # armR hand
            {"name": "bone_11"},  # legL foot
            {"name": "bone_12"},  # legR foot
        ]
        glb = _write_glb(tmp_path, "pelvis.glb", nodes)
        count = _rename_generic_bones(glb, tmp_path)
        assert count >= 8
        with open(glb, "rb") as f:
            f.read(12)
            c_len, _ = struct.unpack("<II", f.read(8))
            data = json.loads(f.read(c_len))
        names = {n["name"] for n in data["nodes"]}
        assert "Pelvis" in names
        assert "LeftUpLeg" in names
        assert "RightUpLeg" in names
