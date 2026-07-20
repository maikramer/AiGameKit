"""Testes dos controlos Omni no GameAssets."""

from __future__ import annotations

from pathlib import Path

import pytest

from gameassets.omni_ctrl import (
    OmniControls,
    merge_omni,
    omni_from_dict,
    omni_is_active,
    omni_to_batch_item,
    omni_to_cli_flags,
    prepare_shape_for_generation,
    resolve_point_from,
    shape_omni_stale,
)


class TestOmniFromDict:
    def test_empty(self) -> None:
        assert omni_from_dict(None) == OmniControls()

    def test_pose_preset(self) -> None:
        o = omni_from_dict({"control_type": "pose", "pose_preset": "quaternius-tpose"})
        assert o.control_type == "pose"
        assert o.pose_preset == "quaternius-tpose"
        assert omni_is_active(o)

    def test_height_footprint_expands_to_bbox_mold(self) -> None:
        pytest.importorskip("text3d.omni_presets")
        from gameassets.omni_ctrl import expand_omni_world_size, omni_to_batch_item

        o = omni_from_dict({"height_m": 3.5, "footprint_m": 0.85})
        assert o.height_m == 3.5
        exp = expand_omni_world_size(o)
        assert exp.control_type == "bbox"
        assert exp.size_m == pytest.approx((0.85, 3.5, 0.85))
        assert exp.bbox is not None
        item = omni_to_batch_item(o)
        assert item["size_m"] == pytest.approx([0.85, 3.5, 0.85])
        assert "bbox" in item


class TestMergeAndFlags:
    def test_row_overrides_profile(self) -> None:
        base = OmniControls(bbox_preset="crate")
        row = OmniControls(pose_preset="quaternius-tpose", control_type="pose")
        m = merge_omni(base, row)
        assert m.control_type == "pose"
        assert m.pose_preset == "quaternius-tpose"
        assert m.bbox_preset == "crate"

    def test_cli_flags(self) -> None:
        flags = omni_to_cli_flags(OmniControls(control_type="pose", pose_preset="quaternius-tpose"))
        assert "--pose-preset" in flags
        assert "quaternius-tpose" in flags

    def test_batch_item(self) -> None:
        item = omni_to_batch_item(OmniControls(bbox_preset="sword", control_type="bbox"))
        assert item["bbox_preset"] == "sword"
        assert item["control_type"] == "bbox"

    def test_point_from(self, tmp_path: Path) -> None:
        shape = tmp_path / "hero_shape.glb"
        shape.write_bytes(b"glTF")
        o = resolve_point_from(OmniControls(point_from="hero"), sibling_shape=shape)
        assert o.control_type == "point"
        assert o.point_cloud == str(shape.resolve())

    def test_point_from_missing(self) -> None:
        with pytest.raises(FileNotFoundError):
            resolve_point_from(OmniControls(point_from="hero"), sibling_shape=None)


