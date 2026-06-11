"""Regressão dos volume decoders hierárquicos (hy3dshape vendorizado).

Bug histórico: HierarchicalVolumeDecoding escalava as coordenadas de refinamento
com dtype int64 (dtype dos índices), truncando resolution<1.0 para 0 — todas as
queries colapsavam em bbox_min e o decode "funcionava" mas devolvia campo
constante (mesh vazia no marching cubes).
"""

from __future__ import annotations

import torch

from text3d.hy3dshape.models.autoencoders.volume_decoders import HierarchicalVolumeDecoding


def _sphere_geo_decoder(queries: torch.Tensor, latents: torch.Tensor) -> torch.Tensor:
    """SDF analítico: positivo dentro de esfera de raio 0.6, surface no zero."""
    radius = 0.6
    dist = torch.linalg.norm(queries, dim=-1, keepdim=True)
    return radius - dist


def _constant_geo_decoder(queries: torch.Tensor, latents: torch.Tensor) -> torch.Tensor:
    return torch.full((*queries.shape[:-1], 1), -5.0, dtype=queries.dtype)


def test_hierarchical_refinement_preserves_surface() -> None:
    latents = torch.zeros(1, 4, 8, dtype=torch.float32)
    decoder = HierarchicalVolumeDecoding()
    grid = decoder(
        latents,
        _sphere_geo_decoder,
        bounds=1.01,
        num_chunks=200_000,
        octree_resolution=128,
        mc_level=0.0,
        enable_pbar=False,
    )
    valid = grid[torch.isfinite(grid)]
    # Campo refinado tem de cruzar o zero (superfície da esfera presente).
    assert (valid > 0).any(), "sem interior — queries de refinamento colapsaram"
    assert (valid < 0).any(), "sem exterior"
    # Sanidade geométrica: centro dentro, canto fora.
    n = grid.shape[-1]
    assert grid[0, n // 2, n // 2, n // 2] > 0
    corner = grid[0, 0, 0, 0]
    assert torch.isnan(corner) or corner < 0


def test_hierarchical_degenerate_field_no_crash() -> None:
    """Campo sem superfície: termina sem excepção e devolve grid coerente."""
    latents = torch.zeros(1, 4, 8, dtype=torch.float32)
    decoder = HierarchicalVolumeDecoding()
    grid = decoder(
        latents,
        _constant_geo_decoder,
        bounds=1.01,
        num_chunks=200_000,
        octree_resolution=128,
        mc_level=0.0,
        enable_pbar=False,
    )
    valid = grid[torch.isfinite(grid)]
    assert valid.numel() > 0
    assert (valid < 0).all()
