"""Testes da CLI (sem exigir bpy para --help)."""

from __future__ import annotations

import json
from pathlib import Path

from animator3d.cli import main
from click.testing import CliRunner


def test_help_lists_commands() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "wave-idle" in result.output
    assert "attack" in result.output
    assert "walk" in result.output
    assert "hover" in result.output
    assert "soar" in result.output
    assert "dive" in result.output
    assert "fire" in result.output
    assert "land" in result.output
    assert "roar" in result.output
    assert "list-clips" in result.output
    assert "list-animations" in result.output
    assert "inspect" in result.output
    assert "texture-project" in result.output


def test_screenshot_help_lists_frame_list() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["screenshot", "--help"])
    assert result.exit_code == 0
    assert "frame-list" in result.output


def test_texture_project_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["texture-project", "--help"])
    assert result.exit_code == 0
    assert "--output" in result.output
    assert "--resolution" in result.output
    assert "--margin" in result.output


# ---------------------------------------------------------------------------
# list-animations — catálogo UAL1/UAL2 (sem bpy, sem GPU)
# ---------------------------------------------------------------------------


def test_list_animations_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--help"])
    assert result.exit_code == 0
    assert "--pack" in result.output
    assert "--profile" in result.output
    assert "--json" in result.output


def test_game_pack_help_points_to_list_animations() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["game-pack", "--help"])
    assert result.exit_code == 0
    assert "list-animations" in result.output


def test_list_animations_default_lists_ual1_and_ual2() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations"])
    assert result.exit_code == 0
    assert "UAL1" in result.output
    assert "UAL2" in result.output
    # Locomoção vem da UAL1; colheita dedicada vem da UAL2. chop não é da
    # UAL2: o pipeline derruba árvore com o swing lateral do Sword_Attack
    # (UAL1); mine vem do pack villager em --pack all.
    assert "idle" in result.output
    assert "Idle_Loop" in result.output
    assert "chop" in result.output
    assert "Sword_Attack" in result.output
    assert "Substituições entre packs" in result.output


def test_list_animations_pack_quaternius2_only() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--pack", "quaternius2"])
    assert result.exit_code == 0
    assert "Farm_Harvest" in result.output
    assert "Zombie_Idle_Loop" in result.output
    # Clips UAL1-only não aparecem neste modo.
    assert "Walk_Formal_Loop" not in result.output


def test_list_animations_json_structure() -> None:
    from animator3d.retarget import load_profile

    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["mode"] == "both"
    assert data["profiles"][0]["label"] == "UAL1"
    assert data["profiles"][1]["label"] == "UAL2"
    hero = load_profile("quaternius-hero").clip_map
    ual2 = load_profile("quaternius2").clip_map
    assert data["total"] == len({**hero, **ual2}) == len(data["clips"])
    by_clip = {c["clip"]: c for c in data["clips"]}
    assert by_clip["idle"]["label"] == "UAL1"
    assert by_clip["gather"]["source_track"] == "Farm_Harvest"
    assert by_clip["gather"]["replaces"] == "Fixing_Kneeling"
    assert set(data["overrides"]) == {k for k in hero if k in ual2}


def test_list_animations_profile_by_name_and_path(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--profile", "quaternius2"])
    assert result.exit_code == 0
    assert "quaternius2" in result.output
    assert "UAL2" not in result.output  # fora do modo pack não há label UAL*

    custom = tmp_path / "custom.yaml"
    custom.write_text(
        "profile: custom\nbone_map: {}\nclip_map:\n  # --- Dança ---\n  dance: Dance_Loop\n",
        encoding="utf-8",
    )
    result = runner.invoke(main, ["list-animations", "--profile", str(custom)])
    assert result.exit_code == 0
    assert "dance" in result.output
    assert "Dance_Loop" in result.output
    assert "Dança" in result.output


def test_list_animations_invalid_profile_fails() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--profile", "definitely-not-a-profile-xyz"])
    assert result.exit_code != 0
    assert "não encontrado" in result.output


def test_list_animations_invalid_pack_fails() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--pack", "mixamo"])
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Combinação de packs (gramática composta --anim-pack/--pack)
# ---------------------------------------------------------------------------


def test_list_animations_pack_all_villager_plus_ual() -> None:
    """all = villager + UAL1 + UAL2: trabalhos kevdev ficam, UAL ganha colisões."""
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--pack", "all", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [p["label"] for p in data["profiles"]] == ["Villager", "UAL1", "UAL2"]
    by_clip = {c["clip"]: c for c in data["clips"]}
    # UAL substitui as colisões (idle/gather); trabalhos exclusivos sobrevivem.
    assert by_clip["idle"]["label"] == "UAL1"
    assert by_clip["gather"]["label"] == "UAL2"
    assert by_clip["mine"]["label"] == "Villager"
    assert by_clip["plow"]["label"] == "Villager"


def test_list_animations_pack_comma_grammar() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--pack", "quaternius2,villager"])
    assert result.exit_code == 0
    assert "quaternius2" in result.output
    assert "Zombie_Idle_Loop" in result.output  # clip exclusivo UAL2
    # villager é o último da cadeia: gather (replace_keys) fica com o dele.
    assert "Gathering01" in result.output


def test_list_animations_pack_comma_with_invalid_token_fails() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["list-animations", "--pack", "both,mixamo"])
    assert result.exit_code != 0


def test_game_pack_anim_pack_composite_accepted() -> None:
    """game-pack valida --anim-pack cedo (BadParameter) — all/vírgulas OK."""
    from aigamekit_shared.anim_packs import expand_anim_packs

    runner = CliRunner()
    result = runner.invoke(main, ["game-pack", "--help"])
    assert result.exit_code == 0
    assert "all" in result.output
    assert "vírgulas" in result.output
    # Gramática partilhada com GameAssets: all expande para villager primeiro.
    assert expand_anim_packs("all") == ["villager", "quaternius", "quaternius2"]


def test_game_pack_anim_pack_invalid_rejected_before_bpy(tmp_path: Path) -> None:
    """Token inválido falha como erro de uso, sem chegar a carregar bpy."""
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "game-pack",
            str(tmp_path / "missing.glb"),
            str(tmp_path / "out.glb"),
            "--anim-pack",
            "mixamo",
        ],
    )
    assert result.exit_code != 0
    assert "anim_pack" in result.output or "mixamo" in result.output
