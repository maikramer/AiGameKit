"""Animações v2.4 (novas fases, holds, anim.json, espelho, animate-set) — CPU, sem modelo."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw

from text2d.anim import ANIMATIONS, get_animation
from text2d.poses import draw_pose_silhouette, pose_prompt
from text2d.quadruped import draw_quad_silhouette, quad_pose_names


class _Render:
    """Render falso: fundo branco + figura escura por célula (o recorte funciona)."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def __call__(self, prompt: str, path: Path, seed: int, refs: list[Path], width: int, height: int) -> None:
        self.calls.append({"prompt": prompt, "path": path, "refs": list(refs), "w": width, "h": height})
        img = Image.new("RGB", (width, height), (255, 255, 255))
        d = ImageDraw.Draw(img)
        cols = max(1, round(width / (height * 0.8)))
        cw = width // cols
        for c in range(cols):
            x0 = c * cw + cw // 6
            d.rectangle([x0, height // 5 + 30 * c, x0 + cw // 4 + 25 * c, height - height // 8], fill=(30, 40, 90))
        img.save(path)


# --- catálogo -----------------------------------------------------------------

NEW_BIPED = ("block", "roll", "shoot", "kick", "punch", "victory", "sneak")
NEW_QUAD = ("quad-idle", "quad-trot", "quad-attack", "quad-jump", "quad-hurt")


@pytest.mark.parametrize("name", NEW_BIPED + NEW_QUAD)
def test_new_animations_registered(name: str) -> None:
    anim = get_animation(name)
    assert len(anim.poses) == 4
    assert anim.body == ("quadruped" if name.startswith("quad") else "biped")


@pytest.mark.parametrize("name", sorted(ANIMATIONS))
def test_holds_match_phase_count(name: str) -> None:
    anim = ANIMATIONS[name]
    assert not anim.holds or len(anim.holds) in (1, len(anim.poses)), name
    assert all(h > 0 for h in anim.holds)


def test_durations_apply_holds_and_round_to_gif_centiseconds() -> None:
    attack = get_animation("attack")
    d = attack.durations(4, 160)
    assert d[1] > d[0] > d[2]  # antecipação longa, golpe curto
    assert all(ms % 10 == 0 for ms in d)
    assert get_animation("walk").durations(3, 125) == [120, 120, 120] or get_animation("walk").durations(3, 125) == [
        130,
        130,
        130,
    ]


def test_durations_repeat_with_phases() -> None:
    attack = get_animation("attack")
    assert attack.durations(8, 100)[:4] == attack.durations(8, 100)[4:]


def test_durations_never_below_gif_minimum() -> None:
    assert min(get_animation("attack").durations(4, 10)) >= 20


def test_death_starts_with_stagger_not_recoil() -> None:
    # A v2.2 começava com "hurt" (atirado para trás) e o esqueleto saía torto.
    assert get_animation("death").poses[0] == "death_hit"


def test_death_holds_last_frame() -> None:
    d = get_animation("death").durations(4, 100)
    assert d[-1] == max(d)


@pytest.mark.parametrize(
    "pose",
    [
        "block_guard",
        "roll_ball",
        "shoot_draw",
        "kick_extend",
        "punch_jab",
        "victory_jump",
        "sneak_contact",
        "death_hit",
    ],
)
@pytest.mark.parametrize("view", ["side", "front", "back", "diag_front", "diag_back"])
def test_new_biped_poses_draw_in_every_view(pose: str, view: str) -> None:
    arr = np.asarray(draw_pose_silhouette(pose, view=view, size=160))[..., 3] > 0
    assert arr.any()
    assert pose_prompt(pose, view=view)


def test_kick_extend_reaches_forward() -> None:
    def right_extent(pose: str) -> int:
        arr = np.asarray(draw_pose_silhouette(pose, size=200))[..., 3] > 0
        return int(np.nonzero(arr.any(axis=0))[0].max())

    assert right_extent("kick_extend") > right_extent("punch_guard") + 10


def test_roll_ball_is_compact() -> None:
    def height(pose: str) -> int:
        bbox = draw_pose_silhouette(pose, size=200).getchannel("A").getbbox()
        assert bbox is not None
        return bbox[3] - bbox[1]

    assert height("roll_ball") < 0.8 * height("idle")


def test_sneak_is_lower_than_walk() -> None:
    def height(pose: str) -> int:
        bbox = draw_pose_silhouette(pose, size=200).getchannel("A").getbbox()
        assert bbox is not None
        return bbox[3] - bbox[1]

    assert height("sneak_contact") < height("walk_contact_front")


def test_sneak_front_view_alternates_legs() -> None:
    a = np.asarray(draw_pose_silhouette("sneak_contact", view="front", size=160))
    b = np.asarray(draw_pose_silhouette("sneak_contact_back", view="front", size=160))
    assert a.tobytes() != b.tobytes()


# --- quadrúpedes ----------------------------------------------------------------


@pytest.mark.parametrize("pose", quad_pose_names())
def test_quad_pose_fits_cell(pose: str) -> None:
    img = draw_quad_silhouette(pose)
    bbox = img.getchannel("A").getbbox()
    assert bbox is not None
    assert bbox[1] > 0, "cabeça cortada no topo"
    assert bbox[3] <= img.height


def _head_top(pose: str) -> int:
    arr = np.asarray(draw_quad_silhouette(pose))[..., 3] > 0
    right = arr[:, int(arr.shape[1] * 0.7) :]
    return int(np.nonzero(right.any(axis=1))[0].min())


def test_quad_sniff_lowers_head_and_alert_raises_it() -> None:
    assert _head_top("quad_sniff") > _head_top("quad_idle") > _head_top("quad_alert")


def test_quad_crouch_is_lower_than_idle() -> None:
    def top(pose: str) -> int:
        bbox = draw_quad_silhouette(pose).getchannel("A").getbbox()
        assert bbox is not None
        return bbox[1]

    assert top("quad_crouch") > top("quad_idle")


def test_quad_walk_stride_longer_than_v22() -> None:
    # O lobo saía de passo curto: o avanço da pata da frente passou de 26 a 32°.
    def feet_spread(pose: str) -> int:
        arr = np.asarray(draw_quad_silhouette(pose))[..., 3] > 0
        rows = np.nonzero(arr.any(axis=1))[0]
        feet = arr[rows.max() - 12 : rows.max() + 1]
        cols = np.nonzero(feet.any(axis=0))[0]
        return int(cols.max() - cols.min())

    assert feet_spread("quad_walk_1") > feet_spread("quad_idle") + 10


def test_quad_trot_pairs_are_diagonal() -> None:
    from text2d.quadruped import _QUAD_POSES

    fn, ff, hn, hf = _QUAD_POSES["trot_1"].legs
    assert fn > 0 and hf > 0 and ff < 0 and hn < 0


# --- generate_cycle: anim.json, holds no GIF -------------------------------------


def _cycle(tmp_path: Path, anim: str = "attack", direction: str = "east") -> dict[str, object]:
    from text2d.sprite import generate_cycle

    return generate_cycle(
        "a knight",
        direction=direction,
        out_dir=tmp_path / anim,
        animation=anim,
        sprite_size=32,
        retries=0,
        measure=False,
        frame_ms=100,
        render=_Render(),
    )


def test_generate_cycle_writes_anim_json(tmp_path: Path) -> None:
    res = _cycle(tmp_path)
    meta = json.loads(Path(str(res["meta"])).read_text())
    assert meta["animation"] == "attack" and meta["loop"] is False
    assert meta["frame_size"] == [32, 32]
    assert [f["x"] for f in meta["frames"]] == [0, 32, 64, 96]
    assert [f["duration_ms"] for f in meta["frames"]] == res["durations"]
    assert meta["total_ms"] == sum(res["durations"])  # type: ignore[arg-type]
    assert meta["atlas"] == "atlas.png"
    px, py = meta["pivot"]
    assert px == 16 and 0 < py <= 32
    assert meta["mirrored_from"] is None


def test_generate_cycle_gif_uses_per_frame_durations(tmp_path: Path) -> None:
    res = _cycle(tmp_path)
    with Image.open(str(res["gif"])) as gif:
        got = []
        for i in range(gif.n_frames):
            gif.seek(i)
            got.append(gif.info["duration"])
    assert got == res["durations"]
    assert len(set(got)) > 1


def test_generate_cycle_mirrored_direction_meta(tmp_path: Path) -> None:
    res = _cycle(tmp_path, anim="walk", direction="west")
    meta = json.loads(Path(str(res["meta"])).read_text())
    assert meta["direction"] == "west" and meta["mirrored_from"] == "east"


# --- mirror_cycle -----------------------------------------------------------------


def test_mirror_cycle_flips_sprites_and_meta(tmp_path: Path) -> None:
    from text2d.sprite import mirror_cycle

    res = _cycle(tmp_path)
    src_dir = Path(str(res["meta"])).parent
    out = mirror_cycle(src_dir, tmp_path / "west", direction="west")
    meta = json.loads(Path(out["meta"]).read_text())
    src_meta = json.loads(Path(str(res["meta"])).read_text())
    assert meta["direction"] == "west" and meta["mirrored_from"] == "east"
    assert meta["pivot"][0] == 32 - src_meta["pivot"][0]
    assert out["durations"] == res["durations"]
    a = np.asarray(Image.open(src_dir / src_meta["frames"][0]["file"]))
    b = np.asarray(Image.open(out["sprites"][0]))
    assert (a[:, ::-1] == b).all()
    assert out["gif"] is not None and out["atlas"] is not None


def test_mirror_cycle_requires_meta(tmp_path: Path) -> None:
    from text2d.sprite import mirror_cycle

    with pytest.raises(FileNotFoundError):
        mirror_cycle(tmp_path, tmp_path / "o", direction="west")


# --- generate_set ---------------------------------------------------------------


def test_generate_set_shares_identity_and_mirrors(tmp_path: Path) -> None:
    from text2d.sprite import generate_set

    render = _Render()
    res = generate_set(
        "a knight",
        out_dir=tmp_path,
        animations=["walk", "attack"],
        directions=["east", "west", "south"],
        render=render,
        sprite_size=24,
        retries=0,
        measure=False,
    )
    identities = [c for c in render.calls if Path(str(c["path"])).name == "identity.png"]
    assert len(identities) == 1 and identities[0]["refs"] == []
    identity = tmp_path / "identity.png"
    sheets = [c for c in render.calls if c not in identities]
    # 2 animações x 2 vistas canónicas (west é espelho, sem GPU); todas com a mesma identidade.
    assert len(sheets) == 4
    assert all(c["refs"][0] == identity for c in sheets)  # type: ignore[index]
    manifest = json.loads(Path(res["set"]).read_text())
    assert manifest["animations"]["walk"] == {
        "east": "walk/east/anim.json",
        "south": "walk/south/anim.json",
        "west": "walk/west/anim.json",
    }
    assert json.loads((tmp_path / "attack/west/anim.json").read_text())["mirrored_from"] == "east"
    assert manifest["identity"] == "identity.png"


def test_generate_set_west_only_keeps_index_to_request(tmp_path: Path) -> None:
    from text2d.sprite import generate_set

    res = generate_set(
        "k", out_dir=tmp_path, animations=["walk"], directions=["west"], render=_Render(), sprite_size=16, retries=0
    )
    assert list(res["animations"]["walk"]) == ["west"]


def test_generate_set_uses_reference_for_first_view(tmp_path: Path) -> None:
    from text2d.sprite import generate_set

    ref = tmp_path / "me.png"
    Image.new("RGB", (16, 16), (255, 255, 255)).save(ref)
    render = _Render()
    generate_set(
        "k", out_dir=tmp_path / "o", animations=["idle"], reference=ref, render=render, sprite_size=16, retries=0
    )
    assert not any(Path(str(c["path"])).name == "identity.png" for c in render.calls)
    assert all(ref in c["refs"] for c in render.calls)  # type: ignore[operator]


def test_generate_set_default_quadruped(tmp_path: Path) -> None:
    from text2d.sprite import DEFAULT_SET, generate_set

    res = generate_set(
        "a wolf", out_dir=tmp_path, body="quadruped", render=_Render(), sprite_size=16, retries=0, measure=False
    )
    assert list(res["animations"]) == list(DEFAULT_SET["quadruped"])


@pytest.mark.parametrize(
    ("anims", "dirs", "msg"),
    [
        (["walk", "quad-walk"], ["east"], "mistura"),
        (["nope"], ["east"], "desconhecidas"),
        (["quad-walk"], ["south"], "perfil"),
    ],
)
def test_generate_set_validates(tmp_path: Path, anims: list[str], dirs: list[str], msg: str) -> None:
    from text2d.sprite import generate_set

    with pytest.raises(ValueError, match=msg):
        generate_set("k", out_dir=tmp_path, animations=anims, directions=dirs, render=_Render())


# --- CLI ------------------------------------------------------------------------


def test_cli_animate_set_rejects_bad_direction() -> None:
    from text2d.cli import cli

    result = CliRunner().invoke(cli, ["animate-set", "k", "--directions", "east,up"])
    assert result.exit_code != 0
    assert "direções inválidas" in result.output


def test_cli_animate_lists_new_anims() -> None:
    from text2d.cli import cli

    anim_opt = next(p for p in cli.commands["animate"].params if p.name == "anim")
    for name in ("sneak", "quad-trot", "quad-attack"):
        assert name in anim_opt.type.choices  # type: ignore[attr-defined]


def test_cli_animate_set_help() -> None:
    from text2d.cli import cli

    result = CliRunner().invoke(cli, ["animate-set", "--help"])
    assert result.exit_code == 0
    assert "--directions" in result.output and "--anims" in result.output


def test_chroma_key_removes_bow_sized_pocket() -> None:
    from text2d.sprite import chroma_key

    # Vão entre arco e corda: bolsa grande (~12% da figura) rodeada de escuro.
    img = Image.new("RGB", (96, 96), (255, 255, 255))
    img.paste((60, 40, 20), (16, 8, 80, 88))
    img.paste((255, 255, 255), (30, 30, 54, 54))
    alpha = np.asarray(chroma_key(img))[..., 3]
    assert alpha[42, 42] == 0
    assert alpha[20, 20] == 255
