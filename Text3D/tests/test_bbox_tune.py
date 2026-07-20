"""Testes do autotune genérico por escala física (metros)."""

from __future__ import annotations

import pytest

from text3d.bbox_tune import (
    apply_bbox_tune,
    characteristic_meters,
    max_octree_for_vram,
    morph_close_meters,
    resolve_morph_close,
    scale_factor_to_meters,
    tune_hunyuan_for_bbox,
    voxel_meters,
)


def test_scale_factor_to_meters() -> None:
    # Mesh Omni ~2u, alvo 7 m → factor 3.5
    f = scale_factor_to_meters(2.0, [6.0, 7.0, 4.5])
    assert f is not None and abs(f - 3.5) < 1e-9
    # Já em metros: factor ≈ 1 → None (não re-escala)
    assert scale_factor_to_meters(7.0, [6.0, 7.0, 4.5]) is None
    # Sem size_m → None
    assert scale_factor_to_meters(2.0, None) is None
    assert scale_factor_to_meters(0.0, [1, 2, 3]) is None


def test_size_m_wins_over_category() -> None:
    char, src = characteristic_meters([6.0, 7.0, 4.5], category="building")
    assert src == "size_m"
    assert char == 7.0


def test_category_fallback() -> None:
    char, src = characteristic_meters(None, category="building")
    assert src == "category"
    assert char == 6.0


def test_bbox_preset_chapel() -> None:
    char, src = characteristic_meters(None, bbox_preset="chapel")
    assert src == "bbox_preset"
    assert char == 7.0


def test_chapel_raises_octree_vs_prop_base() -> None:
    """Capela 7 m em tier balanced sobe octree; sem group_offload tecto 6GB=320."""
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
        size_m=[6.0, 7.0, 4.5],
        total_vram_gib=6.0,
        volume_decoder="flashvdm",
    )
    assert r.applied
    assert r.source == "size_m"
    assert r.char_m == 7.0
    assert r.octree >= 288
    assert r.octree <= 320  # tecto 6GB sem offload
    assert r.octree >= 256  # flashvdm floor
    assert r.steps >= 30
    assert r.morph_close is not None and r.morph_close > 0
    assert r.voxel_m == voxel_meters(7.0, r.octree)


def test_chapel_group_offload_raises_octree_cap() -> None:
    """Com group leaf+stream, 6 GiB permite octree até 416 (VRAM→MC)."""
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
        size_m=[6.0, 7.0, 4.5],
        total_vram_gib=6.0,
        volume_decoder="flashvdm",
        group_offload=True,
    )
    assert r.octree >= 320
    assert r.octree <= 416


def test_humanoid_same_formula_no_raise() -> None:
    """1.7 m já tem voxel < alvo a octree 256 — não sobe (genérico)."""
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
        size_m=[0.5, 1.7, 0.4],
        total_vram_gib=6.0,
        group_offload=True,
    )
    assert r.char_m == 1.7
    assert r.octree == 256
    assert r.morph_close is not None
    # Morph humanoid << morph edifício 10 m (mesma fórmula).
    big = morph_close_meters(10.0, 384)
    assert big is not None
    assert r.morph_close < big


def test_morph_scales_with_char_m() -> None:
    """Igreja 10 m e personagem 1.7 m: mesma fórmula, morph proporcional."""
    m_h = morph_close_meters(1.7, 256)
    m_b = morph_close_meters(10.0, 384)
    assert m_h is not None and m_b is not None
    assert m_b > m_h


def test_chapel_morph_formula_tiny() -> None:
    """Capela 7 m / octree 384: ~1/8 voxel MC (refino sem derreter)."""
    m = morph_close_meters(7.0, 384)
    assert m is not None
    # 1/8 x (7/384) = ~0.0023 m — ordem mm, nao cm
    assert 0.001 <= m <= 0.005


def test_resolve_morph_auto_light_default() -> None:
    assert resolve_morph_close(explicit=0.0, size_m=[6, 7, 4.5]) is None
    assert resolve_morph_close(explicit=0.12, size_m=[6, 7, 4.5]) == 0.12
    auto = resolve_morph_close(explicit=None, size_m=[6, 7, 4.5], octree=384)
    assert auto is not None and 0.001 <= auto <= 0.005
    assert resolve_morph_close(explicit=None, size_m=[6, 7, 4.5], octree=384, auto=False) is None
    # Sem pista de escala: sem morph
    assert resolve_morph_close(explicit=None) is None


def test_terrain_voxel_merge_triple_vs_default() -> None:
    """Cliffs/rochas: morph_close_voxels 3× default (mais fecho, menos detalhe)."""
    from text3d.bbox_tune import DEFAULT_MORPH_VOXELS, morph_close_voxels_for

    assert morph_close_voxels_for(None) == DEFAULT_MORPH_VOXELS
    assert morph_close_voxels_for("terrain") == pytest.approx(3.0 * DEFAULT_MORPH_VOXELS)
    assert morph_close_voxels_for("rock") == pytest.approx(3.0 * DEFAULT_MORPH_VOXELS)
    assert morph_close_voxels_for("prop", explicit=0.5) == 0.5

    prop = resolve_morph_close(explicit=None, size_m=[2.1, 3.0, 2.0], category="prop", octree=256)
    cliff = resolve_morph_close(explicit=None, size_m=[2.1, 3.0, 2.0], category="terrain", octree=256)
    assert prop is not None and cliff is not None
    assert cliff == pytest.approx(3.0 * prop, rel=0.05)