class TestPrepareShapeForGeneration:
    def test_missing_shape_needs_gen(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        assert prepare_shape_for_generation(shape, OmniControls(), force=False) is True
        assert not shape.is_file()

    def test_missing_shape_with_orphan_clean_still_generates(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        clean = tmp_path / "chapel_clean.glb"
        clean.write_bytes(b"glTF")
        # Clean órfão não salta Stage 1 (evita paint sobre mesh gorda/stale).
        assert (
            prepare_shape_for_generation(
                shape,
                OmniControls(bbox_preset="chapel", control_type="bbox"),
                force=False,
                clean_glb=clean,
            )
            is True
        )

    def test_force_unlinks_existing(self, tmp_path: Path) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"old")
        side = Path(str(shape) + ".omni.json")
        side.write_text("{}")
        assert prepare_shape_for_generation(shape, OmniControls(), force=True) is True
        assert not shape.is_file()
        assert not side.is_file()

    def test_fresh_shape_skips_without_force(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        monkeypatch.setattr(
            "gameassets.omni_ctrl.shape_omni_stale",
            lambda *_a, **_k: False,
        )
        assert prepare_shape_for_generation(shape, OmniControls(size_m=(6, 7, 4.5)), force=False) is False
        assert shape.is_file()

    def test_stale_unlinks_and_regens(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"old")
        monkeypatch.setattr(
            "gameassets.omni_ctrl.shape_omni_stale",
            lambda *_a, **_k: True,
        )
        assert prepare_shape_for_generation(shape, OmniControls(bbox_preset="chapel"), force=False) is True
        assert not shape.is_file()


class TestShapeOmniStaleDecodeKnobs:
    def test_bounds_mode_change_stales(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        write_omni_fingerprint(
            shape,
            {
                "control_type": "bbox",
                "bbox_preset": "chapel",
                "size_m": [6.0, 7.0, 4.5],
                "bounds_mode": "auto",
            },
        )
        omni = OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(6.0, 7.0, 4.5))
        assert shape_omni_stale(shape, omni, bounds_mode="auto") is False
        assert shape_omni_stale(shape, omni, bounds_mode="cube") is True

    def test_size_m_change_stales(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "chapel_shape.glb"
        shape.write_bytes(b"glTF")
        write_omni_fingerprint(
            shape,
            {"control_type": "bbox", "bbox_preset": "chapel", "size_m": [6.0, 7.0, 4.5]},
        )
        assert (
            shape_omni_stale(
                shape,
                OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(6.0, 7.0, 4.5)),
            )
            is False
        )
        assert (
            shape_omni_stale(
                shape,
                OmniControls(control_type="bbox", bbox_preset="chapel", size_m=(8.0, 7.0, 4.5)),
            )
            is True
        )


class TestShapeOmniStaleSeed:
    def test_seed_override_stales(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "village_longhouse_shape.glb"
        shape.write_bytes(b"glTF")
        omni = OmniControls(control_type="bbox", bbox_preset="building", size_m=(10.0, 5.0, 6.0))
        write_omni_fingerprint(
            shape,
            {"control_type": "bbox", "bbox_preset": "building", "size_m": [10.0, 5.0, 6.0]},
        )
        # Sidecar sem seed ≡ sem override → fresco.
        assert shape_omni_stale(shape, omni) is False
        # Manifest passa a ter seed: → stale (re-roll cirúrgico).
        assert shape_omni_stale(shape, omni, seed=90210) is True

    def test_seed_match_stays_fresh(self, tmp_path: Path) -> None:
        pytest.importorskip("text3d.omni_presets")
        from text3d.omni_presets import write_omni_fingerprint

        shape = tmp_path / "village_longhouse_shape.glb"
        shape.write_bytes(b"glTF")
        omni = OmniControls(control_type="bbox", bbox_preset="building", size_m=(10.0, 5.0, 6.0))
        write_omni_fingerprint(
            shape,
            {
                "control_type": "bbox",
                "bbox_preset": "building",
                "size_m": [10.0, 5.0, 6.0],
                "seed": 90210,
            },
        )
        assert shape_omni_stale(shape, omni, seed=90210) is False
        # Mudar (ou tirar) o override volta a invalidar — só este shape.
        assert shape_omni_stale(shape, omni, seed=12345) is True
        assert shape_omni_stale(shape, omni) is True


class TestBboxConstraintWarnings:
    def test_tree_oak_narrow_tree_preset_warns(self) -> None:
        from gameassets.omni_ctrl import omni_bbox_constraint_warnings

        pytest.importorskip("text3d.omni_presets")
        # tree=0.55; size aspect L=W=0.75 → bbox contrai vs size (copa larga).
        omni = OmniControls(control_type="bbox", bbox_preset="tree", size_m=(6.0, 8.0, 6.0))
        warns = omni_bbox_constraint_warnings(omni)
        assert len(warns) == 2  # eixos L e W
        assert all("< size_m aspect no eixo" in w for w in warns)

    def test_scorpion_nest_building_preset_warns(self) -> None:
        from gameassets.omni_ctrl import omni_bbox_constraint_warnings

        pytest.importorskip("text3d.omni_presets")
        omni = OmniControls(control_type="bbox", bbox_preset="building", size_m=(2.5, 1.2, 2.5))
        assert omni_bbox_constraint_warnings(omni)

    def test_slack_direction_is_silent(self) -> None:
        from gameassets.omni_ctrl import omni_bbox_constraint_warnings

        pytest.importorskip("text3d.omni_presets")
        # goblin: caixa chibi MAIOR que o aspecto size_m (folga) → sem aviso.
        goblin = OmniControls(control_type="bbox", bbox_preset="humanoid-child", size_m=(0.45, 1.15, 0.35))
        assert omni_bbox_constraint_warnings(goblin) == []
        # slime: blob ≈ aspecto size_m → sem aviso.
        slime = OmniControls(control_type="bbox", bbox_preset="blob", size_m=(0.7, 0.55, 0.7))
        assert omni_bbox_constraint_warnings(slime) == []
        # wolf: quadruped ≥ size_m aspect nos 3 eixos → sem aviso (problema era a imagem).
        wolf = OmniControls(control_type="bbox", bbox_preset="quadruped", size_m=(1.4, 0.75, 0.45))
        assert omni_bbox_constraint_warnings(wolf) == []

    def test_explicit_bbox_and_no_size_m(self) -> None:
        from gameassets.omni_ctrl import omni_bbox_constraint_warnings

        # bbox explícito igual ao aspecto size_m → silêncio (o fix recomendado).
        fixed = OmniControls(control_type="bbox", bbox=(0.5, 1.0, 0.5), size_m=(4.0, 8.0, 4.0))
        assert omni_bbox_constraint_warnings(fixed) == []
        # Sem size_m não há referência → sem aviso.
        assert omni_bbox_constraint_warnings(OmniControls(control_type="bbox", bbox_preset="tree")) == []
        # Weapon/tool: presets finos intencionais (protrusões vazam) → isentos.
        axe = OmniControls(control_type="bbox", bbox_preset="sword", size_m=(0.35, 0.9, 0.12))
        assert omni_bbox_constraint_warnings(axe, category="weapon") == []


class TestDerivedCascade:
    def test_unlink_cascades_downstream(self, tmp_path: Path) -> None:
        from gameassets.omni_ctrl import _unlink_shape_outputs

        meshes = tmp_path / "meshes"
        inter = meshes / "_intermediate"
        inter.mkdir(parents=True)
        shape = inter / "wolf_shape.glb"
        shape.write_bytes(b"glTF")
        Path(str(shape) + ".omni.json").write_text("{}")
        derived = [
            meshes / "wolf.glb",
            inter / "wolf_clean.glb",
            inter / "wolf_painted.glb",
            inter / "wolf_to_paint.glb",
            inter / "wolf_rigged_hi.glb",
            meshes / "wolf_lod0.glb",
            meshes / "wolf_lod1.glb",
            meshes / "wolf_collision.glb",
            meshes / "wolf_rigged_animated.glb",
        ]
        for f in derived:
            f.write_bytes(b"glTF")
        keep = [inter / "wolf_spider_shape.glb", meshes / "wolf_spider_clean.glb"]
        for f in keep:
            f.write_bytes(b"glTF")

        _unlink_shape_outputs(shape)

        assert not shape.exists()
        assert not Path(str(shape) + ".omni.json").exists()
        for f in derived:
            assert not f.exists(), f
        for f in keep:
            assert f.exists(), f

    def test_prefix_safety_rock_vs_rock_mossy(self, tmp_path: Path) -> None:
        from gameassets.omni_ctrl import _derived_glb_paths_for_shape

        inter = tmp_path / "meshes" / "_intermediate"
        inter.mkdir(parents=True)
        shape = inter / "moss_rock_shape.glb"
        shape.write_bytes(b"glTF")
        own = inter / "moss_rock_clean.glb"
        other = inter / "rock_mossy_clean.glb"
        own.write_bytes(b"glTF")
        other.write_bytes(b"glTF")

        paths = _derived_glb_paths_for_shape(shape)
        assert own in paths
        assert other not in paths
