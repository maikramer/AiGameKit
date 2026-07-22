"""Testes do DAG Round 3 (rig sobre painted -> animate x1 -> ladder sobre animated).

Sem GPU/bpy/subprocessos: valida helpers de path, cleanup de legado, gates de
clips duplicadas e contratos estruturais do orquestrador (inspeção de source,
no espírito de ``test_master_topology_origin``).
"""

from __future__ import annotations

import inspect
import json
import struct
from pathlib import Path


def _minimal_glb(path: Path, *, clip_names: list[str] | None = None, with_paint: bool = True) -> None:
    """GLB mínimo com skin; ``clip_names`` controla as animations declaradas."""
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
    if clip_names:
        root["animations"] = [{"name": n, "channels": [], "samplers": []} for n in clip_names]
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


def test_rigged_and_animated_paths_live_in_intermediate(tmp_path: Path) -> None:
    from gameassets.paths import _animated_path, _intermediate_dir, _rigged_path

    mesh_final = tmp_path / "meshes" / "bandit.glb"
    mesh_final.parent.mkdir(parents=True)
    inter = _intermediate_dir(mesh_final)
    assert _rigged_path(mesh_final) == inter / "bandit_rigged.glb"
    assert _animated_path(mesh_final) == inter / "bandit_rigged_animated.glb"


def test_rigged_existing_detects_new_and_legacy_layout(tmp_path: Path) -> None:
    from gameassets.paths import _animated_existing, _animated_path, _rigged_existing, _rigged_path

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "bandit.glb"
    assert _rigged_existing(mesh_final) is None
    assert _animated_existing(mesh_final) is None

    # Layout novo (canónico): _intermediate/
    rigged = _rigged_path(mesh_final)
    rigged.parent.mkdir(parents=True)
    rigged.write_bytes(b"x")
    assert _rigged_existing(mesh_final) == rigged

    # Layout legacy (meshes/): também detectado para resume.
    animated_legacy = meshes / _animated_path(mesh_final).name
    animated_legacy.write_bytes(b"x")
    assert _animated_existing(mesh_final) == animated_legacy


def test_archive_legacy_rig_intermediates(tmp_path: Path) -> None:
    """Intermediários do DAG antigo em meshes/ -> _intermediate/; novos intocados."""
    from gameassets.paths import (
        _animated_path,
        _intermediate_dir,
        _lod_path,
        _rigged_path,
        archive_legacy_rig_intermediates,
    )

    meshes = tmp_path / "meshes"
    meshes.mkdir()
    mesh_final = meshes / "bandit.glb"
    inter = _intermediate_dir(mesh_final)

    legacy = [
        meshes / "bandit_rigged_hi.glb",
        meshes / "bandit_lod0_rigged.glb",
        meshes / "bandit_lod2_rigged.glb",
        meshes / "bandit_lod0_animated.glb",
        meshes / "bandit_lod1_pre_promote.glb",
    ]
    for f in legacy:
        f.write_bytes(b"x")
    keep = [_lod_path(mesh_final, 0), _lod_path(mesh_final, 1)]
    for f in keep:
        f.write_bytes(b"final")

    moved = archive_legacy_rig_intermediates(mesh_final)
    assert len(moved) == len(legacy)
    for f in legacy:
        assert not f.is_file(), f"{f.name} devia ter sido arquivado"
        assert (inter / f.name).is_file(), f"{f.name} não está em _intermediate/"
    for f in keep:
        assert f.is_file(), f"entregável {f.name} não pode ser arquivado"

    # Novos intermediários (em _intermediate/) não são tocados.
    rigged = _rigged_path(mesh_final)
    rigged.parent.mkdir(parents=True, exist_ok=True)
    rigged.write_bytes(b"new-rig")
    animated = _animated_path(mesh_final)
    animated.write_bytes(b"new-anim")
    moved2 = archive_legacy_rig_intermediates(mesh_final)
    assert moved2 == []
    assert rigged.read_bytes() == b"new-rig"
    assert animated.read_bytes() == b"new-anim"


def test_glb_has_duplicate_clips(tmp_path: Path) -> None:
    from gameassets.pipeline import _glb_has_duplicate_clips

    dup = tmp_path / "dup.glb"
    _minimal_glb(dup, clip_names=["attack", "attack.001", "idle"])
    assert _glb_has_duplicate_clips(dup) is True

    clean = tmp_path / "clean.glb"
    _minimal_glb(clean, clip_names=["attack", "idle", "run"])
    assert _glb_has_duplicate_clips(clean) is False

    no_anims = tmp_path / "static.glb"
    _minimal_glb(no_anims)
    assert _glb_has_duplicate_clips(no_anims) is False


def test_run_master_pipeline_rigs_painted_not_clean() -> None:
    """O argv do rig tem de apontar ao painted (Round 3), nunca ao _clean HI."""
    import re

    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    # Rig sobre o painted (round 3) — o antigo caminho usava clean_p/rigged_hi.
    m = re.search(r"_rigging3d_pipeline_argv\(\s*rigging3d_bin,\s*(\w+),\s*(\w+),", src)
    assert m is not None, "chamada _rigging3d_pipeline_argv não encontrada"
    assert m.group(1) == "painted_p", f"rig deve correr sobre painted_p, não {m.group(1)}"
    assert m.group(2) == "rigged_p", f"output do rig deve ser rigged_p, não {m.group(2)}"
    # Uso em código (argv literal / stage names), não menções em docstrings.
    assert '"transfer-weights"' not in src, "argv de transfer-weights por LOD foi removido do DAG"
    assert "rigging3d-hi" not in src, "stage rigging3d-hi (rig sobre _clean) foi removido"
    assert "rigging3d-merge-lod" not in src, "transfer-weights por LOD foi removido do DAG"
    assert "_rigged_hi_path(" not in src and "_rigged_hi_existing(" not in src


def test_run_master_pipeline_single_game_pack() -> None:
    """Um único game-pack por asset (animate), não um por LOD."""
    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    assert '"animate"' in src
    assert "animate-lod" not in src, "game-pack por LOD foi substituído por animate x1"


def test_run_master_pipeline_ladder_sources_from_rig_output() -> None:
    """A ladder LOD corre sobre o animated/rigged (não sobre o painted) no caminho rig."""
    from gameassets.pipeline import run_master_pipeline

    src = inspect.getsource(run_master_pipeline)
    assert "rig_source" in src
    assert "str(rig_source)" in src, "text3d lod deve receber rig_source (animated/rigged)"


def test_lod0_white_guard_requires_parseable_glb(tmp_path: Path) -> None:
    """Ficheiros dummy (não-GLB) em lod0 não bloqueiam o resume como 'branco'."""
    from gameassets.paths import (
        _ROW_DONE,
        _classify_row_state_master,
        _clean_path,
        _lod_path,
        _painted_path,
        _shape_path,
    )

    img = tmp_path / "img.png"
    img.write_bytes(b"png")
    mesh = tmp_path / "mesh.glb"
    for p in (_shape_path(mesh), _clean_path(mesh), _painted_path(mesh)):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    for lvl in range(3):
        _lod_path(mesh, lvl).write_bytes(b"x")  # dummy: unparseable -> não flaga branco
    state = _classify_row_state_master(
        img_final=img, mesh_final=mesh, want_texture=True, wants_rig=False, wants_animate=False
    )
    assert state == _ROW_DONE
