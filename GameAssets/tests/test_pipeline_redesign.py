"""Testes de regressão para o redesign da pipeline (LOD0 master).

Estes testes não correm o pipeline real (que requer GPU + bpy + npx); validam
os helpers de path, regras YAML, parsing de GLB e contratos de dataclasses
introduzidos pelo redesign.
"""

from __future__ import annotations

from pathlib import Path


def test_publish_rigged_animated_alias_and_archive(tmp_path: Path) -> None:
    """After animate→lod0 promote, alias + archive leftover lod*_rigged."""
    from gameassets.paths import (
        _animator3d_output_path,
        archive_leftover_lod_rigged,
        publish_rigged_animated_alias,
    )

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "hero.glb"
    lod0 = meshes / "hero_lod0.glb"
    rigged = meshes / "hero_lod0_rigged.glb"
    lod0.write_bytes(b"animated-content")
    rigged.write_bytes(b"stale-rig")

    alias = publish_rigged_animated_alias(mesh_final, lod0)
    assert alias == _animator3d_output_path(mesh_final)
    assert alias is not None and alias.is_file()
    assert alias.read_bytes() == b"animated-content"

    moved = archive_leftover_lod_rigged(mesh_final)
    assert len(moved) == 1
    assert not rigged.is_file()
    assert (meshes / "_intermediate" / "hero_lod0_rigged.glb").is_file()

    from gameassets.paths import (
        _clean_path,
        _intermediate_dir,
        _lod_animated_path,
        _lod_path,
        _lod_rigged_path,
        _painted_path,
        _rigged_hi_path,
        _shape_path,
    )

    mesh_final = tmp_path / "meshes" / "goblin.glb"
    mesh_final.parent.mkdir(parents=True, exist_ok=True)

    inter = _intermediate_dir(mesh_final)
    assert inter == mesh_final.parent / "_intermediate"

    # _shape_path / _painted_path nascem agora em _intermediate/ (Round 2 fix:
    # evita corrida resume↔move-to-intermediate ao fim do pipeline).
    assert _shape_path(mesh_final) == inter / "goblin_shape.glb"
    assert _painted_path(mesh_final) == inter / "goblin_painted.glb"

    # _clean_path / _rigged_hi_path nascem em _intermediate/
    assert _clean_path(mesh_final) == inter / "goblin_clean.glb"
    assert _rigged_hi_path(mesh_final) == inter / "goblin_rigged_hi.glb"

    # LOD paths em meshes/
    assert _lod_path(mesh_final, 0) == mesh_final.parent / "goblin_lod0.glb"
    assert _lod_path(mesh_final, 2) == mesh_final.parent / "goblin_lod2.glb"
    assert _lod_rigged_path(mesh_final, 1) == mesh_final.parent / "goblin_lod1_rigged.glb"
    assert _lod_animated_path(mesh_final, 0) == mesh_final.parent / "goblin_lod0_animated.glb"


def test_canonical_mesh_final_from_painted_keeps_production_in_meshes(tmp_path: Path) -> None:
    """Bug: passar ``_intermediate/id_painted.glb`` fazia LOD/collision nascerem lá."""
    from gameassets.paths import (
        _canonical_mesh_final,
        _collision_path,
        _intermediate_dir,
        _lod_path,
        _painted_path,
    )

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "witch_hut.glb"
    painted = _painted_path(mesh_final)
    assert painted == meshes / "_intermediate" / "witch_hut_painted.glb"

    assert _canonical_mesh_final(painted) == mesh_final
    assert _lod_path(painted, 0) == meshes / "witch_hut_lod0.glb"
    assert _lod_path(painted, 1) == meshes / "witch_hut_lod1.glb"
    assert _collision_path(painted) == meshes / "witch_hut_collision.glb"
    assert _intermediate_dir(painted) == meshes / "_intermediate"
    # stem composto
    assert _canonical_mesh_final(meshes / "_intermediate" / "hero_lod0_painted.glb") == meshes / "hero.glb"


def test_move_to_intermediate(tmp_path: Path) -> None:
    from gameassets.paths import _intermediate_dir, move_to_intermediate

    mesh_final = tmp_path / "goblin.glb"
    src = tmp_path / "goblin_shape.glb"
    src.write_bytes(b"glTF" + b"\x00" * 16)

    moved = move_to_intermediate(src, mesh_final)
    assert moved == _intermediate_dir(mesh_final) / "goblin_shape.glb"
    assert moved.is_file()
    assert not src.is_file()

    # Idempotente: chamar com source inexistente é no-op
    again = move_to_intermediate(src, mesh_final)
    assert again == src


def test_master_pipeline_profile_default() -> None:
    """Profile.master_pipeline defaulta a False (legacy ativo por defeito)."""
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict(
        {
            "title": "x",
            "genre": "y",
            "tone": "z",
            "style_preset": "w",
        }
    )
    # Round 2: master_pipeline é o default ON (promovido para default).
    assert p.master_pipeline is True
    assert p.master_validate is True
    assert p.master_bake_normals is False


def test_master_pipeline_profile_override() -> None:
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict(
        {
            "title": "x",
            "genre": "y",
            "tone": "z",
            "style_preset": "w",
            "master_pipeline": True,
            "master_bake_normals": True,
            "master_validate": False,
        }
    )
    assert p.master_pipeline is True
    assert p.master_bake_normals is True
    assert p.master_validate is False


def test_rules_yaml_present() -> None:
    """As 5 regras (lod0/1/2/rigged/collision) devem existir."""
    rules_dir = Path(__file__).resolve().parent.parent / "src" / "gameassets" / "data" / "rules"
    for name in ("lod0", "lod1", "lod2", "rigged", "collision"):
        p = rules_dir / f"{name}.yaml"
        assert p.is_file(), f"regra ausente: {p}"
        text = p.read_text()
        assert text.strip(), f"regra vazia: {p}"


def test_rules_lod0_required_attrs() -> None:
    import yaml

    rules_dir = Path(__file__).resolve().parent.parent / "src" / "gameassets" / "data" / "rules"
    rules = yaml.safe_load((rules_dir / "lod0.yaml").read_text())
    attrs = set(rules.get("attributes_required") or [])
    assert {"POSITION", "NORMAL", "TEXCOORD_0", "TANGENT"}.issubset(attrs)
    assert rules.get("texture_format") == "ktx2"
    assert rules.get("compression") == "meshopt"
    assert rules["mesh_totals"]["v_per_tri"]["max"] == 1.6
    # Categorias do plano garantidas
    per_cat = rules["face_count"]["max_per_category"]
    for cat in ("humanoid", "creature", "weapon", "chest"):
        assert cat in per_cat, f"categoria {cat} ausente"


def test_pipeline_master_module_imports() -> None:
    """O módulo do orquestrador deve ser importável."""
    import gameassets.pipeline as pm

    assert hasattr(pm, "run_master_pipeline")
    assert hasattr(pm, "aggregate_master_results")
    assert hasattr(pm, "MasterPipelineResult")
    assert hasattr(pm, "StageResult")


def test_target_faces_examples() -> None:
    """Smoke: target_faces da categoria humanoid não regrediu."""
    from gameassets.categories import get_target_faces

    tf_humanoid = get_target_faces("humanoid", face_ratio=1.0)
    assert tf_humanoid > 0
    assert tf_humanoid <= 38400  # alinhado com regra lod0
