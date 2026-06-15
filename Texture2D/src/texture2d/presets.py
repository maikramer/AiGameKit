"""Presets de materiais para geração de texturas seamless."""

from typing import Any

TEXTURE_PRESETS: dict[str, dict[str, Any]] = {
    "Wood": {
        "prompt": "seamless wood texture, natural grain, high detail, tiling pattern",
        "negative_prompt": "blurry, low quality, distorted",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Fabric": {
        "prompt": "seamless fabric texture, woven material, soft surface, repeating pattern",
        "negative_prompt": "hard surface, metal, plastic",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Metal": {
        "prompt": "seamless metal texture, brushed steel, reflective surface, industrial",
        "negative_prompt": "soft, fabric, organic",
        "guidance_scale": 8.0,
        "num_inference_steps": 60,
    },
    "Stone": {
        "prompt": "seamless stone texture, natural rock surface, rough texture, tiling",
        "negative_prompt": "smooth, polished, artificial",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Brick": {
        "prompt": "seamless brick texture, red bricks, mortar lines, repeating pattern",
        "negative_prompt": "smooth, uniform, painted",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Leather": {
        "prompt": "seamless leather texture, natural grain, soft surface, high detail",
        "negative_prompt": "synthetic, plastic, smooth",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Concrete": {
        "prompt": "seamless concrete texture, rough surface, industrial, tiling pattern",
        "negative_prompt": "smooth, polished, painted",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Marble": {
        "prompt": "seamless marble texture, natural veins, polished surface, elegant",
        "negative_prompt": "rough, matte, artificial",
        "guidance_scale": 8.0,
        "num_inference_steps": 60,
    },
    # Game-dev extras
    "Grass": {
        "prompt": "seamless grass texture, lush green lawn, natural blades, game ground texture, tiling",
        "negative_prompt": "brown, dead, mud, artificial",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Sand": {
        "prompt": "seamless sand texture, fine desert sand, natural dunes detail, game terrain, tiling",
        "negative_prompt": "rocks, grass, water, artificial",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Dirt": {
        "prompt": "seamless dirt texture, brown earth soil, natural ground, game terrain, tiling",
        "negative_prompt": "grass, clean, polished, artificial",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Gravel": {
        "prompt": "seamless gravel texture, small stones, rocky ground path, game terrain, tiling",
        "negative_prompt": "smooth, polished, grass, artificial",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
    "Tile Floor": {
        "prompt": "seamless ceramic tile floor texture, clean grid pattern, game interior, tiling",
        "negative_prompt": "broken, dirty, outdoor, natural",
        "guidance_scale": 7.5,
        "num_inference_steps": 50,
    },
}


def get_preset(name: str) -> dict[str, Any] | None:
    """Obtém um preset pelo nome."""
    return TEXTURE_PRESETS.get(name)


def list_presets() -> list[str]:
    """Lista todos os nomes de presets disponíveis."""
    return list(TEXTURE_PRESETS.keys())


def get_preset_prompt(name: str) -> str | None:
    """Obtém o prompt de um preset."""
    preset = get_preset(name)
    return preset.get("prompt") if preset else None


def get_preset_params(name: str) -> dict[str, Any] | None:
    """Obtém os parâmetros de um preset (excluindo prompt)."""
    preset = get_preset(name)
    if not preset:
        return None
    params = preset.copy()
    params.pop("prompt", None)
    return params
