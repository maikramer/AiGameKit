"""Round 2 — checkpoints e _classify_row_state_master."""

from __future__ import annotations

from pathlib import Path

from glb_fixtures import write_min_glb


def _touch(p: Path) -> None:
    """Cria o ficheiro; GLBs saem parseáveis porque o resume valida geometria."""
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.suffix.lower() == ".glb":
        write_min_glb(p)
        return
    p.write_bytes(b"x")


def test_classify_master_need_image(tmp_path: Path) -> None:
    from gameassets.paths import _ROW_NEED_IMAGE, _classify_row_state_master

    img = tmp_path / "img.png"
    mesh = tmp_path / "mesh.glb"
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_IMAGE


def test_classify_master_need_topology_fix(tmp_path: Path) -> None:
    from gameassets.paths import _ROW_NEED_TOPOLOGY_FIX, _classify_row_state_master, _shape_path

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_TOPOLOGY_FIX


def test_classify_master_need_paint(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_PAINT,
        _classify_row_state_master,
        _clean_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_PAINT


def test_classify_master_need_bake_master(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_BAKE_MASTER,
        _classify_row_state_master,
        _clean_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_BAKE_MASTER


def test_classify_master_need_lod_gen(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_NEED_LOD_GEN,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    _touch(_lod_path(mesh, 0))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_NEED_LOD_GEN


def test_classify_master_done(tmp_path: Path) -> None:
    from gameassets.paths import (
        _ROW_DONE,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    _touch(_lod_path(mesh, 0))
    _touch(_lod_path(mesh, 1))
    _touch(_lod_path(mesh, 2))
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_DONE


def test_classify_master_need_rig(tmp_path: Path) -> None:
    """Round 3: painted pronto mas sem ``_rigged`` -> need_rig (rig sobre painted)."""
    from gameassets.paths import (
        _ROW_NEED_RIG,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    for p in (
        _shape_path(mesh),
        _clean_path(mesh),
        _painted_path(mesh),
        _lod_path(mesh, 0),
        _lod_path(mesh, 1),
        _lod_path(mesh, 2),
    ):
        _touch(p)
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=True, wants_animate=False
    )
    assert state == _ROW_NEED_RIG


def test_classify_master_need_animate(tmp_path: Path) -> None:
    """Round 3: ``_rigged`` pronto mas sem ``_rigged_animated`` -> need_animate."""
    from gameassets.paths import (
        _ROW_NEED_ANIMATE,
        _animated_path,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _rigged_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    for p in (
        _shape_path(mesh),
        _clean_path(mesh),
        _painted_path(mesh),
        _lod_path(mesh, 0),
        _lod_path(mesh, 1),
        _lod_path(mesh, 2),
        _rigged_path(mesh),
    ):
        _touch(p)
    assert not _animated_path(mesh).is_file()
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=True, wants_animate=True
    )
    assert state == _ROW_NEED_ANIMATE


def _minimal_rig_glb(path: Path, *, animated: bool, with_paint: bool = True) -> None:
    """GLB mínimo parseável com skin (+clip se ``animated``) para o classificador."""
    import json
    import struct

    nodes: list[dict] = [{"name": "Armature"}, {"name": "Mesh_skinned", "skin": 0, "mesh": 0}]
    root: dict = {
        "asset": {"version": "2.0"},
        "nodes": nodes,
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}}]}],
        "accessors": [{"count": 3, "type": "VEC3", "componentType": 5126}],
        "bufferViews": [{"buffer": 0, "byteLength": 36}],
        "buffers": [{"byteLength": 36}],
        "skins": [{"joints": [0]}],
        "scenes": [{"nodes": [0]}],
        "scene": 0,
    }
    if with_paint:
        root["materials"] = [{"pbrMetallicRoughness": {"baseColorTexture": {"index": 0}}}]
        root["textures"] = [{"source": 0}]
        root["images"] = [{"uri": "data:image/png;base64,x"}]
    if animated:
        root["animations"] = [{"name": "idle", "channels": [], "samplers": []}]
    js = json.dumps(root).encode("utf-8")
    js += b" " * ((4 - len(js) % 4) % 4)
    bin_chunk = b"\x00" * 36
    total = 12 + 8 + len(js) + 8 + len(bin_chunk)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"glTF"
        + struct.pack("<II", 2, total)
        + struct.pack("<I4s", len(js), b"JSON")
        + js
        + struct.pack("<I4s", len(bin_chunk), b"BIN\x00")
        + bin_chunk
    )


