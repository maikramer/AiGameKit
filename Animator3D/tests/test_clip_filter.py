"""Unit tests for procedural --clips matching (no bpy)."""

from __future__ import annotations

from animator3d.cli import _procedural_action_matches_filter


class TestProceduralClipFilter:
    def test_attack_exact_not_axe_or_sword(self) -> None:
        allowed = {"attack"}
        assert _procedural_action_matches_filter("Animator3D_Attack", allowed)
        assert not _procedural_action_matches_filter("Animator3D_AxeAttack", allowed)
        assert not _procedural_action_matches_filter("Animator3D_SwordAttack", allowed)

    def test_idle_matches_breathe(self) -> None:
        assert _procedural_action_matches_filter("Animator3D_BreatheIdle", {"idle"})

    def test_locomotion(self) -> None:
        allowed = {"idle", "walk", "run", "jump", "roar"}
        assert _procedural_action_matches_filter("Animator3D_Walk", allowed)
        assert _procedural_action_matches_filter("Animator3D_Run", allowed)
        assert _procedural_action_matches_filter("Animator3D_Jump", allowed)
        assert _procedural_action_matches_filter("Animator3D_Roar", allowed)
        assert not _procedural_action_matches_filter("Animator3D_Mine", allowed)
