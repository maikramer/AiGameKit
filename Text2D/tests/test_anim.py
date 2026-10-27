"""Catálogo de animações (text2d.anim) + quadrúpedes + fases aéreas — CPU, sem modelo."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest
from PIL import Image

from text2d.anim import ANIMATIONS, cell_size, check_direction, draw_guide, get_animation, phase_lift, phase_prompt
from text2d.poses import RUN_CYCLE, draw_pose_silhouette, pose_lift
from text2d.quadruped import (
    QUAD_GALLOP_CYCLE,
    QUAD_WALK_CYCLE,
    draw_quad_silhouette,
    quad_pose_names,
    quad_pose_prompt,
)


class _FakeGen:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def generate(self, prompt: str, **kwargs: Any) -> tuple[Image.Image, dict[str, Any]]:
        self.calls.append({"prompt": prompt, **kwargs})
        return Image.new("RGB", (kwargs.get("width") or 64, kwargs.get("height") or 64), (10, 20, 30)), {}

    @staticmethod
    def save_image(image: Image.Image, path: Path, image_format: str | None = None) -> Path:
        image.save(path, format="PNG")
        return path


@pytest.mark.parametrize("name", sorted(ANIMATIONS))
def test_every_animation_has_drawable_poses(name: str) -> None:
    anim = ANIMATIONS[name]
    cell = cell_size(anim, 320)
    guide = draw_guide(anim, list(anim.poses), view="side", cell=cell)
    assert guide.size == (cell[0] * len(anim.poses), cell[1])
    for pose in anim.poses:
        assert phase_prompt(anim, pose, view="side")


def test_get_animation_unknown_lists_available() -> None:
    with pytest.raises(KeyError, match="quad-walk"):
        get_animation("dance")


def test_cell_size_by_body() -> None:
    assert cell_size(ANIMATIONS["walk"], 320) == (256, 320)
    w, h = cell_size(ANIMATIONS["quad-walk"], 320)
    assert w > h and w % 16 == 0 and h % 16 == 0
    assert 4 * w * h == 1280 * 256  # mesmo nº de pixels que a folha bípede


def test_quadruped_rejects_front_views() -> None:
    check_direction(ANIMATIONS["quad-walk"], "east")
    with pytest.raises(ValueError, match="east/west"):
        check_direction(ANIMATIONS["quad-walk"], "south")


def test_run_cycle_alternates_legs() -> None:
    assert len(set(RUN_CYCLE)) == 4
    a = np.asarray(draw_pose_silhouette("run_contact", size=128))
    b = np.asarray(draw_pose_silhouette("run_contact_back", size=128))
    assert a.tobytes() != b.tobytes()


def test_airborne_poses_are_lifted_in_silhouette() -> None:
    assert pose_lift("jump_up") > pose_lift("jump_fall") > 0 == pose_lift("idle")

    def feet_y(pose: str) -> int:
        alpha = np.asarray(draw_pose_silhouette(pose, size=200))[..., 3] > 0
        return int(np.nonzero(alpha.any(axis=1))[0].max())

    assert feet_y("jump_up") < feet_y("idle")


@pytest.mark.parametrize("view", ["diag_front", "diag_back", "back"])
def test_new_views_alternate_between_contacts(view: str) -> None:
    a = np.asarray(draw_pose_silhouette("walk_contact_front", view=view, size=128))
    b = np.asarray(draw_pose_silhouette("walk_contact_back", view=view, size=128))
    assert a.tobytes() != b.tobytes()


def test_back_view_moves_forward_foot_up() -> None:
    # De costas o pé que avança afasta-se do observador → sobe no ecrã.
    arr = np.asarray(draw_pose_silhouette("walk_contact_front", view="back", size=200))[..., 3] > 0
    left = int(np.nonzero(arr[:, :100].any(axis=1))[0].max())
    right = int(np.nonzero(arr[:, 100:].any(axis=1))[0].max())
    assert right < left


def test_quad_cycles_and_prompts() -> None:
    assert len(set(QUAD_WALK_CYCLE)) == 4 and len(set(QUAD_GALLOP_CYCLE)) == 4
    for pose in quad_pose_names():
        assert "leg" in quad_pose_prompt(pose)
    with pytest.raises(KeyError):
        draw_quad_silhouette("nope")


def test_quad_silhouette_has_depth_cue_and_differs_per_phase() -> None:
    frames = [np.asarray(draw_quad_silhouette(p)) for p in QUAD_WALK_CYCLE]
    assert (frames[0][..., :3] == (150, 150, 150)).all(axis=-1).any()
    assert len({f.tobytes() for f in frames}) == 4
    assert phase_lift(ANIMATIONS["quad-gallop"], "gallop_extend") > 0


def test_align_frames_restores_jump_height() -> None:
    from text2d.sprite import align_frames

    def fig() -> Image.Image:
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        img.paste((200, 0, 0, 255), (20, 10, 30, 50))
        return img

    out = align_frames([fig(), fig()], lifts=[0.0, 0.2])
    ground = out[0].getchannel("A").getbbox()
    air = out[1].getchannel("A").getbbox()
    assert ground is not None and air is not None
    assert ground[3] - air[3] == round(0.2 * 40)


def test_generate_cycle_quadruped_uses_wide_cells(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    gen = _FakeGen()
    result = generate_cycle(
        "a brown dog",
        direction="east",
        out_dir=tmp_path / "dog",
        animation="quad-walk",
        size=320,
        sprite_size=32,
        transparent=False,
        generator=gen,
    )
    identity, sheet = gen.calls
    assert "all four legs" in identity["prompt"]
    assert sheet["width"] == 4 * 320 and sheet["height"] == 256
    assert "four-legged walk cycle" in sheet["prompt"] and "frame 1: walking" in sheet["prompt"]
    assert len(result["frames"]) == 4


def test_generate_cycle_jump_prompt_drops_ground_line(tmp_path: Path) -> None:
    from text2d.sprite import sheet_prompt

    jump = sheet_prompt("k", direction="east", poses=list(ANIMATIONS["jump"].poses), animation="jump")
    walk = sheet_prompt("k", direction="east", poses=list(ANIMATIONS["walk"].poses), animation="walk")
    assert "same height as its silhouette" in jump and "walking" not in jump
    assert "feet on the same ground line" in walk


def test_generate_cycle_invalid_direction_creates_nothing(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    with pytest.raises(ValueError):
        generate_cycle("d", direction="north", out_dir=tmp_path / "no", animation="quad-walk", generator=_FakeGen())
    assert not (tmp_path / "no").exists()


def test_cli_animate_help_lists_animations() -> None:
    from click.testing import CliRunner

    from text2d.cli import cli

    result = CliRunner().invoke(cli, ["animate", "--help"])
    assert result.exit_code == 0
    assert "quad-gallop" in result.output


def _white_scene() -> Image.Image:
    """Fundo branco + personagem branca com contorno escuro (o caso do cavalo)."""
    from PIL import ImageDraw

    img = Image.new("RGB", (64, 64), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([16, 8, 48, 56], fill=(250, 250, 250), outline=(30, 30, 30), width=2)
    return img


def test_chroma_key_keeps_white_body_inside_outline() -> None:
    from text2d.sprite import chroma_key

    out = chroma_key(_white_scene())
    assert out is not None
    alpha = np.asarray(out)[..., 3]
    assert alpha[0, 0] == 0 and alpha[63, 63] == 0  # fundo fora
    assert alpha[30, 30] == 255 and alpha[8, 30] == 255  # corpo branco e contorno intactos


def test_chroma_key_pockets_only_on_request() -> None:
    from text2d.sprite import chroma_key

    img = Image.new("RGB", (64, 64), (255, 12, 140))
    img.paste((20, 20, 20), (16, 8, 48, 56))
    img.paste((255, 12, 140), (28, 30, 36, 40))  # bolsa fechada da cor-chave
    assert np.asarray(chroma_key(img))[34, 32, 3] == 255
    assert np.asarray(chroma_key(img, pockets=True))[34, 32, 3] == 0


def test_chroma_key_gives_up_on_busy_border() -> None:
    from text2d.sprite import chroma_key

    noise = np.random.default_rng(0).integers(0, 255, (64, 64, 3), dtype=np.uint8)
    assert chroma_key(Image.fromarray(noise, "RGB")) is None