def test_classify_master_done_promoted_animated(tmp_path: Path) -> None:
    """Round 3: ladder animada completa (skins+clips+paint em lod0/1/2) -> DONE."""
    from gameassets.paths import (
        _ROW_DONE,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    for lvl in range(3):
        _minimal_rig_glb(_lod_path(mesh, lvl), animated=True)
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=True, wants_animate=True
    )
    assert state == _ROW_DONE


def test_classify_master_need_lod_gen_when_lod0_white(tmp_path: Path) -> None:
    """lod0 parseável sem paint com painted+intermediários presentes -> need_lod_gen.

    Sem intermediários o estado correto é need_rig (re-rigar do zero); com
    ``_rigged``/``_rigged_animated`` bons em ``_intermediate/``, basta re-correr
    a ladder (anti mesh branca do DAG antigo).
    """
    from gameassets.paths import (
        _ROW_NEED_LOD_GEN,
        _animated_path,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _rigged_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_shape_path(mesh))
    _touch(_clean_path(mesh))
    _touch(_painted_path(mesh))
    _minimal_rig_glb(_rigged_path(mesh), animated=False)
    _minimal_rig_glb(_animated_path(mesh), animated=True)
    for lvl in range(3):
        _minimal_rig_glb(_lod_path(mesh, lvl), animated=True, with_paint=lvl != 0)
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=True, wants_animate=True
    )
    assert state == _ROW_NEED_LOD_GEN


def test_resume_master_pipeline_importable() -> None:
    from gameassets import pipeline as pipeline_master

    assert hasattr(pipeline_master, "resume_master_pipeline")
    assert hasattr(pipeline_master, "run_master_pipeline")


def test_classify_master_detects_shape_in_intermediate(tmp_path: Path) -> None:
    """Round 2: shape/painted em _intermediate/ devem ser detectados (resume)."""
    from gameassets.paths import (
        _ROW_NEED_TOPOLOGY_FIX,
        _classify_row_state_master,
        _intermediate_dir,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    # shape canónico já é em _intermediate/ desde Round 2.
    canonical_shape = _shape_path(mesh)
    assert canonical_shape == _intermediate_dir(mesh) / canonical_shape.name
    _touch(canonical_shape)

    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    # Shape detectado → próximo é topology-fix (clean ainda não existe).
    assert state == _ROW_NEED_TOPOLOGY_FIX


def test_classify_master_detects_painted_in_intermediate(tmp_path: Path) -> None:
    """Round 2: painted em _intermediate/ deve ser detectado (resume)."""
    from gameassets.paths import (
        _ROW_NEED_BAKE_MASTER,
        _classify_row_state_master,
        _clean_path,
        _intermediate_dir,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    _touch(img)
    mesh = tmp_path / "mesh.glb"
    _touch(_intermediate_dir(mesh) / _shape_path(mesh).name)
    _touch(_clean_path(mesh))
    _touch(_intermediate_dir(mesh) / _painted_path(mesh).name)

    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    # Tem shape (intermediate), clean, painted (intermediate) — falta lod0.
    assert state == _ROW_NEED_BAKE_MASTER


def test_move_to_intermediate_idempotent(tmp_path: Path) -> None:
    """move_to_intermediate é safe se o ficheiro já está no destino."""
    from gameassets.paths import _intermediate_dir, _shape_path, move_to_intermediate

    mesh = tmp_path / "mesh.glb"
    intermediate = _intermediate_dir(mesh) / _shape_path(mesh).name
    intermediate.parent.mkdir(parents=True, exist_ok=True)
    intermediate.write_bytes(b"data")
    # passar o path do intermediate como src — deve preservar.
    result = move_to_intermediate(intermediate, mesh)
    assert intermediate.is_file()
    assert intermediate.read_bytes() == b"data"
    assert result.resolve() == intermediate.resolve()
