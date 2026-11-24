"""Prep de textura para Hunyuan-Paint — inpaint restrito e supersampling de bake.

Double-shell / cascas internas resolvem-se no ``topology-fix`` (shape→clean)
antes do paint. Aqui evitamos bleed de inpaint em ilhas UV nunca baked, damos-
lhes uma cor vinda do que foi mesmo pintado, e melhoramos a precisão do
back-projection em meshes low-poly.
"""

from __future__ import annotations

import contextlib
import os
from typing import Any

import numpy as np

# Dilatação UV (px) à volta de texels com bake trust — só esses buracos
# recebem o inpaint OpenCV. Ilhas nunca vistas vão para `fill_far_holes`.
DEFAULT_INPAINT_DILATE_PX = 16
_NEUTRAL_RGB = (0.45, 0.45, 0.45)

# Amostras trusted usadas no fill mesh-aware (KD-tree 3D). Acima disto o
# subsample é aleatório — a cor da parede vizinha não precisa de todos os texels.
FILL_NEIGHBOUR_SAMPLES = 200_000

# Bias de profundidade escalado pelo declive (shadow-map style).
#
# O ``back_sample`` aceita um texel se ``|z_texel - z_raster| < 3e-3``, com
# ``z_raster`` amostrado no pixel *mais próximo*. Numa superfície de topo à
# câmara o erro dessa amostragem é pequeno; numa superfície quase de perfil o
# mesmo pixel cobre muita profundidade e o texel falha por auto-oclusão — o
# bake sai salpicado e o resto vira ``cv2.inpaint`` (esborratado).
#
# Medido na chapel (13 vistas, atlas 2048), fração do atlas com bake real:
#     3e-3 (upstream) 36.8% | 1e-2 49.8% | 2e-2 55.6%
#     base 3e-3 + 0.05*(1/cos-1) ......... 57.9%
# ... contra 64.6% de área que alguma câmara chega a ver. Alargar de forma
# uniforme também aceitaria superfícies escondidas atrás de geometria de topo;
# escalar pelo declive só relaxa onde o erro de amostragem é mesmo grande.
DEFAULT_DEPTH_BIAS_BASE = 3e-3  # = tolerância do upstream
DEFAULT_DEPTH_BIAS_SLOPE = 0.05
DEFAULT_DEPTH_BIAS_MAX = 0.08

# Peso das vistas de topo/baixo no blend do bake (índices 4 e 5 dos candidatos).
# Upstream usa 0.05 contra 1.0 da frontal — afinado para personagens, onde o
# topo é o cimo da cabeça. Num edifício o telhado é superfície principal: com
# ``bake_exp=6`` e um telhado a ~45°, a vista frontal pesa 0.125 contra 0.006
# do topo, ou seja o telhado é pintado quase só por vistas que o veem de esguelha.
DEFAULT_TOP_VIEW_WEIGHT = 0.05
_TOP_VIEW_IDX = (4, 5)


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


