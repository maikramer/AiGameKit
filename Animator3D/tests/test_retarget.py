"""Testes para o módulo retarget (perfil + helpers + CLI sem bpy)."""

from __future__ import annotations

from math import radians
from pathlib import Path
from types import SimpleNamespace

import bpy  # noqa: F401
from animator3d import retarget as rt
from animator3d.cli import main
from click.testing import CliRunner
from mathutils import Quaternion

# ---------------------------------------------------------------------------
# load_profile
# ---------------------------------------------------------------------------


def test_load_builtin_profile_quaternius() -> None:
    """O perfil quaternius.yaml builtin carrega bone_map + clip_map."""
    profile = rt.load_profile("quaternius")
    assert profile.name == "quaternius"
    # Naming canónico é o próprio Quaternius (identidade), com aliases
    # Mixamo-like para rigs antigos.
    assert profile.bone_map["pelvis"] == ["pelvis", "Hips"]
    assert profile.bone_map["upperarm_l"] == ["upperarm_l", "LeftArm"]
    assert profile.bone_map["thigh_r"] == ["thigh_r", "RightUpLeg"]
    # Clips essenciais do simple-rpg.
    assert profile.clip_map["idle"] == "Idle_Loop"
    assert profile.clip_map["walk"] == "Walk_Loop"
    assert profile.clip_map["death"] == "Death01"
    assert profile.clip_map["attack"] == "Sword_Attack"


def test_load_profile_missing_raises() -> None:
    """Perfil inexistente -> FileNotFoundError."""
    import pytest

    with pytest.raises(FileNotFoundError):
        rt.load_profile("definitely-not-a-profile-xyz")


def test_available_profiles_includes_quaternius() -> None:
    assert "quaternius" in rt.available_profiles()
    assert "quaternius2" in rt.available_profiles()


def test_profile_clip_groups_matches_clip_map_keys() -> None:
    """Todos os perfis bundled: grupos cobrem exactamente as keys do clip_map."""
    for name in rt.available_profiles():
        profile = rt.load_profile(name)
        groups = rt.profile_clip_groups(name)
        assert set(groups) == set(profile.clip_map), f"perfil {name}: keys de grupos != clip_map"


def test_profile_clip_groups_parses_sections() -> None:
    """Secções ``# --- X ---`` viram grupos; keys com aspas são lidas corretamente."""
    groups = rt.profile_clip_groups("quaternius2")
    assert groups["harvest"].startswith("Farming")
    assert groups["sworda"].startswith("Combate espada")
    assert groups["yes"].startswith("Emotes")  # "yes" com aspas no YAML
    assert groups["no"].startswith("Emotes")
    hero = rt.profile_clip_groups("quaternius-hero")
    assert hero["idle"].startswith("Locomoção")
    assert hero["sit"].startswith("Catálogo UAL1 estendido")


def test_profile_clip_groups_without_sections(tmp_path: Path) -> None:
    """YAML sem comentários de secção -> grupos vazios (catálogo continua válido)."""
    p = tmp_path / "plain.yaml"
    p.write_text(
        "profile: plain\nbone_map: {}\nclip_map:\n  idle: Idle_Loop\n  walk: Walk_Loop\n",
        encoding="utf-8",
    )
    assert rt.profile_clip_groups(p) == {"idle": "", "walk": ""}


def test_profile_clip_groups_ignores_prose_comments(tmp_path: Path) -> None:
    """Comentários de prosa (e antes da 1.ª secção) não criam grupos espúrios."""
    p = tmp_path / "prose.yaml"
    p.write_text(
        "# comentário fora do clip_map: não conta\n"
        "profile: prose\nbone_map: {}\n"
        "clip_map:\n"
        "  # nota: primeiro clip fora de qualquer secção\n"
        "  idle: Idle_Loop\n"
        "  # --- Combate ---\n"
        "  attack: Sword_Attack  # comentário inline não estraga a key\n"
        "  # mine: linha de prosa que parece entrada mas é comentário\n"
        "  roll: Roll\n",
        encoding="utf-8",
    )
    groups = rt.profile_clip_groups(p)
    assert groups == {"idle": "", "attack": "Combate", "roll": "Combate"}


