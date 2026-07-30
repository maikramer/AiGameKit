"""Testes do prompt_builder."""

import pytest

from gameassets.manifest import ManifestRow
from gameassets.presets import get_preset, load_presets_bundle
from gameassets.profile import GameProfile
from gameassets.prompt_builder import build_prompt


@pytest.fixture
def preset_lowpoly() -> dict:
    bundle = load_presets_bundle(None)
    return get_preset(bundle, "lowpoly")


def test_build_prompt_contains_idea_and_context(preset_lowpoly: dict) -> None:
    profile = GameProfile(
        title="NomeDoJogoQueNaoDeveAparecerNaImagem",
        genre="G",
        tone="light",
        style_preset="lowpoly",
    )
    row = ManifestRow(id="x", idea="um cofre de metal", kind="prop", generate_3d=False)
    p = build_prompt(profile, preset_lowpoly, row)
    assert "cofre" in p.lower() or "cofre" in p
    assert "G" in p
    assert "light" in p.lower()
    assert "NomeDoJogoQueNaoDeveAparecerNaImagem" not in p
    assert "watermark" in p.lower() or "text overlay" in p.lower()
    assert "Avoid:" in p


def test_kind_character(preset_lowpoly: dict) -> None:
    profile = GameProfile(
        title="A",
        genre="B",
        tone="C",
        style_preset="lowpoly",
        negative_keywords=["blur"],
    )
    row = ManifestRow(id="1", idea="hero", kind="character", generate_3d=False)
    p = build_prompt(profile, preset_lowpoly, row)
    assert "character" in p.lower()


def test_for_3d_adds_hint(preset_lowpoly: dict) -> None:
    profile = GameProfile(
        title="A",
        genre="B",
        tone="C",
        style_preset="lowpoly",
    )
    row = ManifestRow(id="1", idea="x", kind=None, generate_3d=True)
    p2 = build_prompt(profile, preset_lowpoly, row, for_3d=False)
    p3 = build_prompt(profile, preset_lowpoly, row, for_3d=True)
    assert p2 != p3 or "watertight" in p3.lower()


def test_generate_3d_bans_baked_vfx(preset_lowpoly: dict) -> None:
    profile = GameProfile(title="A", genre="B", tone="C", style_preset="lowpoly")
    row = ManifestRow(
        id="forge",
        idea="village forge with chimney",
        kind="environment",
        category="building",
        generate_3d=True,
    )
    p2 = build_prompt(profile, preset_lowpoly, row, for_3d=False)
    p3 = build_prompt(profile, preset_lowpoly, row, for_3d=True)
    assert "smoke plume" in p2.lower() or "without smoke" in p2.lower()
    assert "particle" in p2.lower() or "smoke" in p2.lower()
    assert "static solid" in p3.lower() or "volumetric smoke" in p3.lower()
    assert "Avoid:" in p2
    assert "chimney smoke" in p2.lower() or "smoke plume" in p2.lower()