def _nearest_trusted_colors(
    tex_np: np.ndarray,
    far_holes: np.ndarray,
    trusted: np.ndarray,
    position_map: np.ndarray,
    *,
    max_samples: int = FILL_NEIGHBOUR_SAMPLES,
    valid_uv_mask: np.ndarray | None = None,
) -> np.ndarray | None:
    """Cor do texel baked mais próximo **em 3D** para cada texel nunca baked.

    Args:
        tex_np: Textura (H,W,C) float.
        far_holes: Bool (H,W) dos texels a preencher.
        trusted: Bool (H,W) dos texels realmente baked.
        position_map: (H,W,3) posição 3D por texel (``render.uv_feature_map``).
        max_samples: Tecto de amostras trusted na KD-tree.
        valid_uv_mask: Máscara bool (H,W) de texels cobertos por triângulos UV
            (exclui o gutter/fundo do atlas, que tem posição ``(0,0,0)``).

    Returns:
        (N,C) cores na ordem de ``np.nonzero(far_holes)``, ou ``None`` se não
        houver vizinhos utilizáveis (chama-se então o fill chapado).
    """
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return None

    finite = np.isfinite(position_map).all(axis=-1)
    if valid_uv_mask is not None:
        in_tri = np.asarray(valid_uv_mask, dtype=bool)
        src = trusted & finite & in_tri
        dst = far_holes & finite & in_tri
    else:
        src = trusted & finite
        dst = far_holes & finite
    if not src.any():
        return None

    src_pos = position_map[src]
    src_rgb = tex_np[src]
    if len(src_pos) > max_samples:
        pick = np.random.default_rng(0).choice(len(src_pos), max_samples, replace=False)
        src_pos, src_rgb = src_pos[pick], src_rgb[pick]

    mean_rgb = src_rgb.mean(axis=0, dtype=np.float64, keepdims=True).astype(tex_np.dtype)
    out = np.repeat(mean_rgb, int(far_holes.sum()), axis=0)
    if not dst.any():
        return out.astype(tex_np.dtype, copy=False)

    # Consultar apenas os texels válidos (dentro de triângulos UV) com eps=0.05:
    # evita o pior caso degenerado da cKDTree em superfícies esféricas/equidistantes
    # sem perda visual perceptível no preenchimento de áreas nunca vistas.
    _dist, nn = cKDTree(src_pos).query(position_map[dst], k=1, eps=0.05, workers=-1)
    valid = dst[far_holes]
    out[valid] = src_rgb[nn]
    return out.astype(tex_np.dtype, copy=False)


def fill_far_holes(
    texture: Any,
    far_holes: np.ndarray,
    *,
    trusted: np.ndarray | None = None,
    position_map: Any = None,
    rgb: tuple[float, float, float] = _NEUTRAL_RGB,
) -> Any:
    """Preenche ilhas UV nunca baked com a cor das regiões realmente pintadas.

    ``trusted`` é a máscara dos texels que o bake tocou. **Só esses** contam
    para a cor de preenchimento: ``~far_holes`` inclui os buracos *near* que
    ainda estão a zero (só são inpaintados a seguir), e usá-los na média
    escurece o fill várias vezes — num bake de cinzento 128 a chapel dava 21,
    ou seja o modelo saía preto em vez de neutro.

    Com ``position_map`` (texel → posição 3D, ``render.uv_feature_map``) o fill
    é mesh-aware: cada texel nunca visto herda a cor do texel baked mais próximo
    em 3D (a parede interior fica com a cor da exterior) em vez de uma média
    global chapada.

    Args:
        texture: Textura (H,W,C) — ``torch.Tensor`` ou array.
        far_holes: Bool (H,W) das ilhas nunca baked.
        trusted: Bool (H,W) dos texels baked. ``None`` → usa ``rgb``.
        position_map: (H,W,3) posição 3D por texel, ou (H,W,4) com canal 3 de
            cobertura UV (>0.5 dentro de triângulo), ou ``None``.
        rgb: Cor de recurso quando não há texels trusted.

    Returns:
        Textura do mesmo tipo do input com as ilhas preenchidas.
    """
    if not far_holes.any():
        return texture

    import torch

    is_tensor = isinstance(texture, torch.Tensor)
    tex_np = texture.detach().cpu().numpy() if is_tensor else np.asarray(texture, dtype=np.float32)
    tex_np = tex_np.copy()
    if tex_np.ndim != 3 or far_holes.ndim != 2:
        return texture

    if trusted is not None and np.asarray(trusted).any():
        trusted = np.asarray(trusted, dtype=bool)
        fill: np.ndarray = tex_np[trusted].mean(axis=0)
    else:
        trusted = None
        fill = np.asarray(rgb, dtype=tex_np.dtype)

    patch = None
    if trusted is not None and position_map is not None:
        pos_np = (
            position_map.detach().cpu().numpy() if isinstance(position_map, torch.Tensor) else np.asarray(position_map)
        )
        if pos_np.shape[:2] == far_holes.shape and pos_np.shape[-1] in (3, 4):
            valid_uv = (pos_np[..., 3] > 0.5) if pos_np.shape[-1] == 4 else None
            patch = _nearest_trusted_colors(tex_np, far_holes, trusted, pos_np[..., :3], valid_uv_mask=valid_uv)

    tex_np[far_holes] = fill if patch is None else patch

    if is_tensor:
        return torch.as_tensor(tex_np, dtype=texture.dtype, device=texture.device)
    return tex_np