def test_profile_clip_groups_missing_profile_raises() -> None:
    import pytest

    with pytest.raises(FileNotFoundError):
        rt.profile_clip_groups("definitely-not-a-profile-xyz")


# ---------------------------------------------------------------------------
# remove_clips — substituição de clips no multi-pack (both)
# ---------------------------------------------------------------------------


class _FakeAction:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeStrip:
    def __init__(self, action: _FakeAction) -> None:
        self.action = action


class _FakeTrack:
    def __init__(self, name: str, actions: list[_FakeAction]) -> None:
        self.name = name
        self.strips = [_FakeStrip(a) for a in actions]


class _FakeObjects:
    def __init__(self, objs: list) -> None:
        self._objs = objs

    def get(self, name: str):
        return next((o for o in self._objs if o.name == name), None)

    def __iter__(self):
        return iter(self._objs)


def _fake_bpy_for(arm) -> SimpleNamespace:
    removed_actions: list[_FakeAction] = []
    fake = SimpleNamespace(
        data=SimpleNamespace(
            objects=_FakeObjects([arm]),
            actions=SimpleNamespace(remove=removed_actions.append),
        ),
        _removed_actions=removed_actions,
    )
    return fake


def test_remove_clips_removes_named_tracks_and_orphan_actions(monkeypatch) -> None:
    act_chop, act_idle = _FakeAction("chop"), _FakeAction("idle")
    arm = SimpleNamespace(
        name="Arm",
        type="ARMATURE",
        animation_data=SimpleNamespace(
            nla_tracks=[_FakeTrack("chop", [act_chop]), _FakeTrack("idle", [act_idle])],
        ),
    )
    fake = _fake_bpy_for(arm)
    monkeypatch.setattr(rt, "_bpy", lambda: fake)

    removed = rt.remove_clips("Arm", ["chop"])

    assert removed == ["chop"]
    assert [t.name for t in arm.animation_data.nla_tracks] == ["idle"]
    # a action 'chop' ficou órfã e foi removida; a 'idle' continua em uso
    assert fake._removed_actions == [act_chop]


def test_remove_clips_no_animation_data(monkeypatch) -> None:
    arm = SimpleNamespace(name="Arm", type="ARMATURE", animation_data=None)
    fake = _fake_bpy_for(arm)
    monkeypatch.setattr(rt, "_bpy", lambda: fake)
    assert rt.remove_clips("Arm", ["chop"]) == []


def test_remove_clips_missing_armature(monkeypatch) -> None:
    empty_arm = SimpleNamespace(name="Other", type="ARMATURE", animation_data=None)
    fake = _fake_bpy_for(empty_arm)
    monkeypatch.setattr(rt, "_bpy", lambda: fake)
    assert rt.remove_clips("Arm", ["chop"]) == []


def test_load_profile_normalizes_string_values_to_lists(tmp_path: Path) -> None:
    """bone_map com valores string é normalizado para listas de candidatos."""
    custom = tmp_path / "mini.yaml"
    custom.write_text(
        "profile: mini\nbone_map:\n  a: X\n  b: [Y, Z]\nclip_map: {}\n",
        encoding="utf-8",
    )
    profile = rt.load_profile(custom)
    assert profile.bone_map == {"a": ["X"], "b": ["Y", "Z"]}


# ---------------------------------------------------------------------------
# resolve_bone_pairs — candidatos com fallback de alias
# ---------------------------------------------------------------------------


def test_resolve_bone_pairs_prefers_first_candidate() -> None:
    """Rig novo (naming quaternius): primeiro candidato ganha."""
    target = {"pelvis", "upperarm_l"}
    source = {"pelvis", "upperarm_l"}
    bone_map = {"pelvis": ["pelvis", "Hips"], "upperarm_l": ["upperarm_l", "LeftArm"]}

    pairs, skipped = rt.resolve_bone_pairs(target, source, bone_map)

    assert pairs == {"pelvis": "pelvis", "upperarm_l": "upperarm_l"}
    assert skipped == []


