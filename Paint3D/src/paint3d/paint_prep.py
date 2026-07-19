"""Prep de textura para Hunyuan-Paint — inpaint restrito e supersampling de bake.

Double-shell / cascas internas resolvem-se no ``topology-fix`` (shape→clean)
antes do paint. Aqui só evitamos bleed de inpaint em ilhas UV nunca baked e
melhoramos a precisão do back-projection em meshes low-poly.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import numpy as np

# Dilatação UV (px) à volta de texels com bake trust — só esses buracos
# recebem OpenCV/vertex inpaint. Ilhas nunca vistas ficam cinza neutro.
DEFAULT_INPAINT_DILATE_PX = 16
_NEUTRAL_RGB = (0.45, 0.45, 0.45)

# O back_project do MeshRender testa depth por texel via nearest-sample do
# depth raster — em meshes low-poly (triângulos grandes) ~50% dos texels
# frontais falham o teste (3e-3) e o bake fica salpicado, degradando muito a
# textura (SSIM 0.834 vs 0.878 no high-poly, bench chapel). Subdividir SIMPLE
# (sem suavizar; UVs herdados linearmente) só para o bake recupera a precisão
# por-texel do high-poly a custo de segundos, e a textura serve na mesh
# original. Bench: 160k faces → subdiv x2 (3.8M tris) ≈ SSIM do clean 2.27M.
BAKE_SUBDIV_TARGET_FACES = 2_000_000
BAKE_SUBDIV_MAX_LEVELS = 2


def restrict_inpaint_mask(
    mask: np.ndarray,
    *,
    dilate_px: int = DEFAULT_INPAINT_DILATE_PX,
) -> tuple[np.ndarray, np.ndarray]:
    """Restringe buracos de inpaint à vizinhança de texels com trust.

    Convenção Hunyuan ``uv_inpaint``: ``255`` = keep, ``0`` = inpaint.

    Args:
        mask: Máscara uint8 (H,W) ou (H,W,1).
        dilate_px: Raio de dilatação à volta da região trusted.

    Returns:
        ``(new_mask, far_holes)`` — ``far_holes`` bool onde o buraco está
        longe de qualquer bake (não deve ser inpaintado).
    """
    import cv2

    m = np.asarray(mask)
    if m.ndim == 3:
        m = m.squeeze(-1)
    m = m.astype(np.uint8, copy=False)
    trusted = (m > 0).astype(np.uint8)
    if dilate_px > 0:
        k = 2 * int(dilate_px) + 1
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
        near = cv2.dilate(trusted, kernel)
    else:
        near = trusted
    holes = m == 0
    to_inpaint = holes & (near > 0)
    far_holes = holes & (near == 0)
    new_mask = np.where(to_inpaint, np.uint8(0), np.uint8(255))
    return new_mask, far_holes


def fill_far_holes(texture: Any, far_holes: np.ndarray, *, rgb: tuple[float, float, float] = _NEUTRAL_RGB) -> Any:
    """Preenche ilhas UV nunca baked com cor neutra (ou média dos trusted)."""
    if not far_holes.any():
        return texture

    import torch

    if isinstance(texture, torch.Tensor):
        tex = texture.clone()
        keep = ~torch.as_tensor(far_holes, device=tex.device)
        if keep.ndim == 2 and tex.ndim == 3:
            fill = tex[keep].mean(dim=0) if keep.any() else torch.tensor(rgb, dtype=tex.dtype, device=tex.device)
            tex[far_holes] = fill
        return tex

    arr = np.asarray(texture, dtype=np.float32).copy()
    if arr.ndim == 3 and far_holes.ndim == 2:
        keep = ~far_holes
        fill = arr[keep].mean(axis=0) if keep.any() else np.asarray(rgb, dtype=np.float32)
        arr[far_holes] = fill
    return arr


def compute_bake_subdiv_levels(
    faces: int,
    *,
    target_faces: int = BAKE_SUBDIV_TARGET_FACES,
    max_levels: int = BAKE_SUBDIV_MAX_LEVELS,
) -> int:
    """Níveis de subdivisão SIMPLE para o bake atingir ``target_faces``.

    Nível 1 multiplica triângulos por ~6 (tri → 3 quads → 6 tris);
    níveis seguintes por ~4. Devolve 0 se a mesh já é densa o suficiente.
    """
    if faces <= 0:
        return 0
    levels = 0
    estimate = faces
    while estimate < target_faces and levels < max_levels:
        estimate *= 6 if levels == 0 else 4
        levels += 1
    return levels


def subdivide_bake_mesh(mesh_obj: Any, levels: int) -> None:
    """Aplica subdivisão SIMPLE + triangulate in-place num objeto bpy."""
    if levels <= 0:
        return
    import bpy

    bpy.ops.object.select_all(action="DESELECT")
    mesh_obj.select_set(True)
    bpy.context.view_layer.objects.active = mesh_obj
    mod = mesh_obj.modifiers.new("BakeSubdiv", "SUBSURF")
    mod.subdivision_type = "SIMPLE"
    mod.levels = levels
    mod.render_levels = levels
    bpy.ops.object.modifier_apply(modifier=mod.name)
    tri = mesh_obj.modifiers.new("BakeTri", "TRIANGULATE")
    bpy.ops.object.modifier_apply(modifier=tri.name)


def install_bake_supersampling(
    render: Any,
    *,
    target_faces: int = BAKE_SUBDIV_TARGET_FACES,
    max_levels: int = BAKE_SUBDIV_MAX_LEVELS,
    logger: Any = None,
) -> None:
    """Monkeypatch ``render.load_mesh``/``save_mesh`` para bake supersampled.

    No load: extrai a geometria original (pós UV-wrap), subdivide SIMPLE a
    cópia bpy e carrega a versão densa no renderer — posições/depth por texel
    ficam precisos no ``back_project``. No save: repõe a geometria original
    (mesma bbox → mesma normalização) antes de exportar, mantendo as texturas
    baked. Desativável com ``PAINT3D_BAKE_SUBDIV=0``.
    """
    env = os.environ.get("PAINT3D_BAKE_SUBDIV", "").strip()
    if env == "0":
        return
    if env:
        with contextlib.suppress(ValueError):
            max_levels = max(0, int(env))
    if max_levels <= 0:
        return

    orig_load = render.load_mesh
    orig_save = render.save_mesh
    state: dict[str, Any] = {}

    def _load(mesh: Any = None, **kw: Any) -> Any:
        from paint3d.hy3dpaint.DifferentiableRenderer.mesh_utils import load_mesh as _extract

        try:
            vtx_pos, pos_idx, vtx_uv, uv_idx, _tex = _extract(mesh)
            levels = compute_bake_subdiv_levels(len(pos_idx), target_faces=target_faces, max_levels=max_levels)
            if levels > 0:
                subdivide_bake_mesh(mesh, levels)
                state["orig"] = (vtx_pos, pos_idx, vtx_uv, uv_idx)
                if logger is not None:
                    logger.info(f"Bake supersampling: subdiv SIMPLE x{levels} ({len(pos_idx)} faces → bake denso)")
        except Exception as exc:
            state.pop("orig", None)
            if logger is not None:
                logger.warn(f"Bake supersampling indisponível ({exc}); a usar mesh original")
        return orig_load(mesh=mesh, **kw)

    def _save(mesh_path: Any, downsample: bool = False) -> Any:
        orig = state.pop("orig", None)
        if orig is not None:
            vtx_pos, pos_idx, vtx_uv, uv_idx = orig
            render.set_mesh(vtx_pos, pos_idx, vtx_uv=vtx_uv, uv_idx=uv_idx)
        return orig_save(mesh_path, downsample=downsample)

    render.load_mesh = _load  # type: ignore[method-assign]
    render.save_mesh = _save  # type: ignore[method-assign]


def install_restricted_inpaint(view_processor: Any, *, dilate_px: int = DEFAULT_INPAINT_DILATE_PX) -> None:
    """Monkeypatch ``view_processor.texture_inpaint`` para skip ilhas nunca vistas."""
    orig = view_processor.texture_inpaint

    def _restricted(texture: Any, mask: Any, defualt: Any = None) -> Any:
        if defualt is not None:
            return orig(texture, mask, defualt=defualt)
        mask_np = np.asarray(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np.squeeze(-1)
        new_mask, far = restrict_inpaint_mask(mask_np, dilate_px=dilate_px)
        texture = fill_far_holes(texture, far)
        return orig(texture, new_mask)

    view_processor.texture_inpaint = _restricted  # type: ignore[method-assign]
