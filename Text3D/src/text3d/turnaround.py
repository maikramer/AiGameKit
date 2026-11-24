"""Geração de turnaround coerente (3 vistas ortográficas) e alinhamento de silhueta front↔back.

Resolve dois gargalos críticos de geração 3D multiview (Hunyuan3D-2mv + Paint3D v3 Multi-Ref):
1. Convenção oficial do Hunyuan3D-2mv: ``front``, ``left`` (personagem olhando para a
   esquerda ``<-``, mostrando o seu lado anatómico esquerdo), ``back`` (180° atrás).
2. Alinhamento pixel-a-pixel ``front`` ↔ ``mirror_x(back)``: sem alinhamento, um desvio
   de 20-30 px nas mãos entre a vista frontal e a traseira faz o Hunyuan3D-2mv reconstruir
   a união das duas silhuetas (6 dedos fantasma em vez de 5). :func:`align_back_to_front`
   alinha a escala/cabeça global e opcionalmente trava as duas bandas externas das mãos
   à silhueta espelhada exata da frente (IoU > 0.90), eliminando o 6º dedo nativamente.
"""

from __future__ import annotations

import gc
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image, ImageOps

#: Sugestão de prompt 2D para mãos em A-pose Quaternius natural (palmas voltadas às coxas).
OPEN_FIVE_FINGERS_HINT = (
    "both hands matching the exact relaxed A-pose of the front view with palms facing inward toward the thighs "
    "and backs of the hands facing outward (natural five-finger hands, never six fingers)"
)


def turnaround_seed_from_front_seed(seed: int | None) -> int:
    """Deriva um seed determinístico descorrelacionado do seed da vista frontal.

    Reutilizar o mesmo seed do FLUX.3 Klein da vista frontal na vista traseira faz o
    ruído inicial ``z_T`` reproduzir o layout espacial frontal (peito/fivela/acessórios
    no mesmo lado da imagem) em vez de rodar o sujeito 180°.
    """
    if seed is None:
        return 202
    return ((int(seed) ^ 0x5A3C9E1) + 7919) & 0x7FFFFFFF


def _foreground_mask(arr: np.ndarray, bg_thresh: int = 242) -> np.ndarray:
    if arr.ndim == 3 and arr.shape[-1] == 4:
        alpha = arr[..., 3]
        if int((alpha < 240).sum()) > 64:
            return alpha > 16
    return (arr[..., :3] < bg_thresh).any(axis=-1)


def _flatten_rgb(image: Image.Image) -> Image.Image:
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg
    return image.convert("RGB")


def silhouette_iou(front: Image.Image, back: Image.Image, *, bg_thresh: int = 242) -> dict[str, float]:
    """Mede IoU de silhueta entre ``front`` e ``mirror_x(back)`` (corpo inteiro e zona das mãos)."""
    f_rgb = _flatten_rgb(front)
    b_mir = ImageOps.mirror(_flatten_rgb(back).resize(f_rgb.size, Image.Resampling.LANCZOS))
    fm = _foreground_mask(np.asarray(f_rgb), bg_thresh=bg_thresh)
    bm = _foreground_mask(np.asarray(b_mir), bg_thresh=bg_thresh)

    full_union = int((fm | bm).sum())
    full_iou = float((fm & bm).sum() / max(1, full_union))

    h, w = fm.shape
    hand_band = np.zeros_like(fm)
    hand_band[int(h * 0.42) : int(h * 0.64), int(w * 0.10) : int(w * 0.33)] = True
    hand_band[int(h * 0.42) : int(h * 0.64), int(w * 0.67) : int(w * 0.90)] = True
    h_union = int(((fm | bm) & hand_band).sum())
    hands_iou = float(((fm & bm) & hand_band).sum() / max(1, h_union))
    return {"full_iou": round(full_iou, 4), "hands_iou": round(hands_iou, 4)}