def test_resolve_bone_pairs_falls_back_to_alias() -> None:
    """Rig antigo (naming Mixamo): alias resolve o mapeamento."""
    target = {"Hips", "LeftArm"}
    source = {"pelvis", "upperarm_l"}
    bone_map = {"pelvis": ["pelvis", "Hips"], "upperarm_l": ["upperarm_l", "LeftArm"]}

    pairs, skipped = rt.resolve_bone_pairs(target, source, bone_map)

    assert pairs == {"Hips": "pelvis", "LeftArm": "upperarm_l"}
    assert skipped == []


def test_resolve_bone_pairs_reports_missing_main_bones() -> None:
    """Bones principais sem candidato aparecem em skipped; dedos não."""
    target = {"pelvis"}
    source = {"pelvis", "thigh_l", "index_01_l"}
    bone_map = {
        "pelvis": ["pelvis"],
        "thigh_l": ["thigh_l"],
        "index_01_l": ["index_01_l"],
    }

    pairs, skipped = rt.resolve_bone_pairs(target, source, bone_map)

    assert pairs == {"pelvis": "pelvis"}
    assert skipped == ["thigh_l->thigh_l"]


# ---------------------------------------------------------------------------
# _topo_sort_target_bones — parents antes de filhos
# ---------------------------------------------------------------------------


def test_topo_sort_parents_before_children() -> None:
    """Bones pai aparecem antes dos filhos na ordenação topológica."""
    # Armature mock com hierarquia: Hips -> Spine -> Chest, LeftUpLeg -> LeftLeg
    bones = {
        "Hips": SimpleNamespace(parent=None),
        "Spine": SimpleNamespace(parent=SimpleNamespace(parent=None, name="Hips")),
        "Chest": SimpleNamespace(
            parent=SimpleNamespace(parent=SimpleNamespace(parent=None, name="Hips"), name="Spine")
        ),
        "LeftLeg": SimpleNamespace(parent=SimpleNamespace(parent=None, name="LeftUpLeg")),
    }

    class FakeData:
        def get(self, name):
            return bones.get(name)

    fake_arm = SimpleNamespace(data=SimpleNamespace(bones=FakeData()))
    order = rt._topo_sort_target_bones(fake_arm, list(bones.keys()))

    # Hips (depth 0) vem antes de Spine (depth 1) que vem antes de Chest (depth 2).
    assert order.index("Hips") < order.index("Spine")
    assert order.index("Spine") < order.index("Chest")


def _rotation(axis: tuple[float, float, float], degrees: float) -> Quaternion:
    return Quaternion(axis, radians(degrees))


def _assert_same_rotation(actual: Quaternion, expected: Quaternion) -> None:
    assert actual.rotation_difference(expected).angle < 1e-6


def test_solve_target_basis_maps_source_rest_to_target_rest() -> None:
    source_rest = _rotation((0, 0, 1), 90)
    target_rest = _rotation((1, 0, 0), 45)
    alignment = source_rest.inverted() @ target_rest

    basis = rt._solve_target_basis(source_rest, alignment, target_rest)

    _assert_same_rotation(basis, Quaternion())


def test_axis_correction_matches_source_direction_tpose_vs_apose() -> None:
    """Source T-pose (braço +X) vs target A-pose (braço 45° para baixo).

    Com a correção, a direção mundial do osso target tem de seguir a do source
    em qualquer pose — incluindo o rest do source e uma pose animada.
    """
    from mathutils import Vector

    y = Vector((0, 1, 0))
    # Osso aponta ao longo do seu eixo Y local; rest orienta esse eixo no mundo.
    source_rest = _rotation((0, 0, 1), -90)  # braço ao longo de +X (T-pose)
    target_rest = _rotation((0, 0, 1), -45)  # braço 45° abaixo (A-pose)
    src_dir = source_rest @ y
    tgt_dir = target_rest @ y

    correction = rt._axis_correction(source_rest, target_rest, src_dir, tgt_dir)

    # No rest do source, o target aponta na direção do source (T-pose), não no seu A-pose.
    rest_target_pose = source_rest @ correction
    assert (rest_target_pose @ y - src_dir).length < 1e-6

    # Pose animada: braço desce 80° a partir do T-pose (idle Quaternius).
    animated = _rotation((0, 0, 1), -80) @ source_rest
    animated_target_pose = animated @ correction
    assert (animated_target_pose @ y - animated @ y).length < 1e-6


