"""Poses de quadrúpedes (cão, cavalo, lobo...) — texto + silhueta de perfil.

Mesma estratégia dos bípedes (:mod:`text2d.poses`): o FLUX.3 copia a forma da
silhueta e o texto por extenso confirma a pose. Só há vista de **perfil**
(``east``/``west``): é a vista standard dos sprites de animais e a única em
que as quatro patas se distinguem numa silhueta.

Convenções: patas ``near`` (lado do observador, branco, à frente do corpo) e
``far`` (lado oposto, cinzento, atrás do corpo). Ângulos em graus a partir da
vertical; positivo = para a frente (direita, cabeça do animal).
"""

from __future__ import annotations

import math

from PIL import Image, ImageDraw

# Texto por pose (perfil).
_QUAD_TEXT: dict[str, str] = {
    "quad_idle": "standing still on all four legs, the legs straight under the body, the head raised",
    "quad_walk_1": "walking, the near front leg reaching forward, the far front leg pushed back under the body, "
    "the near hind leg stretched back, the far hind leg stepping forward",
    "quad_walk_2": "walking, the near front leg vertical under the shoulder, the far front leg lifted and "
    "passing forward, the near hind leg lifted and passing forward, the far hind leg vertical",
    "quad_walk_3": "walking, the far front leg reaching forward, the near front leg pushed back under the body, "
    "the far hind leg stretched back, the near hind leg stepping forward",
    "quad_walk_4": "walking, the far front leg vertical under the shoulder, the near front leg lifted and passing "
    "forward, the far hind leg lifted and passing forward, the near hind leg vertical",
    "gallop_extend": "galloping at full stretch in mid-air, both front legs reaching far forward, both hind legs "
    "stretched far back, the body long and low",
    "gallop_front_land": "galloping, the front legs landing on the ground under the chest, the hind legs swinging "
    "forward, the back arched",
    "gallop_gather": "galloping gathered in mid-air, all four legs tucked under the body, the front legs folded "
    "back and the hind legs reaching forward, the back rounded",
    "gallop_hind_push": "galloping, the hind legs planted and pushing off the ground, the front legs reaching "
    "forward off the ground, the body stretching out",
}

# (frente-near, frente-far, trás-near, trás-far) + elevação do corpo (fração da altura).
_QUAD_ANGLES: dict[str, tuple[float, float, float, float, float]] = {
    "quad_idle": (2, -2, -2, 2, 0.0),
    "quad_walk_1": (26, -14, -22, 18, 0.0),
    "quad_walk_2": (0, 14, 14, 0, 0.0),
    "quad_walk_3": (-14, 26, 18, -22, 0.0),
    "quad_walk_4": (14, 0, 0, 14, 0.0),
    "gallop_extend": (48, 36, -50, -38, 0.08),
    "gallop_front_land": (6, -14, 30, 14, 0.0),
    "gallop_gather": (-38, -26, 40, 30, 0.1),
    "gallop_hind_push": (36, 24, -18, -6, 0.03),
}

QUAD_WALK_CYCLE: tuple[str, ...] = ("quad_walk_1", "quad_walk_2", "quad_walk_3", "quad_walk_4")
QUAD_GALLOP_CYCLE: tuple[str, ...] = ("gallop_hind_push", "gallop_extend", "gallop_front_land", "gallop_gather")


def quad_pose_names() -> list[str]:
    """Nomes das poses de quadrúpede."""
    return sorted(_QUAD_TEXT)


def is_quad_pose(pose: str) -> bool:
    """A pose pertence ao vocabulário de quadrúpedes?"""
    return pose in _QUAD_TEXT


def quad_pose_prompt(pose: str) -> str:
    """Frase da pose (quatro patas) para o prompt.

    Raises:
        KeyError: Pose desconhecida.
    """
    return _QUAD_TEXT[pose]


def quad_pose_lift(pose: str) -> float:
    """Elevação das patas (fração da altura) — fases aéreas do galope."""
    return _QUAD_ANGLES[pose][4]


