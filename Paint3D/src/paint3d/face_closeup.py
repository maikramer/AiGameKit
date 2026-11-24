"""Passe de close-up ortográfico na cabeça/rosto para personagens humanoides.

Num bake multiview de corpo inteiro a 512px (personagem de 6-6,5 cabeças),
o rosto ocupa apenas ~36x40 px (~4,5x5 tokens latentes no VAE 8x do SD 2.1),
tornando olhos, sobrancelhas, nariz e lábios borrados ou apagados.

Este módulo executa um passe adicional de 3 vistas fechadas (``0°``, ``+45°``,
``-45°``) focado apenas na cabeça e pescoço, usando o recorte de alta
resolução da referência original e mapas normais/posição ortográficos sem
inclinação vertical (centrando temporariamente a malha na cabeça). O rosto
passa a ocupar ~350x350 px (~44x44 tokens latentes), e o resultado é fundido
no atlas UV do corpo inteiro com máscara espacial 3D + rampa de cosseno suave
e ajuste cromático na zona de transição do pescoço.

Convenção interna de coordenadas do ``MeshRender`` (após ``set_mesh``):
- ``set_mesh`` faz ``vtx[:, [0, 1]] = -vtx[:, [0, 1]]`` e depois troca ``Y ↔ Z``;
- portanto ``-Z`` (``z_min``) é CIMA (coroa da cabeça → linha 0 das imagens
  nvdiffrast) e ``+Z`` (``z_max``) é BAIXO (sola dos pés → linha H-1);
- ``+Y`` é FRENTE (virada para a câmara ``azim=0``);
- ``-X`` é DIREITA no ecrã e ``+X`` é ESQUERDA no ecrã.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image

from .hy3dpaint.DifferentiableRenderer.camera_utils import get_mv_matrix, transform_pos
from .hy3dpaint.utils.pipeline_utils import merge_albedo_mr_views, split_baked_albedo_mr


@dataclass(frozen=True)
class HeadFrame:
    """Volume 3D da cabeça em espaço interno do ``MeshRender`` (``-Z``=cima, ``+Y``=frente)."""

    cam_center: tuple[float, float, float]
    ortho_scale: float
    head_center_xy: tuple[float, float]
    z_min: float  # coroa da cabeça (topo da imagem)
    z_max: float  # sola dos pés (base da imagem)
    neck_z_hi: float  # parte alta do pescoço / queixo (mais próximo de z_min)
    neck_z_lo: float  # base do pescoço / colarinho (mais próximo de z_max)
    radial_inner: float
    radial_outer: float


def resolve_face_closeup_mode(mode: bool | str | None = None) -> str:
    """Normaliza o modo do passe de rosto em ``'on'``, ``'off'`` ou ``'auto'``."""
    env = os.environ.get("PAINT3D_FACE_CLOSEUP", "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return "off"
    if env in ("1", "true", "yes", "on"):
        return "on"
    if env == "auto":
        return "auto"
    if mode is True:
        return "on"
    if mode is False:
        return "off"
    if isinstance(mode, str):
        m = mode.strip().lower()
        if m in ("1", "true", "yes", "on"):
            return "on"
        if m in ("0", "false", "no", "off"):
            return "off"
    return "auto"


def _row_width(mask: np.ndarray, row: int) -> int:
    cols = np.where(mask[row])[0]
    if cols.size == 0:
        return 0
    return int(cols.max() - cols.min() + 1)


def detect_humanoid_head(render: Any, *, force: bool = False) -> HeadFrame | None:
    """Detecta e enquadra a cabeça de um personagem humanoide no ``MeshRender``.

    No espaço interno do ``MeshRender`` (após ``_to_paint_frame`` + ``set_mesh``):
    - ``-Z`` (``z_min``) é CIMA (coroa da cabeça, linha 0 nas imagens);
    - ``+Z`` (``z_max``) é BAIXO (pés, linha H-1 nas imagens);
    - ``+Y`` é FRENTE (virada para a câmara ``azim=0``);
    - ``X`` é horizontal (``-X`` direita no ecrã, ``+X`` esquerda).

    Em modo ``auto`` (``force=False``), rejeita edifícios, quadrúpedes e props
    baixos verificando proporção vertical, estreitamento do pescoço face aos
    ombros e ausência de base sólida larga.
    """
    if render is None or getattr(render, "vtx_pos", None) is None:
        return None
    vp = render.vtx_pos[:, :3].detach().cpu().numpy()
    if vp.shape[0] < 16:
        return None

    vmin = vp.min(axis=0)
    vmax = vp.max(axis=0)
    ext = vmax - vmin
    x_ext, y_ext, z_ext = float(ext[0]), float(ext[1]), float(ext[2])
    if z_ext <= 1e-4:
        return None

    if not force:
        # Humanoide erecto: alto em Z, relativamente estreito em profundidade Y.
        if z_ext < 0.85 * x_ext or z_ext < 1.35 * y_ext:
            return None
        try:
            alpha = render.render_alpha(0, 0, resolution=256, return_type="np")[0, :, :, 0] > 0
        except Exception:
            return None
        ys, xs = np.where(alpha)
        if ys.size < 256:
            return None
        h_px = int(ys.max() - ys.min() + 1)
        w_px = int(xs.max() - xs.min() + 1)
        if h_px < 40 or w_px < 20:
            return None

        def _band_min_width(p0: float, p1: float) -> int:
            r0 = int(ys.min() + h_px * p0)
            r1 = max(r0 + 1, int(ys.min() + h_px * p1))
            widths = [_row_width(alpha, r) for r in range(r0, min(r1, alpha.shape[0]))]
            widths = [w for w in widths if w > 0]
            return min(widths) if widths else 0

        def _band_max_width(p0: float, p1: float) -> int:
            r0 = int(ys.min() + h_px * p0)
            r1 = max(r0 + 1, int(ys.min() + h_px * p1))
            widths = [_row_width(alpha, r) for r in range(r0, min(r1, alpha.shape[0]))]
            return max(widths) if widths else 0

        w_neck = _band_min_width(0.11, 0.21)
        w_shoulders = _band_max_width(0.22, 0.42)
        w_base = _band_max_width(0.86, 0.94)
        if w_shoulders <= 0 or w_neck <= 0:
            return None
        # Edifício/cone alarga até uma base sólida; humanoide tem ombros mais
        # largos que o pescoço e pés mais estreitos que o máximo da envergadura.
        if w_neck > 0.70 * w_shoulders:
            return None
        if w_base > 0.72 * w_px:
            return None

    z_min = float(vmin[2])
    z_max = float(vmax[2])
    h_3d = z_max - z_min

    # Coroa da cabeça (~9.5% superiores a partir de z_min). Usar a coroa + metade
    # frontal (+Y) isola o centro X/Y da cabeça mesmo com aljava, estojo de mapas
    # ou capa volumosa nos ombros/costas.
    crown_pts = vp[vp[:, 2] <= z_min + 0.095 * h_3d]
    if crown_pts.shape[0] < 8:
        crown_pts = vp[vp[:, 2] <= z_min + 0.165 * h_3d]
    if crown_pts.shape[0] < 4:
        return None

    y_cut = float(np.quantile(crown_pts[:, 1], 0.35))
    front_crown = crown_pts[crown_pts[:, 1] >= y_cut]
    if front_crown.shape[0] < 4:
        front_crown = crown_pts

    hx = float(np.median(front_crown[:, 0]))
    hy = float(np.median(front_crown[:, 1]))
    dx = np.abs(front_crown[:, 0] - hx)
    head_radius = float(np.clip(np.quantile(dx, 0.92) * 1.15, 0.055 * h_3d, 0.125 * h_3d))

    # Janela ortográfica justa à cabeça + cabelo + pescoço + topo da gola.
    ortho_scale = float(max(0.24 * h_3d, 2.7 * head_radius))
    cam_cz = z_min + 0.10 * h_3d

    return HeadFrame(
        cam_center=(hx, hy, cam_cz),
        ortho_scale=ortho_scale,
        head_center_xy=(hx, hy),
        z_min=z_min,
        z_max=z_max,
        neck_z_hi=z_min + 0.145 * h_3d,
        neck_z_lo=z_min + 0.195 * h_3d,
        radial_inner=max(head_radius * 1.15, 0.085 * h_3d),
        radial_outer=max(head_radius * 1.55, 0.125 * h_3d),
    )


def _flatten_on_white(image: Image.Image) -> tuple[Image.Image, np.ndarray]:
    """Compõe sobre branco (preservando resolução original) e devolve máscara de sujeito."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        alpha = np.asarray(rgba.getchannel("A"))
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        fg = alpha > 16
        return bg, fg
    rgb = image.convert("RGB")
    arr = np.asarray(rgb)
    fg = (arr < 246).any(axis=-1)
    return rgb, fg