# Abaixo desta saturação média (0-255) sobre o sujeito, a referência é
# praticamente cinzenta — normalmente um blockout/silhueta em vez de arte
# pintada. O multiview não tem cor de onde partir e o asset sai escuro.
#
# O limiar é deliberadamente baixo: um sujeito legitimamente cinzento existe
# (`dead_tree` mede 10.4 e é arte válida — uma árvore morta). Os blockouts que
# motivaram este teste mediam 1.1 e 2.0, ou seja praticamente sem cor nenhuma.
# Preferir deixar passar um cinzento real a gritar por cada asset monocromático.
REFERENCE_MIN_SATURATION = 6.0
REFERENCE_BACKGROUND_LUM = 245.0


def check_reference_image(image: Any, *, logger: Any = None) -> float:
    """Avisa quando a referência do paint é uma silhueta chapada, não arte pintada.

    O Hunyuan-Paint propaga a cor da referência para as vistas que inventa. Se a
    referência vier cinzenta (blockout, render de máscara, text2d falhado), não há
    cor para propagar e o asset baked sai escuro — sem que nada no pipeline falhe.
    Medido nos packs: as referências saudáveis ficam em saturação 10-60; as duas
    pontes partidas em 1.1 e 2.0, e saíram 92% e 52% de textura chapada.

    Args:
        image: Caminho ou ``PIL.Image`` da referência.
        logger: Logger opcional.

    Returns:
        Saturação média do sujeito (0-255); ``-1.0`` se não foi possível medir.
    """
    try:
        from PIL import Image as _Image

        im = _Image.open(image) if isinstance(image, (str, os.PathLike)) else image
        arr = np.asarray(im.convert("RGB"), dtype=np.float32)
    except Exception as exc:
        if logger is not None:
            logger.warn(f"Não consegui inspecionar a referência ({exc})")
        return -1.0

    subject = arr[arr.mean(axis=2) < REFERENCE_BACKGROUND_LUM]
    if subject.size == 0:
        return -1.0
    saturation = float((subject.max(axis=1) - subject.min(axis=1)).mean())
    if saturation < REFERENCE_MIN_SATURATION and logger is not None:
        logger.warn(
            f"Referência quase sem cor (saturação {saturation:.1f} < {REFERENCE_MIN_SATURATION:.0f}) — "
            "parece um blockout/silhueta e não arte pintada; o asset vai sair escuro. "
            "Regenerar o PNG antes de pintar."
        )
    return saturation


def apply_top_view_weight(config: Any, weight: float | None = None, *, logger: Any = None) -> float:
    """Reponderar as vistas de topo/baixo do bake (``candidate_view_weights``).

    Só muda o *blend*: a seleção de vistas é por área nova, e o trust map não
    depende de pesos. Útil em edifícios/terreno, onde o telhado é superfície
    principal e o default de personagem (0.05) o deixa à mercê de vistas de
    esguelha. Override por env: ``PAINT3D_TOP_VIEW_WEIGHT``.

    Args:
        config: ``Hunyuan3DPaintConfig`` do pipeline.
        weight: Peso novo para as vistas de topo e de baixo (``None`` mantém o
            valor da config, salvo override via ``PAINT3D_TOP_VIEW_WEIGHT``).
        logger: Logger opcional.

    Returns:
        O peso efetivamente aplicado.
    """
    env = os.environ.get("PAINT3D_TOP_VIEW_WEIGHT", "").strip()
    if env:
        with contextlib.suppress(ValueError):
            weight = max(0.0, float(env))
    weights = getattr(config, "candidate_view_weights", None)
    if not weights or len(weights) <= max(_TOP_VIEW_IDX):
        return DEFAULT_TOP_VIEW_WEIGHT
    if weight is None:
        return float(weights[_TOP_VIEW_IDX[0]])
    for i in _TOP_VIEW_IDX:
        weights[i] = float(weight)
    if logger is not None and weight != DEFAULT_TOP_VIEW_WEIGHT:
        logger.info(f"Bake: peso das vistas topo/baixo = {weight:g} (default {DEFAULT_TOP_VIEW_WEIGHT:g})")
    return float(weight)


