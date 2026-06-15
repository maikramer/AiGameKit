"""Presets de ambiente para geração de skymaps equirectangular 360°."""

from typing import Any

SKYMAP_PRESETS: dict[str, dict[str, Any]] = {
    "Sunset": {
        "prompt": (
            "equirectangular 360 panorama, golden sunset sky, warm orange and pink clouds, "
            "sun near horizon, dramatic lighting"
        ),
        "negative_prompt": "indoor, ground level, people, text, watermark",
        "guidance_scale": 6.0,
        "num_inference_steps": 40,
    },
    "Night Sky": {
        "prompt": (
            "equirectangular 360 panorama, starry night sky, milky way galaxy, deep space, clear atmosphere, moonlight"
        ),
        "negative_prompt": "daylight, clouds, sun, indoor, text, watermark",
        "guidance_scale": 6.5,
        "num_inference_steps": 45,
    },
    "Overcast": {
        "prompt": (
            "equirectangular 360 panorama, overcast cloudy sky, grey clouds, diffuse soft lighting, moody atmosphere"
        ),
        "negative_prompt": "sun, clear sky, stars, indoor, text, watermark",
        "guidance_scale": 6.0,
        "num_inference_steps": 40,
    },
    "Clear Day": {
        "prompt": ("equirectangular 360 panorama, clear blue sky, few white clouds, bright daylight, calm atmosphere"),
        "negative_prompt": "rain, storm, night, stars, indoor, text, watermark",
        "guidance_scale": 6.0,
        "num_inference_steps": 40,
    },
    "Storm": {
        "prompt": (
            "equirectangular 360 panorama, stormy dark sky, dramatic thunderclouds, lightning bolts, ominous atmosphere"
        ),
        "negative_prompt": "clear sky, sunny, calm, indoor, text, watermark",
        "guidance_scale": 7.0,
        "num_inference_steps": 50,
    },
    "Space": {
        "prompt": (
            "equirectangular 360 panorama, outer space, colorful nebula, distant stars, cosmic dust, deep universe"
        ),
        "negative_prompt": "ground, terrain, buildings, people, text, watermark",
        "guidance_scale": 6.5,
        "num_inference_steps": 45,
    },
    "Alien World": {
        "prompt": (
            "equirectangular 360 panorama, alien planet sky, two moons, exotic colors, "
            "purple and teal atmosphere, sci-fi landscape"
        ),
        "negative_prompt": "earth, realistic, normal sky, indoor, text, watermark",
        "guidance_scale": 7.0,
        "num_inference_steps": 50,
    },
    "Dawn": {
        "prompt": (
            "equirectangular 360 panorama, dawn sky, early morning, pink and orange horizon, "
            "soft light, peaceful atmosphere"
        ),
        "negative_prompt": "night, stars, storm, indoor, text, watermark",
        "guidance_scale": 6.0,
        "num_inference_steps": 40,
    },
    "Underwater": {
        "prompt": (
            "equirectangular 360 panorama, underwater view, light rays through water surface, "
            "deep blue ocean, caustics, bubbles"
        ),
        "negative_prompt": "sky, clouds, land, buildings, text, watermark",
        "guidance_scale": 6.5,
        "num_inference_steps": 45,
    },
    "Fantasy": {
        "prompt": (
            "equirectangular 360 panorama, magical fantasy sky, aurora borealis, floating crystals, "
            "mystical atmosphere, ethereal glow"
        ),
        "negative_prompt": "realistic, modern, urban, indoor, text, watermark",
        "guidance_scale": 7.0,
        "num_inference_steps": 50,
    },
}


def get_preset(name: str) -> dict[str, Any] | None:
    """Obtém um preset pelo nome."""
    return SKYMAP_PRESETS.get(name)


def list_presets() -> list[str]:
    """Lista todos os nomes de presets disponíveis."""
    return list(SKYMAP_PRESETS.keys())


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
