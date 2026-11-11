"""Categorias com casca interna removida no topology-fix."""

from __future__ import annotations

from text3d.utils.mesh_lod import _is_character_shell_category, _is_hollow_shell_category


def test_characters_use_component_shell_mode() -> None:
    for category in ("humanoid", "creature", "Character", " NPC ", "monster"):
        assert _is_character_shell_category(category), category


def test_non_characters_do_not_use_component_shell_mode() -> None:
    for category in ("building", "furniture", "prop", "", None):
        assert not _is_character_shell_category(category), category


def test_buildings_stay_on_the_face_strip_path() -> None:
    assert _is_hollow_shell_category("building")
    assert _is_hollow_shell_category("chapel")
    assert not _is_hollow_shell_category("humanoid")
    assert not _is_hollow_shell_category("creature")
