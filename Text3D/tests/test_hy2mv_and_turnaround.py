"""Testes unitários CPU para turnaround (alinhamento anti-6-dedos) e Hunyuan3D-2mv / fusão Omni."""

from __future__ import annotations

import numpy as np
import pytest
import torch
import trimesh
from click.testing import CliRunner
from PIL import Image, ImageDraw, ImageOps

from text3d.cli import cli
from text3d.hy2mv import fit_mesh_to_bbox_aspect, remap_dinov2_v5_state_dict
from text3d.turnaround import align_back_to_front, prepare_multiview_images, silhouette_iou


def _draw_synthetic_humanoid(
    size: int = 512,
    *,
    hand_shift_y: int = 0,
    hand_shift_x: int = 0,
    scale: float = 1.0,
    body_color: tuple[int, int, int] = (70, 95, 130),
    hand_color: tuple[int, int, int] = (210, 165, 135),
) -> Image.Image:
    """Desenha silhueta estilizada de humanoide com 5 dedos salientes em cada mão."""
    im = Image.new("RGB", (size, size), (255, 255, 255))
    draw = ImageDraw.Draw(im)

    def sx(v: float) -> int:
        return round(size * 0.5 + (v - 0.5) * size * scale)

    def sy(v: float) -> int:
        return round(size * 0.5 + (v - 0.5) * size * scale)

    # Cabeça
    draw.ellipse([sx(0.44), sy(0.08), sx(0.56), sy(0.20)], fill=hand_color)
    # Tronco + pernas
    draw.rectangle([sx(0.38), sy(0.20), sx(0.62), sy(0.62)], fill=body_color)
    draw.rectangle([sx(0.40), sy(0.62), sx(0.48), sy(0.92)], fill=body_color)
    draw.rectangle([sx(0.52), sy(0.62), sx(0.60), sy(0.92)], fill=body_color)
    # Braços até os punhos (o punho acompanha o desvio de ângulo da mão, como num corpo real)
    u = (size / 512.0) * scale
    dx_h = round(hand_shift_x * u)
    dy_h = round(hand_shift_y * u)
    draw.polygon(
        [
            (sx(0.38), sy(0.24)),
            (sx(0.24) - dx_h, sy(0.47) + dy_h),
            (sx(0.28) - dx_h, sy(0.51) + dy_h),
            (sx(0.38), sy(0.33)),
        ],
        fill=body_color,
    )
    draw.polygon(
        [
            (sx(0.62), sy(0.24)),
            (sx(0.76) + dx_h, sy(0.47) + dy_h),
            (sx(0.72) + dx_h, sy(0.51) + dy_h),
            (sx(0.62), sy(0.33)),
        ],
        fill=body_color,
    )
    # 5 dedos em cada mão (fora do punho: x < 0.27 e x > 0.73, y ∈ [0.44, 0.58])
    palm_r = max(3, round(10 * u))
    line_w = max(2, round(4 * u))
    for side in (-1, 1):
        cx = sx(0.5 + side * 0.28) + dx_h * side
        cy = sy(0.51) + dy_h
        draw.ellipse([cx - palm_r, cy - palm_r, cx + palm_r, cy + palm_r], fill=hand_color)
        for fi, dy_f in enumerate((-18, -9, 0, 9, 18)):
            dy_s = round(dy_f * u)
            dx_s = round(side * (22 + (3 if fi == 2 else 0)) * u)
            draw.line([(cx, cy + dy_s // 2), (cx + dx_s, cy + dy_s)], fill=hand_color, width=line_w)
    return im


def test_align_back_to_front_fixes_shift_and_locks_hands() -> None:
    front = _draw_synthetic_humanoid(512, hand_shift_y=0, hand_shift_x=0, scale=1.0, hand_color=(210, 165, 135))
    # Back com escala diferente + mãos deslocadas (simula desvio real do FLUX que criava 6 dedos)
    back_raw = ImageOps.mirror(
        _draw_synthetic_humanoid(
            512,
            hand_shift_y=14,
            hand_shift_x=10,
            scale=0.92,
            body_color=(90, 65, 50),
            hand_color=(150, 110, 90),
        )
    )

    before = silhouette_iou(front, back_raw)
    assert before["hands_iou"] < 0.65

    aligned, after = align_back_to_front(front, back_raw, lock_outer_hands=True)
    assert aligned.size == front.size
    assert after["hands_iou"] > 0.80
    assert after["hands_iou"] > before["hands_iou"] + 0.30
    assert after["full_iou"] > before["full_iou"]
    # Warp elástico preserva pixels autênticos das costas (não cola RGB da frente sobre o antebraço)
    back_hand_px = aligned.getpixel((round(512 * 0.22), round(512 * 0.51)))
    assert back_hand_px[0] < 185


def test_align_back_to_front_without_hand_lock_preserves_back_colors() -> None:
    front = _draw_synthetic_humanoid(512, body_color=(40, 80, 160))
    back_raw = ImageOps.mirror(_draw_synthetic_humanoid(512, scale=0.90, body_color=(140, 60, 40)))

    aligned, metrics = align_back_to_front(front, back_raw, lock_outer_hands=False)
    assert metrics["full_iou"] > 0.85
    # Tronco central mantém a cor traseira (vermelho > azul), não copiou a frente
    center_px = aligned.getpixel((256, 200))
    assert center_px[0] > center_px[2]


def test_prepare_multiview_images_loads_and_aligns(tmp_path) -> None:
    front = _draw_synthetic_humanoid(256)
    back = ImageOps.mirror(_draw_synthetic_humanoid(256, hand_shift_y=10, scale=0.94))
    fp = tmp_path / "front.png"
    bp = tmp_path / "back.png"
    front.save(fp)
    back.save(bp)

    views, meta = prepare_multiview_images({"front": fp, "back": bp}, align_back=True, lock_outer_hands=True)
    assert set(views.keys()) == {"front", "back"}
    assert "back_align" in meta
    assert meta["back_align"]["hands_iou"] > 0.80


def test_remap_dinov2_v5_state_dict_converts_qkv_and_swiglu() -> None:
    w_in = torch.arange(24, dtype=torch.float32).reshape(6, 4)
    old_sd = {
        "main_image_encoder.model.encoder.layer.0.attention.attention.query.weight": torch.ones(4, 4),
        "main_image_encoder.model.encoder.layer.0.attention.attention.key.weight": torch.full((4, 4), 2.0),
        "main_image_encoder.model.encoder.layer.0.attention.attention.value.weight": torch.full((4, 4), 3.0),
        "main_image_encoder.model.encoder.layer.0.attention.output.dense.weight": torch.full((4, 4), 4.0),
        "main_image_encoder.model.encoder.layer.0.mlp.weights_in.weight": w_in,
        "main_image_encoder.model.encoder.layer.0.mlp.weights_out.weight": torch.full((4, 3), 5.0),
        "main_image_encoder.model.layernorm.weight": torch.ones(4),
    }
    expected = {
        "main_image_encoder.model.encoder.layer.0.attention.q_proj.weight",
        "main_image_encoder.model.encoder.layer.0.attention.k_proj.weight",
        "main_image_encoder.model.encoder.layer.0.attention.v_proj.weight",
        "main_image_encoder.model.encoder.layer.0.attention.o_proj.weight",
        "main_image_encoder.model.encoder.layer.0.mlp.gate_proj.weight",
        "main_image_encoder.model.encoder.layer.0.mlp.up_proj.weight",
        "main_image_encoder.model.encoder.layer.0.mlp.down_proj.weight",
        "main_image_encoder.model.layernorm.weight",
    }

    remapped = remap_dinov2_v5_state_dict(old_sd, expected)
    assert set(remapped.keys()) == expected
    assert torch.equal(
        remapped["main_image_encoder.model.encoder.layer.0.mlp.gate_proj.weight"],
        w_in[:3],
    )
    assert torch.equal(
        remapped["main_image_encoder.model.encoder.layer.0.mlp.up_proj.weight"],
        w_in[3:],
    )

    # No-op quando já está em formato v5
    same = remap_dinov2_v5_state_dict(remapped, expected)
    assert same is remapped


def test_fit_mesh_to_bbox_aspect() -> None:
    box = trimesh.creation.box(extents=[1.0, 1.0, 1.0])
    fitted = fit_mesh_to_bbox_aspect(box, [4.0, 2.0, 1.0])
    ext = fitted.extents / float(fitted.extents.max())
    np.testing.assert_allclose(ext, np.array([1.0, 0.5, 0.25]), atol=1e-5)

    with pytest.raises(ValueError):
        fit_mesh_to_bbox_aspect(box, [1.0, 0.0, 1.0])
    with pytest.raises(ValueError):
        fit_mesh_to_bbox_aspect(box, [1.0, 2.0])


def test_cli_exposes_multiview_and_turnaround_commands() -> None:
    runner = CliRunner()

    res_gen = runner.invoke(cli, ["generate", "--help"])
    assert res_gen.exit_code == 0
    for flag in ("--view-back", "--view-left", "--view-right", "--auto-turnaround", "--fuse-omni", "--no-align-back"):
        assert flag in res_gen.output

    res_turn = runner.invoke(cli, ["turnaround", "--help"])
    assert res_turn.exit_code == 0
    for flag in ("--from-image", "--output-dir", "--category", "--back-details", "--include-right", "--no-align-back"):
        assert flag in res_turn.output

    res_err = runner.invoke(cli, ["generate", "test prompt", "--fuse-omni", "-o", "/tmp/x.glb"])
    assert res_err.exit_code != 0
    assert "--fuse-omni" in res_err.output


def test_generate_turnaround_views_enforces_full_180_back_prompt(tmp_path, monkeypatch) -> None:
    import sys
    import types

    from text3d.turnaround import generate_turnaround_views

    # Cria imagem frontal assimétrica: quadrado vermelho no ombro esquerdo da imagem (x=8..20)
    front = Image.new("RGB", (64, 64), (255, 255, 255))
    ImageDraw.Draw(front).rectangle([8, 8, 20, 20], fill=(220, 20, 20))
    fp = tmp_path / "hero.png"
    front.save(fp)

    calls: list[dict] = []

    class _DummyKlein:
        def __init__(self, **_kw):
            pass

        def generate(self, *, prompt: str, refs: list[Image.Image], **_kw):
            calls.append({"prompt": prompt, "refs": [r.copy() for r in refs]})
            return refs[0].copy(), {}

        def unload(self) -> None:
            pass

    fake_mod = types.ModuleType("text2d.generator")
    fake_mod.KleinFluxGenerator = _DummyKlein  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "text2d.generator", fake_mod)

    out = generate_turnaround_views(
        fp,
        tmp_path / "out",
        subject_hint="stylized rural fantasy cartographer",
        back_details="single external map tube",
        category="humanoid",
        include_right=True,
        align_back=False,
        width=64,
        height=64,
    )
    assert set(out.keys()) == {"front", "back", "left", "right"}
    assert len(calls) == 3

    # 1ª chamada (BACK): passa front original (FLUX.3 Klein faz a rotação 180° nativa sem duplo espelhamento)
    back_ref = calls[0]["refs"][0]
    assert back_ref.getpixel((14, 14)) == (220, 20, 20)
    assert "180 degrees" in calls[0]["prompt"]
    assert "NO face" in calls[0]["prompt"]
    assert "NO front belt buckle" in calls[0]["prompt"]
    assert "correctly mirror" in calls[0]["prompt"]
    # 2ª e 3ª chamadas (LEFT / RIGHT): recebem [front_im original, back_im]
    assert calls[1]["refs"][0].getpixel((14, 14)) == (220, 20, 20)
    assert len(calls[1]["refs"]) == 2
    assert len(calls[2]["refs"]) == 2


def test_turnaround_seed_decorrelates_from_front_seed() -> None:
    from text3d.turnaround import turnaround_seed_from_front_seed

    assert turnaround_seed_from_front_seed(None) == 202
    for s in (0, 42, 202, 42424242, 2147483647):
        derived = turnaround_seed_from_front_seed(s)
        assert derived != s
        assert 0 <= derived <= 0x7FFFFFFF
        assert derived == turnaround_seed_from_front_seed(s)