def crop_reference_head(
    ref_image: Image.Image,
    head: HeadFrame,
    target_size: int,
) -> Image.Image:
    """Recorta a região da cabeça na referência em alta resolução alinhada ao ``HeadFrame``."""
    rgb, fg = _flatten_on_white(ref_image)
    w_img, h_img = rgb.size
    ys, xs = np.where(fg)
    if ys.size < 64:
        return rgb.resize((target_size, target_size), Image.Resampling.LANCZOS)

    ry0, ry1 = int(ys.min()), int(ys.max())
    ref_h = max(1, ry1 - ry0 + 1)
    h_3d = max(1e-6, head.z_max - head.z_min)
    px_per_unit = ref_h / h_3d

    # Centro horizontal da coroa (2%..9.5% do topo do sujeito): ignora tubos ou
    # capas que saiam de lado abaixo da coroa da cabeça.
    crown_y0 = ry0 + int(0.02 * ref_h)
    crown_y1 = max(crown_y0 + 2, ry0 + int(0.095 * ref_h))
    crown_cols = np.where(fg[crown_y0 : min(crown_y1, h_img)])[1]
    ref_cx = float(np.median(crown_cols)) if crown_cols.size >= 8 else float(np.median(xs))
    # Em MeshRender, z_min é o topo (ry0) e z cresce para baixo (+Y na imagem).
    ref_cy = float(ry0 + (head.cam_center[2] - head.z_min) * px_per_unit)

    crop_side = max(32, round(head.ortho_scale * px_per_unit))
    half = crop_side / 2.0
    x0 = round(ref_cx - half)
    y0 = round(ref_cy - half)
    x1 = x0 + crop_side
    y1 = y0 + crop_side

    canvas = Image.new("RGB", (crop_side, crop_side), (255, 255, 255))
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1, sy1 = min(w_img, x1), min(h_img, y1)
    if sx1 > sx0 and sy1 > sy0:
        patch = rgb.crop((sx0, sy0, sx1, sy1))
        canvas.paste(patch, (sx0 - x0, sy0 - y0))

    return canvas.resize((int(target_size), int(target_size)), Image.Resampling.LANCZOS)


