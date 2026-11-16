"""Testes do contrato ``gameassets studio`` usado pelo AiGameKit Studio."""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path

import pytest
from click.testing import CliRunner
from glb_fixtures import write_min_glb

from gameassets.cli import main as cli

PROFILE = """\
title: Studio test
genre: fantasy
tone: bright and readable
style_preset: lowpoly
output_dir: .
path_layout: split
images_subdir: images
meshes_subdir: meshes
image_ext: png
generation: fast
text3d:
  preset: fast
paint3d:
  texture_size: 512
refine:
  enabled: true
  views: 1
lod:
  rig_max_level: 2
collision:
  mode: hull
"""

MANIFEST = """\
assets:
  - id: test_prop
    idea: "a small wooden crate with iron bands"
    category: prop
    pipeline: [3d, paint, lod, collision]
    generation: fast
"""


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project(tmp_path: Path) -> Path:
    (tmp_path / "images").mkdir()
    (tmp_path / "meshes" / "_intermediate").mkdir(parents=True)
    (tmp_path / "game.yaml").write_text(PROFILE, encoding="utf-8")
    (tmp_path / "manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    return tmp_path


def _invoke(runner: CliRunner, project: Path, *args: str) -> dict:
    result = runner.invoke(
        cli,
        ["studio", *args, "--project", str(project), "--json"],
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _paths(project: Path) -> dict[str, Path]:
    meshes = project / "meshes"
    inter = meshes / "_intermediate"
    return {
        "image": project / "images" / "test_prop.png",
        "shape": inter / "test_prop_shape.glb",
        "clean": inter / "test_prop_clean.glb",
        "refined": inter / "test_prop_refined.glb",
        "to_paint": inter / "test_prop_to_paint.glb",
        "painted": inter / "test_prop_painted.glb",
        "rigged": inter / "test_prop_rigged.glb",
        "animated": inter / "test_prop_rigged_animated.glb",
        "lod0": meshes / "test_prop_lod0.glb",
        "lod1": meshes / "test_prop_lod1.glb",
        "lod2": meshes / "test_prop_lod2.glb",
        "collision": meshes / "test_prop_collision.glb",
        "precompute": meshes / "test_prop_precompute.json",
    }


def _write_all_artifacts(project: Path) -> dict[str, Path]:
    paths = _paths(project)
    paths["image"].write_bytes(b"png")
    for name in (
        "shape",
        "clean",
        "refined",
        "to_paint",
        "painted",
        "rigged",
        "animated",
        "lod0",
        "lod1",
        "lod2",
        "collision",
    ):
        write_min_glb(paths[name])
    paths["precompute"].write_text("{}", encoding="utf-8")
    (Path(str(paths["shape"]) + ".omni.json")).write_text("{}", encoding="utf-8")
    (Path(str(paths["refined"]) + ".multiview.json")).write_text("{}", encoding="utf-8")
    return paths


def test_studio_check_initial_state(runner: CliRunner, project: Path) -> None:
    payload = _invoke(runner, project, "check")
    assert payload["version"] == 1
    assert payload["asset"]["id"] == "test_prop"
    assert payload["asset"]["category"] == "prop"
    assert payload["asset"]["pipeline"] == ["3d", "paint", "lod", "collision"]
    assert payload["state"] == "need_image"
    assert payload["prompt"]["text2d"]
    assert payload["prompt"]["text3d_hint"]
    assert payload["refine"]["enabled"] is True
    assert payload["refine"]["views"] == ["front"]
    assert payload["artifacts"]["shape"]["exists"] is False
    assert payload["lock"] is None
    # O contrato é a CHAVE existir (o valor é o caminho resolvido ou None);
    # a CI não tem os CLIs do AiGameKit instalados no job do GameAssets.
    assert "text3d" in payload["tools"]


def test_studio_check_master_state_progression(runner: CliRunner, project: Path) -> None:
    paths = _paths(project)
    assert _invoke(runner, project, "check")["state"] == "need_image"

    paths["image"].write_bytes(b"png")
    assert _invoke(runner, project, "check")["state"] == "need_shape"

    write_min_glb(paths["shape"])
    assert _invoke(runner, project, "check")["state"] == "need_topology_fix"

    write_min_glb(paths["clean"])
    assert _invoke(runner, project, "check")["state"] == "need_paint"

    write_min_glb(paths["painted"])
    assert _invoke(runner, project, "check")["state"] == "need_bake_master"

    write_min_glb(paths["lod0"])
    assert _invoke(runner, project, "check")["state"] == "need_lod_gen"

    write_min_glb(paths["lod1"])
    write_min_glb(paths["lod2"])
    assert _invoke(runner, project, "check")["state"] == "need_collision"

    write_min_glb(paths["collision"])
    payload = _invoke(runner, project, "check")
    assert payload["state"] == "done"
    assert payload["promotion"]["lod0"] == "painted"
    assert payload["deliverables"]["lod0"]["exists"] is True


def test_studio_check_reports_glb_metadata(runner: CliRunner, project: Path) -> None:
    paths = _paths(project)
    paths["image"].write_bytes(b"png")
    write_min_glb(paths["shape"], tris=64)
    payload = _invoke(runner, project, "check")
    shape = payload["artifacts"]["shape"]
    assert shape["exists"] is True
    assert shape["glb"]["parsed"] is True
    assert shape["glb"]["triangle_count_total"] == 64
    assert shape["glb"]["materials"] == 1
    assert shape["gates"]["materials"] is True


def test_studio_invalidate_paint_keeps_upstream(runner: CliRunner, project: Path) -> None:
    paths = _write_all_artifacts(project)
    payload = _invoke(runner, project, "invalidate", "--from", "paint")
    assert payload["from"] == "paint"
    assert str(paths["rigged"]) in payload["deleted"]
    assert str(paths["animated"]) in payload["deleted"]
    assert str(paths["lod0"]) in payload["deleted"]
    assert str(paths["collision"]) in payload["deleted"]
    assert str(paths["precompute"]) in payload["deleted"]

    assert paths["image"].exists()
    assert paths["shape"].exists()
    assert paths["clean"].exists()
    assert paths["refined"].exists()
    assert paths["painted"].exists()
    assert not paths["rigged"].exists()
    assert not paths["lod0"].exists()


def test_studio_invalidate_shape_keeps_new_shape(runner: CliRunner, project: Path) -> None:
    paths = _write_all_artifacts(project)
    _invoke(runner, project, "invalidate", "--from", "shape")
    assert paths["image"].exists()
    assert paths["shape"].exists()
    assert Path(str(paths["shape"]) + ".omni.json").exists()
    for name in (
        "clean",
        "refined",
        "to_paint",
        "painted",
        "rigged",
        "animated",
        "lod0",
        "lod1",
        "lod2",
        "collision",
        "precompute",
    ):
        assert not paths[name].exists(), name


def test_studio_invalidate_include_stage_removes_the_stage_itself(runner: CliRunner, project: Path) -> None:
    paths = _write_all_artifacts(project)
    payload = _invoke(runner, project, "invalidate", "--from", "paint", "--include-stage")
    assert payload["include_stage"] is True
    assert str(paths["painted"]) in payload["deleted"]

    assert paths["image"].exists()
    assert paths["shape"].exists()
    assert paths["clean"].exists()
    assert paths["refined"].exists()
    assert not paths["painted"].exists()
    assert not paths["rigged"].exists()
    assert not paths["animated"].exists()
    assert not paths["lod0"].exists()
    assert not paths["collision"].exists()


def test_studio_invalidate_include_stage_shape_removes_sidecar(runner: CliRunner, project: Path) -> None:
    paths = _write_all_artifacts(project)
    _invoke(runner, project, "invalidate", "--from", "shape", "--include-stage")

    assert paths["image"].exists()
    assert not paths["shape"].exists()
    assert not Path(str(paths["shape"]) + ".omni.json").exists()
    assert not paths["clean"].exists()
    assert not paths["painted"].exists()


def test_studio_invalidate_all_removes_image_and_shape(runner: CliRunner, project: Path) -> None:
    paths = _write_all_artifacts(project)
    _invoke(runner, project, "invalidate", "--from", "all")
    for name, path in paths.items():
        assert not path.exists(), name
    assert not Path(str(paths["shape"]) + ".omni.json").exists()
    assert not Path(str(paths["refined"]) + ".multiview.json").exists()


def test_studio_check_reports_lock(runner: CliRunner, project: Path) -> None:
    lock = project / ".gameassets_batch.lock"
    lock.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "host": socket.gethostname(),
                "command": "gameassets batch",
                "mode": "local",
            }
        ),
        encoding="utf-8",
    )
    payload = _invoke(runner, project, "check")
    assert payload["lock"]["pid"] == os.getpid()
    assert payload["lock"]["live"] is True


def test_studio_check_error_is_json(runner: CliRunner, project: Path) -> None:
    (project / "manifest.yaml").unlink()
    result = runner.invoke(
        cli,
        ["studio", "check", "--project", str(project), "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["version"] == 1
    assert "manifest" in payload["error"].lower()
