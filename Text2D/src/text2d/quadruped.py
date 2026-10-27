"""Poses de quadrúpedes (cão, cavalo, lobo...) — texto + silhueta de perfil.

Mesma estratégia dos bípedes (:mod:`text2d.poses`): o FLUX.3 copia a forma da
silhueta e o texto por extenso confirma a pose. Só há vista de **perfil**
(``east``/``west``): é a vista standard dos sprites de animais e a única em
que as quatro patas se distinguem numa silhueta.

Convenções: patas ``near`` (lado do observador, branco, à frente do corpo) e
``far`` (lado oposto, cinzento, atrás do corpo). Ângulos em graus a partir da
vertical; positivo = para a frente (direita, cabeça do animal). A figura é
desenhada e depois **assente no chão** (pata mais baixa na linha do chão menos
``lift``), como nos bípedes — agachar é dobrar as patas, o corpo desce sozinho.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class QuadSpec:
    """Uma pose de quadrúpede (perfil, cabeça à direita).

    Attributes:
        text: Frase da pose para o prompt (fala sempre das patas — "leg").
        legs: Ângulo das coxas ``(frente-near, frente-far, trás-near, trás-far)``.
        lift: Elevação das patas (fração da altura) — fases aéreas.
        head: Inclinação do pescoço em graus (positivo = cabeça em baixo, a
            farejar; negativo = erguida, alerta).
        tilt: Inclinação do corpo inteiro (positivo = focinho para baixo).
        knees: Flexão extra ``(frente, trás)`` — agachar/recolher sem encolher patas.
        tail: Ângulo da cauda (positivo = erguida).
    """

    text: str
    legs: tuple[float, float, float, float]
    lift: float = 0.0
    head: float = 0.0
    tilt: float = 0.0
    knees: tuple[float, float] = (0.0, 0.0)
    tail: float = 25.0


_QUAD_POSES: dict[str, QuadSpec] = {
    "quad_idle": QuadSpec(
        "standing still on all four legs, the legs straight under the body, the head raised", (2, -2, -2, 2)
    ),
    "quad_sniff": QuadSpec(
        "standing on all four legs with the head lowered to sniff the ground, the neck stretched down, the tail "
        "relaxed",
        (6, -2, -4, 2),
        head=100,
        tail=5,
    ),
    "quad_alert": QuadSpec(
        "standing alert on all four legs, the head held high and the ears up, the tail raised",
        (4, -4, -6, 2),
        head=-22,
        tail=55,
    ),
    # Passo (4 tempos): avanço maior que na v2.2 — o lobo saía de passo curto.
    "quad_walk_1": QuadSpec(
        "walking, the near front leg reaching forward, the far front leg pushed back under the body, the near hind "
        "leg stretched back, the far hind leg stepping forward",
        (32, -18, -28, 22),
    ),
    "quad_walk_2": QuadSpec(
        "walking, the near front leg vertical under the shoulder, the far front leg lifted and passing forward, "
        "the near hind leg lifted and passing forward, the far hind leg vertical",
        (0, 16, 16, 0),
        knees=(30, 30),
    ),
    "quad_walk_3": QuadSpec(
        "walking, the far front leg reaching forward, the near front leg pushed back under the body, the far hind "
        "leg stretched back, the near hind leg stepping forward",
        (-18, 32, 22, -28),
    ),
    "quad_walk_4": QuadSpec(
        "walking, the far front leg vertical under the shoulder, the near front leg lifted and passing forward, the "
        "far hind leg lifted and passing forward, the near hind leg vertical",
        (16, 0, 0, 16),
        knees=(30, 30),
    ),
    # Trote: pares diagonais em sincronia + fase suspensa.
    "trot_1": QuadSpec(
        "trotting, the near front leg and the far hind leg reaching forward together, the far front leg and the "
        "near hind leg pushing back together",
        (38, -24, -30, 30),
    ),
    "trot_2": QuadSpec(
        "trotting in a brief moment of suspension, all four legs off the ground and folded under the body",
        (14, 6, 8, 14),
        lift=0.04,
        knees=(45, 40),
    ),
    "trot_3": QuadSpec(
        "trotting, the far front leg and the near hind leg reaching forward together, the near front leg and the "
        "far hind leg pushing back together",
        (-24, 38, 30, -30),
    ),
    "trot_4": QuadSpec(
        "trotting in a brief moment of suspension, all four legs off the ground and folded under the body, the "
        "other diagonal pair leading",
        (6, 14, 14, 8),
        lift=0.04,
        knees=(40, 45),
    ),
    "gallop_extend": QuadSpec(
        "galloping at full stretch in mid-air, both front legs reaching far forward, both hind legs stretched far "
        "back, the body long and low",
        (48, 36, -50, -38),
        lift=0.08,
    ),
    "gallop_front_land": QuadSpec(
        "galloping, the front legs landing on the ground under the chest, the hind legs swinging forward, the "
        "back arched",
        (6, -14, 30, 14),
        tilt=5,
    ),
    "gallop_gather": QuadSpec(
        "galloping gathered in mid-air, all four legs tucked under the body, the front legs folded back and the "
        "hind legs reaching forward, the back rounded",
        (-38, -26, 40, 30),
        lift=0.1,
        knees=(30, 10),
    ),
    "gallop_hind_push": QuadSpec(
        "galloping, the hind legs planted and pushing off the ground, the front legs reaching forward off the "
        "ground, the body stretching out",
        (36, 24, -18, -6),
        lift=0.03,
        tilt=-5,
    ),
    # Ataque: agachar → saltar → morder → recuperar.
    "quad_crouch": QuadSpec(
        "crouched low ready to pounce, all four legs deeply bent, the chest close to the ground, the head low and "
        "forward, the tail straight back",
        (26, 18, -14, -6),
        head=18,
        knees=(70, 65),
        tail=10,
    ),
    "quad_pounce": QuadSpec(
        "leaping forward to pounce in mid-air, both front legs stretched forward with the paws reaching out, both "
        "hind legs stretched far back, the mouth open",
        (70, 58, -55, -45),
        lift=0.16,
        head=-10,
        tilt=-12,
    ),
    "quad_bite": QuadSpec(
        "biting forward with the jaws wide open, the front legs braced forward on the ground, the hind legs "
        "pushing, the head thrust forward and down",
        (30, 22, -24, -14),
        head=22,
        tilt=8,
        knees=(10, 20),
    ),
    "quad_recover": QuadSpec(
        "stepping back after attacking, the legs planted wide on the ground, the head raised and snarling",
        (20, -10, -18, 10),
        head=-8,
        tail=35,
    ),
    # Salto: agachar → impulso → no ar → aterrar.
    "quad_leap": QuadSpec(
        "jumping high in mid-air, the front legs tucked up under the chest, the hind legs stretched back after the "
        "push, the body angled upward",
        (-40, -30, -50, -40),
        lift=0.22,
        head=20,
        tilt=-10,
        knees=(80, 0),
    ),
    "quad_land": QuadSpec(
        "landing from a jump, both front legs reaching down to touch the ground first, the hind legs still in the "
        "air and tucked, the body angled downward",
        (18, 10, 40, 30),
        lift=0.04,
        head=12,
        tilt=10,
        knees=(0, 40),
    ),
    "quad_hurt": QuadSpec(
        "flinching from a hit, the legs splayed and buckling, the body recoiling back, the head thrown up, the tail "
        "tucked low",
        (22, 12, -6, 6),
        head=-10,
        tilt=-10,
        knees=(25, 35),
        tail=-25,
    ),
}

QUAD_WALK_CYCLE: tuple[str, ...] = ("quad_walk_1", "quad_walk_2", "quad_walk_3", "quad_walk_4")
QUAD_TROT_CYCLE: tuple[str, ...] = ("trot_1", "trot_2", "trot_3", "trot_4")
QUAD_GALLOP_CYCLE: tuple[str, ...] = ("gallop_hind_push", "gallop_extend", "gallop_front_land", "gallop_gather")


def quad_pose_names() -> list[str]:
    """Nomes das poses de quadrúpede."""
    return sorted(_QUAD_POSES)


def is_quad_pose(pose: str) -> bool:
    """A pose pertence ao vocabulário de quadrúpedes?"""
    return pose in _QUAD_POSES


def _spec(pose: str) -> QuadSpec:
    try:
        return _QUAD_POSES[pose]
    except KeyError:
        raise KeyError(
            f"pose de quadrúpede desconhecida: {pose!r} (disponíveis: {', '.join(quad_pose_names())})"
        ) from None


def quad_pose_prompt(pose: str) -> str:
    """Frase da pose (quatro patas) para o prompt.

    Raises:
        KeyError: Pose desconhecida.
    """
    return _spec(pose).text


def quad_pose_lift(pose: str) -> float:
    """Elevação das patas (fração da altura) — fases aéreas."""
    return _spec(pose).lift


def draw_quad_silhouette(pose: str, *, width: int = 320, height: int = 256) -> Image.Image:
    """Silhueta de perfil (cabeça à direita) de um quadrúpede na pose.

    Corpo horizontal, pescoço e cabeça à frente, cauda atrás; patas ``near`` a
    branco à frente do corpo e ``far`` a cinzento atrás (pista de profundidade
    — sem ela as fases 1/3 do passo são silhuetas iguais). Desenhada num
    canvas 2x à volta do corpo, rodada por ``tilt`` e assente no chão.

    Raises:
        KeyError: Pose desconhecida.
    """
    spec = _spec(pose)
    fn, ff, hn, hf = spec.legs
    big_w, big_h = width * 2, height * 2
    img = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    far = (150, 150, 150, 255)
    u = height  # unidade
    cx, body_y = big_w / 2, big_h / 2
    body_half_w, body_half_h = width * 0.27, u * 0.1
    leg_len = u * 0.36
    leg_w = max(2, int(u * 0.045))
    shoulder_x, hip_x = cx + body_half_w * 0.72, cx - body_half_w * 0.72
    leg_top = body_y + body_half_h * 0.5

    def leg(x: float, angle: float, knee: float, front: bool, fill: tuple[int, ...]) -> None:
        # Duas secções (coxa + canela). A canela segue 40% do ângulo da coxa
        # (uma linha reta lia-se como pau e o modelo ignorava a pata); ``knee``
        # dobra-a — para trás nas patas da frente (carpo), para a frente nas de
        # trás (curvilhão).
        a = math.radians(angle)
        kx, ky = x + math.sin(a) * leg_len * 0.5, leg_top + math.cos(a) * leg_len * 0.5
        bend = math.radians(angle * 0.4 + (-knee if front else knee))
        ex, ey = kx + math.sin(bend) * leg_len * 0.5, ky + math.cos(bend) * leg_len * 0.5
        d.line([(x, leg_top), (kx, ky), (ex, ey)], fill=fill, width=leg_w, joint="curve")
        d.ellipse([ex - leg_w * 0.7, ey - leg_w * 0.5, ex + leg_w * 0.7, ey + leg_w * 0.5], fill=fill)

    leg(shoulder_x - u * 0.02, ff, spec.knees[0], True, far)
    leg(hip_x + u * 0.02, hf, spec.knees[1], False, far)
    # Corpo, pescoço, cabeça (roda à volta da base do pescoço), cauda.
    d.ellipse([cx - body_half_w, body_y - body_half_h, cx + body_half_w, body_y + body_half_h], fill=white)
    neck_base = (cx + body_half_w * 0.8, body_y - body_half_h * 0.4)
    rel = (body_half_w * 0.35, -u * 0.2 + body_half_h * 0.4)
    h = math.radians(spec.head)
    head_c = (
        neck_base[0] + rel[0] * math.cos(h) - rel[1] * math.sin(h),
        neck_base[1] + rel[0] * math.sin(h) + rel[1] * math.cos(h),
    )
    d.line([neck_base, head_c], fill=white, width=int(u * 0.09))
    hr = u * 0.07
    d.ellipse([head_c[0] - hr, head_c[1] - hr, head_c[0] + hr, head_c[1] + hr], fill=white)
    snout_dy = hr * 0.9 * math.sin(h)  # focinho acompanha a cabeça a descer
    d.ellipse(
        [head_c[0], head_c[1] - hr * 0.45 + snout_dy, head_c[0] + hr * 1.9, head_c[1] + hr * 0.6 + snout_dy],
        fill=white,
    )
    d.polygon(
        [
            (head_c[0] - hr * 0.6, head_c[1] - hr * 0.5),
            (head_c[0] - hr * 0.1, head_c[1] - hr * 1.9),
            (head_c[0] + hr * 0.3, head_c[1] - hr * 0.6),
        ],
        fill=white,
    )  # orelha
    tail_base = (cx - body_half_w * 0.95, body_y - body_half_h * 0.4)
    t = math.radians(spec.tail)
    tail_end = (tail_base[0] - u * 0.13 * math.cos(t), tail_base[1] - u * 0.13 * math.sin(t))
    d.line([tail_base, tail_end], fill=white, width=int(u * 0.035))
    leg(shoulder_x + u * 0.02, fn, spec.knees[0], True, white)
    leg(hip_x - u * 0.02, hn, spec.knees[1], False, white)

    if spec.tilt:
        # PIL: positivo = anti-horário → focinho para cima; tilt positivo = para baixo.
        img = img.rotate(-spec.tilt, resample=Image.Resampling.BICUBIC, center=(cx, body_y))
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    bbox = img.getchannel("A").getbbox()
    if bbox is None:
        return out
    # Fases aéreas altas (salto) podiam cortar a cabeça no topo: a elevação
    # da silhueta é só uma pista (o alinhamento repõe ``lift`` no sprite).
    ground = max(height * 0.94 - spec.lift * height, min(height * 0.94, bbox[3] - bbox[1] + 2))
    out.alpha_composite(img, (round(width * 0.47 - cx), round(ground - bbox[3])))
    return out


def draw_quad_sheet(
    poses: tuple[str, ...] | list[str], *, cell: tuple[int, int] = (320, 256), cols: int = 4
) -> Image.Image:
    """Folha de silhuetas de quadrúpede (grelha de ``cols`` colunas)."""
    cell_w, cell_h = cell
    ncols = max(1, min(cols, len(poses)))
    rows = (len(poses) + ncols - 1) // ncols
    sheet = Image.new("RGB", (cell_w * ncols, cell_h * rows), (0, 0, 0))
    for i, pose in enumerate(poses):
        sil = draw_quad_silhouette(pose, width=cell_w, height=cell_h)
        sheet.paste(sil, ((i % ncols) * cell_w, (i // ncols) * cell_h), sil)
    return sheet
