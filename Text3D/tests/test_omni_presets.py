"""Testes dos presets Omni embutidos."""

from __future__ import annotations

from pathlib import Path

import pytest

from text3d.omni_presets import (
    BBOX_PRESETS,
    category_omni_defaults,
    merge_omni_controls,
    omni_fingerprint,
    omni_fingerprint_matches,
    quaternius_tpose_glb,
    resolve_bbox_preset,
    resolve_pose_preset,
    size_m_from_height,
    size_m_to_bbox,
    write_omni_fingerprint,
)


class TestPosePreset:
    def test_resolve_quaternius_tpose(self) -> None:
        path = resolve_pose_preset("quaternius-tpose")
        assert path.is_file()
        assert path.name == "quaternius_tpose_bone.txt"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 22  # esqueleto mínimo (sem dedos)

    def test_resolve_dwarf_tpose(self) -> None:
        path = resolve_pose_preset("quaternius-tpose-dwarf")
        assert path.is_file()
        assert path.name == "quaternius_tpose_dwarf_bone.txt"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 22  # esqueleto mínimo (sem dedos)
        # aliases
        assert resolve_pose_preset("dwarf-tpose") == path
        assert resolve_pose_preset("chibi-tpose") == path

    def test_dwarf_has_lower_shoulders_vs_adult(self) -> None:
        import numpy as np

        adult = np.loadtxt(resolve_pose_preset("quaternius-tpose"))
        dwarf = np.loadtxt(resolve_pose_preset("quaternius-tpose-dwarf"))
        # Y span do anão mais compacto (corpo baixo); X ainda ~braços abertos.
        adult_y = float(adult[:, 1].max() - adult[:, 1].min())
        dwarf_y = float(dwarf[:, 1].max() - dwarf[:, 1].min())
        assert dwarf_y < adult_y

    def test_glb_reference_exists(self) -> None:
        glb = quaternius_tpose_glb()
        assert glb.is_file()
        assert glb.suffix == ".glb"

    def test_unknown_pose_raises(self) -> None:
        with pytest.raises(KeyError, match="pose_preset"):
            resolve_pose_preset("nope")

    def test_merge_dwarf_pose(self) -> None:
        out = merge_omni_controls(pose_preset="quaternius-tpose-dwarf")
        assert out["control_type"] == "pose"
        assert Path(out["pose_file"]).name == "quaternius_tpose_dwarf_bone.txt"


class TestBboxPreset:
    def test_all_presets_three_floats(self) -> None:
        for name in BBOX_PRESETS:
            dims = resolve_bbox_preset(name)
            assert len(dims) == 3
            assert max(dims) == pytest.approx(1.0)

    def test_sword_aspect(self) -> None:
        L, H, W = resolve_bbox_preset("sword")
        assert pytest.approx(1.0) == H
        assert L < H and W < H

    def test_size_m_normalize(self) -> None:
        # Eixo maior → 1.0 (docs Omni); NÃO 2.0 (enche MC e clipa).
        assert size_m_to_bbox([0.1, 1.2, 0.1]) == pytest.approx([0.1 / 1.2, 1.0, 0.1 / 1.2])