def _render_centered_position_map(
    render: Any,
    elev: float,
    azim: float,
    ortho_scale: float,
    resolution: int,
) -> Image.Image:
    """Renderiza position map normalizado ao volume da cabeça (com ``vtx_pos`` já centrado)."""
    r_mv = get_mv_matrix(elev=elev, azim=azim, camera_distance=render.camera_distance)
    pos_cam = transform_pos(r_mv, render.vtx_pos, keepdim=True)
    pos_clip = transform_pos(render.camera_proj_mat, pos_cam)
    rast_out, _ = render.raster_rasterize(pos_clip, render.pos_idx, resolution=(resolution, resolution))

    tex_pos = (0.5 - render.vtx_pos[:, :3] / float(ortho_scale)).clamp(0.0, 1.0).contiguous()
    position, _ = render.raster_interpolate(tex_pos[None, ...], rast_out, render.pos_idx)
    visible_mask = torch.clamp(rast_out[..., -1:], 0, 1)
    bg = torch.ones(3, dtype=torch.float32, device=render.device)
    img = (position * visible_mask + bg * (1.0 - visible_mask))[0]
    arr = np.clip(img.detach().cpu().numpy() * 255.0, 0, 255).astype(np.uint8)
    return Image.fromarray(arr)


def _build_head_blend_mask(
    render: Any,
    orig_tex_position: torch.Tensor,
    head: HeadFrame,
    max_cos: torch.Tensor,
    head_trust: torch.Tensor,
) -> torch.Tensor:
    """Constrói máscara espacial 3D + rampa de cosseno (H, W, 1) em espaço UV."""
    h, w = render.texture_size
    spatial = torch.zeros((h, w), dtype=torch.float32, device=render.device)

    pos = orig_tex_position[:, :3]
    z = pos[:, 2]
    # z_min = topo da cabeça; z cresce para baixo (pescoço/peito).
    # z <= neck_z_hi → 1.0 (cabeça); z >= neck_z_lo → 0.0 (tronco/gola).
    z_span = max(1e-5, head.neck_z_lo - head.neck_z_hi)
    z_w = ((head.neck_z_lo - z) / z_span).clamp(0.0, 1.0)

    hx, hy = head.head_center_xy
    r_xy = torch.sqrt((pos[:, 0] - hx) ** 2 + (pos[:, 1] - hy) ** 2)
    r_span = max(1e-5, head.radial_outer - head.radial_inner)
    xy_w = ((head.radial_outer - r_xy) / r_span).clamp(0.0, 1.0)

    w_texel = z_w * xy_w
    spatial[render.tex_grid[:, 0], render.tex_grid[:, 1]] = w_texel

    # Rampa suave no ângulo de incidência para não criar degrau na nuca/têmporas.
    mc = max_cos.squeeze(-1) if max_cos.ndim == 3 else max_cos
    cos_w = ((mc - 0.15) / 0.30).clamp(0.0, 1.0)
    trust = head_trust.squeeze(-1).float() if head_trust.ndim == 3 else head_trust.float()
    alpha = (spatial * cos_w * trust).clamp(0.0, 1.0)

    # Suavização subtil (3x3) só na fronteira para evitar degrau no colarinho.
    alpha_4d = alpha.unsqueeze(0).unsqueeze(0)
    alpha_soft = F.avg_pool2d(alpha_4d, kernel_size=3, stride=1, padding=1).squeeze(0).squeeze(0)
    alpha = torch.where(trust > 0.5, torch.minimum(alpha, alpha_soft), torch.zeros_like(alpha))
    return alpha.unsqueeze(-1)