def test_small_prop_keeps_base() -> None:
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
        size_m=[0.8, 0.9, 0.7],
        total_vram_gib=6.0,
    )
    assert r.char_m == 0.9
    # Prop < ref: nao desce abaixo do tier base
    assert r.octree == 256
    assert r.steps == 30


def test_hq_vram_allows_higher_octree() -> None:
    r = tune_hunyuan_for_bbox(
        base_steps=50,
        base_octree=384,
        base_chunks=20000,
        size_m=[6.0, 7.0, 4.5],
        total_vram_gib=12.0,
        volume_decoder="hierarchical",
    )
    assert r.octree >= 384
    assert r.octree <= 512


def test_no_signal_returns_base() -> None:
    r = tune_hunyuan_for_bbox(
        base_steps=30,
        base_octree=256,
        base_chunks=8000,
    )
    assert not r.applied
    assert (r.steps, r.octree, r.chunks) == (30, 256, 8000)
    assert r.morph_close is None


def test_apply_respects_soft_flags() -> None:
    steps, octree, chunks, r = apply_bbox_tune(
        steps=30,
        octree=256,
        chunks=8000,
        size_m=[6.0, 7.0, 4.5],
        category="building",
        bbox_preset="chapel",
        total_vram_gib=6.0,
        volume_decoder="flashvdm",
        tune_steps=False,
        tune_octree=True,
        tune_chunks=False,
    )
    assert steps == 30
    assert chunks == 8000
    assert octree == r.octree
    assert octree > 256
    assert r.morph_close is not None


def test_max_octree_vram_ladder() -> None:
    assert max_octree_for_vram(4.0) == 256
    assert max_octree_for_vram(6.0) == 320
    assert max_octree_for_vram(12.0) == 512
    assert max_octree_for_vram(6.0, group_offload=True) == 416
    assert max_octree_for_vram(5.5, group_offload=True) == 384
    assert max_octree_for_vram(10.0, group_offload=True) == 512


def test_latent_ceiling_caps_octree_despite_vram() -> None:
    """VRAM de sobra (12 GiB, group offload → tecto 512) mas o latent não
    expressa mais que LATENT_DETAIL_CEILING — octree capado em 448."""
    from text3d.bbox_tune import tune_hunyuan_for_bbox

    r = tune_hunyuan_for_bbox(
        base_steps=50,
        base_octree=384,
        base_chunks=20000,
        size_m=[12.0, 10.0, 8.0],
        total_vram_gib=12.0,
        group_offload=True,
    )
    assert r.octree == 448
    assert r.octree < 512


def test_latent_ceiling_env_override(monkeypatch) -> None:
    from text3d.bbox_tune import latent_detail_ceiling, tune_hunyuan_for_bbox

    monkeypatch.setenv("TEXT3D_LATENT_OCTREE_CEILING", "384")
    assert latent_detail_ceiling() == 384
    r = tune_hunyuan_for_bbox(
        base_steps=50,
        base_octree=384,
        base_chunks=20000,
        size_m=[12.0, 10.0, 8.0],
        total_vram_gib=12.0,
        group_offload=True,
    )
    assert r.octree == 384


def test_latent_ceiling_env_invalid_ignored(monkeypatch) -> None:
    from text3d.bbox_tune import LATENT_DETAIL_CEILING, latent_detail_ceiling

    monkeypatch.setenv("TEXT3D_LATENT_OCTREE_CEILING", "banana")
    assert latent_detail_ceiling() == LATENT_DETAIL_CEILING
    monkeypatch.setenv("TEXT3D_LATENT_OCTREE_CEILING", "64")
    assert latent_detail_ceiling() == LATENT_DETAIL_CEILING


def test_target_voxel_perceptual_by_category() -> None:
    from text3d.bbox_tune import target_voxel_for

    # Paridade: sem sinal → alvo clássico 2.5 cm.
    assert target_voxel_for() == 0.025
    # Arma inspecionada nas mãos → voxel mais fino.
    assert target_voxel_for("weapon") < target_voxel_for("building")
    # Árvore nunca vista de perto → voxel mais grosso (clamp topo 6 cm).
    assert target_voxel_for("tree") == 0.0625 or target_voxel_for("tree") <= 0.06
    # Preset mapeia para categoria (chapel → building).
    assert target_voxel_for(bbox_preset="chapel") == target_voxel_for("building")


def test_target_voxel_quality_tiers() -> None:
    from text3d.bbox_tune import target_voxel_for

    hi = target_voxel_for("building", quality="highest")
    lo = target_voxel_for("building", quality="fast")
    med = target_voxel_for("building", quality="medium")
    assert hi < med < lo


def test_weapon_gets_more_octree_than_building_same_size() -> None:
    """Mesmo char_m: arma (inspeção 0.5 m) pede voxel mais fino que edifício."""
    from text3d.bbox_tune import tune_hunyuan_for_bbox

    kw = dict(base_steps=30, base_octree=256, base_chunks=8000, size_m=[1.5, 1.5, 0.3], total_vram_gib=12.0)
    weapon = tune_hunyuan_for_bbox(category="weapon", **kw)
    building = tune_hunyuan_for_bbox(category="building", **kw)
    assert weapon.octree >= building.octree


def test_chunks_decoupled_from_octree() -> None:
    """Chunks não escalam mais com octree — batch é função da VRAM no decode."""
    from text3d.bbox_tune import tune_hunyuan_for_bbox

    r = tune_hunyuan_for_bbox(
        base_steps=50,
        base_octree=256,
        base_chunks=8000,
        size_m=[10.0, 8.0, 6.0],
        total_vram_gib=12.0,
    )
    assert r.octree > 256
    assert r.chunks == 8000