def test_axis_correction_identity_when_rests_equal() -> None:
    """Rests iguais -> correção identidade (retarget 1:1)."""
    from mathutils import Vector

    rest = _rotation((1, 0, 0), 30)
    direction = rest @ Vector((0, 1, 0))

    correction = rt._axis_correction(rest, rest, direction, direction)

    _assert_same_rotation(correction, Quaternion())


def test_solve_target_basis_compensates_animated_parent() -> None:
    source_rest = _rotation((0, 1, 0), 20)
    source_pose = _rotation((0, 0, 1), 65) @ source_rest
    target_parent_rest = _rotation((0, 0, 1), 10)
    target_parent_pose = _rotation((0, 0, 1), 35)
    target_rest = target_parent_rest @ _rotation((1, 0, 0), 40)
    alignment = source_rest.inverted() @ target_rest

    basis = rt._solve_target_basis(
        source_pose,
        alignment,
        target_rest,
        target_parent_pose=target_parent_pose,
        target_parent_rest=target_parent_rest,
    )
    reconstructed_pose = target_parent_pose @ target_parent_rest.inverted() @ target_rest @ basis

    _assert_same_rotation(reconstructed_pose, source_pose @ alignment)


# ---------------------------------------------------------------------------
# rename_existing_clips (mock, sem bpy real)
# ---------------------------------------------------------------------------


def test_rename_existing_clips_no_animation_data_returns_empty(monkeypatch) -> None:
    """Armature sem animation_data -> lista vazia (não crasha)."""
    fake_bpy = SimpleNamespace(data=SimpleNamespace(objects={"Arm": SimpleNamespace(animation_data=None)}))
    monkeypatch.setattr(rt, "_bpy", lambda: fake_bpy)
    assert rt.rename_existing_clips("Arm", {"a": "b"}) == []


# ---------------------------------------------------------------------------
# CLI — help (sem bpy)
# ---------------------------------------------------------------------------


def test_cli_retarget_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["retarget", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--source-track" in result.output
    assert "--clip-name" in result.output
    assert "--replace" in result.output


def test_cli_game_pack_help_lists_procedural_flag() -> None:
    """game-pack expõe --procedural (fallback) — retarget Quaternius é o default."""
    runner = CliRunner()
    result = runner.invoke(main, ["game-pack", "--help"])
    assert result.exit_code == 0
    assert "--procedural" in result.output
    assert "Quaternius" in result.output


def test_cli_retarget_batch_help_lists_options() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["retarget-batch", "--help"])
    assert result.exit_code == 0
    assert "--profile" in result.output
    assert "--source" in result.output
    assert "--clips" in result.output
    assert "--no-fetch" in result.output


def test_cli_rename_clips_help() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["rename-clips", "--help"])
    assert result.exit_code == 0
    assert "--map" in result.output


def test_cli_retarget_batch_requires_source_or_profile_source(tmp_path: Path, monkeypatch) -> None:
    """Sem --source e perfil sem source_path + --no-fetch -> erro claro."""
    runner = CliRunner()
    # perfil custom sem source_path
    custom = tmp_path / "empty.yaml"
    custom.write_text("profile: empty\nbone_map: {}\nclip_map: {}\n", encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "retarget-batch",
            str(tmp_path / "dummy.glb"),
            str(tmp_path / "out.glb"),
            "--profile",
            str(custom),
            "--no-fetch",
        ],
    )
    # Falta bpy OR falta source — qualquer dos dois é erro não-zero.
    assert result.exit_code != 0
