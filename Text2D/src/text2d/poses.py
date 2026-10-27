"""Biblioteca de poses para animação 2D — prompts de pernas/braços + silhuetas.

O FLUX.3 Image Klein **não** tem pose conditioning: o que controla a pose é a
*forma* da referência (o workflow ``image_conditioned`` copia a imagem). Duas
técnicas complementares, ambas validadas E2E (ver
``docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md``):

1. **Prompt de pose** — cada frame leva uma frase explícita sobre as pernas e
   os braços (o texto muda a pose mesmo com a mesma referência: 8% → 16-26% de
   diferença de silhueta entre fases).
2. **Silhueta de pose** — :func:`draw_pose_silhouette` desenha a pose pedida
   (pernas/braços em ângulos concretos) e usa-se essa imagem como referência:
   a forma desenhada é copiada pelo modelo.

Vocabulário de poses (o mesmo em todas as direções,adaptado à vista):

- ``walk_contact_front`` / ``walk_contact_back`` — passadas abertas (contacto)
- ``walk_down`` — perna da frente a empurrar, perna de trás a levantar
- ``walk_passing`` — pernas juntas (a frames de passagem)
- ``run_contact`` / ``run_air`` — corrida
- ``jump_up`` / ``jump_fall`` — salto
- ``idle`` / ``crouch`` — paragem
- ``attack_windup`` / ``attack_strike`` — ataque
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

# ---------------------------------------------------------------------------
# Vocabulário de poses
# ---------------------------------------------------------------------------

# Textos por vista: "side" (perfil, frente/trás) e "front" (de frente, esquerda/direita).
_POSE_TEXT: dict[str, dict[str, str]] = {
    "idle": {
        "side": "standing still with both feet on the ground slightly apart, the front arm and the back arm "
        "hanging relaxed at the sides of the body",
        # Sem "facing the viewer": a vista vem da direção (north = de costas).
        "front": "standing still, both feet on the ground slightly apart, both arms hanging relaxed at the sides "
        "of the body",
    },
    "walk_contact_front": {
        "side": "the leading leg stretched far forward with the heel touching the ground, the trailing leg "
        "stretched far back with only the toe touching the ground, the front arm swung back and the back arm "
        "swung forward, a wide stride",
        "front": "the right leg stretched forward and the left leg stretched back, the right arm swung back and "
        "the left arm swung forward, a wide stride",
    },
    "walk_down": {
        "side": "the leading leg bent sharply under the body pushing off the ground, the trailing leg bent and "
        "lifted behind with the knee raised, the body lowered slightly, both arms pulled in close to the body",
        # De frente o "down" é o bob neutro entre passadas (a silhueta frontal
        # desenha-o com as pernas juntas — texto e silhueta têm de concordar).
        "front": "both legs under the body with the knees slightly bent, the body lowered slightly, both arms "
        "pulled in close to the body",
    },
    "walk_contact_back": {
        "side": "the trailing leg now stretched far forward with the heel touching the ground, the leading leg "
        "stretched far back with only the toe touching the ground, the front arm swung forward and the back arm "
        "swung back, a wide stride",
        "front": "the left leg stretched forward and the right leg stretched back, the left arm swung back and "
        "the right arm swung forward, a wide stride",
    },
    "walk_passing": {
        "side": "both legs together under the body, the trailing leg vertical and just lifting off the ground, "
        "the leading leg vertical and about to step forward, both arms hanging close to the body",
        "front": "both legs together under the body, one foot vertical and just lifting off the ground, the other "
        "foot vertical and about to step forward, both arms hanging close to the body",
    },
    "run_contact": {
        "side": "a running stride with the leading leg stretched far forward, the trailing leg bent back with the "
        "heel raised high, the torso leaning forward, the front arm swung forward and the back arm swung back",
        "front": "a running stride with the right leg stretched far forward, the left leg bent back, the torso "
        "leaning forward, the arms pumping in opposite directions",
    },
    "run_air": {
        "side": "both feet off the ground in mid-air, the leading knee raised high, the trailing leg stretched "
        "back, the torso leaning forward, both arms bent in a running swing",
        "front": "both feet off the ground in mid-air, the right knee raised high, the left leg stretched back, "
        "the torso leaning forward, both arms bent in a running swing",
    },
    "jump_up": {
        "side": "both feet off the ground, the knees tucked up, both arms raised above the head",
        "front": "both feet off the ground, the knees tucked up, both arms raised above the head",
    },
    "jump_fall": {
        "side": "both feet off the ground, the legs extended downward and slightly apart, both arms spread out to "
        "the sides",
        "front": "both feet off the ground, the legs extended downward and slightly apart, both arms spread out "
        "to the sides",
    },
    "crouch": {
        "side": "crouched low with the knees bent and the torso leaning forward, both arms lowered",
        "front": "crouched low with the knees bent and the torso leaning forward, both arms lowered",
    },
    "attack_ready": {
        "side": "standing in a fighting stance, the weapon held forward in both arms, one leg forward and the "
        "other braced behind, the knees slightly bent",
        "front": "standing in a fighting stance facing forward, the weapon held in front of the body, the feet "
        "apart, the knees slightly bent",
    },
    "attack_windup": {
        "side": "the weapon raised high above the head with both arms up, the body leaning back, one leg forward "
        "and the other braced behind",
        "front": "the weapon raised high above the head with both arms up, the feet apart and braced",
    },
    "attack_strike": {
        "side": "the weapon swung down and forward, the front arm extended, the back arm pulled back, the body "
        "leaning forward, the legs in a wide stance",
        "front": "the weapon swung down and forward, the right arm extended, the left arm pulled back, the body "
        "leaning forward, the legs in a wide stance",
    },
    "attack_follow": {
        "side": "the weapon swept low past the body after the strike, the arms extended down and forward, the "
        "front knee bent deeply, the back leg stretched behind",
        "front": "the weapon swept low across the body after the strike, the arms extended down, the knees bent",
    },
    "idle_breathe": {
        "side": "standing still breathing out, the shoulders and chest slightly lowered, the knees very slightly "
        "bent, both arms hanging relaxed at the sides",
        "front": "standing still breathing out, the shoulders slightly lowered, the knees very slightly bent, both "
        "arms hanging relaxed at the sides",
    },
    "run_contact_back": {
        "side": "a running stride with the trailing leg now stretched far forward, the leading leg bent back with "
        "the heel raised high, the torso leaning forward, the front arm swung back and the back arm swung forward",
        "front": "a running stride with the left leg stretched far forward, the right leg bent back, the torso "
        "leaning forward, the arms pumping in opposite directions",
    },
    "run_air_back": {
        "side": "both feet off the ground in mid-air, the trailing knee now raised high, the leading leg "
        "stretched back, the torso leaning forward, both arms bent in a running swing",
        "front": "both feet off the ground in mid-air, the left knee raised high, the right leg stretched back, "
        "the torso leaning forward, both arms bent in a running swing",
    },
}

# Ordem canónica de um ciclo de caminhada (4 frames): contacto → passagem →
# contacto da outra perna → passagem com a perna dobrada. Alternar passada
# aberta e pernas juntas é o que dá o "andar" no loop (contacto→down→contacto
# deixava 3 frames de passada aberta seguidos e o GIF parecia parado).
WALK_CYCLE: tuple[str, ...] = ("walk_contact_front", "walk_passing", "walk_contact_back", "walk_down")

# Ordem canónica de um ciclo de corrida (4 frames) — alterna a perna da frente
# (a v1 repetia run_contact/run_air com a mesma perna e o loop "coxeava").
RUN_CYCLE: tuple[str, ...] = ("run_contact", "run_air", "run_contact_back", "run_air_back")

# Direções → (vista, texto de direção, espelha?). Vistas: "side" (perfil),
# "front" (de frente), "back" (de costas), "diag_front"/"diag_back" (três
# quartos). Cada vista tem a sua silhueta; o texto de pose usa side/front.
DIRECTIONS: dict[str, dict[str, Any]] = {
    "south": {"view": "front", "text": "facing the viewer, front view", "mirror": False},
    "south_east": {
        "view": "diag_front",
        "text": "seen from the front-right three-quarter angle, walking toward the lower right corner of the frame",
        "mirror": False,
    },
    "east": {
        "view": "side",
        "text": "in full right-side profile facing the right edge of the frame, walking to the right",
        "mirror": False,
    },
    "north_east": {
        "view": "diag_back",
        "text": "seen from behind at a three-quarter angle, walking toward the upper right corner of the frame",
        "mirror": False,
    },
    "north": {
        "view": "back",
        "text": "seen from directly behind, back view, walking away from the viewer",
        "mirror": False,
    },
    "north_west": {
        "view": "diag_back",
        "text": "seen from behind at a three-quarter angle, walking toward the upper left corner of the frame",
        "mirror": True,
    },
    "west": {
        "view": "side",
        "text": "in full left-side profile facing the left edge of the frame, walking to the left",
        "mirror": True,
    },
    "south_west": {
        "view": "diag_front",
        "text": "seen from the front-left three-quarter angle, walking toward the lower left corner of the frame",
        "mirror": True,
    },
}

# Ângulos (graus) por pose para a silhueta procedural: perna da frente, perna de
# trás, braço da frente, braço de trás. Positivo = para a frente (direita).
_POSE_ANGLES: dict[str, tuple[float, float, float, float]] = {
    "idle": (4, -4, -3, 3),
    "walk_contact_front": (32, -28, -25, 25),
    "walk_down": (12, -34, -10, 12),
    "walk_contact_back": (-28, 32, 25, -25),
    "walk_passing": (4, -6, -4, 6),
    "run_contact": (44, -38, 30, -30),
    "run_air": (30, -46, 26, -26),
    "jump_up": (24, -24, 55, -55),
    "jump_fall": (12, -12, 40, -40),
    "crouch": (22, -22, -18, 18),
    "attack_ready": (20, -20, 30, 30),
    "attack_windup": (18, -18, 160, 160),
    "attack_strike": (26, -20, 70, -20),
    "attack_follow": (34, -30, 40, 10),
    "idle_breathe": (5, -5, -4, 4),
    "run_contact_back": (-38, 44, -30, 30),
    "run_air_back": (-46, 30, -26, 26),
}

# Forma por pose: (agachamento, elevação) em fração da altura. O agachamento
# baixa o tronco e encolhe as pernas; a elevação tira os pés do chão (salto,
# fase aérea da corrida) — o pós-processamento repõe essa altura depois de
# alinhar os pés, senão o salto ficava colado ao chão.
_POSE_SHAPE: dict[str, tuple[float, float]] = {
    "crouch": (0.1, 0.0),
    "jump_up": (0.05, 0.2),
    "jump_fall": (0.0, 0.12),
    "run_air": (0.0, 0.06),
    "run_air_back": (0.0, 0.06),
    "walk_down": (0.02, 0.0),
    "idle_breathe": (0.015, 0.0),
    "attack_ready": (0.03, 0.0),
    "attack_strike": (0.04, 0.0),
    "attack_follow": (0.07, 0.0),
}

# Vista → vocabulário de texto (as diagonais falam de perfil, as costas como de frente).
_TEXT_VIEW: dict[str, str] = {
    "side": "side",
    "front": "front",
    "back": "front",
    "diag_front": "side",
    "diag_back": "side",
}


def pose_names() -> list[str]:
    """Nomes de pose disponíveis (para ``--help`` e validação)."""
    return sorted(_POSE_TEXT)


def pose_prompt(pose: str, *, view: str = "side") -> str:
    """Frase de pose (pernas/braços) para o prompt.

    Args:
        pose: Nome da pose (ver :func:`pose_names`).
        view: Vista (``side``, ``front``, ``back``, ``diag_front``,
            ``diag_back``) — as diagonais usam o texto de perfil e as costas o
            de frente.

    Raises:
        KeyError: Pose desconhecida.
    """
    entry = _POSE_TEXT[pose]
    return entry.get(_TEXT_VIEW.get(view, view)) or entry["side"]


def pose_lift(pose: str) -> float:
    """Elevação dos pés (fração da altura da personagem) — 0 para poses no chão."""
    return _POSE_SHAPE.get(pose, (0.0, 0.0))[1]


def direction_prompt(direction: str) -> str:
    """Texto de direção (vista + para onde anda) para o prompt."""
    return str(DIRECTIONS[direction]["text"])


def facing_prompt(direction: str) -> str:
    """Só a vista da direção, sem "walking ..." — para frames parados.

    A identidade do ``walk-cycle`` tem de sair **parada**: com o texto de
    direção completo ("..., walking to the right") o modelo desenha-a a meio da
    passada e depois copia essa pose para todos os frames, ignorando as
    silhuetas.
    """
    return direction_prompt(direction).split(", walking")[0]


def is_mirrored(direction: str) -> bool:
    """A direção é o espelho de outra (W é espelho de E, etc.)?"""
    return bool(DIRECTIONS[direction].get("mirror"))


# Pares de direções espelhadas (o valor é a direção canónica do par).
_MIRROR_PAIRS: dict[str, str] = {
    "west": "east",
    "north_west": "north_east",
    "south_west": "south_east",
}


def canonical_direction(direction: str) -> str:
    """Direção "de origem" de um set: as espelhadas partem da canónica.

    ``west`` -> ``east``, ``north_west`` -> ``north_east``,
    ``south_west`` -> ``south_east``. Gerar a canónica e espelhar dá a
    **mesma personagem** nos dois sets (truque clássico de sprite 2D).
    """
    return _MIRROR_PAIRS.get(direction, direction)


def direction_view(direction: str) -> str:
    """Vista da direção (``side``/``front``/``back``/``diag_front``/``diag_back``)."""
    return str(DIRECTIONS[direction]["view"])


def build_prompt(
    character: str,
    *,
    direction: str,
    pose: str,
    extra: str = "",
) -> str:
    """Monta o prompt completo de um frame: personagem + direção + pose.

    A ordem é propositada: a descrição do **alvo** (quem é, de que vista, em
    que pose) vem antes das restrições de estilo.
    """
    view = direction_view(direction)
    parts = [
        character.strip().rstrip("."),
        direction_prompt(direction),
        pose_prompt(pose, view=view),
    ]
    if extra.strip():
        parts.append(extra.strip().rstrip("."))
    return ", ".join(p for p in parts if p)


def walk_cycle_prompts(
    character: str,
    *,
    direction: str,
    poses: tuple[str, ...] = WALK_CYCLE,
    extra: str = "",
) -> list[str]:
    """Prompts de um ciclo (um por frame, com a pose certa)."""
    return [build_prompt(character, direction=direction, pose=pose, extra=extra) for pose in poses]


# ---------------------------------------------------------------------------
# Silhueta procedural (referência de forma)
# ---------------------------------------------------------------------------


def draw_pose_silhouette(pose: str, *, view: str = "side", size: int = 256) -> Image.Image:
    """Desenha a silhueta da pose (branco/cinzento sobre transparente).

    Serve de **referência de forma**: o modelo copia a pose desenhada. Regras
    por vista (todas validadas E2E na 4050):

    - ``side``: membros pelos ângulos de :data:`_POSE_ANGLES`; os do lado de
      **trás** a cinzento e atrás do tronco (sem essa pista os dois contactos
      de perfil são silhuetas iguais e o modelo não troca a perna).
    - ``front`` / ``back``: pernas **verticais** — pernas em "V" lateral faziam
      o modelo virar a personagem de perfil. A passada lê-se no comprimento: de
      frente o pé da frente fica mais abaixo (mais perto); de costas o pé da
      frente afasta-se (sobe) e o de trás mostra a sola.
    - ``diag_front`` / ``diag_back``: ângulos de perfil atenuados (a passada
      vai em profundidade) mais o encurtamento de frente/costas; a silhueta de
      perfil puro fazia frames da diagonal saírem de perfil.

    Args:
        pose: Nome da pose.
        view: Vista (ver :data:`DIRECTIONS`).
        size: Lado da imagem em pixels.

    Raises:
        KeyError: Pose desconhecida.
    """
    if pose not in _POSE_ANGLES:
        raise KeyError(f"pose desconhecida: {pose!r} (disponíveis: {', '.join(pose_names())})")
    front_leg, back_leg, front_arm, back_arm = _POSE_ANGLES[pose]
    squash, lift = _POSE_SHAPE.get(pose, (0.0, 0.0))

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    far = (150, 150, 150, 255)
    cx = size * 0.5
    hip_y = size * (0.56 + squash - lift)
    torso_len = size * 0.22
    head_r = size * 0.085
    leg_len = size * 0.28 * (1.0 - 2.2 * squash)
    arm_len = size * 0.2
    leg_w = max(2, int(size * 0.05))
    arm_w = max(2, int(size * 0.042))
    sh_y = hip_y - torso_len * 0.85
    torso_half = size * {"side": 0.085, "diag_front": 0.075, "diag_back": 0.075}.get(view, 0.085)

    def limb(x: float, y: float, angle_deg: float, length: float, width: int, fill: tuple[int, ...]) -> None:
        a = math.radians(angle_deg)
        d.line([(x, y), (x + math.sin(a) * length, y + math.cos(a) * length)], fill=fill, width=width)

    def foot(x: float, y: float, r: float, fill: tuple[int, ...]) -> None:
        d.ellipse([x - r, y - r * 0.7, x + r, y + r * 0.7], fill=fill)

    def torso_and_head() -> None:
        d.rounded_rectangle(
            [cx - torso_half, hip_y - torso_len, cx + torso_half, hip_y], radius=size * 0.03, fill=white
        )
        head_cy = sh_y - head_r * 0.85
        d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=white)
        d.rounded_rectangle(
            [cx - head_r * 1.05, head_cy - head_r * 1.3, cx + head_r * 1.1, head_cy - head_r * 0.3],
            radius=size * 0.02,
            fill=white,
        )

    if view in ("front", "back"):
        # Passada -1..1: >0 = perna direita (do observador) à frente. Só a perna
        # da frente conta: walk_down/passing ficam neutras → ciclo D, N, E, N.
        stride = max(-1.0, min(1.0, front_leg / 30.0))
        if abs(stride) < 0.5:
            stride = 0.0
        for side, sign in ((1, 1.0), (-1, -1.0)):
            ahead = stride * sign  # >0 esta perna à frente
            if view == "front":
                scale = 1.0 + 0.08 * max(ahead, 0.0) - 0.28 * max(-ahead, 0.0)
                r = size * (0.035 + 0.01 * max(ahead, 0.0))
            else:
                scale = 1.0 - 0.24 * max(ahead, 0.0) + 0.04 * max(-ahead, 0.0)
                r = size * (0.035 + 0.014 * max(-ahead, 0.0))  # sola do pé de trás à vista
            x = cx + side * size * 0.045
            length = leg_len * scale
            d.line([(x, hip_y), (x, hip_y + length)], fill=white, width=int(leg_w * 1.1))
            foot(x, hip_y + length, r, white)
        torso_and_head()
        swinging = pose.startswith(("idle", "walk", "run"))
        for side, sign in ((1, 1.0), (-1, -1.0)):
            x = cx + side * size * 0.1
            if swinging:
                # Braço oposto à perna da frente vem à frente → encurta (perspetiva).
                length = arm_len * (1.0 - 0.18 * max(-stride * sign, 0.0))
                angle = side * 8.0
            else:
                length, angle = arm_len, side * float(front_arm)
            limb(x, sh_y, angle, length, arm_w, white)
        return img

    if view in ("diag_front", "diag_back"):
        # Passada em profundidade: o componente lateral encolhe e a perna que vai
        # à frente alonga (vem para o observador) ou encurta (afasta-se).
        k = 0.22 if view == "diag_front" else -0.22
        lateral = 0.55

        def diag_leg(x: float, angle: float, fill: tuple[int, ...]) -> None:
            length = leg_len * (1.0 + k * math.sin(math.radians(angle)))
            a = math.radians(angle * lateral)
            ex, ey = x + math.sin(a) * length, hip_y + math.cos(a) * length
            d.line([(x, hip_y), (ex, ey)], fill=fill, width=leg_w)
            foot(ex, ey, size * 0.032, fill)

        diag_leg(cx + size * 0.03, back_leg, far)
        limb(cx - size * 0.05, sh_y, back_arm * lateral, arm_len, arm_w, far)
        diag_leg(cx - size * 0.03, front_leg, white)
        torso_and_head()
        limb(cx + size * 0.05, sh_y, front_arm * lateral, arm_len, arm_w, white)
        return img

    # Perfil: lado de trás primeiro (fica tapado pelo tronco).
    limb(cx + size * 0.018, hip_y, back_leg, leg_len, leg_w, far)
    limb(cx - size * 0.022, sh_y, back_arm, arm_len, arm_w, far)
    limb(cx - size * 0.018, hip_y, front_leg, leg_len, leg_w, white)
    torso_and_head()
    limb(cx + size * 0.022, sh_y, front_arm, arm_len, arm_w, white)
    return img


def draw_pose_sheet(
    poses: tuple[str, ...] | list[str],
    *,
    view: str = "side",
    cell: tuple[int, int] = (256, 320),
) -> Image.Image:
    """Folha horizontal de silhuetas (uma célula por pose) sobre fundo preto.

    É a referência de forma do ``walk-cycle``: gerar **todos os frames numa só
    imagem** com esta folha como guia dá a mesma personagem, escala e linha do
    chão em todas as células (o que frames gerados em separado não garantem).

    Args:
        poses: Poses, da esquerda para a direita.
        view: ``"side"`` ou ``"front"``.
        cell: ``(largura, altura)`` de cada célula em pixels.

    Raises:
        KeyError: Pose desconhecida.
    """
    cell_w, cell_h = cell
    sheet = Image.new("RGB", (cell_w * len(poses), cell_h), (0, 0, 0))
    for i, pose in enumerate(poses):
        sil = draw_pose_silhouette(pose, view=view, size=cell_h)
        sheet.paste(sil, (i * cell_w + (cell_w - cell_h) // 2, 0), sil)
    return sheet


def save_pose_silhouette(pose: str, path: str | Path, *, view: str = "side", size: int = 256) -> Path:
    """Desenha a silhueta e grava-a em PNG (usável como ``--ref``)."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    draw_pose_silhouette(pose, view=view, size=size).save(out, format="PNG")
    return out


# ---------------------------------------------------------------------------
# Métrica de variação (validar que os frames são diferentes)
# ---------------------------------------------------------------------------


def alpha_difference(a: Image.Image, b: Image.Image) -> float:
    """Fração de pixels com alpha diferente entre duas silhuetas (0 = iguais).

    Métrica objetiva de "o frame realmente mudou de pose" — usada pelo
    ``walk-cycle --measure`` e pelos testes.
    """
    from PIL import ImageChops

    alpha_a = a.convert("RGBA").getchannel("A")
    alpha_b = b.convert("RGBA").getchannel("A")
    if alpha_a.size != alpha_b.size:
        alpha_b = alpha_b.resize(alpha_a.size)
    diff = ImageChops.difference(alpha_a, alpha_b)
    changed = sum(diff.histogram()[1:])
    return changed / float(alpha_a.size[0] * alpha_a.size[1])