class TestMergeOmniControls:
    def test_pose_preset_implies_pose(self) -> None:
        out = merge_omni_controls(pose_preset="quaternius-tpose")
        assert out["control_type"] == "pose"
        assert Path(out["pose_file"]).is_file()

    def test_bbox_preset_implies_bbox(self) -> None:
        out = merge_omni_controls(bbox_preset="door")
        assert out["control_type"] == "bbox"
        assert out["bbox"] == list(BBOX_PRESETS["door"])

    def test_size_alias(self) -> None:
        out = merge_omni_controls(size=[0.2, 1.0, 0.1])
        assert out["control_type"] == "bbox"
        assert out["bbox"] == [0.2, 1.0, 0.1]

    def test_category_humanoid_soft(self) -> None:
        out = merge_omni_controls(category="humanoid")
        assert out["control_type"] == "pose"
        assert out["pose_preset"] == "quaternius-apose"

    def test_size_m_alone_does_not_become_bbox(self) -> None:
        out = merge_omni_controls(size_m=[0.55, 1.55, 0.4], category="humanoid")
        assert out["control_type"] == "pose"
        assert out["bbox"] is None
        assert out["size_m"] == [0.55, 1.55, 0.4]

    def test_cube_plus_noncubic_size_m_uses_size_aspect(self) -> None:
        # slime: cube engorda; aspect size_m prevalece.
        out = merge_omni_controls(
            control_type="bbox",
            bbox_preset="cube",
            size_m=[0.7, 0.55, 0.7],
        )
        assert out["control_type"] == "bbox"
        assert out["bbox_preset"] is None
        assert out["bbox"] == pytest.approx([1.0, 0.55 / 0.7, 1.0])

    def test_blob_preset(self) -> None:
        out = merge_omni_controls(bbox_preset="blob")
        assert out["control_type"] == "bbox"
        assert out["bbox"] == pytest.approx([1.0, 1.0, 1.0])

    def test_tree_preset_not_paper_thin(self) -> None:
        out = merge_omni_controls(bbox_preset="tree")
        assert out["bbox"][0] == pytest.approx(out["bbox"][2])
        assert out["bbox"][0] >= 0.5  # L=W gordos o bastante p/ tronco cilíndrico

    def test_pose_plus_size_m_keeps_pose_no_bbox(self) -> None:
        out = merge_omni_controls(
            control_type="pose",
            pose_preset="quaternius-tpose",
            size_m=[0.55, 1.55, 0.4],
        )
        assert out["control_type"] == "pose"
        assert out["bbox"] is None

    def test_height_footprint_is_bbox_mold_not_just_scale(self) -> None:
        # Author seta altura+footprint → bbox Omni = aspect (modelo enche), + size_m.
        out = merge_omni_controls(height_m=3.5, footprint_m=0.85)
        assert out["control_type"] == "bbox"
        assert out["size_m"] == pytest.approx([0.85, 3.5, 0.85])
        assert out["bbox"] == pytest.approx(size_m_to_bbox([0.85, 3.5, 0.85]))
        assert out["bbox_preset"] is None
        # Mais fino que tree (0.55): molde coluna.
        assert out["bbox"][0] < 0.55

    def test_height_alone_with_pose_no_bbox_inject(self) -> None:
        out = merge_omni_controls(
            control_type="pose",
            pose_preset="quaternius-apose",
            height_m=1.55,
        )
        assert out["control_type"] == "pose"
        assert out["bbox"] is None
        assert out["size_m"] == pytest.approx(size_m_from_height(1.55))

    def test_height_footprint_does_not_override_user_bbox(self) -> None:
        out = merge_omni_controls(
            control_type="bbox",
            bbox=[0.5, 1.0, 0.5],
            height_m=3.5,
            footprint_m=0.85,
        )
        assert out["bbox"] == [0.5, 1.0, 0.5]
        assert out["size_m"] == pytest.approx([0.85, 3.5, 0.85])

    def test_explicit_none_beats_category(self) -> None:
        # control_type none + no other fields → category fills
        assert category_omni_defaults("weapon")["bbox_preset"] == "sword"
        out = merge_omni_controls(control_type="bbox", bbox=[1, 1, 1], category="humanoid")
        assert out["control_type"] == "bbox"
        assert out["bbox"] == [1, 1, 1]


class TestFingerprint:
    def test_roundtrip(self, tmp_path: Path) -> None:
        glb = tmp_path / "hero_shape.glb"
        glb.write_bytes(b"glTF")
        ctrl = {"control_type": "pose", "pose_preset": "quaternius-tpose"}
        write_omni_fingerprint(glb, ctrl)
        assert omni_fingerprint_matches(glb, ctrl)
        assert not omni_fingerprint_matches(glb, {"control_type": "bbox", "bbox_preset": "sword"})
        assert omni_fingerprint(ctrl)["pose_preset"] == "quaternius-tpose"
        assert omni_fingerprint(ctrl)["bbox_axis_max"] == pytest.approx(1.0)

    def test_legacy_sidecar_without_axis_max_mismatches(self, tmp_path: Path) -> None:
        import json

        glb = tmp_path / "pine_shape.glb"
        glb.write_bytes(b"glTF")
        side = Path(str(glb) + ".omni.json")
        side.write_text(
            json.dumps({"control_type": "bbox", "bbox_preset": "tree", "size_m": [2.0, 9.0, 2.0]}),
            encoding="utf-8",
        )
        assert not omni_fingerprint_matches(glb, {"control_type": "bbox", "bbox_preset": "tree"})

    def test_size_m_in_merge_and_fingerprint(self) -> None:
        out = merge_omni_controls(size_m=[6.0, 7.0, 4.5], bbox_preset="chapel")
        assert out["size_m"] == [6.0, 7.0, 4.5]
        fp = omni_fingerprint(out)
        assert fp["size_m"] == [6.0, 7.0, 4.5]
        assert omni_fingerprint({**out, "size_m": [6.0, 7.0, 5.0]})["size_m"] != fp["size_m"]

    def test_fingerprint_stores_resolved_bbox_for_preset(self) -> None:
        # Mudar BBOX_PRESETS['tree'] tem de invalidar sidecars (não só o nome).
        fp = omni_fingerprint({"control_type": "bbox", "bbox_preset": "tree"})
        assert fp["bbox"] == pytest.approx(list(BBOX_PRESETS["tree"]))
        assert fp["bbox_preset"] == "tree"

    def test_bounds_auto_normalizes_like_missing(self) -> None:
        a = omni_fingerprint({"control_type": "bbox", "bounds_mode": "auto"})
        b = omni_fingerprint({"control_type": "bbox"})
        assert a["bounds_mode"] is None
        assert a == b
        cube = omni_fingerprint({"control_type": "bbox", "bounds_mode": "cube"})
        assert cube["bounds_mode"] == "cube"

    def test_mc_auto_normalizes_like_missing(self) -> None:
        a = omni_fingerprint({"control_type": "bbox", "mc_level": "auto"})
        b = omni_fingerprint({"control_type": "bbox"})
        assert a["mc_level"] is None
        assert a == b
        explicit = omni_fingerprint({"control_type": "bbox", "mc_level": -0.002})
        assert explicit["mc_level"] == pytest.approx(-0.002)


