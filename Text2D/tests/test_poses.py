"""Testes da biblioteca de poses e do gerador de sprite sheets."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
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
    assert direction_view("north") == "back"
    assert direction_view("south_east") == "diag_front"
    assert direction_view("north_west") == "diag_back"


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
    assert (np.asarray(img)[..., 3] > 0).any()


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
        retries=0,
        generator=gen,
    )
    # Identidade + UMA folha com os 4 frames (mesma imagem = mesma personagem).
    assert len(gen.calls) == 2
    identity, sheet = gen.calls
    assert identity["refs"] is None and identity["width"] == identity["height"] == 512
    assert "standing still" in identity["prompt"]  # idle, não uma pose de passada
    assert sheet["seed"] == 8
    assert len(sheet["refs"]) == 2  # identidade + folha de silhuetas
    assert sheet["width"] == 4 * 48 and sheet["height"] == 64
    assert len(result["frames"]) == 4
    assert all(p.is_file() for p in result["frames"])
    assert result["sheet"] is not None and result["sheet"].is_file()
    assert result["atlas"] is not None and Image.open(result["atlas"]).size == (4 * 32, 32)
    assert result["gif"] is not None and result["gif"].is_file()
    assert (out / "poses_00.png").is_file()


def test_generate_cycle_grid_and_multiple_sheets(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    gen = _FakeGen()
    result = generate_cycle(
        "a blue knight",
        direction="east",
        out_dir=tmp_path / "c10",
        frames=10,
        size=64,
        sprite_size=16,
        transparent=False,
        retries=0,
        generator=gen,
    )
    # 10 frames → grelha 4x2 (8) + folha de 2, mesma identidade e seed.
    assert len(gen.calls) == 3
    assert [(c["width"], c["height"]) for c in gen.calls[1:]] == [(4 * 48, 2 * 64), (2 * 48, 64)]
    assert gen.calls[1]["refs"][0] == gen.calls[2]["refs"][0]
    assert gen.calls[1]["seed"] == gen.calls[2]["seed"]
    assert "2 rows of 4" in gen.calls[1]["prompt"]
    assert len(result["frames"]) == 10


def test_generate_cycle_retries_when_frames_are_copies(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    gen = _FakeGen()  # devolve sempre a mesma imagem → variação 0
    result = generate_cycle(
        "k",
        direction="east",
        out_dir=tmp_path / "r",
        frames=4,
        size=64,
        sprite_size=16,
        transparent=False,
        retries=2,
        generator=gen,
    )
    # identidade + 3 tentativas de folha, cada uma com seed nova
    assert len(gen.calls) == 4
    assert [c["seed"] for c in gen.calls[1:]] == [101, 102, 103]
    assert result["attempts"] == 3


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
    assert "right edge" in gen.calls[1]["prompt"]
    assert (out / "mirrored").is_dir()
    assert len(result["sprites"]) == 4  # 2 pedidos + 2 espelhados


def test_generate_cycle_uses_render_callback(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    seen: list[tuple[str, int, list[Path], int, int]] = []

    def _render(prompt: str, path: Path, seed: int, refs: list[Path], width: int, height: int) -> None:
        seen.append((prompt, seed, list(refs), width, height))
        path.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (width, height)).save(path)

    result = generate_cycle(
        "a blue knight",
        direction="east",
        out_dir=tmp_path / "c",
        frames=2,
        size=64,
        steps=4,
        seed=4,
        sprite_size=16,
        transparent=False,
        retries=0,
        render=_render,
    )
    assert len(seen) == 2  # identidade + 1 folha
    assert seen[1][1] == 5
    assert seen[1][3:] == (2 * 48, 64)
    assert len(result["frames"]) == 2


def test_generate_cycle_with_reference_skips_identity(tmp_path: Path) -> None:
    from text2d.sprite import generate_cycle

    ref = tmp_path / "ref.png"
    Image.new("RGB", (32, 32), (200, 0, 0)).save(ref)
    gen = _FakeGen()
    generate_cycle(
        "k", direction="east", out_dir=tmp_path / "r", frames=4, reference=ref, size=64, retries=0, generator=gen
    )
    assert len(gen.calls) == 1
    assert gen.calls[0]["refs"][0] == ref


# ---------------------------------------------------------------------------
# sprite — pós-processamento (alinhamento + paleta comum)
# ---------------------------------------------------------------------------


def _figure(x: int, y: int, *, leg: int = 0, size: int = 64) -> Image.Image:
    """Boneco RGBA: tronco 8x20 em (x, y) e uma perna que se afasta ``leg`` px."""
    from PIL import ImageDraw

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rectangle([x, y, x + 8, y + 20], fill=(200, 30, 30, 255))
    d.line([(x + 4, y + 20), (x + 4 + leg, y + 34)], fill=(30, 30, 200, 255), width=3)
    return img


def test_align_frames_puts_torso_and_feet_in_same_place() -> None:
    from text2d.sprite import align_frames

    a = _figure(10, 5, leg=10)
    b = _figure(30, 20, leg=-8)  # deslocado e com a perna para trás
    out = align_frames([a, b])
    assert out[0].size == out[1].size and out[0].width == out[0].height
    ba = out[0].getchannel("A").getbbox()
    bb = out[1].getchannel("A").getbbox()
    assert ba is not None and bb is not None
    assert ba[3] == bb[3]  # pés na mesma linha
    # Tronco no mesmo x (a perna não arrasta o âncora).
    ta = out[0].crop((0, ba[1], out[0].width, ba[1] + 10)).getchannel("A").getbbox()
    tb = out[1].crop((0, bb[1], out[1].width, bb[1] + 10)).getchannel("A").getbbox()
    assert ta is not None and tb is not None and abs(ta[0] - tb[0]) <= 1


def test_align_frames_keeps_empty_frames() -> None:
    from text2d.sprite import align_frames

    empty = Image.new("RGBA", (16, 16), (0, 0, 0, 0))
    out = align_frames([empty, _figure(4, 4)])
    assert len(out) == 2
    assert out[0].getchannel("A").getbbox() is None


def test_to_sprites_shares_palette_and_binarizes_alpha() -> None:
    from text2d.sprite import to_sprites

    frames = [_figure(10, 5, leg=10), _figure(12, 6, leg=-6)]
    sprites = to_sprites(frames, sprite_size=32, colors=4)
    assert all(s.size == (32, 32) for s in sprites)
    colors = set()
    for s in sprites:
        arr = np.asarray(s)
        assert set(np.unique(arr[..., 3]).tolist()) <= {0, 255}
        colors |= {tuple(c) for c in arr[arr[..., 3] > 0, :3].tolist()}
    assert len(colors) <= 4  # uma paleta para o ciclo todo


def test_draw_pose_sheet_has_one_cell_per_pose() -> None:
    from text2d.poses import draw_pose_sheet

    sheet = draw_pose_sheet(WALK_CYCLE, cell=(48, 64))
    assert sheet.size == (4 * 48, 64)
    for i in range(4):
        assert sheet.crop((i * 48, 0, (i + 1) * 48, 64)).getbbox() is not None


def test_contact_poses_differ_by_depth_cue() -> None:
    # De perfil, contacto da frente e de trás têm a mesma forma; o cinzento do
    # lado de trás é o que diz ao modelo qual perna vai à frente.
    a = draw_pose_silhouette("walk_contact_front", size=128).convert("RGB")
    b = draw_pose_silhouette("walk_contact_back", size=128).convert("RGB")
    assert a.tobytes() != b.tobytes()
    assert (np.asarray(a) == (150, 150, 150)).all(axis=-1).any()


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


def test_identity_prompt_is_static_and_respects_back_view() -> None:
    # A identidade não pode estar "walking" (o modelo copiava essa passada para
    # todos os frames) nem "facing the viewer" quando a vista é de costas.
    from text2d.sprite import identity_prompt

    east = identity_prompt("a knight", direction="east")
    assert "walking" not in east and "standing still" in east and "right-side profile" in east
    north = identity_prompt("a knight", direction="north")
    assert "behind" in north and "facing the viewer" not in north


def test_sheet_prompt_references_identity_and_silhouettes() -> None:
    from text2d.sprite import sheet_prompt

    prompt = sheet_prompt("a knight.", direction="east", poses=WALK_CYCLE)
    assert "4-frame" in prompt and "first image" in prompt and "second image" in prompt
    assert "a knight," in prompt
    assert "frame 1: the leading leg" in prompt and "frame 4:" in prompt


def test_generate_cycle_warns_when_frames_are_copies(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    from text2d.sprite import generate_cycle

    def _render(prompt: str, path: Path, seed: int, refs: list[Path], width: int, height: int) -> None:
        # Folha com 4 bonecos idênticos (a falha que o aviso tem de apanhar).
        img = Image.new("RGB", (width, height), (255, 255, 255))
        cell = width // 4 if width > height else width
        for i in range(max(1, width // cell)):
            img.paste(_figure(10, 5).convert("RGB"), (i * cell, 0), _figure(10, 5))
        img.save(path)

    generate_cycle(
        "k",
        direction="east",
        out_dir=tmp_path / "w",
        frames=4,
        size=64,
        sprite_size=32,
        transparent=False,
        render=_render,
    )
    assert "frames quase iguais" in capsys.readouterr().out


def test_front_silhouettes_alternate_legs_without_sideways_spread() -> None:
    # De frente: pé direito à frente, passa a esquerda (levantada), pé esquerdo
    # à frente, passa a direita — e pernas verticais (sem "V" lateral, que fazia
    # o modelo virar a personagem de perfil).
    from text2d.poses import draw_pose_silhouette

    def feet(pose: str) -> tuple[int, int]:
        arr = np.asarray(draw_pose_silhouette(pose, view="front", size=200))[..., 3] > 0
        return int(np.nonzero(arr[:, :100].any(axis=1))[0].max()), int(np.nonzero(arr[:, 100:].any(axis=1))[0].max())

    l0, r0 = feet("walk_contact_front")
    l1, r1 = feet("walk_passing")
    l2, r2 = feet("walk_contact_back")
    l3, r3 = feet("walk_passing_back")
    assert r0 > l0 and l2 > r2  # pé da frente mais abaixo, alternando
    assert l1 < r1 and r3 < l3  # em cada passagem sobe a perna que estava atrás
    width = np.asarray(draw_pose_silhouette("walk_contact_front", view="front", size=200))[..., 3] > 0
    assert np.ptp(np.nonzero(width.any(axis=0))[0]) < 0.4 * 200  # sem pernas abertas para os lados
