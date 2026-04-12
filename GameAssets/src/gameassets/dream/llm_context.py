"""System prompt e schema para o LLM planner do dream."""

from __future__ import annotations

import json
from typing import Any

DREAM_PLAN_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["title", "genre", "tone", "style_preset", "assets", "scene"],
    "properties": {
        "title": {"type": "string"},
        "genre": {"type": "string"},
        "tone": {"type": "string"},
        "style_preset": {"type": "string"},
        "sky_prompt": {"type": "string"},
        "negative_keywords": {"type": "array", "items": {"type": "string"}},
        "assets": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "idea", "kind"],
                "properties": {
                    "id": {"type": "string"},
                    "idea": {"type": "string"},
                    "kind": {"type": "string", "enum": ["prop", "character", "environment"]},
                    "generate_3d": {"type": "boolean"},
                    "generate_audio": {"type": "boolean"},
                    "generate_rig": {"type": "boolean"},
                    "generate_parts": {"type": "boolean"},
                },
            },
        },
        "scene": {
            "type": "object",
            "required": ["placements"],
            "properties": {
                "sky_color": {"type": "string"},
                "ground_size": {"type": "number"},
                "spawn_y": {"type": "number"},
                "placements": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["asset_id", "pos"],
                        "properties": {
                            "asset_id": {"type": "string"},
                            "pos": {"type": "string"},
                            "scale": {"type": "string"},
                        },
                    },
                },
            },
        },
        "terrain": {
            "type": "object",
            "properties": {
                "enabled": {"type": "boolean", "description": "Whether to generate procedural terrain"},
                "seed": {"type": "integer", "description": "Random seed for terrain generation (omit for random)"},
                "prompt": {
                    "type": "string",
                    "description": "Terrain description, e.g. mountain island, flat plains with river, volcanic island",
                },
                "world_size": {"type": "number", "description": "World extent in meters (default: 768)"},
                "max_height": {"type": "number", "description": "Max terrain height in meters (default: 50)"},
                "size": {
                    "type": "integer",
                    "description": "Heightmap resolution in pixels (default: 2048; larger = more detail)",
                },
                "river_threshold": {
                    "type": "number",
                    "description": "Flow accumulation threshold for rivers (default: 4000; lower = more rivers)",
                },
                "erosion_particles": {
                    "type": "integer",
                    "description": "Erosion particle count (default: 80000)",
                },
                "lake_min_area": {
                    "type": "integer",
                    "description": "Minimum lake size in heightmap pixels (default: 20000; higher = only large basins)",
                },
                "lake_max_count": {
                    "type": "integer",
                    "description": "Max lakes to place (default: 3; each is one Water entity; 0 = unlimited)",
                },
            },
        },
    },
}

VIBEGAME_RECIPES = [
    "GameObject",
    "MeshRenderer",
    "static-part",
    "dynamic-part",
    "kinematic-part",
    "Player",
    "PlayerGLTF",
    "OrbitCamera",
    "FollowCamera",
    "GLTFLoader",
    "GLTFDynamic",
    "SpawnGroup",
    "Terrain",
    "Water",
    "Fog",
    "AudioSource",
    "postprocessing",
    "bloom",
    "vignette",
    "chromatic-aberration",
    "noise",
    "Paragraph",
    "Word",
]

SCENE_RULES = """\
- The scene MUST have at least one large ground platform (static-part or GLTFLoader).
- spawn_y should be above the highest ground surface so the player does not clip.
- Positions use "X Y Z" strings (Y is up). Reasonable range: -50 to 50 per axis.
- Scale uses "X Y Z" strings. Ground platforms typically "10 1 10" or larger.
- Each placement references an asset_id from the assets array (only 3D assets).
- Audio-only assets (generate_audio=true, generate_3d=false) do NOT appear in placements.
- Characters with generate_rig=true should have kind="character".
- Props are small objects; environments are large surfaces or backdrops.
- Keep total assets <= max_assets to avoid excessive GPU time.
- sky_prompt should describe a 360-degree equirectangular panoramic sky.
- Provide varied, creative ideas for each asset — avoid generic descriptions.
- All string values in the JSON must be valid JSON (escaped quotes if needed).
- Characters with generate_rig=true must use `<PlayerGLTF>` instead of `<GLTFLoader>` in the scene XML.
- Enable terrain (terrain.enabled=true) for open-world, exploration, RPG, or outdoor games that benefit
  from a procedural landscape instead of a flat ground plane.
- Do NOT enable terrain for simple platformers, interior scenes, or games that only need a flat arena.
- terrain.prompt should describe the landscape character (e.g. "mountain island",
  "rolling hills with scattered lakes", "volcanic terrain").
- terrain.world_size should match the scale of the game: 128-384 for small arenas,
  512-768 for exploration, 768+ for large worlds.
- terrain.max_height controls elevation range: 20-30 for gentle hills, 40-80 for mountains,
  100+ for dramatic peaks.
- terrain.lake_max_count: use 2-3 for believable open worlds (each lake is a Water entity); avoid large values.\
"""


def build_system_prompt(
    *,
    preset_names: list[str],
    max_assets: int = 8,
    with_audio: bool = True,
    with_sky: bool = True,
) -> str:
    presets_str = ", ".join(preset_names) if preset_names else "lowpoly"
    schema_str = json.dumps(DREAM_PLAN_SCHEMA, indent=2)

    audio_note = (
        "You may include audio assets (generate_audio=true, generate_3d=false) for sound effects."
        if with_audio
        else "Do NOT include audio assets (with_audio is disabled)."
    )

    sky_note = (
        "Include a sky_prompt field for a 360-degree equirectangular sky image."
        if with_sky
        else "Do NOT include sky_prompt (sky generation is disabled)."
    )

    return f"""\
You are an expert game designer and asset planner. Given a game concept described in \
natural language, produce a JSON object that defines the game's assets, visual style, \
and scene layout for a 3D browser game powered by VibeGame (ECS + Three.js).

## Available style presets
{presets_str}

## JSON Schema
{schema_str}

## Scene layout rules
{SCENE_RULES}

## Additional constraints
- Maximum total assets: {max_assets}
- {audio_note}
- {sky_note}
- style_preset MUST be one of the available presets listed above.
- The JSON must be valid and parseable. Do NOT wrap it in markdown code fences.
- Respond ONLY with the JSON object — no explanation, no extra text.

## VibeGame recipes (for reference — used later to build the scene XML)
{", ".join(VIBEGAME_RECIPES)}
"""
