"""Testes dos geradores de clips de ``animator3d.humanoid`` com bpy mockado.

Os geradores (``gait_clip``, ``idle_clip``, ``jump_clip``, ``fall_clip``,
``attack_clip``, etc.) apenas interagem com o Blender através de
``rig.key_pose``/``rig.finish_action``. Substituímos esses métodos por um
gravador para capturar (frame, pose) sem executar ops do Blender.

``try_humanoid_clip`` percorre ainda ``bpy_ops`` e ``_bpy()`` — esses pontos
são mockados com ``monkeypatch`` + ``MagicMock``.
"""

from __future__ import annotations

import importlib.util
from typing import Any

import pytest
from animator3d import bpy_ops, humanoid
from animator3d.humanoid import HumanoidRig, Pose
from pytest import MonkeyPatch

pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("bpy") is None,
    reason="bpy (Blender 5.1) nao disponivel neste ambiente",
)

# ---------------------------------------------------------------------------
# Fixtures — cadeias no estilo Mixamo.
# ---------------------------------------------------------------------------

MIXAMO_CHAINS: dict[str, list[str]] = {
    "body": ["mixamorig:Hips"],
    "spine": ["mixamorig:Spine", "mixamorig:Spine1", "mixamorig:Spine2"],
    "neck": ["mixamorig:Neck", "mixamorig:Head"],
    "arm_r": [
        "mixamorig:RightShoulder",
        "mixamorig:RightArm",
        "mixamorig:RightForeArm",
        "mixamorig:RightHand",
    ],
    "arm_l": [
        "mixamorig:LeftShoulder",
        "mixamorig:LeftArm",
        "mixamorig:LeftForeArm",
        "mixamorig:LeftHand",
    ],
    "leg_r": [
        "mixamorig:RightUpLeg",
        "mixamorig:RightLeg",
        "mixamorig:RightFoot",
        "mixamorig:RightToeBase",
    ],
    "leg_l": [
        "mixamorig:LeftUpLeg",
        "mixamorig:LeftLeg",
        "mixamorig:LeftFoot",
        "mixamorig:LeftToeBase",
    ],
}


class _RecordingRig(HumanoidRig):
    """HumanoidRig que grava key_poses em vez de escrever no Blender.

    Herda os métodos anatómicos (``arm_bones``/``leg_bones``/``spine``/...) da
    classe real — dependem só de ``self.chains`` — mas descarta o ``__init__``
    que importa ``mathutils``/``bpy``.
    """

    def __init__(self, armature_name: str = "Armature", chains: dict[str, list[str]] | None = None) -> None:
        self.chains = chains if chains is not None else MIXAMO_CHAINS
        self.keyframes: list[tuple[int, Pose]] = []
        self.finish_calls: list[tuple[Any, bool]] = []

    def key_pose(self, frame: int, pose: Pose) -> None:
        self.keyframes.append((frame, pose))

    def finish_action(self, action: Any, *, cyclic: bool) -> None:
        self.finish_calls.append((action, cyclic))


def _make_rig() -> _RecordingRig:
    return _RecordingRig()


def _assert_frames_in_range(rig: _RecordingRig, frame_start: int, frame_end: int) -> None:
    assert rig.keyframes, "clip não gerou nenhuma keyframe"
    frames = [f for f, _ in rig.keyframes]
    assert min(frames) >= frame_start
    assert max(frames) <= frame_end


# ---------------------------------------------------------------------------
# gait_clip (walk / run)
# ---------------------------------------------------------------------------


class TestGaitClip:
    def test_walk_produces_keyframes_in_range(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=48, cycles=2.0, params=humanoid._WALK_PARAMS)
        _assert_frames_in_range(rig, 1, 48)

    def test_walk_keyframe_count_for_two_cycles(self) -> None:
        # 2 ciclos * 8 keys + 1 key de fecho do loop = 17
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=48, cycles=2.0, params=humanoid._WALK_PARAMS)
        assert len(rig.keyframes) == 17

    def test_walk_single_cycle(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=24, cycles=1.0, params=humanoid._WALK_PARAMS)
        # 1 ciclo * 8 keys + 1 fecho = 9
        assert len(rig.keyframes) == 9

    def test_run_uses_run_params(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=36, cycles=2.0, params=humanoid._RUN_PARAMS)
        _assert_frames_in_range(rig, 1, 36)
        assert len(rig.keyframes) > 0

    def test_zero_cycles_clamped_to_one(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=24, cycles=0.0, params=humanoid._WALK_PARAMS)
        # cycles arredondado para 0 vira max(0,1)=1 → 9 keys
        assert len(rig.keyframes) == 9

    def test_loop_close_key_at_frame_end(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=48, cycles=2.0, params=humanoid._WALK_PARAMS)
        last_frame, _ = rig.keyframes[-1]
        assert last_frame == 48

    def test_all_poses_nonempty(self) -> None:
        rig = _make_rig()
        humanoid.gait_clip(rig, frame_start=1, frame_end=48, cycles=2.0, params=humanoid._WALK_PARAMS)
        for _frame, pose in rig.keyframes:
            assert len(pose) > 0


