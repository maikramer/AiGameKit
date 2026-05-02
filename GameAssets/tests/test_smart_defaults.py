"""Round 2 — smart defaults para bake-normals por categoria."""

from __future__ import annotations


def test_bake_normals_categories_set() -> None:
    from gameassets.categories import BAKE_NORMALS_CATEGORIES

    assert "humanoid" in BAKE_NORMALS_CATEGORIES
    assert "creature" in BAKE_NORMALS_CATEGORIES
    assert "armor" in BAKE_NORMALS_CATEGORIES
    assert "weapon" in BAKE_NORMALS_CATEGORIES
    assert "vegetation" not in BAKE_NORMALS_CATEGORIES
    assert "effects" not in BAKE_NORMALS_CATEGORIES


def test_category_wants_bake_normals_humanoid() -> None:
    from gameassets.categories import category_wants_bake_normals

    assert category_wants_bake_normals("humanoid") is True
    assert category_wants_bake_normals("Humanoid") is True
    assert category_wants_bake_normals("creature") is True


def test_category_wants_bake_normals_off_for_vegetation() -> None:
    from gameassets.categories import category_wants_bake_normals

    assert category_wants_bake_normals("vegetation") is False
    assert category_wants_bake_normals("effects") is False
    assert category_wants_bake_normals("") is False
    assert category_wants_bake_normals(None) is False


def test_category_overrides_take_precedence() -> None:
    from gameassets.categories import category_wants_bake_normals

    overrides = ["custom_category"]
    assert category_wants_bake_normals("humanoid", overrides=overrides) is False
    assert category_wants_bake_normals("custom_category", overrides=overrides) is True
    assert category_wants_bake_normals("anything", overrides=[]) is False


def test_animator_preset_for_category() -> None:
    from gameassets.categories import animator_preset_for_category

    assert animator_preset_for_category("humanoid") == "humanoid"
    assert animator_preset_for_category("creature") == "creature"
    assert animator_preset_for_category("weapon") == "static"
    assert animator_preset_for_category("vegetation") == "static"
    assert animator_preset_for_category(None) == "static"


def test_profile_has_master_bake_normals_categories_field() -> None:
    from gameassets.profile import GameProfile

    base = {"title": "t", "output_dir": "/tmp/out", "genre": "g", "tone": "n", "style_preset": "s"}
    p = GameProfile.from_dict(base)
    assert hasattr(p, "master_bake_normals_categories")
    assert p.master_bake_normals_categories is None


def test_profile_loads_master_bake_normals_categories_from_dict() -> None:
    from gameassets.profile import GameProfile

    p = GameProfile.from_dict(
        {
            "title": "t",
            "output_dir": "/tmp/out",
            "genre": "g",
            "tone": "n",
            "style_preset": "s",
            "master_bake_normals_categories": ["humanoid", "armor"],
        }
    )
    assert p.master_bake_normals_categories == ["humanoid", "armor"]