def align_back_to_front(
    front: Image.Image,
    back: Image.Image,
    *,
    lock_outer_hands: bool = True,
    bg_thresh: int = 242,
    hand_x_frac: float = 0.260,
    hand_y_band: tuple[float, float] = (0.43, 0.63),
) -> tuple[Image.Image, dict[str, float]]:
    """Alinha a vista traseira ao espelho horizontal exato da vista frontal.

    1. Reescala uniformemente ``back`` para igualar a altura do sujeito em ``front``.
    2. Alinha o topo vertical e o centro horizontal da cabeça (evita deslocamentos de
       enquadramento causados por capas/acessórios assimétricos).
    3. Quando ``lock_outer_hands=True`` (humanoides), trava as duas regiões externas das
       mãos (além do punho) à silhueta espelhada exata da frente com transição suave de
       20 px — impedindo o Hunyuan3D-2mv de gerar 6 dedos por desalinhamento front↔back.
    """
    f_rgb = _flatten_rgb(front)
    b_rgb = _flatten_rgb(back)
    w, h = f_rgb.size
    if b_rgb.size != (w, h):
        b_rgb = b_rgb.resize((w, h), Image.Resampling.LANCZOS)

    f_mir = np.asarray(ImageOps.mirror(f_rgb), dtype=np.float32)
    ba = np.asarray(b_rgb, dtype=np.float32)
    fm = _foreground_mask(f_mir, bg_thresh=bg_thresh)
    bm = _foreground_mask(ba, bg_thresh=bg_thresh)

    f_ys, f_xs = np.where(fm)
    b_ys, b_xs = np.where(bm)
    if f_ys.size < 64 or b_ys.size < 64:
        return b_rgb, silhouette_iou(f_rgb, b_rgb, bg_thresh=bg_thresh)

    fy0, fy1 = int(f_ys.min()), int(f_ys.max())
    by0, by1 = int(b_ys.min()), int(b_ys.max())
    fh = max(1, fy1 - fy0)
    bh = max(1, by1 - by0)
    scale = fh / float(bh)

    new_w = max(1, round(w * scale))
    new_h = max(1, round(h * scale))
    ba_scaled = cv2.resize(ba, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
    bs_m = _foreground_mask(ba_scaled, bg_thresh=bg_thresh)

    # Centro horizontal da cabeça/topo (primeiros 15% da altura) → imune a capas/armas laterais
    f_head_xs = np.where(fm[fy0 : min(h, fy0 + max(4, int(0.15 * fh)))])[1]
    by0_s = round(by0 * scale)
    b_head_slice = bs_m[max(0, by0_s) : min(new_h, by0_s + max(4, int(0.15 * fh)))]
    b_head_xs = np.where(b_head_slice)[1]

    f_cx = float(np.median(f_head_xs)) if f_head_xs.size >= 4 else float(np.median(f_xs))
    b_cx = float(np.median(b_head_xs)) if b_head_xs.size >= 4 else float(np.median(b_xs) * scale)
    dx = round(f_cx - b_cx)
    dy = round(fy0 - by0_s)

    canvas = np.full((h, w, 3), 255.0, dtype=np.float32)
    src_x0 = max(0, -dx)
    dst_x0 = max(0, dx)
    src_y0 = max(0, -dy)
    dst_y0 = max(0, dy)
    copy_w = min(new_w - src_x0, w - dst_x0)
    copy_h = min(new_h - src_y0, h - dst_y0)
    if copy_w > 0 and copy_h > 0:
        canvas[dst_y0 : dst_y0 + copy_h, dst_x0 : dst_x0 + copy_w] = ba_scaled[
            src_y0 : src_y0 + copy_h, src_x0 : src_x0 + copy_w
        ]

    out = canvas
    if lock_outer_hands:
        # Alinhamento elástico suave (cv2.remap) por mão: translada rigidamente a palma
        # e os 5 dedos até coincidirem com a silhueta espelhada da frente, com decaimento
        # gaussiano suave APENAS para dentro ao longo do antebraço (zero cortes, zero
        # antebraço duplo, 100% pixels autênticos das costas) e apara halo fora da mão.
        ys_grid, xs_grid = np.mgrid[0:h, 0:w].astype(np.float32)
        disp_x = np.zeros((h, w), dtype=np.float32)
        disp_y = np.zeros((h, w), dtype=np.float32)
        cm = _foreground_mask(canvas, bg_thresh=bg_thresh)

        y0 = int(hand_y_band[0] * h)
        y1 = int(hand_y_band[1] * h)
        max_shift = max(16.0, 0.065 * w)
        sigma_x = max(12.0, 0.048 * w)
        sigma_y = max(12.0, 0.055 * h)

        for is_left in (True, False):
            # Correlação de fase na zona externa da mão encontra o deslocamento exato (sub-pixel)
            hand_cut = int(hand_x_frac * w) if is_left else int((1.0 - hand_x_frac) * w)
            hx0, hx1 = (0, hand_cut) if is_left else (hand_cut, w)
            sub_f = fm[y0:y1, hx0:hx1].astype(np.float32)
            sub_b = cm[y0:y1, hx0:hx1].astype(np.float32)
            if sub_f.sum() < 16 or sub_b.sum() < 16:
                continue
            (dx_pc, dy_pc), _ = cv2.phaseCorrelate(sub_b, sub_f)
            sh_x = float(np.clip(dx_pc, -max_shift, max_shift))
            sh_y = float(np.clip(dy_pc, -max_shift, max_shift))
            if abs(sh_x) < 0.75 and abs(sh_y) < 0.75:
                continue
            sfy, _ = np.where(sub_f > 0.5)
            f_hy = float(np.median(sfy)) + y0
            hand_half_h = max(0.055 * h, 0.55 * float(sfy.max() - sfy.min()))
            # Platô rígido em toda a mão para fora do punho; decai suavemente pelo antebraço
            wrist_x = float(hand_cut)
            excess_x = np.maximum(0.0, xs_grid - wrist_x) if is_left else np.maximum(0.0, wrist_x - xs_grid)
            excess_y = np.maximum(0.0, np.abs(ys_grid - f_hy) - hand_half_h)
            g = np.exp(-0.5 * ((excess_x / sigma_x) ** 2 + (excess_y / sigma_y) ** 2))
            disp_x += sh_x * g
            disp_y += sh_y * g

        if float(np.max(np.abs(disp_x))) >= 1.0 or float(np.max(np.abs(disp_y))) >= 1.0:
            map_x = np.clip(xs_grid - disp_x, 0.0, float(w - 1))
            map_y = np.clip(ys_grid - disp_y, 0.0, float(h - 1))
            out = cv2.remap(
                canvas,
                map_x,
                map_y,
                interpolation=cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(255.0, 255.0, 255.0),
            )
        else:
            out = canvas.copy()

        # Apara apenas pixels órfãos fora da silhueta dilatada das mãos frontais (anti-6º dedo)
        # sem nunca colar pixels frontais sobre o antebraço traseiro.
        dil_r = max(2, round(0.007 * w))
        k_dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dil_r + 1, 2 * dil_r + 1))
        fm_dil = cv2.dilate(fm.astype(np.uint8), k_dil) > 0
        trim_frac = max(0.18, hand_x_frac - 0.015)
        hand_zone = np.zeros((h, w), dtype=bool)
        hand_zone[y0:y1, : int(trim_frac * w)] = True
        hand_zone[y0:y1, int((1.0 - trim_frac) * w) :] = True
        stray = hand_zone & (~fm_dil)
        if stray.any():
            out[stray] = 255.0

    aligned_im = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    metrics = silhouette_iou(f_rgb, aligned_im, bg_thresh=bg_thresh)
    return aligned_im, metrics