def orient_visible_faces_outward(
    render: Any,
    *,
    resolution: int = 512,
    logger: Any = None,
) -> int:
    """Corrige faces com winding invertido votando visibilidade de 30 câmaras exteriores.

    Malhas não-watertight ou simplificadas (ex.: edifícios de várias cascas)
    chegam por vezes ao Paint3D com paredes/telhados de normais invertidas para
    dentro. Sem esta correcção:
      - ``render_normal`` alimenta o UNet com normais invertidas (frente verde em
        vez de magenta);
      - ``back_project`` rejeita todas as faces invertidas (``cos < 0``), derrubando
        o bake trust e deixando paredes inteiras ao inpaint;
      - motores com backface culling (Bevy) ocultam as faces invertidas.

    No espaço interno do ``MeshRender`` (``set_mesh`` troca Y↔Z com det=-1), uma
    face glTF virada para fora tem ``cross(v1-v0, v2-v0)`` alinhada com
    ``lookat_world = -r_mv[2, :3]`` (mesmo sinal usado em ``back_project``).
    Desligar via ``PAINT3D_ORIENT_NORMALS=0``.

    Returns:
        Número de faces invertidas corrigidas.
    """
    env = os.environ.get("PAINT3D_ORIENT_NORMALS", "1").strip().lower()
    if env in ("0", "false", "no", "off"):
        return 0
    if render is None or getattr(render, "vtx_pos", None) is None or getattr(render, "pos_idx", None) is None:
        return 0

    import torch
    import torch.nn.functional as F

    from .hy3dpaint.DifferentiableRenderer.camera_utils import get_mv_matrix, transform_pos

    n_faces = int(render.pos_idx.shape[0])
    if n_faces == 0:
        return 0

    device = render.vtx_pos.device
    tris = render.vtx_pos[render.pos_idx.long()]
    fn_world = F.normalize(torch.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0], dim=-1), dim=-1)

    votes = torch.zeros(n_faces, device=device, dtype=torch.float32)
    cam_pairs = [
        (0, 0),
        (0, 90),
        (0, 180),
        (0, 270),
        (90, 0),
        (-90, 180),
    ] + [(elev, azim) for azim in range(0, 360, 30) for elev in (25, -25)]

    res = (int(resolution), int(resolution))
    with torch.no_grad():
        for elev, azim in cam_pairs:
            r_mv = get_mv_matrix(elev=elev, azim=azim, camera_distance=render.camera_distance)
            pos_cam = transform_pos(r_mv, render.vtx_pos, keepdim=True)
            pos_clip = transform_pos(render.camera_proj_mat, pos_cam)
            rast_out, _ = render.raster_rasterize(pos_clip, render.pos_idx, resolution=res)
            tri_id = rast_out[0, ..., 3].long()
            fg = tri_id > 0
            if not fg.any():
                continue
            fids = tri_id[fg] - 1
            counts = torch.bincount(fids, minlength=n_faces).float()
            # Espaço reflectido do MeshRender: faces exteriores apontam ao longo de lookat (-r_mv[2, :3]).
            lookat_world = torch.from_numpy(-r_mv[2, :3]).to(device=device, dtype=torch.float32)
            dots = (fn_world * lookat_world).sum(dim=-1)
            votes += counts * dots

    flip = votes < 0
    n_flip = int(flip.sum().item())
    if n_flip == 0:
        return 0

    render.pos_idx[flip] = render.pos_idx[flip][:, [0, 2, 1]]
    render._crease_normals_cache = None
    uv_idx = getattr(render, "uv_idx", None)
    if uv_idx is not None and uv_idx.shape == render.pos_idx.shape and uv_idx.data_ptr() != render.pos_idx.data_ptr():
        uv_idx[flip] = uv_idx[flip][:, [0, 2, 1]]

    if uv_idx is not None and getattr(render, "vtx_uv", None) is not None:
        render.extract_textiles()

    if logger is not None:
        logger.info(f"Orientação exterior: {n_flip:,}/{n_faces:,} ({n_flip / n_faces:.1%}) faces invertidas corrigidas")
    return n_flip


