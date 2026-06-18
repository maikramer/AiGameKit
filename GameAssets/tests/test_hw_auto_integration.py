"""Integração hw-auto: GameAssets respeita/propaga a auto-detecção dos sub-tools.

Por defeito (hw_auto: true) NENHUMA flag é injectada — os sub-tools auto-detectam.
Com hw_auto: false no game.yaml, --no-hw-auto propaga a text2d/text3d/paint3d/rigging3d.
"""

from __future__ import annotations

from pathlib import Path

from gameassets.helpers import _append_text2d_profile_args
from gameassets.pipeline import _paint3d_texture_argv, _rigging3d_pipeline_argv, _text3d_argv
from gameassets.profile import GameProfile, Paint3DProfile

_BASE = {"title": "T", "genre": "rpg", "tone": "dark", "style_preset": "low poly"}


def _profile(**extra) -> GameProfile:
    return GameProfile.from_dict({**_BASE, **extra})


def test_profile_hw_auto_defaults_true() -> None:
    assert _profile().hw_auto is True
    assert _profile(hw_auto=False).hw_auto is False


def test_text3d_argv_default_lets_tool_autodetect() -> None:
    p = _profile(text3d={"preset": "fast"})
    args = _text3d_argv("text3d", p, Path("img.png"), Path("out.glb"))
    assert "--no-hw-auto" not in args
    assert "--low-vram" not in args


def test_text3d_argv_propagates_no_hw_auto() -> None:
    p = _profile(hw_auto=False, text3d={"preset": "fast"})
    args = _text3d_argv("text3d", p, Path("img.png"), Path("out.glb"))
    assert "--no-hw-auto" in args


def test_text2d_args_propagate_no_hw_auto() -> None:
    argv: list[str] = []
    _append_text2d_profile_args(_profile(hw_auto=False, text2d={"width": 768}), argv)
    assert "--no-hw-auto" in argv

    argv2: list[str] = []
    _append_text2d_profile_args(_profile(text2d={"width": 768}), argv2)
    assert "--no-hw-auto" not in argv2


def test_paint3d_argv_propagates_no_hw_auto() -> None:
    args = _paint3d_texture_argv(
        "paint3d", Paint3DProfile(), Path("m.glb"), Path("i.png"), Path("o.glb"), hw_auto=False
    )
    assert "--no-hw-auto" in args
    args_on = _paint3d_texture_argv("paint3d", Paint3DProfile(), Path("m.glb"), Path("i.png"), Path("o.glb"))
    assert "--no-hw-auto" not in args_on


def test_rigging3d_argv_no_hw_auto_before_subcommand() -> None:
    args = _rigging3d_pipeline_argv(
        "rigging3d", Path("in.glb"), Path("out.glb"), seed=None, rig_profile=None, hw_auto=False
    )
    # Opção de grupo: tem de vir ANTES do subcomando "pipeline".
    assert args.index("--no-hw-auto") < args.index("pipeline")
    args_on = _rigging3d_pipeline_argv("rigging3d", Path("in.glb"), Path("out.glb"), seed=None, rig_profile=None)
    assert "--no-hw-auto" not in args_on


def test_explicit_profile_low_vram_text2d_still_passed() -> None:
    """Text2D override manual continua a ganhar; Text3D low_vram é no-op (removido)."""
    p = _profile(text3d={"low_vram": True}, text2d={"low_vram": True})
    args = _text3d_argv("text3d", p, Path("img.png"), Path("out.glb"))
    assert "--low-vram" not in args
    argv: list[str] = []
    _append_text2d_profile_args(p, argv)
    assert "--low-vram" in argv
