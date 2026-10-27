"""Biblioteca de poses bípedes para animação 2D — texto + silhueta articulada.

O FLUX.3 Image Klein **não** tem pose conditioning: a pose vem da *forma* de
uma silhueta desenhada (o workflow ``image_conditioned`` copia-a) e o texto
de cada fase confirma-a. Ver ``docs/findings/TEXT2D_FLUX3_KLEIN_MIGRATION.md``.

Cada pose (:class:`PoseSpec`) define, para o lado **near** (do observador,
branco) e **far** (cinzento, atrás do corpo): ângulo da coxa/braço a partir da
vertical (positivo = para a frente), flexão do joelho/cotovelo, elevação dos
pés e inclinação do corpo inteiro (dano, queda). A silhueta é desenhada
articulada e assente no chão automaticamente — agachar é dobrar os joelhos,
não encolher pernas.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw


@dataclass(frozen=True)
class PoseSpec:
    """Uma pose bípede.

    Attributes:
        side / front: Texto da pose de perfil / de frente.
        legs: Ângulo das coxas ``(near, far)`` em graus (positivo = para a frente).
        arms: Ângulo dos braços ``(near, far)`` (90 = horizontal à frente, 180 = para cima).
        knees: Flexão dos joelhos ``(near, far)`` — a canela roda para trás.
        elbows: Flexão dos cotovelos ``(near, far)`` — o antebraço roda para a frente.
        lift: Elevação dos pés (fração da altura) — salto, fase aérea.
        tilt: Inclinação do corpo inteiro em graus (negativo = para trás).
    """

    side: str
    front: str
    legs: tuple[float, float]
    arms: tuple[float, float]
    knees: tuple[float, float] = (3.0, 3.0)
    elbows: tuple[float, float] = (10.0, 10.0)
    lift: float = 0.0
    tilt: float = 0.0


_ARMS_SWING = "the arms swinging opposite to the legs"

# Ciclo de caminhada (perfil): near = perna do lado do observador. Em cada
# passagem passa a perna que estava ATRÁS no contacto anterior — a v2 tinha o
# 4.º frame a passar a mesma perna do 2.º (o loop "arrastava" uma perna).
_POSES: dict[str, PoseSpec] = {
    "idle": PoseSpec(
        "standing still with both feet on the ground slightly apart, the front arm and the back arm hanging "
        "relaxed at the sides of the body",
        "standing still, both feet on the ground slightly apart, both arms hanging relaxed at the sides of the body",
        legs=(3, -3),
        arms=(-3, 3),
        elbows=(8, 8),
    ),
    "idle_breathe": PoseSpec(
        "standing still breathing out, the shoulders and chest slightly lowered, the knees very slightly bent, "
        "both arms hanging relaxed at the sides",
        "standing still breathing out, the shoulders slightly lowered, the knees very slightly bent, both arms "
        "hanging relaxed at the sides",
        legs=(5, -4),
        arms=(-4, 4),
        knees=(16, 16),
        elbows=(14, 14),
    ),
    "walk_contact_front": PoseSpec(
        "the leading leg stretched far forward with the heel touching the ground, the trailing leg stretched far "
        "back with only the toe touching the ground, the front arm swung back and the back arm swung forward, a "
        "wide stride",
        "the right leg stretched forward and the left leg stretched back, the right arm swung back and the left "
        "arm swung forward, a wide stride",
        legs=(30, -26),
        arms=(-24, 22),
        knees=(4, 2),
        elbows=(14, 14),
    ),
    "walk_down": PoseSpec(
        "the leading leg planted forward with the knee bent taking the weight, the trailing leg pushing off "
        f"behind with the heel raised, the body at its lowest, {_ARMS_SWING}",
        f"both knees bent with the body at its lowest, the left heel raised behind, {_ARMS_SWING}",
        legs=(18, -30),
        arms=(-18, 16),
        knees=(28, 32),
        elbows=(16, 16),
    ),
    "walk_passing": PoseSpec(
        "the supporting leg straight under the body, the other leg lifted with the knee bent passing forward "
        "next to it, both arms hanging close to the body",
        "standing on the right leg, the left leg lifted with the knee bent, both arms hanging close to the body",
        legs=(2, 8),
        arms=(-4, 4),
        knees=(4, 52),
        elbows=(14, 14),
    ),
    "walk_up": PoseSpec(
        "rising on the toes of the back leg, the other leg swinging forward with the knee bent, the body at its "
        f"highest, {_ARMS_SWING}",
        f"rising on the right toes, the left knee raised forward, the body at its highest, {_ARMS_SWING}",
        legs=(-14, 26),
        arms=(10, -12),
        knees=(0, 40),
        elbows=(14, 14),
        lift=0.015,
    ),
    "walk_contact_back": PoseSpec(
        "the trailing leg now stretched far forward with the heel touching the ground, the leading leg stretched "
        "far back with only the toe touching the ground, the front arm swung forward and the back arm swung back, "
        "a wide stride",
        "the left leg stretched forward and the right leg stretched back, the left arm swung back and the right "
        "arm swung forward, a wide stride",
        legs=(-26, 30),
        arms=(22, -24),
        knees=(2, 4),
        elbows=(14, 14),
    ),
    "walk_down_back": PoseSpec(
        "the other leg now planted forward with the knee bent taking the weight, the first leg pushing off behind "
        f"with the heel raised, the body at its lowest, {_ARMS_SWING}",
        f"both knees bent with the body at its lowest, the right heel raised behind, {_ARMS_SWING}",
        legs=(-30, 18),
        arms=(16, -18),
        knees=(32, 28),
        elbows=(16, 16),
    ),
    "walk_passing_back": PoseSpec(
        "the other leg now straight under the body supporting it, the first leg lifted with the knee bent passing "
        "forward next to it, both arms hanging close to the body",
        "standing on the left leg, the right leg lifted with the knee bent, both arms hanging close to the body",
        legs=(8, 2),
        arms=(4, -4),
        knees=(52, 4),
        elbows=(14, 14),
    ),
    "walk_up_back": PoseSpec(
        "rising on the toes of the other back leg, the first leg swinging forward with the knee bent, the body at "
        f"its highest, {_ARMS_SWING}",
        f"rising on the left toes, the right knee raised forward, the body at its highest, {_ARMS_SWING}",
        legs=(26, -14),
        arms=(-12, 10),
        knees=(40, 0),
        elbows=(14, 14),
        lift=0.015,
    ),
    "run_contact": PoseSpec(
        "a running stride with the leading leg stretched far forward, the trailing leg bent back with the heel "
        "raised high, the torso leaning forward, the front arm swung forward and the back arm swung back",
        "a running stride with the right leg stretched far forward, the left leg bent back, the torso leaning "
        "forward, the arms pumping in opposite directions",
        legs=(40, -30),
        arms=(-35, 40),
        knees=(12, 80),
        elbows=(85, 85),
    ),
    "run_air": PoseSpec(
        "both feet off the ground in mid-air, the leading knee raised high, the trailing leg stretched back, the "
        "torso leaning forward, both arms bent in a running swing",
        "both feet off the ground in mid-air, the right knee raised high, the left leg stretched back, the torso "
        "leaning forward, both arms bent in a running swing",
        legs=(45, -38),
        arms=(-30, 35),
        knees=(85, 35),
        elbows=(85, 85),
        lift=0.06,
    ),
    "run_contact_back": PoseSpec(
        "a running stride with the trailing leg now stretched far forward, the leading leg bent back with the heel "
        "raised high, the torso leaning forward, the front arm swung back and the back arm swung forward",
        "a running stride with the left leg stretched far forward, the right leg bent back, the torso leaning "
        "forward, the arms pumping in opposite directions",
        legs=(-30, 40),
        arms=(40, -35),
        knees=(80, 12),
        elbows=(85, 85),
    ),
    "run_air_back": PoseSpec(
        "both feet off the ground in mid-air, the trailing knee now raised high, the leading leg stretched back, "
        "the torso leaning forward, both arms bent in a running swing",
        "both feet off the ground in mid-air, the left knee raised high, the right leg stretched back, the torso "
        "leaning forward, both arms bent in a running swing",
        legs=(-38, 45),
        arms=(35, -30),
        knees=(35, 85),
        elbows=(85, 85),
        lift=0.06,
    ),
    "crouch": PoseSpec(
        "crouched low with both knees deeply bent and the torso leaning forward, both arms lowered in front",
        "crouched low with both knees deeply bent, both arms lowered in front",
        legs=(55, 40),
        arms=(30, 20),
        knees=(115, 105),
        elbows=(30, 30),
    ),
    "jump_up": PoseSpec(
        "both feet off the ground, the knees tucked up high, both arms raised above the head",
        "both feet off the ground, the knees tucked up high, both arms raised above the head",
        legs=(45, 30),
        arms=(160, 150),
        knees=(110, 100),
        elbows=(10, 10),
        lift=0.2,
    ),
    "jump_fall": PoseSpec(
        "both feet off the ground falling, the legs extended downward and slightly apart, both arms spread up and "
        "out to the sides",
        "both feet off the ground falling, the legs extended downward and slightly apart, both arms spread up and "
        "out to the sides",
        legs=(12, -8),
        arms=(125, 115),
        knees=(15, 30),
        elbows=(20, 20),
        lift=0.12,
    ),
    "attack_ready": PoseSpec(
        "standing in a fighting stance, the weapon held forward in both arms, one leg forward and the other "
        "braced behind, the knees slightly bent",
        "standing in a fighting stance facing forward, the weapon held in front of the body, the feet apart, the "
        "knees slightly bent",
        legs=(22, -20),
        arms=(40, 30),
        knees=(18, 12),
        elbows=(60, 60),
    ),
    "attack_windup": PoseSpec(
        "the weapon raised high above the head with both arms up, the body leaning back, one leg forward and the "
        "other braced behind",
        "the weapon raised high above the head with both arms up, the feet apart and braced",
        legs=(20, -22),
        arms=(165, 160),
        knees=(10, 10),
        elbows=(30, 30),
        tilt=-6,
    ),
    "attack_strike": PoseSpec(
        "the weapon swung down and forward, the front arm extended, the back arm pulled back, the body leaning "
        "forward, the legs in a wide stance",
        "the weapon swung down and forward, the right arm extended, the left arm pulled back, the body leaning "
        "forward, the legs in a wide stance",
        legs=(32, -28),
        arms=(80, 30),
        knees=(25, 5),
        elbows=(10, 40),
        tilt=8,
    ),
    "attack_follow": PoseSpec(
        "the weapon swept low past the body after the strike, the arms extended down and forward, the front knee "
        "bent deeply, the back leg stretched behind",
        "the weapon swept low across the body after the strike, the arms extended down, the knees bent",
        legs=(42, -35),
        arms=(40, 20),
        knees=(50, 5),
        elbows=(10, 20),
        tilt=12,
    ),
    "hurt": PoseSpec(
        "recoiling from a hit, the head and torso thrown back, both arms flung back and out, the knees buckling",
        "recoiling from a hit, the head thrown back, both arms flung out to the sides, the knees buckling",
        legs=(10, -14),
        arms=(-45, -35),
        knees=(25, 15),
        elbows=(30, 30),
        tilt=-14,
    ),
    "death_kneel": PoseSpec(
        "collapsing onto both knees, the torso slumped forward, both arms hanging limp",
        "collapsing onto both knees, the torso slumped forward, both arms hanging limp",
        legs=(70, 50),
        arms=(15, 10),
        knees=(140, 130),
        elbows=(15, 15),
        tilt=10,
    ),
    "death_fall": PoseSpec(
        "falling over backwards defeated, the body tipping back, both legs giving way, the arms thrown up",
        "falling over defeated, the body tipping to the side, both legs giving way, the arms thrown up",
        legs=(35, 15),
        arms=(-70, -50),
        knees=(45, 45),
        elbows=(20, 20),
        tilt=-55,
    ),
    "death_lie": PoseSpec(
        "lying flat on the back on the ground, defeated, the legs stretched out, the arms limp at the sides",
        "lying flat on the ground, defeated, the legs stretched out, the arms limp at the sides",
        legs=(10, 2),
        arms=(-15, -8),
        knees=(10, 15),
        elbows=(10, 10),
        tilt=-88,
    ),
    "cast_gather": PoseSpec(
        "gathering magic, both arms bent with the hands drawn together in front of the chest and a small glow "
        "between them, the feet planted apart, the knees slightly bent",
        "gathering magic, both hands drawn together in front of the chest with a small glow between them, the feet "
        "planted apart",
        legs=(14, -12),
        arms=(40, 35),
        knees=(14, 10),
        elbows=(95, 95),
    ),
    "cast_raise": PoseSpec(
        "both arms raised high above the head channeling magic, a bright glow over the hands, the body leaning "
        "back, the legs braced",
        "both arms raised high above the head channeling magic, a bright glow over the hands, the legs braced",
        legs=(16, -14),
        arms=(165, 155),
        knees=(8, 8),
        elbows=(15, 15),
        tilt=-5,
    ),
    "cast_release": PoseSpec(
        "thrusting both arms straight forward releasing a burst of magic from the hands, the body leaning into "
        "it, the front knee bent",
        "thrusting both hands toward the viewer releasing a burst of magic, the feet braced apart",
        legs=(26, -24),
        arms=(88, 82),
        knees=(28, 4),
        elbows=(0, 0),
        tilt=6,
    ),
    "cast_follow": PoseSpec(
        "the arms lowering after casting, the hands still glowing faintly, the body recovering upright, the knees "
        "slightly bent",
        "the arms lowering after casting, the hands still glowing faintly, the knees slightly bent",
        legs=(16, -14),
        arms=(55, 45),
        knees=(14, 10),
        elbows=(30, 30),
    ),
    # Morte: a 1.ª fase era o "hurt" (atirado para trás) e o modelo desenhava-o
    # torto antes de ajoelhar — cambalear para a frente encadeia com o joelho.
    "death_hit": PoseSpec(
        "staggering from a fatal blow, one hand clutching the chest, the other arm hanging, the knees starting to "
        "buckle, the head dropping",
        "staggering from a fatal blow, one hand clutching the chest, the knees starting to buckle, the head dropping",
        legs=(10, -10),
        arms=(30, -8),
        knees=(30, 22),
        elbows=(115, 10),
        tilt=6,
    ),
    "block_guard": PoseSpec(
        "blocking, the shield or weapon raised in front of the face with the front forearm held up vertically, the "
        "feet braced apart, the knees bent, the body turned slightly behind the guard",
        "blocking, the shield or weapon raised in front of the face, both forearms up, the feet braced apart, the "
        "knees bent",
        legs=(20, -22),
        arms=(70, 45),
        knees=(24, 14),
        elbows=(95, 80),
    ),
    "block_impact": PoseSpec(
        "absorbing a blow behind the raised guard, pushed back, the torso leaning back, the back leg bracing hard, "
        "the knees deeply bent, both arms locked behind the guard",
        "absorbing a blow behind the raised guard, pushed back, the knees deeply bent, the feet sliding, both arms "
        "locked behind the guard",
        legs=(26, -30),
        arms=(65, 40),
        knees=(38, 20),
        elbows=(100, 85),
        tilt=-9,
    ),
    "roll_tuck": PoseSpec(
        "diving forward into a roll, the body curled with the head tucked down, the knees pulled to the chest, the "
        "arms wrapped around the shins",
        "diving into a roll, the body curled into a ball, the head tucked down, the knees pulled to the chest",
        legs=(80, 70),
        arms=(60, 50),
        knees=(130, 125),
        elbows=(70, 70),
        tilt=50,
    ),
    "roll_ball": PoseSpec(
        "rolling on the ground curled into a tight ball, upside down mid-roll, the back rounded on the ground, the "
        "knees tucked to the chest, the arms hugging the legs",
        "rolling on the ground curled into a tight ball, the knees tucked to the chest, the arms hugging the legs",
        legs=(85, 75),
        arms=(70, 60),
        knees=(135, 130),
        elbows=(80, 80),
        tilt=170,
    ),
    "roll_rise": PoseSpec(
        "coming up out of a roll onto one knee, the body still low, the other foot planted forward, the arms "
        "reaching forward for balance",
        "coming up out of a roll into a low crouch, the body still low, the arms reaching forward for balance",
        legs=(60, -20),
        arms=(55, 40),
        knees=(100, 120),
        elbows=(30, 30),
        tilt=15,
    ),
    "shoot_nock": PoseSpec(
        "holding a bow lowered in the front hand, the back hand reaching to nock an arrow on the string, standing "
        "sideways with the legs apart, both arms low",
        "holding a bow lowered in front of the body, nocking an arrow, the feet apart",
        legs=(16, -16),
        arms=(30, 20),
        knees=(8, 6),
        elbows=(20, 70),
    ),
    "shoot_draw": PoseSpec(
        "aiming a bow at full draw, the front arm stretched straight forward holding the bow, the back hand pulling "
        "the string back to the cheek with the elbow high, the feet planted apart",
        "aiming a bow at full draw toward the viewer, the bow arm stretched forward, the string hand at the cheek",
        legs=(18, -18),
        arms=(92, 92),
        knees=(8, 6),
        elbows=(0, 160),
    ),
    "shoot_release": PoseSpec(
        "releasing an arrow from the bow, the front arm still stretched forward, the back hand flung open behind "
        "the head after letting the string go, the arrow flying away, "
        "the legs planted apart",
        "releasing an arrow, the bow arm stretched forward, the string hand flung back past the ear",
        legs=(18, -18),
        arms=(92, 120),
        knees=(8, 6),
        elbows=(0, 90),
        tilt=-3,
    ),
    "shoot_recover": PoseSpec(
        "lowering the bow after the shot, the front arm dropping, the back hand returning to the side, the legs in a "
        "relaxed stance",
        "lowering the bow after the shot, both arms dropping, relaxed stance",
        legs=(14, -14),
        arms=(45, 10),
        knees=(8, 6),
        elbows=(15, 20),
    ),
    "kick_chamber": PoseSpec(
        "raising the front knee high to chamber a kick, the foot pulled back under the thigh, balancing on the "
        "back leg, both arms bent with the fists up in guard",
        "raising the right knee high to chamber a kick, balancing on the left leg, both arms bent with the fists up",
        legs=(85, -4),
        arms=(60, 50),
        knees=(120, 8),
        elbows=(110, 110),
        tilt=-4,
    ),
    "kick_extend": PoseSpec(
        "a high front kick with the front leg fully extended forward at waist height, the torso leaning back, "
        "balancing on the back leg, the arms out for balance",
        "a high kick with the right leg fully extended toward the viewer, balancing on the left leg, the arms out",
        legs=(95, -6),
        arms=(30, -40),
        knees=(0, 8),
        elbows=(30, 30),
        tilt=-14,
    ),
    "kick_recover": PoseSpec(
        "pulling the kicking leg back with the knee bent, about to set the foot down, both arms bent with the fists "
        "returning to guard",
        "pulling the kicking leg back down, both arms bent with the fists returning to guard",
        legs=(50, -10),
        arms=(55, 45),
        knees=(80, 10),
        elbows=(100, 100),
    ),
    "punch_guard": PoseSpec(
        "a boxing stance, both fists raised in front of the chin, both arms bent with the elbows tucked in, one foot "
        "forward, the knees bent",
        "a boxing stance facing forward, both fists raised in front of the chin, the knees bent",
        legs=(18, -18),
        arms=(55, 50),
        knees=(16, 12),
        elbows=(120, 120),
    ),
    "punch_jab": PoseSpec(
        "throwing a straight punch with the front arm fully extended forward at shoulder height, the back fist "
        "guarding the chin, leaning into the punch, the front "
        "knee bent",
        "throwing a straight punch toward the viewer with the right arm extended, the left fist guarding the chin",
        legs=(24, -22),
        arms=(90, 50),
        knees=(20, 8),
        elbows=(0, 120),
        tilt=5,
    ),
    "punch_cross": PoseSpec(
        "throwing a powerful cross punch with the back arm fully extended forward, the front fist pulled back to "
        "the chin, the hips and shoulders rotated into the punch, "
        "the back leg pushing off",
        "throwing a cross punch toward the viewer with the left arm extended, the right fist pulled back",
        legs=(26, -26),
        arms=(45, 90),
        knees=(24, 4),
        elbows=(120, 0),
        tilt=8,
    ),
    "victory_fist": PoseSpec(
        "celebrating victory, one fist thrust high above the head, the other hand on the hip, standing proud "
        "on straight legs, the chest out, one arm up and one arm bent",
        "celebrating victory, one fist thrust high above the head, the other hand on the hip, standing proud",
        legs=(6, -6),
        arms=(175, -20),
        knees=(4, 4),
        elbows=(5, 110),
    ),
    "victory_jump": PoseSpec(
        "jumping for joy in mid-air, both arms thrown up high in a V, the legs tucked under, a big smile",
        "jumping for joy in mid-air, both arms thrown up high in a V, the legs tucked under",
        legs=(30, 15),
        arms=(165, 150),
        knees=(70, 60),
        elbows=(5, 5),
        lift=0.12,
    ),
    "sneak_contact": PoseSpec(
        "sneaking on tiptoe in a low crouch, the front leg reaching forward carefully, the back leg bent behind, "
        "the torso hunched forward, both arms bent in front of the chest",
        "sneaking in a low crouch, the right leg stepping forward carefully, the torso hunched, the arms bent in front",
        legs=(55, -20),
        arms=(45, 30),
        knees=(95, 60),
        elbows=(90, 90),
        tilt=10,
    ),
    "sneak_passing": PoseSpec(
        "sneaking in a low crouch, the back leg lifted and passing forward quietly with the knee bent, the torso "
        "hunched forward, both arms bent in front of the chest",
        "sneaking in a low crouch, the left knee lifted, the torso hunched, the arms bent in front",
        legs=(30, 45),
        arms=(45, 30),
        knees=(85, 120),
        elbows=(90, 90),
        tilt=10,
    ),
    "sneak_contact_back": PoseSpec(
        "sneaking on tiptoe in a low crouch, the other leg now reaching forward carefully, the first leg bent "
        "behind, the torso hunched forward, both arms bent in front of the chest",
        "sneaking in a low crouch, the left leg stepping forward carefully, the torso hunched, the arms bent in front",
        legs=(-20, 55),
        arms=(30, 45),
        knees=(60, 95),
        elbows=(90, 90),
        tilt=10,
    ),
    "sneak_passing_back": PoseSpec(
        "sneaking in a low crouch, the first leg now lifted and passing forward quietly with the knee bent, the "
        "torso hunched forward, both arms bent in front of the chest",
        "sneaking in a low crouch, the right knee lifted, the torso hunched, the arms bent in front",
        legs=(45, 30),
        arms=(30, 45),
        knees=(120, 85),
        elbows=(90, 90),
        tilt=10,
    ),
}

# Ciclos canónicos. Walk 4: contacto → passa a perna de trás → contacto da
# outra → passa a outra. Walk 8 acrescenta o "down" (mais baixo) e o "up" (mais
# alto) de cada passada — o bob vertical que dá peso ao andar.
WALK_CYCLE: tuple[str, ...] = ("walk_contact_front", "walk_passing", "walk_contact_back", "walk_passing_back")
WALK_CYCLE_8: tuple[str, ...] = (
    "walk_contact_front",
    "walk_down",
    "walk_passing",
    "walk_up",
    "walk_contact_back",
    "walk_down_back",
    "walk_passing_back",
    "walk_up_back",
)

# Ciclo de corrida (4 frames) — alterna a perna da frente.
RUN_CYCLE: tuple[str, ...] = ("run_contact", "run_air", "run_contact_back", "run_air_back")

# Furtivo: o andar de 4 tempos agachado (mesma alternância de pernas).
SNEAK_CYCLE: tuple[str, ...] = ("sneak_contact", "sneak_passing", "sneak_contact_back", "sneak_passing_back")

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
    return sorted(_POSES)


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
    spec = _POSES[pose]
    return spec.front if _TEXT_VIEW.get(view, view) == "front" else spec.side


def pose_lift(pose: str) -> float:
    """Elevação dos pés (fração da altura da personagem) — 0 para poses no chão."""
    return _POSES[pose].lift


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
# Silhueta procedural articulada (referência de forma)
# ---------------------------------------------------------------------------

_WHITE = (255, 255, 255, 255)
_FAR = (150, 150, 150, 255)


def _pose(pose: str) -> PoseSpec:
    try:
        return _POSES[pose]
    except KeyError:
        raise KeyError(f"pose desconhecida: {pose!r} (disponíveis: {', '.join(pose_names())})") from None


def draw_pose_silhouette(pose: str, *, view: str = "side", size: int = 256) -> Image.Image:
    """Desenha a silhueta articulada da pose (branco/cinzento sobre transparente).

    Serve de **referência de forma**: o modelo copia a pose desenhada. Regras
    (validadas E2E na 4050):

    - Membros com **joelho e cotovelo** (coxa+canela, braço+antebraço) e pé a
      apontar para a frente: com pernas retas um agachamento era só "pernas
      curtas" e o modelo desenhava a personagem de pé.
    - Lado **far** a cinzento e atrás do tronco (sem essa pista os dois
      contactos de perfil são silhuetas iguais).
    - ``front``/``back``: pernas verticais (em "V" o modelo virava de perfil);
      a passada lê-se no comprimento e o joelho dobrado levanta o pé.
    - ``diag_*``: ângulos de perfil atenuados + encurtamento em profundidade.
    - A figura é desenhada à volta da anca e depois **assente no chão** (o pé
      mais baixo na linha do chão, menos ``lift``) e rodada por ``tilt``.

    Args:
        pose: Nome da pose.
        view: ``side``, ``front``, ``back``, ``diag_front`` ou ``diag_back``.
        size: Lado da imagem em pixels.

    Raises:
        KeyError: Pose desconhecida.
    """
    spec = _pose(pose)
    big = size * 2
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size * 0.92  # escala do corpo (deixa folga para braços erguidos)
    hip = (big / 2, big / 2)
    thigh = shin = s * 0.145
    upper_arm = forearm = s * 0.105
    leg_w = max(2, int(s * 0.05))
    arm_w = max(2, int(s * 0.04))
    torso_len = s * 0.22
    head_r = s * 0.085
    torso_half = s * (0.075 if view.startswith("diag") else 0.085)
    sh_y = hip[1] - torso_len * 0.85

    def seg(p: tuple[float, float], angle: float, length: float) -> tuple[float, float]:
        a = math.radians(angle)
        return p[0] + math.sin(a) * length, p[1] + math.cos(a) * length

    def chain(
        p0: tuple[float, float],
        pts: list[tuple[float, float]],
        width: int,
        fill: tuple[int, ...],
        outline: bool = False,
    ) -> None:
        # Contorno preto nos membros near: sem ele o braço branco por cima do
        # tronco branco desaparecia (só se via a mão a sair do corpo).
        layers = [((0, 0, 0, 255), width + max(2, width // 2)), (fill, width)] if outline else [(fill, width)]
        for color, w in layers:
            d.line([p0, *pts], fill=color, width=w, joint="curve")
            for q in (p0, *pts):
                r = w / 2
                d.ellipse([q[0] - r, q[1] - r, q[0] + r, q[1] + r], fill=color)

    def torso_and_head(facing: float) -> None:
        d.rounded_rectangle(
            [hip[0] - torso_half, hip[1] - torso_len, hip[0] + torso_half, hip[1]], radius=s * 0.03, fill=_WHITE
        )
        cy = sh_y - head_r * 0.85
        d.ellipse([hip[0] - head_r, cy - head_r, hip[0] + head_r, cy + head_r], fill=_WHITE)
        if facing:
            # Nariz/viseira: diz ao modelo para onde a cabeça olha.
            nx = hip[0] + facing * head_r
            d.polygon(
                [(nx, cy - head_r * 0.3), (nx + facing * head_r * 0.45, cy + head_r * 0.15), (nx, cy + head_r * 0.4)],
                fill=_WHITE,
            )

    if view in ("front", "back"):
        _draw_front_back(d, spec, pose, view, hip, s, thigh + shin, upper_arm + forearm, leg_w, arm_w, sh_y)
        torso_and_head(0.0)
        _draw_front_arms(d, spec, pose, hip, s, upper_arm + forearm, arm_w, sh_y)
    else:
        lateral = 0.55 if view.startswith("diag") else 1.0
        depth = {"diag_front": 0.22, "diag_back": -0.22}.get(view, 0.0)
        facing = 0.0 if view == "diag_back" else (0.6 if view == "diag_front" else 1.0)

        def leg(i: int, x_off: float, fill: tuple[int, ...]) -> None:
            ang = spec.legs[i]
            scale = 1.0 + depth * math.sin(math.radians(ang))
            p0 = (hip[0] + x_off, hip[1])
            knee = seg(p0, ang * lateral, thigh * scale)
            ankle = seg(knee, (ang - spec.knees[i]) * lateral, shin * scale)
            toe = (ankle[0] + s * 0.05 * max(lateral, 0.6), ankle[1])
            chain(p0, [knee, ankle, toe], leg_w, fill, outline=fill == _WHITE)

        def arm(i: int, x_off: float, fill: tuple[int, ...]) -> None:
            ang = spec.arms[i]
            p0 = (hip[0] + x_off, sh_y)
            elbow = seg(p0, ang * lateral, upper_arm)
            hand = seg(elbow, (ang + spec.elbows[i]) * lateral, forearm)
            chain(p0, [elbow, hand], arm_w, fill, outline=fill == _WHITE)

        leg(1, s * 0.018, _FAR)
        arm(1, -s * 0.022, _FAR)
        leg(0, -s * 0.018, _WHITE)
        torso_and_head(facing)
        arm(0, s * 0.022, _WHITE)

    if spec.tilt:
        # Roda à volta da anca (PIL: positivo = anti-horário = para trás numa
        # personagem virada à direita).
        img = img.rotate(-spec.tilt, resample=Image.Resampling.BICUBIC, center=hip)
    return _ground(img, size=size, lift=spec.lift, center_x=hip[0] if not spec.tilt else None)


def _front_stride(spec: PoseSpec) -> float:
    """Passada de frente/costas -1..1 (>0 = perna near à frente).

    Mede-se pela diferença entre as coxas, não pelo ângulo da near: numa pose
    agachada as duas coxas vão para a frente (furtivo, agachar) e a v2.2 lia
    isso como passada larga.
    """
    return max(-1.0, min(1.0, (spec.legs[0] - spec.legs[1]) / 56.0))


def _draw_front_back(
    d: ImageDraw.ImageDraw,
    spec: PoseSpec,
    pose: str,
    view: str,
    hip: tuple[float, float],
    s: float,
    leg_len: float,
    arm_len: float,
    leg_w: int,
    arm_w: int,
    sh_y: float,
) -> None:
    """Pernas de frente/costas: verticais; passada no comprimento, joelho levanta o pé."""
    del pose, arm_len, arm_w, sh_y
    stride = _front_stride(spec)
    if abs(stride) < 0.5:
        stride = 0.0
    for i, side in ((0, 1), (1, -1)):
        ahead = stride * side
        if view == "front":
            scale = 1.0 + 0.08 * max(ahead, 0.0) - 0.28 * max(-ahead, 0.0)
            foot = s * (0.035 + 0.01 * max(ahead, 0.0))
        else:
            # De costas o pé que avança afasta-se (sobe); o de trás mostra a sola.
            scale = 1.0 - 0.24 * max(ahead, 0.0) + 0.04 * max(-ahead, 0.0)
            foot = s * (0.035 + 0.014 * max(-ahead, 0.0))
        bend = spec.knees[i]
        # Joelho dobrado: o pé sobe e o joelho abre um pouco para fora.
        lift_frac = 1.0 - 0.5 * (1 - math.cos(math.radians(bend)))
        length = leg_len * scale * lift_frac
        x = hip[0] + side * s * 0.045
        knee = (x + side * s * 0.02 * math.sin(math.radians(bend)), hip[1] + length * 0.5)
        end = (x, hip[1] + length)
        d.line([(x, hip[1]), knee, end], fill=_WHITE, width=int(leg_w * 1.1), joint="curve")
        d.ellipse([end[0] - foot, end[1] - foot * 0.7, end[0] + foot, end[1] + foot * 0.7], fill=_WHITE)


def _draw_front_arms(
    d: ImageDraw.ImageDraw,
    spec: PoseSpec,
    pose: str,
    hip: tuple[float, float],
    s: float,
    arm_len: float,
    arm_w: int,
    sh_y: float,
) -> None:
    """Braços de frente: o ângulo de perfil vira altura (cos) — erguido = para cima."""
    swinging = pose.startswith(("idle", "walk", "run"))
    stride = _front_stride(spec)
    for i, side in ((0, 1), (1, -1)):
        x = hip[0] + side * s * 0.1
        if swinging:
            # Braço oposto à perna da frente vem à frente → encurta (perspetiva).
            length = arm_len * (1.0 - 0.18 * max(-stride * side, 0.0))
            end = (x + side * math.sin(math.radians(8)) * length, sh_y + math.cos(math.radians(8)) * length)
        else:
            a = math.radians(spec.arms[i])
            vert = math.cos(a) * arm_len  # >0 para baixo, <0 para cima
            end = (x + side * s * (0.05 + 0.04 * abs(math.sin(a))), sh_y + vert)
        d.line([(x, sh_y), end], fill=_WHITE, width=arm_w)
        r = arm_w * 0.8
        d.ellipse([end[0] - r, end[1] - r, end[0] + r, end[1] + r], fill=_WHITE)


def _ground(img: Image.Image, *, size: int, lift: float, center_x: float | None) -> Image.Image:
    """Recorta a figura desenhada em ``img`` (2x) para ``size``, pés no chão.

    O ponto mais baixo fica na linha do chão (0.94 de ``size``) menos ``lift``;
    horizontalmente a anca (ou o centro do bbox, se o corpo rodou) fica ao centro.
    """
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bbox = img.getchannel("A").getbbox()
    if bbox is None:
        return out
    cx = center_x if center_x is not None else (bbox[0] + bbox[2]) / 2
    ground = size * 0.94 - lift * size
    out.alpha_composite(img, (round(size / 2 - cx), round(ground - bbox[3])))
    return out


def draw_pose_sheet(
    poses: tuple[str, ...] | list[str],
    *,
    view: str = "side",
    cell: tuple[int, int] = (256, 320),
    cols: int = 4,
) -> Image.Image:
    """Folha de silhuetas (grelha de ``cols`` colunas) sobre fundo preto.

    É a referência de forma do ``animate``: gerar **todos os frames numa só
    imagem** com esta folha como guia dá a mesma personagem, escala e linha do
    chão em todas as células. 8 frames = 2 linhas de 4 (1024x640).

    Raises:
        KeyError: Pose desconhecida.
    """
    cell_w, cell_h = cell
    ncols = max(1, min(cols, len(poses)))
    rows = (len(poses) + ncols - 1) // ncols
    sheet = Image.new("RGB", (cell_w * ncols, cell_h * rows), (0, 0, 0))
    for i, pose in enumerate(poses):
        sil = draw_pose_silhouette(pose, view=view, size=cell_h)
        x = (i % ncols) * cell_w + (cell_w - cell_h) // 2
        sheet.paste(sil, (x, (i // ncols) * cell_h), sil)
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