def generate_turnaround_views(
    front_path: str | Path,
    out_dir: str | Path | None = None,
    *,
    subject_hint: str = "",
    back_details: str = "",
    category: str = "humanoid",
    include_right: bool = False,
    align_back: bool = True,
    width: int = 1024,
    height: int = 1024,
    steps: int = 8,
    seed: int = 202,
    device: str = "cuda",
    memory_efficient: bool = True,
    verbose: bool = False,
) -> dict[str, Path]:
    """Gera vistas ortográficas coerentes (``back``, ``left`` e opcional ``right``) com FLUX.3 Klein.

    Convenção Hunyuan3D-2mv garantida:
    - ``front``: imagem de entrada original;
    - ``back``: vista traseira 180°, alinhada pixel-a-pixel com ``mirror_x(front)``;
    - ``left``: perfil esquerdo anatómico com o sujeito virado para a ESQUERDA (``<-``).

    Devolve ``{"front": Path, "back": Path, "left": Path, ...}`` prontos para
    ``Hunyuan2MVGenerator`` e para ``Paint3D`` Multi-Ref nativo.
    """
    import torch

    from text2d.generator import KleinFluxGenerator

    front_p = Path(front_path).resolve()
    dst_dir = Path(out_dir).resolve() if out_dir is not None else front_p.parent
    dst_dir.mkdir(parents=True, exist_ok=True)
    stem = front_p.stem

    with Image.open(front_p) as im:
        front_im = _flatten_rgb(im)

    is_humanoid = str(category or "").strip().lower() in ("humanoid", "character", "npc", "hero")
    subj = subject_hint.strip() or ("fantasy character" if is_humanoid else "stylized 3D game asset")
    back_extra = f" {back_details.strip()}" if back_details.strip() else ""
    hand_clause = f" {OPEN_FIVE_FINGERS_HINT}." if is_humanoid else ""

    # FLUX.3 Klein realiza uma rotação 3D real de 180° quando o prompt descreve
    # explicitamente as costas completas (cabeça/tronco/cinto/calcanhares): passar
    # ``front_im`` sem pré-espelhar faz o modelo espelhar naturalmente ombros,
    # bolsas e acessórios assimétricos para o lado anatómico correto nas costas
    # (pré-espelhar provocava duplo espelhamento).
    p_back = (
        f"Strict orthographic BACK view of the exact same {subj} from the reference image, turned 180 degrees "
        f"facing completely AWAY from the viewer (seen directly from behind, full body head to heels on pure "
        f"solid white #FFFFFF background). Replace ALL front-facing details with the rear side of the body from "
        f"top to bottom: back of the head and hair (NO face, NO eyes), back of neck and shoulders, solid closed "
        f"back of the vest/tunic/cloak/armor across the back (NO front chest opening, NO V-neck shirt front, NO "
        f"front buttons), back of the belt strap (NO front belt buckle), back of trousers/skirt and seat, backs "
        f"of knees and calves, and rear heels of both boots pointing AWAY from the viewer (NO forward-pointing "
        f"boot toes). Match the exact height, standing A-pose, arm angles, colors, fabrics, and correctly mirror "
        f"asymmetric left/right details for the 180-degree rear view (anything seen on the left side of the front "
        f"reference sits on the right side of this back view, and vice versa; never duplicate accessories on both "
        f"sides).{back_extra}{hand_clause} "
        f"Any external back accessory sits cleanly outside the back (never sunk into the body). "
        f"Matte hand-painted stylized game concept art, crisp planes, no ground shadow."
    )
    # Convenção oficial Tencent Hunyuan3D-2mv (assets/example_mv_images/1/left.png):
    # "left" = lado esquerdo anatómico do objeto virado para a câmara, frente apontando para ESQUERDA (<-).
    side_hand_clause = (
        " The visible arm hangs straight down vertically at the side in strict orthographic side profile of the "
        "A-pose (arm aligned vertically with the hip/thigh, NOT reaching forward or backward), with a relaxed "
        "open five-finger hand seen broadside (never merged, never six fingers)."
        if is_humanoid
        else ""
    )
    p_left = (
        f"Strict orthographic LEFT SIDE PROFILE view of the exact same {subj} from the reference images, "
        f"facing strictly to the LEFT side of the frame (viewer sees the subject's anatomical LEFT side, nose/"
        f"front pointing LEFT, back pointing RIGHT), exact same height, pose, and framing on pure solid white "
        f"#FFFFFF background.{back_extra}{side_hand_clause} Preserve exact materials, proportions, and any external "
        f"back accessories clearly separated behind the back. Matte hand-painted stylized game concept art, crisp "
        f"planes, no ground shadow."
    )
    p_right = (
        f"Strict orthographic RIGHT SIDE PROFILE view of the exact same {subj} from the reference images, "
        f"facing strictly to the RIGHT side of the frame (viewer sees the subject's anatomical RIGHT side, nose/"
        f"front pointing RIGHT, back pointing LEFT), exact same height, pose, and framing on pure solid white "
        f"#FFFFFF background.{back_extra}{side_hand_clause} Matte hand-painted stylized game concept art, crisp "
        f"planes, no ground shadow."
    )

    out_views: dict[str, Path] = {"front": front_p}
    gen = KleinFluxGenerator(device=device, memory_efficient=memory_efficient, verbose=verbose)
    try:
        back_raw, _ = gen.generate(
            prompt=p_back,
            refs=[front_im],
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=1.0,
            seed=seed,
        )
        if align_back:
            back_im, iou = align_back_to_front(front_im, back_raw, lock_outer_hands=is_humanoid)
            if verbose:
                print(
                    f"[turnaround] back alinhado: full_iou={iou['full_iou']:.3f}, hands_iou={iou['hands_iou']:.3f}",
                    flush=True,
                )
        else:
            back_im = back_raw
        back_p = dst_dir / f"{stem}_back.png"
        back_im.save(back_p)
        out_views["back"] = back_p

        left_im, _ = gen.generate(
            prompt=p_left,
            refs=[front_im, back_im],
            width=width,
            height=height,
            num_inference_steps=steps,
            guidance_scale=1.0,
            seed=seed + 101,
        )
        left_p = dst_dir / f"{stem}_left.png"
        left_im.save(left_p)
        out_views["left"] = left_p

        if include_right:
            right_im, _ = gen.generate(
                prompt=p_right,
                refs=[front_im, back_im],
                width=width,
                height=height,
                num_inference_steps=steps,
                guidance_scale=1.0,
                seed=seed + 202,
            )
            right_p = dst_dir / f"{stem}_right.png"
            right_im.save(right_p)
            out_views["right"] = right_p
    finally:
        gen.unload()
        del gen
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    return out_views


def prepare_multiview_images(
    views: dict[str, str | Path | Image.Image],
    *,
    align_back: bool = True,
    lock_outer_hands: bool = True,
) -> tuple[dict[str, Image.Image], dict[str, Any]]:
    """Carrega dict ``{front, back, left, right}`` e aplica alinhamento front↔back quando pedido."""
    loaded: dict[str, Image.Image] = {}
    for k, val in views.items():
        if isinstance(val, Image.Image):
            loaded[k] = val.copy()
        else:
            with Image.open(val) as im:
                im.load()
                loaded[k] = im.convert("RGBA") if "A" in im.getbands() else im.convert("RGB")

    meta: dict[str, Any] = {}
    if align_back and "front" in loaded and "back" in loaded:
        aligned_back, iou = align_back_to_front(
            loaded["front"],
            loaded["back"],
            lock_outer_hands=lock_outer_hands,
        )
        loaded["back"] = aligned_back
        meta["back_align"] = iou
    return loaded, meta
