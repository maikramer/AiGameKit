"""Round 2 — animated.yaml e atualização de rigged.yaml."""

from __future__ import annotations

from pathlib import Path

import yaml


def _rules_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "src" / "gameassets" / "data" / "rules"


def test_animated_rules_file_exists() -> None:
    assert (_rules_dir() / "animated.yaml").is_file()


def test_animated_rules_require_skin_and_tangent() -> None:
    data = yaml.safe_load((_rules_dir() / "animated.yaml").read_text())
    attrs = data.get("attributes_required") or []
    assert "JOINTS_0" in attrs
    assert "WEIGHTS_0" in attrs
    assert "TANGENT" in attrs
    assert "POSITION" in attrs
    assert "TEXCOORD_0" in attrs


def test_animated_rules_require_ktx2_meshopt() -> None:
    data = yaml.safe_load((_rules_dir() / "animated.yaml").read_text())
    assert data.get("texture_format") == "ktx2"
    assert data.get("compression") == "meshopt"


def test_animated_rules_require_armature_and_actions() -> None:
    data = yaml.safe_load((_rules_dir() / "animated.yaml").read_text())
    arms = data.get("armatures") or []
    assert len(arms) >= 1
    bc = arms[0].get("bone_count") or {}
    assert bc.get("min", 0) >= 1
    assert int(data.get("actions_min", 0)) >= 1


def test_rigged_rules_now_require_ktx2_meshopt_tangent() -> None:
    data = yaml.safe_load((_rules_dir() / "rigged.yaml").read_text())
    assert data.get("texture_format") == "ktx2"
    assert data.get("compression") == "meshopt"
    attrs = data.get("attributes_required") or []
    assert "TANGENT" in attrs
    assert "JOINTS_0" in attrs
    assert "WEIGHTS_0" in attrs