# ---------------------------------------------------------------------------
# idle_clip
# ---------------------------------------------------------------------------


class TestIdleClip:
    def test_produces_keyframes_in_range(self) -> None:
        rig = _make_rig()
        humanoid.idle_clip(rig, frame_start=1, frame_end=72, cycles=2.0, breath_amp=0.035)
        _assert_frames_in_range(rig, 1, 72)

    def test_keyframe_count(self) -> None:
        # n_keys = max(round(cycles)*4, 4) = 8; range(8+1) = 9 keys
        rig = _make_rig()
        humanoid.idle_clip(rig, frame_start=1, frame_end=72, cycles=2.0, breath_amp=0.035)
        assert len(rig.keyframes) == 9

    def test_min_keys_when_cycles_below_one(self) -> None:
        rig = _make_rig()
        humanoid.idle_clip(rig, frame_start=1, frame_end=24, cycles=0.1, breath_amp=0.035)
        assert len(rig.keyframes) >= 4

    def test_first_frame_is_start(self) -> None:
        rig = _make_rig()
        humanoid.idle_clip(rig, frame_start=5, frame_end=40, cycles=1.0, breath_amp=0.02)
        assert rig.keyframes[0][0] == 5

    def test_idle_is_neutral_no_weapon_pose(self) -> None:
        """Idle não deve forçar o braço direito em pose de guarda de arma.

        Regressão: antes o idle hardcoded pitch=0.22/roll=0.14 no RightArm e
        pitch=-0.62 no RightForeArm (guarda de espada). Agora deve ser neutro.
        """
        rig = _make_rig()
        humanoid.idle_clip(rig, frame_start=1, frame_end=72, cycles=2.0, breath_amp=0.035)
        ab = rig.arm_bones("r")
        for _frame, pose in rig.keyframes:
            upper = pose.get(ab["upper"], {})
            # Idle neutro: pitch do braço deve ser ~0 (respiração subtil), não 0.22.
            assert abs(upper.get("pitch", 0.0)) < 0.1, f"RightArm pitch={upper.get('pitch')} — idle não é neutro"
            fore = pose.get(ab["fore"], {})
            # Forearm não deve estar flexionado em guarda (-0.62).
            assert abs(fore.get("pitch", 0.0)) < 0.3, f"RightForeArm pitch={fore.get('pitch')} — pose de arma"


# ---------------------------------------------------------------------------
# jump_clip
# ---------------------------------------------------------------------------


class TestJumpClip:
    def test_produces_keyframes_in_range(self) -> None:
        rig = _make_rig()
        humanoid.jump_clip(rig, frame_start=1, frame_end=36)
        _assert_frames_in_range(rig, 1, 36)

    def test_keyframe_count(self) -> None:
        # base, anticipo, crouch, launch, tuck, descend, land, end = 8
        rig = _make_rig()
        humanoid.jump_clip(rig, frame_start=1, frame_end=36)
        assert len(rig.keyframes) == 8

    def test_last_frame_is_end(self) -> None:
        rig = _make_rig()
        humanoid.jump_clip(rig, frame_start=1, frame_end=50)
        assert rig.keyframes[-1][0] == 50


# ---------------------------------------------------------------------------
# fall_clip
# ---------------------------------------------------------------------------


class TestFallClip:
    def test_produces_keyframes_in_range(self) -> None:
        rig = _make_rig()
        humanoid.fall_clip(rig, frame_start=1, frame_end=24)
        _assert_frames_in_range(rig, 1, 24)

    def test_keyframe_count(self) -> None:
        # n=4 → range(5) = 5 keys
        rig = _make_rig()
        humanoid.fall_clip(rig, frame_start=1, frame_end=24)
        assert len(rig.keyframes) == 5

    def test_spans_full_range(self) -> None:
        rig = _make_rig()
        humanoid.fall_clip(rig, frame_start=10, frame_end=30)
        frames = [f for f, _ in rig.keyframes]
        assert min(frames) == 10
        assert max(frames) == 30


# ---------------------------------------------------------------------------
# attack_clip
# ---------------------------------------------------------------------------