def install_depth_bias(
    render: Any,
    *,
    base: float = DEFAULT_DEPTH_BIAS_BASE,
    slope: float = DEFAULT_DEPTH_BIAS_SLOPE,
    max_bias: float = DEFAULT_DEPTH_BIAS_MAX,
    logger: Any = None,
) -> None:
    """Liga o bias de profundidade escalado pelo declive no ``back_project``.

    Sem isto o bake usa a tolerância constante ``3e-3`` do upstream e perde
    metade dos texels que as câmaras realmente veem (chapel: 36.8% de bake real
    contra 64.6% de área visível); o que se perde cai no ``cv2.inpaint``, que é
    o aspeto esborratado. Desligar com ``PAINT3D_DEPTH_BIAS_SLOPE=0``.

    Args:
        render: ``MeshRender`` do pipeline.
        base: Tolerância mínima (superfícies de topo à câmara).
        slope: Fator de ``(1/cos - 1)``; 0 desliga (comportamento upstream).
        max_bias: Tecto da tolerância — impede aceitar geometria bem escondida.
        logger: Logger opcional.
    """
    env = os.environ.get("PAINT3D_DEPTH_BIAS_SLOPE", "").strip()
    if env:
        with contextlib.suppress(ValueError):
            slope = max(0.0, float(env))
    render.depth_bias_base = float(base)
    render.depth_bias_slope = float(slope)
    render.depth_bias_max = float(max_bias)
    if logger is not None and slope > 0:
        logger.info(f"Bake depth bias: base={base:g} slope={slope:g} max={max_bias:g}")


def _texel_position_map(render: Any, shape: tuple[int, int], logger: Any = None) -> Any:
    """Mapa texel → (x, y, z, uv_coverage) via ``uv_feature_map`` ou ``None``."""
    if render is None:
        return None
    try:
        import torch

        vtx = render.vtx_pos[:, :3]
        ones = torch.ones((vtx.shape[0], 1), dtype=vtx.dtype, device=vtx.device)
        pos = render.uv_feature_map(torch.cat([vtx, ones], dim=-1).contiguous())
    except Exception as exc:  # renderer sem mesh carregada / rasterizer em falta
        if logger is not None:
            logger.warn(f"Fill mesh-aware indisponível ({exc}); a usar média dos texels pintados")
        return None
    return pos if tuple(pos.shape[:2]) == tuple(shape) else None


def install_restricted_inpaint(
    view_processor: Any,
    *,
    dilate_px: int = DEFAULT_INPAINT_DILATE_PX,
    logger: Any = None,
) -> None:
    """Monkeypatch ``view_processor.texture_inpaint`` para skip ilhas nunca vistas.

    O inpaint OpenCV é UV-space e sangra entre ilhas vizinhas no atlas, por isso
    só corre à volta de texels com bake trust. O resto (cascas internas,
    interiores nunca vistos pelas câmaras) é preenchido por
    :func:`fill_far_holes` a partir das regiões pintadas — nunca com uma média
    que inclua texels ainda por pintar, senão o asset sai preto.
    """
    orig = view_processor.texture_inpaint

    def _restricted(texture: Any, mask: Any, defualt: Any = None) -> Any:
        if defualt is not None:
            return orig(texture, mask, defualt=defualt)
        mask_np = np.asarray(mask)
        if mask_np.ndim == 3:
            mask_np = mask_np.squeeze(-1)
        new_mask, far = restrict_inpaint_mask(mask_np, dilate_px=dilate_px)
        trusted = mask_np > 0
        if logger is not None and far.any():
            logger.info(
                f"Inpaint restrito: bake trust {trusted.mean():.1%} do atlas, "
                f"{far.mean():.1%} nunca visto (fill a partir do pintado)"
            )
        pos_map = _texel_position_map(getattr(view_processor, "render", None), far.shape, logger=logger)
        texture = fill_far_holes(texture, far, trusted=trusted, position_map=pos_map)
        return orig(texture, new_mask)

    view_processor.texture_inpaint = _restricted  # type: ignore[method-assign]
