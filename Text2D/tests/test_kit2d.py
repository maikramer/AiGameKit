"""Ferramentas de jogo 2D: tileset (autotile), parallax, vfx e items — CPU, render falso."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest
from click.testing import CliRunner
from PIL import Image, ImageDraw, ImageFilter

from text2d import items, kit2d, parallax, tileset, vfx


def _noise(w: int, h: int, seed: int, base: tuple[int, int, int]) -> Image.Image:
    rng = np.random.default_rng(seed)
    arr = np.clip(np.array(base) + rng.normal(0, 30, (h, w, 3)), 0, 255).astype(np.uint8)
    return Image.fromarray(arr, "RGB")


def _fake_layer(w: int, h: int, seed: int) -> Image.Image:
    """Magenta em cima, 'montanhas' escuras em baixo com um buraco fechado de magenta."""
    img = Image.new("RGB", (w, h), kit2d.MAGENTA)
    d = ImageDraw.Draw(img)
    pts = [(0, h)] + [(x, int(h * 0.5 + 60 * np.sin(x / 90 + seed))) for x in range(0, w + 1, 32)] + [(w, h)]
    d.polygon(pts, fill=(40, 30, 60))
    d.ellipse((w // 2 - 20, h - 120, w // 2 + 20, h - 80), fill=kit2d.MAGENTA)
    return img


def _fake_vfx(w: int, h: int) -> Image.Image:
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    cell = h // 2 if h > 256 else h
    for i in range(w // cell * (h // cell)):
        cx, cy = (i % (w // cell)) * cell + cell // 2, (i // (w // cell)) * cell + cell // 2
        r = 20 + 10 * i
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 160 - 10 * i, 40))
        d.ellipse((cx - r // 3, cy - r // 3, cx + r // 3, cy + r // 3), fill=(255, 255, 230))
    return img


def _fake_items(w: int, h: int, *, skip: int | None = None, overflow: bool = False) -> Image.Image:
    img = Image.new("RGB", (w, h), (255, 255, 255))
    d = ImageDraw.Draw(img)
    cols, rows = w // 256, h // 256
    for i in range(cols * rows):
        if i == skip:
            continue
        x0, y0 = (i % cols) * 256, (i // cols) * 256
        colour = (40 + 25 * i, 120, 200 - 20 * i)
        right = x0 + (276 if overflow and i == 0 else 196)
        d.rectangle((x0 + 60, y0 + 60, right, y0 + 196), fill=colour, outline=(10, 10, 10), width=4)
    return img


class FakeRender:
    """Render falso: escolhe a imagem pelo prompt e regista as chamadas."""

    def __init__(self, **opts: object) -> None:
        self.calls: list[dict[str, object]] = []
        self.opts = opts

    def __call__(self, prompt: str, path: Path, seed: int, refs: list[Path], width: int, height: int) -> None:
        self.calls.append({"prompt": prompt, "seed": seed, "refs": list(refs), "size": (width, height)})
        if "terrain texture" in prompt:
            img = _noise(width, height, seed, (60, 160, 60) if seed % 2 == 0 else (130, 90, 40))
        elif "background layer" in prompt:
            if "empty sky" in prompt:
                img = Image.linear_gradient("L").resize((width, height)).convert("RGB")
            else:
                img = _fake_layer(width, height, seed)
        elif "VFX" in prompt:
            img = _fake_vfx(width, height)
        else:
            img = _fake_items(width, height, **self.opts)  # type: ignore[arg-type]
        img.save(path)


# ---------------------------------------------------------------------------
# kit2d
# ---------------------------------------------------------------------------


class TestKit2d:
    def test_make_tileable_wraps_x(self) -> None:
        img = _noise(200, 100, 1, (120, 120, 120))
        out = np.asarray(kit2d.make_tileable(img, axes="x")).astype(int)
        assert out.shape == (100, 150, 3)
        wrap = np.abs(out[:, -1] - out[:, 0]).mean()
        inner = np.abs(out[:, 1:] - out[:, :-1]).mean()
        assert wrap <= inner * 1.5

    def test_make_tileable_wraps_both(self) -> None:
        img = _noise(128, 128, 2, (100, 100, 100))
        out = np.asarray(kit2d.make_tileable(img)).astype(int)
        assert out.shape == (96, 96, 3)
        inner = np.abs(out[1:] - out[:-1]).mean()
        assert np.abs(out[-1] - out[0]).mean() <= inner * 1.5

    def test_make_tileable_rgba_no_colour_halo(self) -> None:
        arr = np.zeros((40, 80, 4), np.uint8)
        arr[:, :, :3] = (255, 0, 255)  # cor escondida sob alpha 0
        arr[20:, :, :] = (10, 200, 10, 255)
        out = np.asarray(kit2d.make_tileable(Image.fromarray(arr, "RGBA"), axes="x"))
        visible = out[out[..., 3] > 0]
        assert visible[:, 0].max() < 40  # sem vermelho do magenta

    def test_split_grid(self) -> None:
        cells = kit2d.split_grid(Image.new("RGB", (400, 200)), 4, 2)
        assert len(cells) == 8
        assert all(c.size == (100, 100) for c in cells)

    def test_quantize_shared_palette_and_alpha(self) -> None:
        a = _noise(32, 32, 3, (200, 50, 50)).convert("RGBA")
        b = _noise(32, 32, 4, (50, 50, 200)).convert("RGBA")
        b.putalpha(Image.linear_gradient("L").resize((32, 32)))
        qa, qb = kit2d.quantize_shared([a, b], 8, alpha_levels=4)
        colours = {tuple(p) for img in (qa, qb) for p in np.asarray(img)[..., :3].reshape(-1, 3)}
        assert len(colours) <= 8
        assert set(np.unique(np.asarray(qb)[..., 3])) <= {0, 85, 170, 255}

    def test_quantize_binary_alpha(self) -> None:
        img = Image.new("RGBA", (8, 8), (100, 100, 100, 100))
        (q,) = kit2d.quantize_shared([img], 4)
        assert set(np.unique(np.asarray(q)[..., 3])) <= {0, 255}

    def test_slug(self) -> None:
        assert kit2d.slug("Health Potion (big)") == "health_potion_big"
        assert kit2d.slug("!!!") == "item"

    def test_preview_scales(self) -> None:
        assert kit2d.preview(Image.new("RGBA", (10, 6)), scale=3).size == (30, 18)

    def test_resolve_render_passthrough(self) -> None:
        fake = FakeRender()
        assert kit2d.resolve_render(fake, None, 4) is fake


# ---------------------------------------------------------------------------
# tileset
# ---------------------------------------------------------------------------


class TestTileset:
    def test_periodic_noise_is_periodic(self) -> None:
        n = tileset._periodic_noise(32, 0)
        assert np.abs(n[:, -1] - n[:, 0]).mean() < 0.2
        assert np.abs(n[-1] - n[0]).mean() < 0.2

    @pytest.mark.parametrize("idx, expected", [(0, 0.0), (15, 1.0)])
    def test_uniform_corners_fill(self, idx: int, expected: float) -> None:
        m = tileset.corner_mask(idx, 32, tileset._periodic_noise(32, 0))
        assert m.mean() == expected

    def test_single_corner_covers_its_quadrant(self) -> None:
        m = tileset.corner_mask(tileset.CORNER_BITS["br"], 32, tileset._periodic_noise(32, 0))
        assert m[24:, 24:].mean() > 0.8
        assert m[:8, :8].mean() == 0

    @pytest.mark.parametrize("left, right", [(2 | 8, 1 | 4), (2, 1), (8, 4), (2 | 8, 1 | 4 | 2 | 8)])
    def test_shared_edge_matches(self, left: int, right: int) -> None:
        """Aresta direita de um tile = aresta esquerda do vizinho com os mesmos cantos."""
        noise = tileset._periodic_noise(32, 5)
        a = tileset.corner_mask(left, 32, noise)
        b = tileset.corner_mask(right, 32, noise)
        assert (a[:, -1] != b[:, 0]).mean() < 0.1

    def test_build_tiles_extremes_are_pure(self) -> None:
        base = Image.new("RGBA", (16, 16), (10, 200, 10, 255))
        top = Image.new("RGBA", (16, 16), (150, 90, 30, 255))
        tiles = tileset.build_tiles(base, top, tile=16)
        assert len(tiles) == 16
        assert np.array_equal(np.asarray(tiles[0]), np.asarray(base))
        assert np.array_equal(np.asarray(tiles[15]), np.asarray(top))

    def test_build_tiles_shadow_on_border(self) -> None:
        base = Image.new("RGBA", (16, 16), (100, 200, 100, 255))
        top = Image.new("RGBA", (16, 16), (150, 90, 30, 255))
        t = np.asarray(tileset.build_tiles(base, top, tile=16)[8])  # só br
        colours = {tuple(p) for p in t[..., :3].reshape(-1, 3)}
        assert (70, 140, 70) in colours  # sombra 0.7 do base

    def test_boost_detail_raises_contrast(self) -> None:
        flat = _noise(32, 32, 1, (100, 150, 100)).filter(ImageFilter.BoxBlur(3))
        lum = lambda im: np.asarray(im.convert("L")).astype(float).std()  # noqa: E731
        assert lum(tileset.boost_detail(flat)) > lum(flat) * 1.5

    def test_generate_outputs(self, tmp_path: Path) -> None:
        fake = FakeRender()
        r = tileset.generate_tileset("grass", "dirt", out_dir=tmp_path, tile=16, render=fake)
        assert len(fake.calls) == 2
        assert all(c["size"] == (512, 512) for c in fake.calls)
        assert Image.open(r["tileset"]).size == (64, 64)
        meta = json.loads(r["json"].read_text())
        assert meta["layout"] == "corner-16"
        assert meta["tiles"][5]["corners"] == {"tl": 1, "tr": 0, "bl": 1, "br": 0}
        assert [t["name"] for t in meta["terrains"]] == ["grass", "dirt"]
        assert Image.open(r["preview"]).size == (16 * 16 * 2, 10 * 16 * 2)

    def test_tsx_is_valid_wangset(self, tmp_path: Path) -> None:
        r = tileset.generate_tileset("grass", "dirt", out_dir=tmp_path, tile=16, render=FakeRender())
        root = ET.parse(r["tsx"]).getroot()
        assert root.get("tilecount") == "16"
        tiles = root.findall("./wangsets/wangset/wangtile")
        assert len(tiles) == 16
        # tile 1 = só tl em cima → último valor (top-left) = 2, resto 1.
        assert tiles[1].get("wangid") == "0,1,0,1,0,1,0,2"
        assert root.find("./wangsets/wangset").get("type") == "corner"

    def test_given_textures_skip_render(self, tmp_path: Path) -> None:
        a, b = tmp_path / "a.png", tmp_path / "b.png"
        _noise(512, 512, 0, (60, 160, 60)).save(a)
        _noise(300, 300, 1, (130, 90, 40)).save(b)  # outro tamanho: redimensionado
        fake = FakeRender()
        tileset.generate_tileset("g", "d", out_dir=tmp_path / "o", base_texture=a, top_texture=b, render=fake)
        assert fake.calls == []

    def test_missing_texture(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            tileset.generate_tileset("g", "d", out_dir=tmp_path, base_texture=tmp_path / "x.png", render=FakeRender())

    @pytest.mark.parametrize("tile", [5, 50, 100])
    def test_bad_tile_size(self, tmp_path: Path, tile: int) -> None:
        with pytest.raises(ValueError, match="tile"):
            tileset.generate_tileset("g", "d", out_dir=tmp_path, tile=tile, render=FakeRender())

    def test_texture_prompt(self) -> None:
        p = tileset.texture_prompt("lush grass.")
        assert "lush grass ground" in p and "seamless" in p and "top-down" in p


# ---------------------------------------------------------------------------
# parallax
# ---------------------------------------------------------------------------


class TestParallax:
    def test_layer_prompts(self) -> None:
        assert "magenta" not in parallax.layer_prompt("forest", "sky")
        for name in ("far", "mid", "near"):
            assert "magenta" in parallax.layer_prompt("forest", name)

    def test_scroll_factors_increase(self) -> None:
        f = [parallax.LAYERS[n][1] for n in parallax.DEFAULT_LAYERS]
        assert f == sorted(f) and f[0] < 0.1 and f[-1] < 1

    def test_key_layer_cuts_magenta_and_pocket(self) -> None:
        img = _fake_layer(640, 256, 0)
        out = np.asarray(parallax.key_layer(img))
        assert out[5, :, 3].max() == 0  # topo transparente
        assert out[-5, :, 3].min() == 255  # base opaca
        assert out[256 - 100, 320, 3] == 0  # buraco fechado removido

    def test_key_layer_despills(self) -> None:
        img = _fake_layer(640, 256, 0)
        out = np.asarray(parallax.key_layer(img)).astype(int)
        vis = out[out[..., 3] > 0]
        assert ((np.minimum(vis[:, 0], vis[:, 2]) - vis[:, 1]) <= 60).all()

    def test_generate(self, tmp_path: Path) -> None:
        fake = FakeRender()
        r = parallax.generate_parallax("forest", out_dir=tmp_path, height=64, gif_frames=6, render=fake)
        assert len(r["layers"]) == 4
        assert all(c["size"] == parallax.GEN_SIZE for c in fake.calls)
        sky, *rest = (np.asarray(Image.open(p)) for p in r["layers"])
        assert sky.shape == (64, 128, 4) and sky[..., 3].min() == 255
        near = rest[-1]
        assert near[0, :, 3].max() == 0 and near[-1, :, 3].min() == 255
        meta = json.loads(r["json"].read_text())
        assert [layer["name"] for layer in meta["layers"]] == list(parallax.DEFAULT_LAYERS)
        assert meta["repeat"] == "x"
        assert Image.open(r["gif"]).n_frames == 6

    def test_layer_tiles_in_x(self, tmp_path: Path) -> None:
        r = parallax.generate_parallax("forest", out_dir=tmp_path, layers=["mid"], gif_frames=0, render=FakeRender())
        a = np.asarray(Image.open(r["layers"][0]))[..., 3] > 0
        heights = a.sum(axis=0)
        assert abs(int(heights[-1]) - int(heights[0])) <= max(4, np.abs(np.diff(heights.astype(int))).max() * 2)
        assert r["gif"] is None

    @pytest.mark.parametrize("bad", [[], ["ground"]])
    def test_invalid_layers(self, tmp_path: Path, bad: list[str]) -> None:
        with pytest.raises(ValueError, match="camadas"):
            parallax.generate_parallax("x", out_dir=tmp_path, layers=bad, render=FakeRender())

    def test_compose_wraps(self) -> None:
        layer = Image.new("RGBA", (10, 4), (255, 0, 0, 255))
        layer.paste((0, 0, 255, 255), (0, 0, 5, 4))
        out = np.asarray(parallax.compose([layer], [5]))
        assert tuple(out[0, 0, :3]) == (255, 0, 0)


# ---------------------------------------------------------------------------
# vfx
# ---------------------------------------------------------------------------


class TestVfx:
    @pytest.mark.parametrize("name", list(vfx.EFFECTS))
    def test_effect_guides_draw(self, name: str) -> None:
        eff = vfx.EFFECTS[name]
        assert len(eff.phases) == 8
        guide = np.asarray(vfx.draw_guide(eff, 8, cell=64).convert("L"))
        assert guide.shape == (128, 256)
        assert guide[:64, :64].max() > 30  # primeiro frame visível

    def test_explosion_guide_grows(self) -> None:
        eff = vfx.EFFECTS["explosion"]
        a0 = (np.asarray(vfx._guide_cell(eff, 0, 8, 64)) > 20).sum()
        a3 = (np.asarray(vfx._guide_cell(eff, 3, 8, 64)) > 20).sum()
        assert a3 > a0 * 2

    def test_light_to_alpha(self) -> None:
        img = Image.new("RGB", (32, 32), (2, 2, 2))
        ImageDraw.Draw(img).ellipse((8, 8, 24, 24), fill=(255, 128, 0))
        out = np.asarray(vfx.light_to_alpha(img))
        assert out[0, 0, 3] == 0
        assert out[16, 16, 3] == 255
        assert out[16, 16, 0] > 240

    def test_dim_smoke_is_translucent(self) -> None:
        img = Image.new("RGB", (16, 16), (0, 0, 0))
        img.paste((90, 90, 90), (4, 4, 12, 12))
        a = np.asarray(vfx.light_to_alpha(img))[8, 8, 3]
        assert 60 < a < 120

    def test_prompt_lists_frames_and_subject(self) -> None:
        p = vfx.sheet_prompt(vfx.EFFECTS["explosion"], 8, subject="green acid explosion")
        assert "green acid explosion" in p and "frame 8:" in p and "black background" in p

    def test_phase_mapping_fewer_frames(self) -> None:
        eff = vfx.EFFECTS["explosion"]
        assert [vfx._phase(eff, i, 4) for i in range(4)] == list(eff.phases[::2])

    def test_generate(self, tmp_path: Path) -> None:
        fake = FakeRender()
        r = vfx.generate_vfx("slash", out_dir=tmp_path, sprite_size=32, render=fake)
        assert fake.calls[0]["size"] == (1024, 512)
        assert fake.calls[0]["refs"] == [r["guide"]]
        meta = json.loads(r["meta"].read_text())
        assert meta["kind"] == "vfx" and meta["pivot"] == [16, 16] and not meta["loop"]
        d = [f["duration_ms"] for f in meta["frames"]]
        assert d[0] < d[-1]  # holds do slash: início rápido
        assert Image.open(r["atlas"]).size == (32 * 8, 32)
        assert Image.open(r["gif"]).n_frames >= 2

    def test_generate_four_frames_single_row(self, tmp_path: Path) -> None:
        fake = FakeRender()
        r = vfx.generate_vfx("fire", out_dir=tmp_path, frames=4, sprite_size=None, render=fake)
        assert fake.calls[0]["size"] == (1024, 256)
        assert json.loads(r["meta"].read_text())["loop"] is True
        assert r["sprites"][0].size == (256, 256)

    def test_smoke_blend_alpha(self, tmp_path: Path) -> None:
        r = vfx.generate_vfx("smoke", out_dir=tmp_path, frames=2, render=FakeRender())
        assert json.loads(r["meta"].read_text())["blend"] == "alpha"

    @pytest.mark.parametrize("frames", [1, 9])
    def test_bad_frames(self, tmp_path: Path, frames: int) -> None:
        with pytest.raises(ValueError, match="frames"):
            vfx.generate_vfx("fire", out_dir=tmp_path, frames=frames, render=FakeRender())

    def test_unknown_effect(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="desconhecido"):
            vfx.generate_vfx("tornado", out_dir=tmp_path, render=FakeRender())


# ---------------------------------------------------------------------------
# items
# ---------------------------------------------------------------------------


class TestItems:
    def test_assign_cells_by_centroid(self) -> None:
        alpha = np.zeros((100, 200), bool)
        alpha[10:40, 10:40] = True  # célula 0
        alpha[60:90, 140:170] = True  # célula 3 (2x2)
        alpha[10:40, 85:120] = True  # centro x=102 → célula 1, passa a fronteira
        masks = items.assign_cells(alpha, 2, 2)
        assert masks[0].sum() == 900 and masks[3].sum() == 900
        assert masks[1].sum() == 30 * 35
        assert masks[2].sum() == 0

    def test_assign_cells_drops_specks(self) -> None:
        alpha = np.zeros((100, 100), bool)
        alpha[50, 50] = True
        assert sum(m.sum() for m in items.assign_cells(alpha, 1, 1, min_frac=0.001)) == 0

    def test_frame_icon_centres(self) -> None:
        rgba = Image.new("RGBA", (100, 100), (200, 0, 0, 255))
        mask = np.zeros((100, 100), bool)
        mask[10:30, 50:90] = True
        icon = np.asarray(items.frame_icon(rgba, mask, size=20))
        assert icon.shape == (20, 20, 4)
        ys = np.nonzero(icon[..., 3])[0]
        assert ys.min() > 2 and ys.max() < 17  # centrado na vertical
        assert items.frame_icon(rgba, np.zeros((100, 100), bool), size=20) is None

    def test_prompt(self) -> None:
        p = items.sheet_prompt(["potion", "sword"], theme="dark fantasy")
        assert "cell 1: potion; cell 2: sword" in p and "dark fantasy theme" in p and "2x1" in p

    def test_generate(self, tmp_path: Path) -> None:
        names = ["health potion", "iron sword", "shield", "coin", "key"]
        fake = FakeRender()
        r = items.generate_items(names, out_dir=tmp_path, icon_size=24, render=fake)
        assert fake.calls[0]["size"] == (1024, 512)
        assert set(r["icons"]) == set(names) and r["missing"] == []
        meta = json.loads(r["json"].read_text())
        assert meta["items"]["health_potion"]["file"] == "icons/health_potion.png"
        assert Image.open(r["atlas"]).size == (5 * 24, 24)
        assert Image.open(r["icons"]["coin"]).size == (24, 24)

    def test_missing_cell_reported(self, tmp_path: Path) -> None:
        r = items.generate_items(["a", "b", "c", "d"], out_dir=tmp_path, render=FakeRender(skip=2))
        assert r["missing"] == ["c"]
        assert json.loads(r["json"].read_text())["missing"] == ["c"]

    def test_overflow_item_kept_whole(self, tmp_path: Path) -> None:
        r = items.generate_items(["a", "b", "c", "d"], out_dir=tmp_path, icon_size=64, render=FakeRender(overflow=True))
        alpha = np.asarray(Image.open(r["icons"]["a"]))[..., 3] > 0
        ys, xs = np.nonzero(alpha)
        assert (xs.max() - xs.min()) > (ys.max() - ys.min())  # retângulo largo inteiro

    def test_more_than_eight_uses_first_sheet_as_ref(self, tmp_path: Path) -> None:
        fake = FakeRender()
        names = [f"item {i}" for i in range(10)]
        r = items.generate_items(names, out_dir=tmp_path, render=fake)
        assert len(fake.calls) == 2
        assert fake.calls[0]["refs"] == []
        assert fake.calls[1]["refs"] == [tmp_path / "sheet_0_raw.png"]
        assert fake.calls[1]["size"] == (512, 256)
        assert len(r["icons"]) == 10

    def test_reference_used_everywhere(self, tmp_path: Path) -> None:
        ref = tmp_path / "style.png"
        Image.new("RGB", (64, 64)).save(ref)
        fake = FakeRender()
        items.generate_items(["a", "b"], out_dir=tmp_path / "o", reference=ref, render=fake)
        assert fake.calls[0]["refs"] == [ref] and "reference" in str(fake.calls[0]["prompt"])

    def test_duplicates_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="repetidos"):
            items.generate_items(["Gold Coin", "gold coin"], out_dir=tmp_path, render=FakeRender())

    def test_empty_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="vazia"):
            items.generate_items([" ", ""], out_dir=tmp_path, render=FakeRender())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


class TestCli:
    @pytest.mark.parametrize("cmd", ["tileset", "parallax", "vfx", "items"])
    def test_commands_registered(self, cmd: str) -> None:
        from text2d.cli import cli

        assert cmd in cli.commands
        names = {p.name for p in cli.commands[cmd].params}
        assert {"steps", "seed", "style", "no_vramd"} <= names

    def test_vfx_choices(self) -> None:
        from text2d.cli import cli

        effect = next(p for p in cli.commands["vfx"].params if p.name == "effect")
        assert list(effect.type.choices) == list(vfx.EFFECTS)

    def test_items_requires_names(self) -> None:
        from text2d.cli import cli

        res = CliRunner().invoke(cli, ["items"])
        assert res.exit_code != 0 and "itens" in res.output

    def test_tileset_cli_with_textures(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from text2d import cli as cli_mod

        a, b = tmp_path / "a.png", tmp_path / "b.png"
        _noise(512, 512, 0, (60, 160, 60)).save(a)
        _noise(512, 512, 1, (130, 90, 40)).save(b)
        monkeypatch.setattr(cli_mod, "_sprite_renderer", lambda **_: (FakeRender(), lambda: None))
        res = CliRunner().invoke(
            cli_mod.cli,
            ["tileset", "grass", "dirt", "-O", str(tmp_path / "o"), "--base-texture", str(a), "--top-texture", str(b)],
        )
        assert res.exit_code == 0, res.output
        assert (tmp_path / "o" / "tileset.tsx").is_file()

    def test_items_cli_from_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from text2d import cli as cli_mod

        lst = tmp_path / "items.txt"
        lst.write_text("potion\nsword\n\n", encoding="utf-8")
        monkeypatch.setattr(cli_mod, "_sprite_renderer", lambda **_: (FakeRender(), lambda: None))
        res = CliRunner().invoke(cli_mod.cli, ["items", "--file", str(lst), "-O", str(tmp_path / "o")])
        assert res.exit_code == 0, res.output
        assert json.loads((tmp_path / "o" / "items.json").read_text())["items"].keys() == {"potion", "sword"}
