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

# O back_project do MeshRender testa depth por texel via nearest-sample do
# depth raster — em meshes low-poly (triângulos grandes) ~50% dos texels
# frontais falham o teste (3e-3) e o bake fica salpicado, degradando muito a
# textura (SSIM 0.834 vs 0.878 no high-poly, bench chapel). Subdividir SIMPLE
# (sem suavizar; UVs herdados linearmente) só para o bake recupera a precisão
# por-texel do high-poly a custo de segundos, e a textura serve na mesh
# original. Bench: 160k faces → subdiv x2 (3.8M tris) ≈ SSIM do clean 2.27M.
BAKE_SUBDIV_TARGET_FACES = 2_000_000
BAKE_SUBDIV_MAX_LEVELS = 2

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
) -> np.ndarray | None:
    """Cor do texel baked mais próximo **em 3D** para cada texel nunca baked.

    Args:
        tex_np: Textura (H,W,C) float.
        far_holes: Bool (H,W) dos texels a preencher.
        trusted: Bool (H,W) dos texels realmente baked.
        position_map: (H,W,3) posição 3D por texel (``render.uv_feature_map``).
        max_samples: Tecto de amostras trusted na KD-tree.

    Returns:
        (N,C) cores na ordem de ``np.nonzero(far_holes)``, ou ``None`` se não
        houver vizinhos utilizáveis (chama-se então o fill chapado).
    """
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        return None

    src = trusted & np.isfinite(position_map).all(axis=-1)
    dst = far_holes & np.isfinite(position_map).all(axis=-1)
    if not src.any() or not dst.any():
        return None

    src_pos = position_map[src]
    src_rgb = tex_np[src]
    if len(src_pos) > max_samples:
        pick = np.random.default_rng(0).choice(len(src_pos), max_samples, replace=False)
        src_pos, src_rgb = src_pos[pick], src_rgb[pick]

    _dist, nn = cKDTree(src_pos).query(position_map[far_holes], k=1, workers=-1)
    out = np.repeat(src_rgb.mean(axis=0, keepdims=True), int(far_holes.sum()), axis=0)
    # Texels far sem posição 3D (fora de qualquer triângulo) ficam na média.
    valid = dst[far_holes]
    out[valid] = src_rgb[nn[valid]]
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
        position_map: (H,W,3) posição 3D por texel, ou ``None``.
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
        if pos_np.shape[:2] == far_holes.shape and pos_np.shape[-1] == 3:
            patch = _nearest_trusted_colors(tex_np, far_holes, trusted, pos_np)

    tex_np[far_holes] = fill if patch is None else patch

    if is_tensor:
        return torch.as_tensor(tex_np, dtype=texture.dtype, device=texture.device)
    return tex_np


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


def apply_top_view_weight(config: Any, weight: float = DEFAULT_TOP_VIEW_WEIGHT, *, logger: Any = None) -> float:
    """Reponderar as vistas de topo/baixo do bake (``candidate_view_weights``).

    Só muda o *blend*: a seleção de vistas é por área nova, e o trust map não
    depende de pesos. Útil em edifícios/terreno, onde o telhado é superfície
    principal e o default de personagem (0.05) o deixa à mercê de vistas de
    esguelha. Override por env: ``PAINT3D_TOP_VIEW_WEIGHT``.

    Args:
        config: ``Hunyuan3DPaintConfig`` do pipeline.
        weight: Peso novo para as vistas de topo e de baixo.
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
    for i in _TOP_VIEW_IDX:
        weights[i] = float(weight)
    if logger is not None and weight != DEFAULT_TOP_VIEW_WEIGHT:
        logger.info(f"Bake: peso das vistas topo/baixo = {weight:g} (default {DEFAULT_TOP_VIEW_WEIGHT:g})")
    return float(weight)


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
    """Mapa texel → posição 3D (``uv_feature_map``) ou ``None`` se indisponível."""
    if render is None:
        return None
    try:
        pos = render.uv_feature_map(render.vtx_pos[:, :3].contiguous())
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
