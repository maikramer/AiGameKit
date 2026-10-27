"""Catálogo de animações 2D (bípedes e quadrúpedes) para ``text2d animate``.

Cada animação define o corpo, as fases (poses, uma por frame) e o limiar de
variação abaixo do qual os frames são considerados cópias. O desenho das
silhuetas e o texto de cada pose vêm de :mod:`text2d.poses` (bípedes) e
:mod:`text2d.quadruped`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PIL import Image

from .poses import (
    RUN_CYCLE,
    SNEAK_CYCLE,
    WALK_CYCLE,
    WALK_CYCLE_8,
    direction_view,
    draw_pose_sheet,
    pose_lift,
    pose_prompt,
)
from .quadruped import (
    QUAD_GALLOP_CYCLE,
    QUAD_TROT_CYCLE,
    QUAD_WALK_CYCLE,
    draw_quad_sheet,
    quad_pose_lift,
    quad_pose_prompt,
)


@dataclass(frozen=True)
class Animation:
    """Uma animação: corpo + fases + como a descrever no prompt.

    Attributes:
        name: Chave CLI (``--anim``).
        body: ``"biped"`` ou ``"quadruped"``.
        poses: Fases do ciclo (repetidas se ``--frames`` for maior).
        label: Nome da animação no prompt ("walk cycle", "jump").
        min_variation: Variação média mínima por vista antes do aviso de
            "frames iguais" (0 = sem aviso; idle varia pouco de propósito).
        loop: Ciclo contínuo (andar) vs. sequência (salto) — a métrica fecha o
            loop e o ``anim.json`` diz ao motor se repete.
        holds: Multiplicador da duração de cada fase (``frame_ms`` x hold).
            Ataques vivem do contraste: antecipação longa, golpe curto, pose
            final segurada. Vazio = todas iguais.
    """

    name: str
    body: str
    poses: tuple[str, ...]
    label: str
    min_variation: dict[str, float] = field(default_factory=dict)
    loop: bool = True
    holds: tuple[float, ...] = ()

    def durations(self, frames: int, frame_ms: int) -> list[int]:
        """Duração (ms) de cada frame — ``holds`` repetem com as fases do ciclo.

        Arredonda a 10 ms: o GIF guarda centésimos de segundo.
        """
        holds = self.holds or (1.0,)
        return [max(20, round(frame_ms * holds[i % len(holds)] / 10) * 10) for i in range(frames)]


# Limiares medidos (a 96 px, após alinhar): perfil cópia 2.8% / bom 6-12%;
# frente cópia 1.5% / bom 4.6%. As diagonais e as costas ficam entre os dois.
_WALK_FLOOR = {"side": 0.04, "diag_front": 0.035, "diag_back": 0.035, "front": 0.03, "back": 0.03}

ANIMATIONS: dict[str, Animation] = {
    "walk": Animation("walk", "biped", WALK_CYCLE, "walk cycle", _WALK_FLOOR),
    # 8 fases numa grelha 2x4 (uma só geração): contacto/baixo/passagem/cima
    # por passada. Frames consecutivos mudam menos → limiar a metade.
    "walk8": Animation(
        "walk8", "biped", WALK_CYCLE_8, "smooth 8-frame walk cycle", {k: v / 2 for k, v in _WALK_FLOOR.items()}
    ),
    "run": Animation("run", "biped", RUN_CYCLE, "run cycle", _WALK_FLOOR),
    "idle": Animation("idle", "biped", ("idle", "idle_breathe"), "idle breathing loop", {}),
    "jump": Animation("jump", "biped", ("crouch", "jump_up", "jump_fall", "crouch"), "jump", _WALK_FLOOR, loop=False),
    "attack": Animation(
        "attack",
        "biped",
        ("attack_ready", "attack_windup", "attack_strike", "attack_follow"),
        "melee weapon attack",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1.6, 0.6, 1.2),
    ),
    "hurt": Animation(
        "hurt",
        "biped",
        ("idle", "hurt", "hurt", "idle"),
        "hit reaction",
        _WALK_FLOOR,
        loop=False,
        holds=(0.6, 1, 1.4, 1),
    ),
    "death": Animation(
        "death",
        "biped",
        ("death_hit", "death_kneel", "death_fall", "death_lie"),
        "death collapse",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1, 0.8, 4),
    ),
    "cast": Animation(
        "cast",
        "biped",
        ("cast_gather", "cast_raise", "cast_release", "cast_follow"),
        "magic spell casting",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1.6, 0.8, 1.2),
    ),
    "block": Animation(
        "block",
        "biped",
        ("attack_ready", "block_guard", "block_impact", "block_guard"),
        "shield block",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1, 0.8, 1.4),
    ),
    "roll": Animation(
        "roll",
        "biped",
        ("crouch", "roll_tuck", "roll_ball", "roll_rise"),
        "forward dodge roll",
        _WALK_FLOOR,
        loop=False,
        holds=(0.8, 0.7, 0.7, 1.2),
    ),
    "shoot": Animation(
        "shoot",
        "biped",
        ("shoot_nock", "shoot_draw", "shoot_release", "shoot_recover"),
        "archery bow shot",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1.8, 0.6, 1.2),
    ),
    "kick": Animation(
        "kick",
        "biped",
        ("punch_guard", "kick_chamber", "kick_extend", "kick_recover"),
        "martial arts kick",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1, 0.8, 1),
    ),
    "punch": Animation(
        "punch",
        "biped",
        ("punch_guard", "punch_jab", "punch_guard", "punch_cross"),
        "boxing punch combo",
        _WALK_FLOOR,
        holds=(1, 0.6, 1, 0.8),
    ),
    "victory": Animation(
        "victory",
        "biped",
        ("idle", "victory_fist", "victory_jump", "victory_fist"),
        "victory celebration",
        _WALK_FLOOR,
        loop=False,
        holds=(1, 1, 1, 2),
    ),
    "sneak": Animation("sneak", "biped", SNEAK_CYCLE, "sneaking crouch walk cycle", _WALK_FLOOR, holds=(1.2,)),
    "quad-walk": Animation("quad-walk", "quadruped", QUAD_WALK_CYCLE, "four-legged walk cycle", {"side": 0.03}),
    "quad-trot": Animation("quad-trot", "quadruped", QUAD_TROT_CYCLE, "four-legged trot cycle", {"side": 0.03}),
    "quad-gallop": Animation("quad-gallop", "quadruped", QUAD_GALLOP_CYCLE, "four-legged gallop cycle", {"side": 0.04}),
    # Idle de 4 fases (a v2.1 tinha 1 e foi removido): olhar em volta e farejar.
    "quad-idle": Animation(
        "quad-idle",
        "quadruped",
        ("quad_idle", "quad_sniff", "quad_idle", "quad_alert"),
        "four-legged idle, sniffing and looking around",
        {},
        holds=(1.5, 2, 1, 2),
    ),
    "quad-attack": Animation(
        "quad-attack",
        "quadruped",
        ("quad_crouch", "quad_pounce", "quad_bite", "quad_recover"),
        "four-legged pounce and bite attack",
        {"side": 0.04},
        loop=False,
        holds=(1.6, 0.8, 0.8, 1.2),
    ),
    "quad-jump": Animation(
        "quad-jump",
        "quadruped",
        ("quad_crouch", "quad_leap", "quad_land", "quad_crouch"),
        "four-legged jump",
        {"side": 0.04},
        loop=False,
    ),
    "quad-hurt": Animation(
        "quad-hurt",
        "quadruped",
        ("quad_idle", "quad_hurt", "quad_hurt", "quad_idle"),
        "four-legged hit reaction",
        {"side": 0.03},
        loop=False,
        holds=(0.6, 1, 1.4, 1),
    ),
}

# Vistas suportadas por corpo (quadrúpedes: só perfil — ver text2d.quadruped).
_BODY_VIEWS: dict[str, tuple[str, ...]] = {
    "biped": ("side", "front", "back", "diag_front", "diag_back"),
    "quadruped": ("side",),
}


def get_animation(name: str) -> Animation:
    """Animação pelo nome.

    Raises:
        KeyError: Nome desconhecido (a mensagem lista os disponíveis).
    """
    try:
        return ANIMATIONS[name]
    except KeyError:
        raise KeyError(f"animação desconhecida: {name!r} (disponíveis: {', '.join(ANIMATIONS)})") from None


def check_direction(anim: Animation, direction: str) -> None:
    """Valida que a direção tem vista suportada pelo corpo da animação.

    Raises:
        ValueError: Ex.: quadrúpede de frente.
    """
    view = direction_view(direction)
    if view not in _BODY_VIEWS[anim.body]:
        raise ValueError(
            f"'{anim.name}' ({anim.body}) só suporta as direções de perfil (east/west); recebido {direction!r}"
        )


def cell_size(anim: Animation, size: int) -> tuple[int, int]:
    """Célula ``(largura, altura)`` múltipla de 16 por corpo.

    Bípede: 4:5 de pé (``size`` = altura). Quadrúpede: 5:4 deitado (largura =
    ``size``) — 4 células dão 1280x256, o mesmo nº de pixels de 1024x320.
    """
    if anim.body == "quadruped":
        height = max(64, round(size * 0.8 / 16) * 16)
        return max(80, round(height * 1.25 / 16) * 16), height
    height = max(64, (size // 16) * 16)
    return max(48, round(height * 0.8 / 16) * 16), height


def phase_prompt(anim: Animation, pose: str, *, view: str) -> str:
    """Texto da pose de uma fase (por corpo)."""
    return quad_pose_prompt(pose) if anim.body == "quadruped" else pose_prompt(pose, view=view)


def phase_lift(anim: Animation, pose: str) -> float:
    """Elevação dos pés da fase (fração da altura)."""
    return quad_pose_lift(pose) if anim.body == "quadruped" else pose_lift(pose)


def draw_guide(anim: Animation, poses: list[str], *, view: str, cell: tuple[int, int], cols: int = 4) -> Image.Image:
    """Folha de silhuetas da animação (referência de forma), em grelha de ``cols``."""
    if anim.body == "quadruped":
        return draw_quad_sheet(poses, cell=cell, cols=cols)
    return draw_pose_sheet(poses, view=view, cell=cell, cols=cols)
