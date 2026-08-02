"""Testes de regressão para o redesign da pipeline (LOD0 master).

Estes testes não correm o pipeline real (que requer GPU + bpy + npx); validam
os helpers de path, regras YAML, parsing de GLB e contratos de dataclasses
introduzidos pelo redesign.
"""

from __future__ import annotations

from pathlib import Path


def test_finalize_deliverables_archives_bare_and_aliases(tmp_path: Path) -> None:
    """Com lod0: bare + *_rigged_animated + lod*_rigged → _intermediate/."""
    from gameassets.paths import (
        archive_leftover_lod_rigged,
        finalize_mesh_deliverables,
    )

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "hero.glb"
    lod0 = meshes / "hero_lod0.glb"
    rigged = meshes / "hero_lod0_rigged.glb"
    alias = meshes / "hero_rigged_animated.glb"
    lod0.write_bytes(b"animated-content")
    mesh_final.write_bytes(b"bare-dup")
    rigged.write_bytes(b"stale-rig")
    alias.write_bytes(b"alias-dup")

    moved = archive_leftover_lod_rigged(mesh_final)
    assert len(moved) == 1
    assert not rigged.is_file()
    assert (meshes / "_intermediate" / "hero_lod0_rigged.glb").is_file()

    finalized = finalize_mesh_deliverables(mesh_final)
    assert not mesh_final.is_file()
    assert not alias.is_file()
    assert lod0.is_file()
    assert (meshes / "_intermediate" / "hero.glb").is_file()
    assert (meshes / "_intermediate" / "hero_rigged_animated.glb").is_file()
    assert len(finalized) >= 2


def test_finalize_promotes_bare_to_lod0_when_missing(tmp_path: Path) -> None:
    from gameassets.paths import _lod_path, finalize_mesh_deliverables

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "crate.glb"
    mesh_final.write_bytes(b"only-bare")
    finalize_mesh_deliverables(mesh_final)
    lod0 = _lod_path(mesh_final, 0)
    assert lod0.is_file()
    assert lod0.read_bytes() == b"only-bare"
    assert not mesh_final.is_file()


def test_path_helpers_layout(tmp_path: Path) -> None:
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


def _minimal_glb(path: Path, *, animated: bool) -> None:
    """GLB mínimo com (opcional) skin+clip+paint — suficiente para classifiers."""
    import json
    import struct

    nodes: list[dict] = [{"name": "Mesh"}]
    meshes = [{"primitives": [{"attributes": {"POSITION": 0}}]}]
    materials = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
    images = [{"uri": "data:image/png;base64,x"}]
    textures = [{"source": 0}]
    accessors = [{"count": 3, "type": "VEC3", "componentType": 5126}]
    buffer_views = [{"buffer": 0, "byteLength": 36}]
    buffers = [{"byteLength": 36}]
    skins: list[dict] = []
    animations: list[dict] = []
    if animated:
        nodes = [{"name": "Armature"}, {"name": "Mesh_skinned", "skin": 0, "mesh": 0}]
        skins = [{"joints": [0]}]
        animations = [{"name": "idle", "channels": [], "samplers": []}]
    root = {
        "asset": {"version": "2.0"},
        "nodes": nodes,
        "meshes": meshes,
        "materials": materials,
        "textures": textures,
        "images": images,
        "accessors": accessors,
        "bufferViews": buffer_views,
        "buffers": buffers,
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    if skins:
        root["skins"] = skins
    if animations:
        root["animations"] = animations
    js = json.dumps(root).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_chunk = b"\x00" * 36
    bin_chunk += b"\x00" * ((4 - len(bin_chunk) % 4) % 4)
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)
    path.write_bytes(
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
        + struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
        + bin_chunk
    )


def test_glb_is_promoted_animated_detects_skin_and_clips(tmp_path: Path) -> None:
    from gameassets.paths import _glb_is_promoted_animated, _glb_is_promoted_rigged

    anim = tmp_path / "hero_lod0.glb"
    static = tmp_path / "crate_lod0.glb"
    _minimal_glb(anim, animated=True)
    _minimal_glb(static, animated=False)
    assert _glb_is_promoted_animated(anim) is True
    assert _glb_is_promoted_rigged(anim) is True
    assert _glb_is_promoted_animated(static) is False


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


def _touch(p: Path) -> Path:
    from tests.glb_fixtures import write_min_glb

    return write_min_glb(p)


def test_collision_source_prefers_clean_for_static(tmp_path: Path) -> None:
    """Estático: ``_clean`` (fechado) em vez de ``_painted`` (94/109 fechados)."""
    from gameassets.pipeline import collision_source

    meshes = tmp_path / "meshes"
    mesh_final = meshes / "crate.glb"
    inter = meshes / "_intermediate"
    clean = _touch(inter / "crate_clean.glb")
    painted = _touch(inter / "crate_painted.glb")
    lod0 = _touch(meshes / "crate_lod0.glb")

    assert collision_source(mesh_final, painted, lod0, rigged=False) == clean


def test_collision_source_falls_back_to_painted(tmp_path: Path) -> None:
    from gameassets.pipeline import collision_source

    meshes = tmp_path / "meshes"
    mesh_final = meshes / "crate.glb"
    painted = _touch(meshes / "_intermediate" / "crate_painted.glb")
    lod0 = _touch(meshes / "crate_lod0.glb")

    assert collision_source(mesh_final, painted, lod0, rigged=False) == painted


def test_collision_source_uses_lod0_when_rigged(tmp_path: Path) -> None:
    """Riggado: o painted está em T-pose (herói 1.27 m vs 0.67 m posado)."""
    from gameassets.pipeline import collision_source

    meshes = tmp_path / "meshes"
    mesh_final = meshes / "hero.glb"
    inter = meshes / "_intermediate"
    _touch(inter / "hero_clean.glb")
    painted = _touch(inter / "hero_painted.glb")
    lod0 = _touch(meshes / "hero_lod0.glb")

    assert collision_source(mesh_final, painted, lod0, rigged=True) == lod0


def test_collision_alignment_deviation_flags_offset(tmp_path: Path) -> None:
    from tests.glb_fixtures import write_glb_with_bounds

    from gameassets.pipeline import collision_alignment_deviation

    lod0 = write_glb_with_bounds(tmp_path / "a_lod0.glb", [-1, 0, -1], [1, 2, 1])
    aligned = write_glb_with_bounds(tmp_path / "a_collision.glb", [-1, 0, -1], [1, 2, 1])
    assert collision_alignment_deviation(aligned, lod0) == 0.0

    off = write_glb_with_bounds(tmp_path / "b_collision.glb", [-1, 0, -1], [1, 2.5, 1])
    dev = collision_alignment_deviation(off, lod0)
    assert dev is not None and dev == 0.5


def test_collision_alignment_skips_skinned(tmp_path: Path) -> None:
    """Skinned: accessors em espaço de bind — sem veredicto."""
    from tests.glb_fixtures import write_glb_with_bounds

    from gameassets.pipeline import collision_alignment_deviation

    lod0 = write_glb_with_bounds(tmp_path / "s_lod0.glb", [-1, 0, -1], [1, 2, 1], skinned=True)
    coll = write_glb_with_bounds(tmp_path / "s_collision.glb", [-9, 0, -9], [9, 9, 9])
    assert collision_alignment_deviation(coll, lod0) is None
