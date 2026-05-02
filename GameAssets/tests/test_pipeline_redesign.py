"""Testes de regressão para o redesign da pipeline (LOD0 master).

Estes testes não correm o pipeline real (que requer GPU + bpy + npx); validam
os helpers de path, regras YAML, parsing de GLB e contratos de dataclasses
introduzidos pelo redesign.
"""

from __future__ import annotations

from pathlib import Path


def test_intermediate_dir_layout(tmp_path: Path) -> None:
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