def _match_head_color(
    head_tex: torch.Tensor,
    base_tex: torch.Tensor,
    blend_alpha: torch.Tensor,
    base_trust: torch.Tensor,
    *,
    max_shift: float = 0.06,
) -> torch.Tensor:
    """Ajuste cromático suave na zona de transição pescoço/têmporas."""
    a = blend_alpha.squeeze(-1)
    bt = base_trust.squeeze(-1) if base_trust.ndim == 3 else base_trust
    ring = (a > 0.05) & (a < 0.90) & bt
    if int(ring.sum().item()) < 32:
        ring = (a > 0.5) & bt
    if int(ring.sum().item()) < 32:
        return head_tex
    delta = (base_tex[ring].mean(dim=0) - head_tex[ring].mean(dim=0)).clamp(-max_shift, max_shift)
    return (head_tex + delta.view(1, 1, -1)).clamp(0.0, 1.0)


@torch.no_grad()
def apply_face_closeup_pass(
    pipeline: Any,
    raw_ref_image: Image.Image,
    base_texture: torch.Tensor,
    base_mask_np: np.ndarray,
    base_texture_mr: torch.Tensor | None = None,
    *,
    mode: bool | str | None = None,
    debug_dir: str = "",
    logger: Any = None,
) -> tuple[torch.Tensor, np.ndarray, torch.Tensor | None, bool]:
    """Executa o passe de close-up de 3 vistas na cabeça e funde-o no atlas do corpo.

    Returns:
        ``(texture, mask_np, texture_mr, applied)``.
    """
    resolved = resolve_face_closeup_mode(mode)
    if resolved == "off":
        return base_texture, base_mask_np, base_texture_mr, False

    render = pipeline.render
    head = detect_humanoid_head(render, force=(resolved == "on"))
    if head is None:
        return base_texture, base_mask_np, base_texture_mr, False

    if logger is not None:
        logger.info(
            f"Face close-up: cabeça detectada (scale={head.ortho_scale:.3f}, "
            f"center=({head.cam_center[0]:.2f}, {head.cam_center[1]:.2f}, {head.cam_center[2]:.2f})) — "
            "a correr passe 3 vistas (0°, ±45°)"
        )

    orig_ortho = float(render.ortho_scale)
    orig_unreliable = int(render.bake_unreliable_kernel_size)
    orig_vtx_pos = render.vtx_pos.clone()
    orig_tex_position = render.tex_position.clone()
    view_res = int(pipeline.config.resolution)
    render_size = int(pipeline.config.render_size)

    elevs = [0.0, 0.0, 0.0]
    azims = [0.0, 45.0, -45.0]
    weights = [1.0, 0.40, 0.40]

    try:
        # Centrar temporariamente a malha na cabeça garante câmara ortográfica
        # estritamente horizontal (get_mv_matrix com center!=0 inclinaria a câmara
        # a partir da origem). Restaurado bit-a-bit no bloco finally.
        center_t = torch.tensor(head.cam_center, dtype=orig_vtx_pos.dtype, device=orig_vtx_pos.device)
        render.vtx_pos = orig_vtx_pos - center_t
        shifted_tex = orig_tex_position.clone()
        shifted_tex[:, :3] = orig_tex_position[:, :3] - center_t
        render.tex_position = shifted_tex
        render.set_orth_scale(head.ortho_scale)

        head_normals = [
            render.render_normal(
                elev,
                azim,
                resolution=view_res,
                use_abs_coor=True,
                return_type="pl",
            )
            for elev, azim in zip(elevs, azims, strict=True)
        ]
        head_positions = [
            _render_centered_position_map(render, elev, azim, head.ortho_scale, view_res)
            for elev, azim in zip(elevs, azims, strict=True)
        ]
        head_ref = crop_reference_head(raw_ref_image, head, view_res)

        mv = pipeline.models["multiview_model"](
            [head_ref],
            head_normals + head_positions,
            prompt="high quality",
            custom_view_size=view_res,
            resize_input=True,
        )

        head_albedos = [pipeline.models["super_model"](im) for im in mv["albedo"]]
        head_mrs = (
            [pipeline.models["super_model"](im) for im in mv["mr"]] if pipeline.config.enhance_mr else list(mv["mr"])
        )

        if debug_dir:
            os.makedirs(debug_dir, exist_ok=True)
            head_ref.save(os.path.join(debug_dir, "head_ref.png"))
            for i, az in enumerate(azims):
                head_normals[i].save(os.path.join(debug_dir, f"head_normal_{i:02d}_a{int(az)}.png"))
                head_positions[i].save(os.path.join(debug_dir, f"head_position_{i:02d}_a{int(az)}.png"))
                mv["albedo"][i].save(os.path.join(debug_dir, f"head_albedo_{i:02d}_a{int(az)}.png"))
                head_albedos[i].save(os.path.join(debug_dir, f"head_enhanced_{i:02d}_a{int(az)}.png"))

        head_albedos = [im.resize((render_size, render_size), Image.Resampling.LANCZOS) for im in head_albedos]
        head_mrs = [im.resize((render_size, render_size), Image.Resampling.LANCZOS) for im in head_mrs]
        merged = merge_albedo_mr_views(head_albedos, head_mrs)

        # Kernel de borda reduzido para não apagar pálpebras, nariz ou lábios.
        render.set_boundary_unreliable_scale(0.5)
        h_uv, w_uv = render.texture_size
        tex_sum = torch.zeros((h_uv, w_uv, 6), dtype=torch.float32, device=render.device)
        w_sum = torch.zeros((h_uv, w_uv, 1), dtype=torch.float32, device=render.device)
        max_cos = torch.zeros((h_uv, w_uv, 1), dtype=torch.float32, device=render.device)
        blur_k = torch.ones((1, 1, 3, 3), device=render.device) / 9.0

        for view, elev, azim, weight in zip(merged, elevs, azims, weights, strict=True):
            ptex, raw_cos, pbound = render.back_project(view, elev, azim)
            max_cos = torch.maximum(max_cos, raw_cos)
            pcos = weight * (raw_cos**pipeline.config.bake_exp)
            bm = F.conv2d(pbound.permute(2, 0, 1).unsqueeze(0), blur_k, padding=1).squeeze(0).permute(1, 2, 0)
            pcos = pcos * (0.70 + 0.30 * bm.clamp(0.0, 1.0)).clamp(0.0, 1.0)
            tex_sum += ptex * pcos
            w_sum += pcos

        head_trust = w_sum > 1e-8
        head_tex6 = tex_sum / torch.clamp(w_sum, min=1e-8)
        head_albedo, head_mr = split_baked_albedo_mr(head_tex6)

        blend_alpha = _build_head_blend_mask(render, orig_tex_position, head, max_cos, head_trust)
        base_trust_t = torch.from_numpy(base_mask_np > 0).to(device=render.device)
        head_albedo = _match_head_color(head_albedo, base_texture, blend_alpha, base_trust_t)

        out_albedo = base_texture * (1.0 - blend_alpha) + head_albedo * blend_alpha
        if base_texture_mr is not None:
            # O ramo MR do Paint 2.1 num close-up de rosto costuma alucinar busto
            # metálico (metallic até 0.91, roughness 0.00-0.25), deixando o rosto a
            # brilhar como cromo mesmo com roughness alto no viewer. Pele e cabelo
            # humanoides são sempre dielétricos mates (metallic <= 0.03, rough 0.78-0.94).
            safe_head_mr = head_mr.clone()
            safe_head_mr[..., 0] = safe_head_mr[..., 0].clamp(0.0, 0.03)
            safe_head_mr[..., 1] = (0.78 + safe_head_mr[..., 1].clamp(0.0, 1.0) * 0.16).clamp(0.78, 0.94)
            out_mr = base_texture_mr * (1.0 - blend_alpha) + safe_head_mr * blend_alpha
        else:
            out_mr = None
        added = (blend_alpha.squeeze(-1) > 0.35).detach().cpu().numpy()
        out_mask_np = np.where(added, np.uint8(255), base_mask_np)

        if debug_dir:
            alpha_img = (blend_alpha.squeeze(-1).clamp(0, 1).detach().cpu().numpy() * 255).astype(np.uint8)
            Image.fromarray(alpha_img).save(os.path.join(debug_dir, "head_blend_alpha.png"))

        return out_albedo, out_mask_np, out_mr, True
    finally:
        render.vtx_pos = orig_vtx_pos
        render.tex_position = orig_tex_position
        render.set_orth_scale(orig_ortho)
        render.bake_unreliable_kernel_size = orig_unreliable
