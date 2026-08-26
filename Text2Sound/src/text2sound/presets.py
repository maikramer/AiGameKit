"""Text2Sound — presets temáticos para desenvolvimento de jogos.

Cada preset define prompt, duração e *kind* (audio_kind do QualityEngine)
otimizados para cenários comuns de game audio. Os parâmetros de inferência
(steps, cfg, sampler) não fazem parte do preset: vêm do ``ModelSpec`` do
modelo ativo (SA3: steps 8, cfg 1.0, pingpong) ou do ``--quality`` tier.
"""

from __future__ import annotations

from typing import Any

from aigamekit_shared.presets import (
    get_preset_ci as _get_preset_ci,
)
from aigamekit_shared.presets import (
    list_presets as _list_presets,
)

AUDIO_PRESETS: dict[str, dict[str, Any]] = {
    # ── Ambiences (12) ─────────────────────────────────────────────────
    "ambient": {
        "prompt": (
            "Calm ambient soundscape with gentle wind, distant birds chirping, "
            "and soft nature atmosphere, peaceful and immersive"
        ),
        "kind": "ambient_loop",
        "duration": 45,
    },
    "forest": {
        "prompt": (
            "Dense forest ambience with birds singing, leaves rustling in wind, "
            "distant stream flowing, rich woodland atmosphere"
        ),
        "kind": "ambient_loop",
        "duration": 45,
    },
    "ocean": {
        "prompt": "Ocean waves crashing on shore, rhythmic sea surf, coastal seascape with seagulls in distance",
        "kind": "ambient_loop",
        "duration": 45,
    },
    "rain": {
        "prompt": (
            "Steady rainfall with distant thunder rumbles, rain hitting surfaces, "
            "atmospheric storm ambience for game environment"
        ),
        "kind": "ambient_loop",
        "duration": 45,
    },
    "wind": {
        "prompt": "Strong wind blowing through open landscape, gusty howling wind, atmospheric outdoor wind ambience",
        "kind": "ambient_loop",
        "duration": 30,
    },
    "dungeon": {
        "prompt": (
            "Dark dungeon ambience with dripping water echoes, distant chains, "
            "eerie underground cave atmosphere, subtle tension"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "tavern": {
        "prompt": (
            "Busy medieval tavern atmosphere with crowd chatter, "
            "clinking glasses, crackling fireplace, and distant lute music"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "cave": {
        "prompt": (
            "Underground cave ambience with water drips, echoing stalactites, "
            "deep subterranean resonance, mysterious and dark"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "city": {
        "prompt": (
            "Bustling city streets ambience with traffic, crowd murmurs, distant sirens, urban daytime atmosphere"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "desert": {
        "prompt": (
            "Vast desert ambience with hot wind, sand grains swirling, "
            "distant heat shimmer, arid and desolate atmosphere"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "space": {
        "prompt": (
            "Deep space ambience with low frequency hum, cosmic radiation static, "
            "ethereal void atmosphere, sci-fi environment"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    "underwater": {
        "prompt": (
            "Underwater ambience with muffled bubbles, distant whale song, deep ocean current, aquatic atmosphere"
        ),
        "kind": "ambient_loop",
        "duration": 30,
    },
    # ── Music (6) ──────────────────────────────────────────────────────
    "battle": {
        "prompt": (
            "Intense orchestral battle music with epic drums, brass fanfare, "
            "driving percussion and heroic strings, cinematic game combat soundtrack"
        ),
        "kind": "music_loop",
        "duration": 30,
    },
    "menu": {
        "prompt": (
            "Soft loopable menu music with gentle piano, warm pads, "
            "and subtle ambient textures, calm and inviting game menu theme"
        ),
        "kind": "music_loop",
        "duration": 30,
    },
    "victory": {
        "prompt": (
            "Triumphant victory fanfare with brass and strings, uplifting celebratory short jingle, game level complete"
        ),
        "kind": "music_loop",
        "duration": 8,
    },
    "defeat": {
        "prompt": (
            "Somber defeat music with slow minor chords, descending melody, "
            "melancholic piano and strings, game over theme"
        ),
        "kind": "music_loop",
        "duration": 8,
    },
    "exploration": {
        "prompt": (
            "Peaceful exploration music with acoustic guitar, light percussion, "
            "ambient pads, adventurous wandering game soundtrack"
        ),
        "kind": "music_loop",
        "duration": 30,
    },
    "boss": {
        "prompt": (
            "Epic boss battle music with heavy orchestral hits, choir, "
            "aggressive brass, intense timpani, dramatic boss fight soundtrack"
        ),
        "kind": "music_loop",
        "duration": 30,
    },
    # ── SFX Impact (5) ─────────────────────────────────────────────────
    "explosion": {
        "prompt": "Powerful explosion blast with deep bass impact, debris scattering, cinematic boom sound effect",
        "kind": "sfx_impact",
        "duration": 5,
    },
    "sword-clash": {
        "prompt": (
            "Metal sword clash and parry, sharp metallic impact of blades, weapon combat sound effect with ring"
        ),
        "kind": "sfx_impact",
        "duration": 2,
    },
    "punch": {
        "prompt": ("Heavy punch impact with body hit thud, bone crunch, close-range melee combat punch sound effect"),
        "kind": "sfx_impact",
        "duration": 1.5,
    },
    "gunshot": {
        "prompt": ("Single gunshot with sharp crack and echo, muzzle blast, realistic firearm discharge sound effect"),
        "kind": "sfx_impact",
        "duration": 1,
    },
    "arrow": {
        "prompt": (
            "Arrow release with bowstring twang and swift flight whoosh, arrow impact thud, archery sound effect"
        ),
        "kind": "sfx_impact",
        "duration": 1.5,
    },
    # ── SFX Magic (4) ──────────────────────────────────────────────────
    "magic-spell": {
        "prompt": (
            "Magical spell cast with shimmering energy, mystical whoosh "
            "and crystalline sparkle, fantasy magic sound effect"
        ),
        "kind": "sfx_magic",
        "duration": 3,
    },
    "heal": {
        "prompt": (
            "Healing spell with warm glowing chime, gentle rising tone, "
            "restorative magic sound effect with soft sparkle"
        ),
        "kind": "sfx_magic",
        "duration": 2,
    },
    "teleport": {
        "prompt": (
            "Teleportation whoosh with spatial warp, shimmering energy displacement, instant travel magic sound effect"
        ),
        "kind": "sfx_magic",
        "duration": 2,
    },
    "shield": {
        "prompt": (
            "Magic shield activate with shimmering energy barrier, deflect impact, "
            "protective ward sound effect with resonance"
        ),
        "kind": "sfx_magic",
        "duration": 2,
    },
    # ── SFX Movement (4) ───────────────────────────────────────────────
    "footsteps-stone": {
        "prompt": "Footsteps walking on stone floor, clear and rhythmic steps on hard surface, indoor stone corridor",
        "kind": "sfx_movement",
        "duration": 4,
    },
    "footsteps-grass": {
        "prompt": (
            "Footsteps walking on grass and soft ground, quiet rustling of vegetation underfoot, outdoor nature path"
        ),
        "kind": "sfx_movement",
        "duration": 4,
    },
    "footsteps-wood": {
        "prompt": (
            "Footsteps walking on wooden planks, creaking floorboards, rhythmic steps on timber surface indoors"
        ),
        "kind": "sfx_movement",
        "duration": 4,
    },
    "footsteps-water": {
        "prompt": (
            "Footsteps wading through shallow water, splashing and squelching, wet ground movement sound effect"
        ),
        "kind": "sfx_movement",
        "duration": 4,
    },
    # ── SFX UI (4) ─────────────────────────────────────────────────────
    "ui-click": {
        "prompt": "Short UI click sound, clean digital button press, crisp interface interaction sound effect",
        "kind": "sfx_ui",
        "duration": 1,
    },
    "ui-confirm": {
        "prompt": "Positive confirmation chime, bright ascending tone, success notification sound for game interface",
        "kind": "sfx_ui",
        "duration": 1.5,
    },
    "ui-cancel": {
        "prompt": ("Negative cancel sound, descending tone with soft thud, action dismissed interface sound effect"),
        "kind": "sfx_ui",
        "duration": 1,
    },
    "ui-hover": {
        "prompt": ("Subtle UI hover sound, very soft tick, gentle interface mouse-over feedback"),
        "kind": "sfx_ui",
        "duration": 0.5,
    },
    # ── SFX Creature (3) ───────────────────────────────────────────────
    "creature-growl": {
        "prompt": (
            "Low creature growl with guttural rumble, threatening animal vocalization, monster warning sound effect"
        ),
        "kind": "sfx_creature",
        "duration": 3,
    },
    "creature-roar": {
        "prompt": (
            "Powerful creature roar with deep bass, aggressive beast vocalization, "
            "massive monster battle roar sound effect"
        ),
        "kind": "sfx_creature",
        "duration": 3,
    },
    "creature-death": {
        "prompt": (
            "Creature death cry with fading whimper, monstrous final breath, beast defeat and collapse sound effect"
        ),
        "kind": "sfx_creature",
        "duration": 3,
    },
    # ── SFX Destruction (3) ────────────────────────────────────────────
    "glass-break": {
        "prompt": (
            "Sharp glass shattering with multiple fragments, "
            "window break with scattered debris, destruction sound effect"
        ),
        "kind": "sfx_destruction",
        "duration": 1.5,
    },
    "wood-break": {
        "prompt": ("Wood splintering and cracking, wooden plank snap with breaking timber, destruction sound effect"),
        "kind": "sfx_destruction",
        "duration": 2,
    },
    "stone-crumble": {
        "prompt": (
            "Rocks crumbling and stones collapsing, heavy debris falling with dust, cave-in destruction sound effect"
        ),
        "kind": "sfx_destruction",
        "duration": 3,
    },
    # ── SFX Weapon (3) ─────────────────────────────────────────────────
    "sword-draw": {
        "prompt": (
            "Metal sword drawing from scabbard, blade sliding with sharp metallic ring, unsheathing weapon sound effect"
        ),
        "kind": "sfx_weapon",
        "duration": 1.5,
    },
    "bow-draw": {
        "prompt": "Bowstring tension and arrow nock, wooden bow creaking under tension, archery draw sound effect",
        "kind": "sfx_weapon",
        "duration": 2,
    },
    "weapon-reload": {
        "prompt": (
            "Mechanical weapon reload, magazine click and slide action, "
            "firearm reloading sound effect with metallic clicks"
        ),
        "kind": "sfx_weapon",
        "duration": 2,
    },
    # ── SFX Mechanical (4) ─────────────────────────────────────────────
    "door-open": {
        "prompt": "Heavy wooden door opening with creaking hinges, dungeon door swing, mechanical door sound effect",
        "kind": "sfx_mechanical",
        "duration": 2,
    },
    "door-close": {
        "prompt": (
            "Heavy door closing with solid thud and latch click, wooden door slam shut, mechanical close sound effect"
        ),
        "kind": "sfx_mechanical",
        "duration": 2,
    },
    "lever": {
        "prompt": (
            "Mechanical lever being pulled with gear engagement, metallic lever throw with mechanism sound effect"
        ),
        "kind": "sfx_mechanical",
        "duration": 1.5,
    },
    "clockwork": {
        "prompt": (
            "Clockwork gears turning with ticking mechanisms, "
            "intricate mechanical device operating, steampunk machinery sound"
        ),
        "kind": "sfx_mechanical",
        "duration": 3,
    },
    # ── SFX Elemental (3) ──────────────────────────────────────────────
    "fire-crackle": {
        "prompt": (
            "Fire crackling and burning with popping embers, "
            "campfire flames with wood burning, fire elemental sound effect"
        ),
        "kind": "sfx_elemental",
        "duration": 4,
    },
    "water-splash": {
        "prompt": (
            "Object splashing into water with ripples, heavy splash and water droplets, liquid impact sound effect"
        ),
        "kind": "sfx_elemental",
        "duration": 2,
    },
    "electricity-zap": {
        "prompt": (
            "Electric discharge zap with buzzing crackle, lightning bolt strike, electricity elemental sound effect"
        ),
        "kind": "sfx_elemental",
        "duration": 1.5,
    },
    # ── SFX Vocal (3) ──────────────────────────────────────────────────
    "grunt-effort": {
        "prompt": "Short human grunt of physical effort, heavy exertion vocalization, male exertion sound effect",
        "kind": "sfx_vocal",
        "duration": 1,
    },
    "battle-cry": {
        "prompt": "Powerful battle cry shout, warrior charging with loud yell, battle vocalization sound effect",
        "kind": "sfx_vocal",
        "duration": 2,
    },
    "death-scream": {
        "prompt": "Short death scream, character final cry, human defeat and dying vocalization sound effect",
        "kind": "sfx_vocal",
        "duration": 2,
    },
    # ── SFX Collectible (3) ────────────────────────────────────────────
    "coin-pickup": {
        "prompt": "Magical coin pickup chime, short sparkling coin collection, rewarding currency pickup sound effect",
        "kind": "sfx_collectible",
        "duration": 1,
    },
    "gem-collect": {
        "prompt": (
            "Precious gem collection with bright crystalline sparkle, "
            "valuable treasure pickup, rewarding chime sound effect"
        ),
        "kind": "sfx_collectible",
        "duration": 1.5,
    },
    "item-drop": {
        "prompt": (
            "Object dropping on ground with dull thud, item falling and landing on surface, inventory drop sound effect"
        ),
        "kind": "sfx_collectible",
        "duration": 1.5,
    },
    # ── SFX Alarm (2) ──────────────────────────────────────────────────
    "alarm-klaxon": {
        "prompt": "Loud emergency klaxon alarm siren, rotating warning horn, urgent alert signal sound effect",
        "kind": "sfx_alarm",
        "duration": 3,
    },
    "bell-toll": {
        "prompt": "Deep bell tolling with resonance decay, church tower bell ring, heavy bell strike sound effect",
        "kind": "sfx_alarm",
        "duration": 4,
    },
    # ── SFX Ambient Spot (1) ───────────────────────────────────────────
    "thunder-clap": {
        "prompt": (
            "Loud thunder clap with deep bass rumble, "
            "lightning strike with atmospheric boom, storm thunder sound effect"
        ),
        "kind": "sfx_ambient_sfx",
        "duration": 4,
    },
}


def list_presets() -> list[str]:
    """Retorna nomes dos presets disponíveis, ordenados."""
    return _list_presets(AUDIO_PRESETS, sorted_names=True)


def get_preset(name: str) -> dict[str, Any]:
    """Retorna preset pelo nome (case-insensitive).

    Raises:
        KeyError: Preset não encontrado.
    """
    return _get_preset_ci(AUDIO_PRESETS, name)