class TestAttackClip:
    def test_produces_keyframes_in_range(self) -> None:
        rig = _make_rig()
        humanoid.attack_clip(rig, frame_start=1, frame_end=24, strikes=1)
        _assert_frames_in_range(rig, 1, 24)

    def test_single_strike_keyframe_count(self) -> None:
        # base, windup, strike, overshoot, settle, end = 6 (desde a revisão
        # biomecânica que adicionou overshoot + settle como keys separadas).
        rig = _make_rig()
        humanoid.attack_clip(rig, frame_start=1, frame_end=24, strikes=1)
        assert len(rig.keyframes) == 6

    def test_two_strikes_doubles_keys(self) -> None:
        rig = _make_rig()
        humanoid.attack_clip(rig, frame_start=1, frame_end=48, strikes=2)
        assert len(rig.keyframes) == 12

    def test_zero_strikes_clamped_to_one(self) -> None:
        rig = _make_rig()
        humanoid.attack_clip(rig, frame_start=1, frame_end=24, strikes=0)
        assert len(rig.keyframes) == 6


# ---------------------------------------------------------------------------
# try_humanoid_clip — dispatch + early returns
# ---------------------------------------------------------------------------


class TestTryHumanoidClipDispatch:
    def test_unknown_kind_returns_false(self) -> None:
        result = humanoid.try_humanoid_clip(
            "backflip",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=24,
            action_name="X",
        )
        assert result is False

    def test_non_humanoid_chains_returns_false(self) -> None:
        result = humanoid.try_humanoid_clip(
            "walk",
            "Armature",
            {},  # sem cadeias → não humanoide
            frame_start=1,
            frame_end=24,
            action_name="X",
        )
        assert result is False

    def test_walk_dispatches_and_returns_true(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "walk",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=48,
            action_name="WalkAction",
        )
        assert result is True
        assert rig.keyframes, "walk dispatch não gerou keyframes"
        rig.finish_action.assert_called_once()
        _assert_mock_frames_in_range(rig, 1, 48)

    def test_run_dispatches(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "run",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=36,
            action_name="RunAction",
        )
        assert result is True
        assert len(rig.keyframes) > 0

    def test_jump_dispatches(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "jump",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=36,
            action_name="JumpAction",
        )
        assert result is True
        assert len(rig.keyframes) == 8

    def test_fall_dispatches(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "fall",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=24,
            action_name="FallAction",
        )
        assert result is True
        assert len(rig.keyframes) == 5

    def test_idle_dispatches(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "idle",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=72,
            action_name="IdleAction",
        )
        assert result is True
        assert len(rig.keyframes) > 0

    def test_attack_dispatches(self, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            "attack",
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=24,
            action_name="AttackAction",
            strikes=2,
        )
        assert result is True
        # 6 keys/strike (base, windup, strike, overshoot, settle, end) × 2 = 12.
        assert len(rig.keyframes) == 12

    @pytest.mark.parametrize("kind", ["mine", "chop", "spear", "axe", "sword", "gather", "turn"])
    def test_tool_clips_dispatch(self, kind: str, monkeypatch: MonkeyPatch) -> None:
        rig = _patch_bpy_path(monkeypatch)
        result = humanoid.try_humanoid_clip(
            kind,
            "Armature",
            MIXAMO_CHAINS,
            frame_start=1,
            frame_end=40,
            action_name=f"{kind}Action",
        )
        assert result is True
        assert rig.keyframes, f"{kind} não gerou keyframes"


def _patch_bpy_path(monkeypatch: MonkeyPatch) -> Any:
    """Mocka ``_bpy``, ``bpy_ops.*`` e ``HumanoidRig`` para ``try_humanoid_clip``.

    Devolve o rig gravador (Mock) injetado no lugar de ``HumanoidRig``.
    """
    from unittest.mock import MagicMock

    scene = MagicMock()
    fake_bpy = MagicMock()
    fake_bpy.context.scene = scene
    monkeypatch.setattr(humanoid, "_bpy", lambda: fake_bpy)

    monkeypatch.setattr(bpy_ops, "normalize_armature_before_animation", MagicMock())
    monkeypatch.setattr(bpy_ops, "stash_if_needed_for_action", MagicMock())
    action_sentinel = MagicMock(name="action")
    monkeypatch.setattr(bpy_ops, "ensure_action", MagicMock(return_value=action_sentinel))
    monkeypatch.setattr(bpy_ops, "finalize_current_action_to_nla", MagicMock())

    rig = MagicMock(spec=_RecordingRig)
    rig.chains = MIXAMO_CHAINS
    rig.keyframes = []
    rig.key_pose = lambda frame, pose: rig.keyframes.append((frame, pose))
    rig.finish_action = MagicMock()

    rig_factory = MagicMock(return_value=rig)
    rig_factory.is_humanoid = staticmethod(HumanoidRig.is_humanoid)
    monkeypatch.setattr(humanoid, "HumanoidRig", rig_factory)
    return rig


def _assert_mock_frames_in_range(rig: Any, frame_start: int, frame_end: int) -> None:
    assert rig.keyframes
    frames = [f for f, _ in rig.keyframes]
    assert min(frames) >= frame_start
    assert max(frames) <= frame_end
