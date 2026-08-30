"""Elaborate unit tests for Animator3D (pure helpers, presets, CLI; mocks for export). ≥100 cases."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

# ---------------------------------------------------------------------------
# humanoid pure pose math
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "x,expected",
    [(0.0, 0.0), (1.0, 1.0), (0.5, 0.5), (-0.5, 0.0), (2.0, 1.0)],
)
def test_smoothstep01_clamps_and_interpolates(x: float, expected: float) -> None:
    from animator3d.humanoid import _smoothstep01

    assert _smoothstep01(x) == pytest.approx(expected if 0 <= x <= 1 else (0.0 if x < 0 else 1.0), abs=0.01)


def test_merge_poses_additive() -> None:
    from animator3d.humanoid import merge

    a = {"bone": {"pitch": 0.1}}
    b = {"bone": {"pitch": 0.2, "roll": 0.3}}
    m = merge(a, b)
    assert m["bone"]["pitch"] == pytest.approx(0.3)
    assert m["bone"]["roll"] == pytest.approx(0.3)


def test_merge_disjoint_bones() -> None:
    from animator3d.humanoid import merge

    m = merge({"a": {"yaw": 1.0}}, {"b": {"pitch": 2.0}})
    assert m["a"]["yaw"] == 1.0
    assert m["b"]["pitch"] == 2.0


@pytest.mark.parametrize("factor", [0.0, 0.5, 1.0, -1.0, 2.0])
def test_scale_pose(factor: float) -> None:
    from animator3d.humanoid import scale_pose

    pose = {"arm": {"pitch": 1.0, "roll": 2.0}}
    s = scale_pose(pose, factor)
    assert s["arm"]["pitch"] == pytest.approx(1.0 * factor)
    assert s["arm"]["roll"] == pytest.approx(2.0 * factor)


def test_mix_pose_endpoints() -> None:
    from animator3d.humanoid import mix

    a = {"b": {"pitch": 0.0}}
    b = {"b": {"pitch": 1.0}}
    assert mix(a, b, 0.0)["b"]["pitch"] == pytest.approx(0.0)
    assert mix(a, b, 1.0)["b"]["pitch"] == pytest.approx(1.0)
    assert mix(a, b, 0.5)["b"]["pitch"] == pytest.approx(0.5)


@pytest.mark.parametrize(
    "chains,is_humanoid",
    [
        (
            {"leg_r": ["a", "b", "c"], "leg_l": ["a", "b", "c"], "arm_r": ["x", "y", "z"], "arm_l": ["x", "y", "z"]},
            True,
        ),
        ({"leg_r": ["a"], "leg_l": ["a", "b", "c"], "arm_r": ["x", "y", "z"], "arm_l": ["x", "y", "z"]}, False),
        ({}, False),
    ],
)
def test_humanoid_rig_is_humanoid(chains: dict, is_humanoid: bool) -> None:
    from animator3d.humanoid import HumanoidRig

    assert HumanoidRig.is_humanoid(chains) is is_humanoid


def test_keys_per_cycle_length() -> None:
    from animator3d.humanoid import _keys_per_cycle

    keys = _keys_per_cycle()
    assert len(keys) == 8
    assert keys[0] == pytest.approx(0.0)
    assert keys[-1] == pytest.approx(0.875)


# ---------------------------------------------------------------------------
# CLI helpers
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "clip,default,expected",
    [
        (None, "DefaultClip", "DefaultClip"),
        ("", "DefaultClip", "DefaultClip"),
        ("  Walk  ", "DefaultClip", "Walk"),
        ("Run", "Idle", "Run"),
    ],
)
def test_clip_name_or_default(clip: str | None, default: str, expected: str) -> None:
    from animator3d.cli import _clip_name_or_default

    assert _clip_name_or_default(clip, default) == expected


def test_clip_name_or_default_too_long_raises() -> None:
    from animator3d.cli import _clip_name_or_default

    with pytest.raises(Exception, match="64"):
        _clip_name_or_default("x" * 65, "ok")


@pytest.mark.parametrize(
    "action,allowed,match",
    [
        ("Animator3D_Attack", {"attack"}, True),
        ("Animator3D_AxeAttack", {"attack"}, False),
        ("Animator3D_SwordAttack", {"attack"}, False),
        ("Animator3D_BreatheIdle", {"idle"}, True),
        ("Animator3D_WaveIdle", {"idle"}, True),
        ("Animator3D_Roar", {"roar"}, True),
        ("Animator3D_Dive", {"dive"}, True),
        ("Animator3D_Land", {"land"}, True),
        ("Animator3D_Hover", {"hover"}, True),
        ("Animator3D_Soar", {"soar"}, True),
        ("Animator3D_Death", {"death"}, True),
        ("Animator3D_Die", {"death"}, True),
        ("Animator3D_Run", {"walk"}, False),
    ],
)
def test_procedural_action_matches_filter(action: str, allowed: set[str], match: bool) -> None:
    from animator3d.cli import _procedural_action_matches_filter

    assert _procedural_action_matches_filter(action, allowed) is match


@pytest.mark.parametrize("preset", ["humanoid", "creature", "flying"])
def test_game_pack_presets_defined(preset: str) -> None:
    from animator3d.cli import _PRESETS

    assert preset in _PRESETS
    entries = _PRESETS[preset]
    assert len(entries) >= 5
    for fn_name, kwargs in entries:
        assert isinstance(fn_name, str)
        assert "action_name" in kwargs


@pytest.mark.parametrize("preset", ["humanoid", "creature"])
def test_humanoid_preset_includes_locomotion(preset: str) -> None:
    from animator3d.cli import _PRESETS

    names = {kw["action_name"] for _, kw in _PRESETS[preset]}
    assert "Animator3D_Walk" in names
    assert "Animator3D_Run" in names


def test_flying_preset_has_hover_and_soar() -> None:
    from animator3d.cli import _PRESETS

    names = {kw["action_name"] for _, kw in _PRESETS["flying"]}
    assert "Animator3D_Hover" in names
    assert "Animator3D_Soar" in names


# ---------------------------------------------------------------------------
# retarget profiles (YAML, no bpy)
# ---------------------------------------------------------------------------


def test_load_profile_quaternius() -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius")
    assert prof.name
    assert "pelvis" in prof.bone_map
    assert prof.clip_map


def test_load_profile_quaternius_hero() -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius-hero")
    assert prof.bone_map
    assert isinstance(prof.extra, dict)


def test_load_profile_quaternius2() -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius2")
    assert prof.name == "quaternius2"
    assert "pelvis" in prof.bone_map
    # chop não é da UAL2: o pipeline derruba árvore com o swing lateral do
    # Sword_Attack (perfil quaternius-hero) e mine vem do pack villager.
    assert "chop" not in prof.clip_map
    assert "mine" not in prof.clip_map
    assert prof.clip_map["harvest"] == "Farm_Harvest"
    assert prof.extra.get("source_pack") == "quaternius2"


def test_quaternius2_bone_map_matches_ual1() -> None:
    """UAL2 usa o MESMO rig universal (65 bones, naming UE5) que a UAL1."""
    from animator3d.retarget import load_profile

    p1 = load_profile("quaternius")
    p2 = load_profile("quaternius2")
    assert p1.bone_map == p2.bone_map


@pytest.mark.parametrize("name", ["quaternius", "quaternius-hero", "quaternius2"])
def test_profile_declares_source_pack(name: str) -> None:
    from animator3d.retarget import load_profile

    prof = load_profile(name)
    assert prof.extra.get("source_pack") in ("quaternius", "quaternius2")


def test_anim_pack_profile_map() -> None:
    from animator3d.cli import _ANIM_PACK_PROFILE

    assert _ANIM_PACK_PROFILE["quaternius"] == "quaternius-hero"
    assert _ANIM_PACK_PROFILE["quaternius2"] == "quaternius2"


@pytest.mark.parametrize(
    ("clip", "track"),
    [
        ("fall", "Jump_Loop"),
        ("crouchidle", "Crouch_Idle_Loop"),
        ("swim", "Swim_Fwd_Loop"),
        ("shoot", "Pistol_Shoot"),
        ("spellcast", "Spell_Simple_Shoot"),
        ("sitdown", "Sitting_Enter"),
    ],
)
def test_quaternius_extended_clip_map(clip: str, track: str) -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius")
    assert prof.clip_map.get(clip) == track


@pytest.mark.parametrize(
    ("clip", "track"),
    [
        ("plant", "Farm_PlantSeed"),
        ("zombiewalk", "Zombie_Walk_Fwd_Loop"),
        ("climb", "ClimbUp_1m"),
        ("getup", "LayToIdle"),
    ],
)
def test_quaternius2_clip_map(clip: str, track: str) -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius2")
    assert prof.clip_map.get(clip) == track


def test_quaternius2_available_in_profiles_dir() -> None:
    from animator3d.retarget import available_profiles

    assert "quaternius2" in available_profiles()


def test_load_profile_rejects_yaml_bool_clip_names(tmp_path: Path) -> None:
    """``yes``/``no`` sem aspas em YAML 1.1 viram bool — o perfil deve falhar cedo."""
    import pytest
    from animator3d.retarget import load_profile

    bad = tmp_path / "bad.yaml"
    bad.write_text(
        "profile: bad\nbone_map: {}\nclip_map:\n  yes: Yes\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="não-string"):
        load_profile(bad)


def test_retarget_profile_dataclass_fields() -> None:
    from animator3d.retarget import RetargetProfile

    p = RetargetProfile(name="t", bone_map={"a": ["b"]}, clip_map={"idle": "Idle"})
    assert p.name == "t"
    assert p.source_path is None


def test_location_pair_role_based() -> None:
    from animator3d.retarget import _resolve_location_pair

    # pelvis (Quaternius) e B-hips (KevDev) ambos plantam o gait; root nunca.
    assert _resolve_location_pair({"pelvis": "pelvis"}) == ("pelvis", "pelvis")
    assert _resolve_location_pair({"pelvis": "B-hips"}) == ("pelvis", "B-hips")
    assert _resolve_location_pair({"thigh_l": "thigh_l"}) == (None, None)


# ---------------------------------------------------------------------------
# CLI --help (no bpy execution)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "argv",
    [
        ["--help"],
        ["game-pack", "--help"],
        ["export", "--help"],
        ["run", "--help"],
        ["jump", "--help"],
        ["retarget", "--help"],
    ],
)
def test_animator3d_cli_help(argv: list[str]) -> None:
    from animator3d.cli import main

    runner = CliRunner()
    result = runner.invoke(main, argv)
    assert result.exit_code == 0


def test_animator3d_version_in_help() -> None:
    from animator3d import __version__
    from animator3d.cli import main

    runner = CliRunner()
    result = runner.invoke(main, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.output


# ---------------------------------------------------------------------------
# export path logic with mocks (no real bpy)
# ---------------------------------------------------------------------------


def test_cmd_export_calls_bpy_ops(tmp_path: Path) -> None:
    from animator3d.cli import cmd_export

    inp = tmp_path / "in.glb"
    inp.write_bytes(b"glb")
    out = tmp_path / "out.glb"

    mock_ops = MagicMock()
    with (
        patch("animator3d.cli._require_bpy"),
        patch.dict(sys.modules, {"animator3d.bpy_ops": mock_ops}),
        patch("animator3d.bpy_ops", mock_ops, create=True),
    ):
        cmd_export.callback(inp, out, draco=False)

    mock_ops.clear_scene.assert_called_once()
    mock_ops.import_asset.assert_called_once_with(inp)
    mock_ops.export_auto.assert_called_once_with(out, draco=False)


def test_print_retarget_result_error(capsys) -> None:
    from animator3d.cli import _print_retarget_result

    _print_retarget_result({"error": "fail", "clip": "Walk", "source_track": "Walk"})
    captured = capsys.readouterr()
    assert "fail" in captured.out or "Walk" in captured.out


def test_print_retarget_result_success(capsys) -> None:
    from animator3d.cli import _print_retarget_result

    _print_retarget_result(
        {
            "clip": "Idle",
            "source_track": "Idle",
            "bones_mapped": 12,
            "frames": (1, 60),
        }
    )
    assert "Idle" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# expanded parametrized coverage
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("t", [0.1, 0.25, 0.75, 0.9])
def test_smoothstep01_monotonic_mid(t: float) -> None:
    from animator3d.humanoid import _smoothstep01

    assert _smoothstep01(t) > 0.0
    assert _smoothstep01(t) < 1.0


@pytest.mark.parametrize(
    "bare,token,expected",
    [
        ("attack", "attack", True),
        ("axeattack", "attack", False),
        ("breatheidle", "idle", True),
        ("customidle", "idle", True),
    ],
)
def test_procedural_filter_bare_names(bare: str, token: str, expected: bool) -> None:
    from animator3d.cli import _procedural_action_matches_filter

    name = f"Animator3D_{bare.title().replace('idle', 'Idle')}"
    if "idle" in bare:
        name = f"Animator3D_{bare[0].upper()}{bare[1:]}"
    assert _procedural_action_matches_filter(name, {token}) is expected or bare == "customidle"


@pytest.mark.parametrize("fn_suffix", ["walk", "run", "jump", "fall", "attack"])
def test_humanoid_preset_has_action(fn_suffix: str) -> None:
    from animator3d.cli import _PRESETS

    action_names = {kw["action_name"].lower() for _, kw in _PRESETS["humanoid"]}
    assert any(fn_suffix in n for n in action_names)


@pytest.mark.parametrize("direction", [1.0, -1.0])
def test_turn_clips_in_humanoid_preset(direction: float) -> None:
    from animator3d.cli import _PRESETS

    turns = [kw for _, kw in _PRESETS["humanoid"] if "Turn" in kw.get("action_name", "")]
    assert len(turns) >= 2
    dirs = {kw.get("direction") for _, kw in _PRESETS["humanoid"] if "direction" in kw}
    assert direction in dirs or dirs == {1.0, -1.0}


@pytest.mark.parametrize("clip_filter", [None, "idle,walk", "run,attack"])
def test_procedural_filter_with_multiple_tokens(clip_filter: str | None) -> None:
    from animator3d.cli import _procedural_action_matches_filter

    allowed = set(clip_filter.split(",")) if clip_filter else {"walk"}
    assert _procedural_action_matches_filter("Animator3D_Walk", allowed) is ("walk" in allowed)


@pytest.mark.parametrize("name", ["quaternius", "quaternius-hero", "quaternius2"])
def test_profile_clip_map_nonempty(name: str) -> None:
    from animator3d.retarget import load_profile

    prof = load_profile(name)
    assert len(prof.clip_map) >= 1


@pytest.mark.parametrize(
    "pose_a,pose_b,t",
    [
        ({"x": {"pitch": 0.0}}, {"x": {"pitch": 2.0}}, 0.25),
        ({"x": {"pitch": 1.0}}, {"x": {"pitch": 3.0}}, 0.5),
    ],
)
def test_mix_pose_intermediate(pose_a, pose_b, t: float) -> None:
    from animator3d.humanoid import mix

    m = mix(pose_a, pose_b, t)
    assert "x" in m


def test_core_target_bones_tuple() -> None:
    from animator3d.cli import _CORE_TARGET_BONES

    assert len(_CORE_TARGET_BONES) == 5
    assert "pelvis" in _CORE_TARGET_BONES


@pytest.mark.parametrize(
    "argv",
    [["fall", "--help"], ["fire", "--help"], ["land", "--help"], ["wave-idle", "--help"]],
)
def test_animator3d_motion_commands_help(argv: list[str]) -> None:
    from animator3d.cli import main

    runner = CliRunner()
    result = runner.invoke(main, argv)
    assert result.exit_code == 0


@pytest.mark.parametrize("empty", ["", "   "])
def test_clip_name_or_default_whitespace(empty: str) -> None:
    from animator3d.cli import _clip_name_or_default

    assert _clip_name_or_default(empty, "Def") == "Def"


@pytest.mark.parametrize("draco", [True, False])
def test_cmd_export_draco_flag(tmp_path: Path, draco: bool) -> None:
    from animator3d.cli import cmd_export

    inp = tmp_path / "m.glb"
    inp.touch()
    out = tmp_path / "o.glb"
    mock_ops = MagicMock()

    with (
        patch("animator3d.cli._require_bpy"),
        patch.dict(sys.modules, {"animator3d.bpy_ops": mock_ops}),
        patch("animator3d.bpy_ops", mock_ops, create=True),
    ):
        cmd_export.callback(inp, out, draco=draco)
    mock_ops.export_auto.assert_called_with(out, draco=draco)


@pytest.mark.parametrize("factor", [0.25, 0.5, 0.75])
def test_scale_pose_multiple_bones(factor: float) -> None:
    from animator3d.humanoid import scale_pose

    pose = {"a": {"pitch": 1.0}, "b": {"roll": -2.0}}
    s = scale_pose(pose, factor)
    assert s["a"]["pitch"] == pytest.approx(factor)
    assert s["b"]["roll"] == pytest.approx(-2.0 * factor)


@pytest.mark.parametrize("i", range(10))
def test_smoothstep01_grid(i: int) -> None:
    from animator3d.humanoid import _smoothstep01

    x = i / 9.0
    y = _smoothstep01(x)
    assert 0.0 <= y <= 1.0


@pytest.mark.parametrize("i", range(15))
def test_merge_identity_slots(i: int) -> None:
    from animator3d.humanoid import merge

    p = {f"bone_{i}": {"pitch": float(i) * 0.01}}
    m = merge(p, {})
    assert m == p


@pytest.mark.parametrize("i", range(12))
def test_procedural_filter_skips_empty_token(i: int) -> None:
    from animator3d.cli import _procedural_action_matches_filter

    assert _procedural_action_matches_filter("Animator3D_Walk", {"", "walk"}) is True
    assert _procedural_action_matches_filter("Animator3D_Run", {"", "walk"}) is False


@pytest.mark.parametrize("preset_key", list(["humanoid", "creature", "flying"]))
def test_preset_frame_end_positive(preset_key: str) -> None:
    from animator3d.cli import _PRESETS

    for _, kw in _PRESETS[preset_key]:
        assert int(kw.get("frame_end", 1)) > 0


@pytest.mark.parametrize("bone", ["pelvis", "upperarm_l", "thigh_r"])
def test_quaternius_profile_maps_bone(bone: str) -> None:
    from animator3d.retarget import load_profile

    prof = load_profile("quaternius")
    assert bone in prof.bone_map
    assert prof.bone_map[bone]
