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
        "front": "standing still facing the viewer, both feet on the ground slightly apart, both arms hanging "
        "relaxed at the sides of the body",
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
        "front": "the right leg bent under the body pushing off the ground, the left leg bent and lifted with the "
        "knee raised, the body lowered slightly, both arms pulled in close to the body",
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
    "attack_windup": {
        "side": "the sword raised high above the head with both arms up, the body turned sideways, one leg "
        "forward and the other braced behind",
        "front": "the sword raised high above the head with both arms up, the body turned sideways, one leg "
        "forward and the other braced behind",
    },
    "attack_strike": {
        "side": "the sword swung down and forward, the front arm extended, the back arm pulled back, the body "
        "leaning forward, the legs in a wide stance",
        "front": "the sword swung down and forward, the right arm extended, the left arm pulled back, the body "
        "leaning forward, the legs in a wide stance",
    },
}

# Ordem canónica de um ciclo de caminhada (4 frames).
WALK_CYCLE: tuple[str, ...] = ("walk_contact_front", "walk_down", "walk_contact_back", "walk_passing")

# Ordem canónica de um ciclo de corrida (4 frames).
RUN_CYCLE: tuple[str, ...] = ("run_contact", "run_air", "run_contact", "run_air")

# Direções → (vista, texto de direção, espelha?). "side" = perfil, "front" = de frente.
DIRECTIONS: dict[str, dict[str, Any]] = {
    "south": {"view": "front", "text": "facing the viewer, front view", "mirror": False},
    "south_east": {
        "view": "side",
        "text": "seen from the front-right three-quarter angle, walking toward the lower right corner of the frame",
        "mirror": False,
    },
    "east": {
        "view": "side",
        "text": "in full right-side profile facing the right edge of the frame, walking to the right",
        "mirror": False,
    },
    "north_east": {
        "view": "front",
        "text": "seen from behind at a three-quarter angle, walking toward the upper right corner of the frame",
        "mirror": False,
    },
    "north": {
        "view": "front",
        "text": "seen from directly behind, back view, walking away from the viewer",
        "mirror": False,
    },
    "north_west": {
        "view": "front",
        "text": "seen from behind at a three-quarter angle, walking toward the upper left corner of the frame",
        "mirror": True,
    },
    "west": {
        "view": "side",
        "text": "in full left-side profile facing the left edge of the frame, walking to the left",
        "mirror": True,
    },
    "south_west": {
        "view": "side",
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
    "attack_windup": (18, -18, 60, 60),
    "attack_strike": (26, -20, 35, -20),
}


def pose_names() -> list[str]:
    """Nomes de pose disponíveis (para ``--help`` e validação)."""
    return sorted(_POSE_TEXT)


def pose_prompt(pose: str, *, view: str = "side") -> str:
    """Frase de pose (pernas/braços) para o prompt.

    Args:
        pose: Nome da pose (ver :func:`pose_names`).
        view: ``"side"`` (perfil — frente/trás) ou ``"front"`` (de frente —
            esquerda/direita).

    Raises:
        KeyError: Pose desconhecida.
    """
    entry = _POSE_TEXT[pose]
    return entry.get(view) or entry["side"]


def direction_prompt(direction: str) -> str:
    """Texto de direção (vista + para onde anda) para o prompt."""
    return str(DIRECTIONS[direction]["text"])


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
    """``"side"`` ou ``"front"`` — que vocabulário de pose usar."""
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
    """Desenha a silhueta da pose (branco sobre transparente).

    Serve de **referência de forma**: o modelo copia a pose desenhada (validado
    E2E — as pernas do sprite seguem os ângulos aqui definidos).

    Args:
        pose: Nome da pose.
        view: ``"side"`` (perfil) ou ``"front"`` (de frente).
        size: Lado da imagem em pixels.

    Raises:
        KeyError: Pose desconhecida.
    """
    if pose not in _POSE_ANGLES:
        raise KeyError(f"pose desconhecida: {pose!r} (disponíveis: {', '.join(pose_names())})")
    front_leg, back_leg, front_arm, back_arm = _POSE_ANGLES[pose]
    if view == "front":
        # De frente: pernas simétricas em torno da vertical (o "leading" vai para
        # a direita do observador).
        front_leg, back_leg = front_leg, -front_leg
        front_arm, back_arm = front_arm, -front_arm

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    white = (255, 255, 255, 255)
    cx = size * 0.5
    hip_y = size * 0.56
    torso_len = size * 0.22
    head_r = size * 0.085

    def limb(x: float, y: float, angle_deg: float, length: float, width: int) -> tuple[float, float]:
        a = math.radians(angle_deg)
        ex, ey = x + math.sin(a) * length, y + math.cos(a) * length
        d.line([(x, y), (ex, ey)], fill=white, width=width)
        return ex, ey

    leg_w = max(2, int(size * 0.05))
    arm_w = max(2, int(size * 0.042))
    # Pernas (do quadril para baixo).
    limb(cx - size * 0.018, hip_y, front_leg, size * 0.28, leg_w)
    limb(cx + size * 0.018, hip_y, back_leg, size * 0.28, leg_w)
    # Tronco.
    d.rounded_rectangle(
        [cx - size * 0.085, hip_y - torso_len, cx + size * 0.085, hip_y],
        radius=size * 0.03,
        fill=white,
    )
    # Braços (dos ombros).
    sh_y = hip_y - torso_len * 0.85
    limb(cx + size * 0.022, sh_y, front_arm, size * 0.2, arm_w)
    limb(cx - size * 0.022, sh_y, back_arm, size * 0.2, arm_w)
    # Cabeça + elmo.
    head_cy = sh_y - head_r * 0.85
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=white)
    d.rounded_rectangle(
        [cx - head_r * 1.05, head_cy - head_r * 1.3, cx + head_r * 1.1, head_cy - head_r * 0.3],
        radius=size * 0.02,
        fill=white,
    )
    return img


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
