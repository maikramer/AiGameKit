"""Testes da biblioteca de poses e do gerador de sprite sheets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from PIL import Image

from text2d.poses import (
    DIRECTIONS,
    WALK_CYCLE,
    alpha_difference,
    build_prompt,
    canonical_direction,
    direction_prompt,
    direction_view,
    draw_pose_silhouette,
    is_mirrored,
    pose_names,
    pose_prompt,
    save_pose_silhouette,
    walk_cycle_prompts,
)

# ---------------------------------------------------------------------------
# Biblioteca de poses
# ---------------------------------------------------------------------------


def test_pose_names_contains_walk_cycle() -> None:
    names = pose_names()
    for phase in WALK_CYCLE:
        assert phase in names


def test_walk_cycle_is_four_phases() -> None:
    assert len(WALK_CYCLE) == 4
    assert len(set(WALK_CYCLE)) == 4


def test_pose_prompt_side_and_front_differ() -> None:
    side = pose_prompt("walk_contact_front", view="side")
    front = pose_prompt("walk_contact_front", view="front")
    assert side != front
    assert "leg" in side and "leg" in front


def test_pose_prompt_mentions_legs_and_arms() -> None:
    for name in pose_names():
        text = pose_prompt(name, view="side")
        assert "leg" in text or "feet" in text or "knee" in text, name
        assert "arm" in text or "arms" in text, name


def test_pose_prompt_unknown_pose_raises() -> None:
    with pytest.raises(KeyError):
        pose_prompt("pose_que_nao_existe")


def test_directions_cover_eight() -> None:
    assert len(DIRECTIONS) == 8
    for name in DIRECTIONS:
        assert direction_prompt(name)


def test_direction_view_side_and_front() -> None:
    assert direction_view("east") == "side"
    assert direction_view("west") == "side"
    assert direction_view("south") == "front"
    assert direction_view("north") == "front"


def test_mirrored_directions() -> None:
    assert is_mirrored("west") is True
    assert is_mirrored("north_west") is True
    assert is_mirrored("south_west") is True
    assert is_mirrored("east") is False
    assert is_mirrored("south") is False


def test_canonical_direction_maps_mirrored_pairs() -> None:
    assert canonical_direction("west") == "east"
    assert canonical_direction("north_west") == "north_east"
    assert canonical_direction("south_west") == "south_east"
    assert canonical_direction("east") == "east"
    assert canonical_direction("south") == "south"


def test_build_prompt_contains_all_parts() -> None:
    prompt = build_prompt("a blue knight", direction="east", pose="walk_down", extra="flat colors")
    assert "a blue knight" in prompt
    assert "profile" in prompt
    assert "knee" in prompt
    assert "flat colors" in prompt


def test_walk_cycle_prompts_differ_per_frame() -> None:
    prompts = walk_cycle_prompts("a blue knight", direction="east")
    assert len(prompts) == 4
    assert len(set(prompts)) == 4


def test_walk_cycle_prompts_respect_direction() -> None:
    east = walk_cycle_prompts("knight", direction="east")[0]
    south = walk_cycle_prompts("knight", direction="south")[0]
    assert east != south
    assert "right" in east  # perfil: frente/trás
    assert "right" in south  # de frente: esquerda/direita


# ---------------------------------------------------------------------------
# Silhueta de pose
# ---------------------------------------------------------------------------


def test_draw_pose_silhouette_is_rgba_with_content() -> None:
    img = draw_pose_silhouette("walk_contact_front", size=128)
    assert img.mode == "RGBA"
    assert img.size == (128, 128)
    assert any(p[3] > 0 for p in img.getdata())


def test_draw_pose_silhouette_differs_between_poses() -> None:
    a = draw_pose_silhouette("walk_contact_front", size=128)
    b = draw_pose_silhouette("walk_passing", size=128)
    assert alpha_difference(a, b) > 0.01


def test_draw_pose_silhouette_unknown_pose_raises() -> None:
    with pytest.raises(KeyError):
        draw_pose_silhouette("nope")


def test_save_pose_silhouette(tmp_path: Path) -> None:
    out = save_pose_silhouette("run_air", tmp_path / "sil.png", size=64)
    assert out.is_file()
    assert Image.open(out).mode == "RGBA"


# ---------------------------------------------------------------------------
# Métrica
# ---------------------------------------------------------------------------


def test_alpha_difference_identical_is_zero() -> None:
    a = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    assert alpha_difference(a, a) == 0.0


def test_alpha_difference_different_is_positive() -> None:
    a = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    b = Image.new("RGBA", (8, 8), (255, 0, 0, 0))
    assert alpha_difference(a, b) == 1.0


# ---------------------------------------------------------------------------
# sprite.generate_cycle (generator mockado)
# ---------------------------------------------------------------------------


class _FakeGen:
    """Generator falso: regista prompts e escreve PNGs."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, **kwargs: Any) -> tuple[Image.Image, dict[str, Any]]:
        self.calls.append({"prompt": prompt, **kwargs})
        return Image.new("RGB", (64, 64), (10, 20, 30)), {}

    @staticmethod
    def save_image(image: Image.Image, path: Path, image_format: str | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        image.save(path, format="PNG")
        return path


def test_generate_cycle_writes_frames_and_sheet(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    gen = _FakeGen()
    out = tmp_path / "cycle"
    result = generate_cycle(
        "a blue knight",
        direction="east",
        out_dir=out,
        frames=4,
        size=64,
        steps=4,
        seed=7,
        sprite_size=32,
        transparent=False,
        generator=gen,
    )
    # 1 frame de referência + 4 frames do ciclo.
    assert len(gen.calls) == 5
    assert len(result["frames"]) == 4
    assert all(p.is_file() for p in result["frames"])
    assert result["sheet"] is not None and result["sheet"].is_file()
    assert result["gif"] is not None and result["gif"].is_file()
    # Cada frame usa a referência e uma seed própria.
    frame_calls = gen.calls[1:]
    assert [c["seed"] for c in frame_calls] == [8, 9, 10, 11]
    assert all(c["refs"] for c in frame_calls)
    # O prompt de cada frame tem a pose da fase (pernas descritas).
    assert len({c["prompt"] for c in frame_calls}) == 4


def test_generate_cycle_mirrored_direction_uses_canonical(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    gen = _FakeGen()
    out = tmp_path / "cycle_w"
    result = generate_cycle(
        "a blue knight",
        direction="west",
        out_dir=out,
        frames=2,
        size=64,
        steps=4,
        sprite_size=32,
        transparent=False,
        generator=gen,
    )
    # O prompt usa a direção canónica (east) e o set pedido fica espelhado.
    assert "right edge" in gen.calls[0]["prompt"]
    assert (out / "mirrored").is_dir()
    assert len(result["sprites"]) == 4  # 2 pedidos + 2 espelhados


def test_generate_cycle_uses_render_callback(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    seen: list[tuple[str, int, list[Path]]] = []

    def _render(prompt: str, path: Path, seed: int, refs: list[Path]) -> None:
        seen.append((prompt, seed, list(refs)))
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (32, 32)).save(path)

    result = generate_cycle(
        "a blue knight",
        direction="east",
        out_dir=tmp_path / "c",
        frames=2,
        size=32,
        steps=4,
        seed=4,
        sprite_size=16,
        transparent=False,
        render=_render,
    )
    assert len(seen) == 3  # referência + 2 frames
    assert seen[1][1] == 5 and seen[2][1] == 6
    assert result["frames"]


def test_generate_cycle_invalid_frames_raises(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    with pytest.raises(ValueError, match="frames"):
        generate_cycle("k", direction="east", out_dir=tmp_path / "x", frames=0, generator=_FakeGen())


def test_generate_cycle_missing_reference_raises(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    with pytest.raises(FileNotFoundError):
        generate_cycle(
            "k",
            direction="east",
            out_dir=tmp_path / "x",
            frames=1,
            reference=tmp_path / "nao_existe.png",
            generator=_FakeGen(),
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_poses_lists_poses() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["poses"])
    assert r.exit_code == 0
    assert "walk_contact_front" in r.output
    assert "walk_passing" in r.output


def test_cli_generate_pose_appends_text_to_payload(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from text2d import cli

    captured: dict[str, Any] = {}

    def _fake_delegate(backend: str, *, payload: dict, **kwargs: object) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(cli, "delegate_or_prepare", _fake_delegate)
    r = CliRunner().invoke(
        cli.cli, ["generate", "a knight", "--pose", "walk_contact_front", "-o", str(tmp_path / "o.png")]
    )
    assert r.exit_code == 0, r.output
    assert "the leading leg stretched far forward" in captured["payload"]["prompt"]


def test_cli_generate_pose_free_text(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from text2d import cli

    captured: dict[str, Any] = {}

    def _fake_delegate(backend: str, *, payload: dict, **kwargs: object) -> bool:
        captured["payload"] = payload
        return True

    monkeypatch.setattr(cli, "delegate_or_prepare", _fake_delegate)
    r = CliRunner().invoke(
        cli.cli, ["generate", "a knight", "--pose", "the left leg raised high", "-o", str(tmp_path / "o.png")]
    )
    assert r.exit_code == 0, r.output
    assert "the left leg raised high" in captured["payload"]["prompt"]


def test_cli_walk_cycle_help() -> None:
    from text2d.cli import cli

    r = CliRunner().invoke(cli, ["walk-cycle", "--help"])
    assert r.exit_code == 0
    for flag in ("--direction", "--frames", "--out-dir", "--ref", "--measure"):
        assert flag in r.output