class TestFingerprintSeed:
    def test_seed_absent_equiv_none(self) -> None:
        a = omni_fingerprint({"control_type": "bbox", "bbox_preset": "building"})
        b = omni_fingerprint({"control_type": "bbox", "bbox_preset": "building", "seed": None})
        assert a == b
        assert a["seed"] is None

    def test_seed_change_invalidates(self, tmp_path: Path) -> None:
        shape = tmp_path / "house_shape.glb"
        shape.write_bytes(b"glTF")
        base = {"control_type": "bbox", "bbox_preset": "building", "size_m": [10.0, 5.0, 6.0]}
        write_omni_fingerprint(shape, base)
        assert omni_fingerprint_matches(shape, base)
        assert omni_fingerprint_matches(shape, {**base, "seed": 90210}) is False
        write_omni_fingerprint(shape, {**base, "seed": 90210})
        assert omni_fingerprint_matches(shape, {**base, "seed": 90210})
        assert omni_fingerprint_matches(shape, base) is False

    def test_seed_normalized(self) -> None:
        fp = omni_fingerprint({"control_type": "bbox", "seed": "90210"})
        assert fp["seed"] == 90210
        fp2 = omni_fingerprint({"control_type": "bbox", "seed": ""})
        assert fp2["seed"] is None


class TestAposePreset:
    def test_resolve_apose(self) -> None:
        path = resolve_pose_preset("quaternius-apose")
        assert path.is_file()
        assert path.name == "quaternius_apose_bone.txt"
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 22  # esqueleto mínimo (sem dedos)
        # aliases
        assert resolve_pose_preset("a-pose") == path
        assert resolve_pose_preset("apose") == path

    def test_apose_arms_below_horizontal(self) -> None:
        import json
        import math

        import numpy as np

        from text3d.omni_presets import omni_data_path

        data = json.loads(omni_data_path("quaternius_apose_bone.json").read_text(encoding="utf-8"))
        for name in ("upperarm_l", "upperarm_r"):
            a, b = (np.array(data[name][i]) for i in (0, 1))
            d = b - a
            # Braços ~45° abaixo da horizontal (A-pose para musculados/gordos).
            assert math.degrees(math.atan2(d[1], abs(d[0]))) == pytest.approx(-45.0, abs=2.0)

    def test_tpose_has_no_finger_bones(self) -> None:
        import json

        from text3d.omni_presets import omni_data_path

        data = json.loads(omni_data_path("quaternius_tpose_bone.json").read_text(encoding="utf-8"))
        names = [k for k in data if k != "_meta"]
        # Esqueleto mínimo: 22 ossos, zero dedos (dedos → dedos esquisitos no Omni).
        assert len(names) == 22
        assert not [n for n in names if n.startswith(("index", "middle", "ring", "pinky", "thumb"))]
        # Guia essencial presente: coluna, cabeça, braços até ao pulso, pernas.
        for required in ("pelvis", "Head", "upperarm_l", "hand_l", "hand_r", "thigh_l", "ball_r"):
            assert required in names


class TestFingerprintMcLevelZero:
    def test_zero_literal_distinct_from_auto(self) -> None:
        """mc_level=0 literal ≠ auto (auto=-1/octree) — mudança tem de invalidar."""
        auto = omni_fingerprint({"control_type": "bbox", "mc_level": "auto"})
        zero = omni_fingerprint({"control_type": "bbox", "mc_level": 0})
        assert auto["mc_level"] is None
        assert zero["mc_level"] == 0.0
        assert auto != zero

    def test_zero_change_invalidates(self, tmp_path: Path) -> None:
        shape = tmp_path / "hero_shape.glb"
        shape.write_bytes(b"glTF")
        base = {"control_type": "pose", "pose_preset": "quaternius-tpose", "mc_level": "auto"}
        write_omni_fingerprint(shape, base)
        assert omni_fingerprint_matches(shape, base)
        assert omni_fingerprint_matches(shape, {**base, "mc_level": 0}) is False
