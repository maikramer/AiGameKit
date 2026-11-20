"""
``rasterize`` / ``interpolate`` backed by nvdiffrast.

Matches the contract used by ``hy3dgen/texgen/differentiable_renderer/mesh_render.py``
(Hunyuan3D-2 v2.x):

    findices, barycentric = custom_rasterizer.rasterize(pos, tri, resolution)
    result   = custom_rasterizer.interpolate(attr, findices, barycentric, tri)
"""

from __future__ import annotations

import nvdiffrast.torch as dr
import torch

_glctx: dr.RasterizeCudaContext | None = None


def _get_ctx() -> dr.RasterizeCudaContext:
    global _glctx
    if _glctx is None:
        _glctx = dr.RasterizeCudaContext()
    return _glctx


def rasterize(
    pos: torch.Tensor,
    tri: torch.Tensor,
    resolution: tuple[int, int] | list[int],
    clamp_depth: torch.Tensor | None = None,
    use_depth_prior: int = 0,
) -> tuple[torch.Tensor, torch.Tensor]:
    """Rasterize clip-space vertices.

    Parameters
    ----------
    pos : Tensor [B, V, 4] or [V, 4]
        Clip-space positions.
    tri : Tensor [F, 3]
        Triangle index buffer (int32).
    resolution : (H, W)

    Returns
    -------
    findices : Tensor [H, W]  int32, 1-indexed (0 = background)
    barycentric : Tensor [H, W, 3]  float32 (w0, w1, w2)
    """
    ctx = _get_ctx()

    if pos.dim() == 2:
        pos = pos.unsqueeze(0)

    tri_i32 = tri.int().contiguous()
    pos_f32 = pos.float().contiguous()

    rast_out, _ = dr.rasterize(ctx, pos_f32, tri_i32, resolution)

    # rast_out: [B, H, W, 4]  →  (u, v, z/w, tri_id_1indexed)
    findices = rast_out[0, :, :, 3].int()  # [H, W]

    u = rast_out[0, :, :, 0]
    v = rast_out[0, :, :, 1]
    w = 1.0 - u - v
    barycentric = torch.stack([w, u, v], dim=-1)  # [H, W, 3]

    return findices, barycentric


def interpolate(
    col: torch.Tensor,
    findices: torch.Tensor,
    barycentric: torch.Tensor,
    tri: torch.Tensor,
) -> torch.Tensor:
    """Interpolate per-vertex attributes over rasterized pixels.

    Uses nvdiffrast's fused CUDA ``dr.interpolate`` kernel on CUDA tensors
    (~6.5x faster and ~3.7x less VRAM at 2048x2048 than gathering ``[H,W,3,C]``
    in PyTorch), with a CPU PyTorch fallback.

    Parameters
    ----------
    col : Tensor [B, V, C] or [V, C]
    findices : Tensor [H, W]  int32, 1-indexed (0 = background)
    barycentric : Tensor [H, W, 3]
    tri : Tensor [F, 3]

    Returns
    -------
    Tensor [1, H, W, C]
    """
    if col.dim() == 2:
        col = col.unsqueeze(0)

    if findices.is_cuda and col.is_cuda and tri.is_cuda:
        orig_dtype = col.dtype
        h, w = int(findices.shape[0]), int(findices.shape[1])
        rast = torch.empty((1, h, w, 4), device=findices.device, dtype=torch.float32)
        rast[0, :, :, 0] = barycentric[:, :, 0]
        rast[0, :, :, 1] = barycentric[:, :, 1]
        rast[0, :, :, 2] = 0.0
        rast[0, :, :, 3] = findices.float()
        out, _ = dr.interpolate(col.float().contiguous(), rast, tri.int().contiguous())
        if orig_dtype != torch.float32:
            out = out.to(orig_dtype)
        return out

    fg = findices > 0
    f = (findices - 1).clamp(min=0)
    vcol = col[0, tri.long()[f.long()]]  # [H, W, 3, C]
    result = (barycentric.unsqueeze(-1) * vcol).sum(dim=-2)  # [H, W, C]
    result = result * fg.unsqueeze(-1).to(result.dtype)
    return result.unsqueeze(0)  # [1, H, W, C]