def draw_quad_silhouette(pose: str, *, width: int = 320, height: int = 256) -> Image.Image:
    """Silhueta de perfil (cabeça à direita) de um quadrúpede na pose.

    Corpo horizontal, pescoço e cabeça à frente, cauda atrás; patas ``near`` a
    branco à frente do corpo e ``far`` a cinzento atrás (pista de profundidade
    — sem ela as fases 1/3 do passo são silhuetas iguais).

    Raises:
        KeyError: Pose desconhecida.
    """
    if pose not in _QUAD_ANGLES:
        raise KeyError(f"pose de quadrúpede desconhecida: {pose!r} (disponíveis: {', '.join(quad_pose_names())})")
    fn, ff, hn, hf, lift = _QUAD_ANGLES[pose]
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    far = (150, 150, 150, 255)
    u = height  # unidade
    cx = width * 0.47
    body_y = u * (0.48 - lift)
    body_half_w, body_half_h = width * 0.27, u * 0.1
    leg_len = u * 0.36
    leg_w = max(2, int(u * 0.045))
    shoulder_x, hip_x = cx + body_half_w * 0.72, cx - body_half_w * 0.72
    leg_top = body_y + body_half_h * 0.5

    def leg(x: float, angle: float, fill: tuple[int, ...]) -> None:
        # Duas secções (coxa + canela) com o joelho a dobrar para trás nas fases
        # recolhidas: uma linha reta lia-se como pau e o modelo ignorava a pata.
        a = math.radians(angle)
        kx, ky = x + math.sin(a) * leg_len * 0.5, leg_top + math.cos(a) * leg_len * 0.5
        bend = math.radians(angle * 0.4)
        ex, ey = kx + math.sin(bend) * leg_len * 0.5, ky + math.cos(bend) * leg_len * 0.5
        d.line([(x, leg_top), (kx, ky), (ex, ey)], fill=fill, width=leg_w, joint="curve")
        d.ellipse([ex - leg_w * 0.7, ey - leg_w * 0.5, ex + leg_w * 0.7, ey + leg_w * 0.5], fill=fill)

    leg(shoulder_x - u * 0.02, ff, far)
    leg(hip_x + u * 0.02, hf, far)
    # Corpo, pescoço, cabeça, cauda.
    d.ellipse([cx - body_half_w, body_y - body_half_h, cx + body_half_w, body_y + body_half_h], fill=white)
    neck_base = (cx + body_half_w * 0.8, body_y - body_half_h * 0.4)
    head_c = (cx + body_half_w * 1.15, body_y - u * 0.2)
    d.line([neck_base, head_c], fill=white, width=int(u * 0.09))
    hr = u * 0.07
    d.ellipse([head_c[0] - hr, head_c[1] - hr, head_c[0] + hr, head_c[1] + hr], fill=white)
    d.ellipse([head_c[0], head_c[1] - hr * 0.45, head_c[0] + hr * 1.9, head_c[1] + hr * 0.6], fill=white)  # focinho
    d.polygon(
        [
            (head_c[0] - hr * 0.6, head_c[1] - hr * 0.5),
            (head_c[0] - hr * 0.1, head_c[1] - hr * 1.9),
            (head_c[0] + hr * 0.3, head_c[1] - hr * 0.6),
        ],
        fill=white,
    )  # orelha
    tail_base = (cx - body_half_w * 0.95, body_y - body_half_h * 0.4)
    d.line([tail_base, (tail_base[0] - u * 0.12, tail_base[1] - u * 0.06)], fill=white, width=int(u * 0.035))
    leg(shoulder_x + u * 0.02, fn, white)
    leg(hip_x - u * 0.02, hn, white)
    return img


def draw_quad_sheet(poses: tuple[str, ...] | list[str], *, cell: tuple[int, int] = (320, 256)) -> Image.Image:
    """Folha horizontal de silhuetas de quadrúpede (uma célula por pose)."""
    cell_w, cell_h = cell
    sheet = Image.new("RGB", (cell_w * len(poses), cell_h), (0, 0, 0))
    for i, pose in enumerate(poses):
        sil = draw_quad_silhouette(pose, width=cell_w, height=cell_h)
        sheet.paste(sil, (i * cell_w, 0), sil)
    return sheet
